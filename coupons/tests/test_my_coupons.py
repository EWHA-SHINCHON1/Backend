from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from coupons.models import Coupon
from coupons.tests.helpers import logged_in_client, make_promotion, make_store

User = get_user_model()

LIST_URL = '/api/v1/me/coupons/'


def freeze(now):
    return mock.patch('django.utils.timezone.now', return_value=now)


class MyCouponListTests(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        self.user = User.objects.create_user(username='u1')
        self.client = logged_in_client(self.user)
        self.store = make_store(
            name='갓구움 베이커리',
            promotion_context='내부 메모',
            business_hours='매일 10-20시',
        )
        self.store.set_usage_pin('1234')
        self.store.save()

    def issue(self, user=None, promotion=None, issued_at=None, **kwargs):
        promotion = promotion or make_promotion(self.store, benefit='아메리카노 1잔', terms='1인 1회')
        kwargs.setdefault('expires_at', promotion.redeem_until)
        return Coupon.objects.create(
            user=user or self.user,
            promotion=promotion,
            issued_at=issued_at or self.now,
            **kwargs,
        )

    def get(self, params=None, client=None):
        return (client or self.client).get(LIST_URL, params or {})

    def ids(self, response):
        return [item['id'] for item in response.json()['results']]

    # 인증·소유자

    def test_anonymous_is_rejected(self):
        response = self.get(client=APIClient())
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error']['code'], 'AUTHENTICATION_REQUIRED')

    def test_only_own_coupons(self):
        mine = self.issue()
        self.issue(user=User.objects.create_user(username='u2'))
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.ids(response), [str(mine.pk)])
        self.assertEqual(response.json()['count'], 1)

    def test_empty_list(self):
        self.assertEqual(
            self.get().json(),
            {'count': 0, 'next': None, 'previous': None, 'results': []},
        )

    # 응답 필드

    def test_item_fields(self):
        coupon = self.issue()
        item = self.get().json()['results'][0]

        self.assertEqual(set(item), {'id', 'status', 'issued_at', 'expires_at', 'used_at', 'promotion'})
        self.assertEqual(set(item['promotion']), {'id', 'title', 'benefit', 'image_url', 'store'})
        self.assertEqual(item['promotion']['store'], {'id': self.store.pk, 'name': '갓구움 베이커리'})
        self.assertEqual(item['id'], str(coupon.pk))
        self.assertEqual(item['status'], 'available')
        self.assertIsNone(item['used_at'])
        self.assertEqual(item['promotion']['id'], coupon.promotion_id)
        self.assertEqual(item['promotion']['benefit'], '아메리카노 1잔')

    def test_expires_at_comes_from_coupon_not_promotion(self):
        coupon = self.issue(expires_at=self.now + timedelta(days=3))
        item = self.get().json()['results'][0]
        self.assertEqual(item['expires_at'], timezone.localtime(coupon.expires_at).strftime('%Y-%m-%dT%H:%M:%S%z'))

    def test_internal_fields_are_not_exposed(self):
        self.issue()
        text = self.get().content.decode()
        for secret in ('내부 메모', 'promotion_context', 'usage_pin_hash', self.store.usage_pin_hash, 'token'):
            self.assertNotIn(secret, text)

    # 상태 필터

    def make_each_status(self):
        available = self.issue(expires_at=self.now + timedelta(days=1))
        used = self.issue(used_at=self.now - timedelta(hours=1))
        expired = self.issue(expires_at=self.now - timedelta(seconds=1))
        return available, used, expired

    def test_default_returns_all_statuses(self):
        coupons = self.make_each_status()
        response = self.get()
        self.assertEqual(set(self.ids(response)), {str(c.pk) for c in coupons})

    def test_status_filters(self):
        available, used, expired = self.make_each_status()
        for status, coupon in (('available', available), ('used', used), ('expired', expired)):
            with self.subTest(status=status):
                response = self.get({'status': status})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.ids(response), [str(coupon.pk)])
                self.assertEqual(response.json()['results'][0]['status'], status)

    def test_expires_at_boundary_is_expired(self):
        coupon = self.issue(expires_at=self.now)
        with freeze(self.now):
            self.assertEqual(self.ids(self.get({'status': 'expired'})), [str(coupon.pk)])
            self.assertEqual(self.ids(self.get({'status': 'available'})), [])
        with freeze(self.now - timedelta(microseconds=1)):
            self.assertEqual(self.ids(self.get({'status': 'available'})), [str(coupon.pk)])

    def test_used_has_priority_over_expired(self):
        coupon = self.issue(expires_at=self.now - timedelta(days=1), used_at=self.now - timedelta(days=2))
        self.assertEqual(self.ids(self.get({'status': 'used'})), [str(coupon.pk)])
        self.assertEqual(self.ids(self.get({'status': 'expired'})), [])
        self.assertEqual(self.get().json()['results'][0]['status'], 'used')

    def test_invalid_status(self):
        for value in ('USED', 'all', ''):
            with self.subTest(value=value):
                response = self.get({'status': value})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['error']['code'], 'INVALID_COUPON_STATUS')

    def test_filter_status_matches_get_status(self):
        """QuerySet 필터와 get_status()가 같은 규칙인지 확인"""
        coupons = self.make_each_status()
        for status in Coupon.Status.values:
            filtered = set(Coupon.objects.filter_status(status, self.now))
            expected = {c for c in coupons if c.get_status(now=self.now) == status}
            self.assertEqual(filtered, expected)

    # 종료·비공개 프로모션

    def test_coupons_of_ended_or_unpublished_promotions_are_listed(self):
        ended = self.issue(promotion=make_promotion(
            self.store,
            starts_at=self.now - timedelta(days=10),
            ends_at=self.now - timedelta(days=5),
            redeem_until=self.now + timedelta(days=1),
        ))
        hidden = self.issue(promotion=make_promotion(self.store, is_published=False))
        self.assertEqual(set(self.ids(self.get())), {str(ended.pk), str(hidden.pk)})

    # 정렬·페이지네이션

    def test_ordered_by_latest_issue_then_id(self):
        old = self.issue(issued_at=self.now - timedelta(days=1))
        same_time = [self.issue(issued_at=self.now) for _ in range(3)]
        expected = sorted((str(c.pk) for c in same_time), reverse=True) + [str(old.pk)]
        self.assertEqual(self.ids(self.get()), expected)

    def test_pagination(self):
        coupons = [self.issue(issued_at=self.now - timedelta(minutes=i)) for i in range(25)]

        first = self.get()
        self.assertEqual(first.json()['count'], 25)
        self.assertIsNone(first.json()['previous'])
        self.assertIn('page=2', first.json()['next'])

        second = self.get({'page': 2})
        self.assertIsNone(second.json()['next'])
        self.assertIsNotNone(second.json()['previous'])
        self.assertEqual(self.ids(first) + self.ids(second), [str(c.pk) for c in coupons])
        self.assertEqual((len(self.ids(first)), len(self.ids(second))), (20, 5))

    def test_page_out_of_range(self):
        self.issue()
        response = self.get({'page': 2})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error']['code'], 'NOT_FOUND')

    def test_query_count_does_not_grow_with_coupons(self):
        self.issue()
        with CaptureQueriesContext(connection) as one:
            self.get()
        for _ in range(4):
            self.issue(promotion=make_promotion(make_store()))
        with CaptureQueriesContext(connection) as five:
            self.assertEqual(self.get().json()['count'], 5)
        self.assertEqual(len(one), len(five))
