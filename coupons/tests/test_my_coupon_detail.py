import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from coupons.models import Coupon
from coupons.tests.helpers import logged_in_client, make_promotion, make_store
from stores.models import StoreAccessToken

User = get_user_model()


def detail_url(coupon_id):
    return f'/api/v1/me/coupons/{coupon_id}/'


class MyCouponDetailTests(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        self.user = User.objects.create_user(username='u1')
        self.client = logged_in_client(self.user)
        self.store = make_store(
            name='갓구움 베이커리',
            address='서울 서대문구 신촌로 1',
            business_hours='매일 10:00-20:00, 월 휴무',
            map_url='https://map.example.com/1',
            promotion_context='내부 운영 메모',
        )
        self.store.set_usage_pin('1234')
        self.store.save()
        StoreAccessToken.objects.create(store=self.store, token_hash='a' * 64)
        self.promotion = make_promotion(
            self.store,
            title='오픈 기념',
            benefit='아메리카노 1잔',
            terms='1인 1회, 다른 할인과 중복 불가',
            image_url='https://img.example.com/1.png',
        )

    def issue(self, user=None, promotion=None, **kwargs):
        promotion = promotion or self.promotion
        kwargs.setdefault('expires_at', promotion.redeem_until)
        return Coupon.objects.create(user=user or self.user, promotion=promotion, **kwargs)

    def get(self, coupon_id, client=None):
        return (client or self.client).get(detail_url(coupon_id))

    def assert_not_found(self, response):
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error']['code'], 'COUPON_NOT_FOUND')

    # 인증·소유자

    def test_anonymous_is_rejected(self):
        coupon = self.issue()
        response = self.get(coupon.pk, client=APIClient())
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error']['code'], 'AUTHENTICATION_REQUIRED')

    def test_other_users_coupon_is_not_found(self):
        others = self.issue(user=User.objects.create_user(username='u2'))
        self.assert_not_found(self.get(others.pk))

    def test_unknown_uuid_is_not_found(self):
        self.issue()
        self.assert_not_found(self.get(uuid.uuid4()))

    # 응답

    def test_detail_fields(self):
        coupon = self.issue()
        response = self.get(coupon.pk)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        fmt = '%Y-%m-%dT%H:%M:%S%z'
        self.assertEqual(body, {
            'id': str(coupon.pk),
            'status': 'available',
            'issued_at': timezone.localtime(coupon.issued_at).strftime(fmt),
            'expires_at': timezone.localtime(coupon.expires_at).strftime(fmt),
            'used_at': None,
            'promotion': {
                'id': self.promotion.pk,
                'title': '오픈 기념',
                'benefit': '아메리카노 1잔',
                'terms': '1인 1회, 다른 할인과 중복 불가',
                'image_url': 'https://img.example.com/1.png',
                'store': {
                    'id': self.store.pk,
                    'name': '갓구움 베이커리',
                    'address': '서울 서대문구 신촌로 1',
                    'business_hours': '매일 10:00-20:00, 월 휴무',
                    'map_url': 'https://map.example.com/1',
                },
            },
        })

    def test_blank_optional_fields_are_empty_strings(self):
        store = make_store()
        coupon = self.issue(promotion=make_promotion(store))
        promotion = self.get(coupon.pk).json()['promotion']
        self.assertEqual((promotion['benefit'], promotion['terms'], promotion['image_url']), ('', '', ''))
        self.assertEqual((promotion['store']['business_hours'], promotion['store']['map_url']), ('', ''))

    def test_expires_at_comes_from_coupon_not_promotion(self):
        coupon = self.issue(expires_at=self.now + timedelta(days=2))
        body = self.get(coupon.pk).json()
        self.assertEqual(body['expires_at'], timezone.localtime(coupon.expires_at).strftime('%Y-%m-%dT%H:%M:%S%z'))
        self.assertNotEqual(coupon.expires_at, self.promotion.redeem_until)

    def test_internal_fields_are_not_exposed(self):
        coupon = self.issue()
        text = self.get(coupon.pk).content.decode()
        for secret in (
            '내부 운영 메모', 'promotion_context', 'usage_pin', self.store.usage_pin_hash,
            'pin_updated_at', 'token', 'a' * 64, 'is_active',
        ):
            self.assertNotIn(secret, text)

    # 상태

    def test_used_coupon(self):
        used_at = self.now - timedelta(hours=1)
        coupon = self.issue(used_at=used_at)
        body = self.get(coupon.pk).json()
        self.assertEqual(body['status'], 'used')
        self.assertEqual(body['used_at'], timezone.localtime(used_at).strftime('%Y-%m-%dT%H:%M:%S%z'))

    def test_expired_coupon(self):
        coupon = self.issue(expires_at=self.now - timedelta(seconds=1))
        self.assertEqual(self.get(coupon.pk).json()['status'], 'expired')

    def test_used_has_priority_over_expired(self):
        coupon = self.issue(expires_at=self.now - timedelta(days=1), used_at=self.now - timedelta(days=2))
        self.assertEqual(self.get(coupon.pk).json()['status'], 'used')

    # 종료·비공개 프로모션

    def test_coupon_of_ended_promotion_is_visible(self):
        promotion = make_promotion(
            self.store,
            starts_at=self.now - timedelta(days=10),
            ends_at=self.now - timedelta(days=5),
            redeem_until=self.now + timedelta(days=1),
        )
        coupon = self.issue(promotion=promotion)
        response = self.get(coupon.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'available')

    def test_coupon_of_unpublished_promotion_is_visible(self):
        coupon = self.issue()
        self.promotion.is_published = False
        self.promotion.save()
        self.assertEqual(self.get(coupon.pk).status_code, 200)

    def test_coupon_of_inactive_store_is_visible(self):
        """Store.is_active 정책은 미확정이므로 조회를 제한하지 않는다."""
        coupon = self.issue()
        self.store.is_active = False
        self.store.save()
        self.assertEqual(self.get(coupon.pk).status_code, 200)

    def test_single_query_for_coupon_promotion_store(self):
        coupon = self.issue()
        self.get(coupon.pk)  # 세션 캐시 워밍
        with self.assertNumQueries(3):  # 세션, 사용자, 쿠폰+프로모션+매장
            self.get(coupon.pk)
