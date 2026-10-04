import threading
from datetime import datetime, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from coupons.exceptions import PromotionNotFound
from coupons.models import Coupon
from coupons.services import issue_coupon
from coupons.tests.helpers import (
    csrf_client,
    issue_url,
    logged_in_client,
    make_non_coupon_promotion,
    make_promotion,
    run_in_thread,
    wait_for_lock_waiter,
)
from promotions.models import Promotion

User = get_user_model()


def freeze(now):
    return mock.patch('django.utils.timezone.now', return_value=now)


class CouponIssueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u1')
        self.client = logged_in_client(self.user)
        self.promotion = make_promotion(total_quantity=2)

    def post(self, promotion_id=None, client=None, data=None):
        client = client or self.client
        return client.post(issue_url(promotion_id or self.promotion.pk), data or {}, format='json')

    def assert_error(self, response, status_code, code):
        self.assertEqual(response.status_code, status_code)
        self.assertEqual(response.json()['error']['code'], code)
        self.assertFalse(Coupon.objects.filter(user=self.user, promotion_id=self.promotion.pk).exists())

    # 정상 발급 / 재요청

    def test_first_issue_returns_201_and_copies_redeem_until(self):
        response = self.post()

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIs(body['created'], True)
        self.assertEqual(set(body['coupon']), {'id', 'status', 'issued_at', 'expires_at'})
        self.assertEqual(body['coupon']['status'], 'available')

        coupon = Coupon.objects.get()
        self.assertEqual(str(coupon.pk), body['coupon']['id'])
        self.assertEqual(coupon.user, self.user)
        self.assertEqual(coupon.promotion, self.promotion)
        self.assertEqual(coupon.expires_at, self.promotion.redeem_until)
        self.assertIsNone(coupon.used_at)
        # 응답 시각은 프로젝트 타임존(Asia/Seoul) 기준
        expires_at = datetime.strptime(body['coupon']['expires_at'], '%Y-%m-%dT%H:%M:%S%z')
        self.assertEqual(expires_at, self.promotion.redeem_until)
        self.assertTrue(body['coupon']['expires_at'].endswith('+0900'))

    def test_reissue_returns_200_with_same_coupon(self):
        first = self.post().json()['coupon']
        response = self.post()

        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()['created'], False)
        self.assertEqual(response.json()['coupon'], first)
        self.assertEqual(Coupon.objects.count(), 1)

    def test_user_id_in_body_is_ignored(self):
        other = User.objects.create_user(username='u2')
        self.post(data={'user_id': other.pk})
        self.assertEqual(Coupon.objects.get().user, self.user)

    def test_different_users_get_different_coupons(self):
        other_client = logged_in_client(User.objects.create_user(username='u2'))
        id1 = self.post().json()['coupon']['id']
        id2 = self.post(client=other_client).json()['coupon']['id']
        self.assertNotEqual(id1, id2)
        self.assertEqual(self.promotion.coupons.count(), 2)

    # 기존 쿠폰은 어떤 상태에서도 그대로 반환

    def issue_existing(self, **kwargs):
        kwargs.setdefault('expires_at', self.promotion.redeem_until)
        return Coupon.objects.create(user=self.user, promotion=self.promotion, **kwargs)

    def assert_returns_existing(self, coupon, expected_status):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()['created'], False)
        self.assertEqual(response.json()['coupon']['id'], str(coupon.pk))
        self.assertEqual(response.json()['coupon']['status'], expected_status)
        self.assertEqual(Coupon.objects.filter(user=self.user).count(), 1)

    def test_existing_used_coupon_is_returned_not_reissued(self):
        coupon = self.issue_existing(used_at=timezone.now())
        self.assert_returns_existing(coupon, 'used')

    def test_existing_expired_coupon_is_returned_not_reissued(self):
        coupon = self.issue_existing(expires_at=timezone.now() - timedelta(seconds=1))
        self.assert_returns_existing(coupon, 'expired')

    def test_existing_coupon_returned_after_promotion_ended(self):
        coupon = self.issue_existing()
        with freeze(self.promotion.ends_at + timedelta(days=1)):
            self.assert_returns_existing(coupon, 'available')

    def test_existing_coupon_returned_after_unpublished(self):
        coupon = self.issue_existing()
        Promotion.objects.filter(pk=self.promotion.pk).update(is_published=False)
        self.assert_returns_existing(coupon, 'available')

    def test_existing_coupon_returned_after_sold_out(self):
        coupon = self.issue_existing()
        Coupon.objects.create(
            user=User.objects.create_user(username='u2'),
            promotion=self.promotion,
            expires_at=self.promotion.redeem_until,
        )
        self.assert_returns_existing(coupon, 'available')

    # 인증·CSRF

    def test_anonymous_is_rejected(self):
        response = self.post(client=APIClient())
        self.assert_error(response, 401, 'AUTHENTICATION_REQUIRED')

    def test_csrf_token_is_required(self):
        client, token = csrf_client(self.user)
        response = client.post(issue_url(self.promotion.pk), {}, format='json')
        self.assert_error(response, 403, 'CSRF_FAILED')

        response = client.post(issue_url(self.promotion.pk), {}, format='json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 201)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(issue_url(self.promotion.pk)).status_code, 405)

    # 신규 발급 거부

    def test_unknown_promotion(self):
        response = self.post(promotion_id=999999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error']['code'], 'PROMOTION_NOT_FOUND')

    def test_unpublished_promotion_is_not_found(self):
        self.promotion.is_published = False
        self.promotion.save()
        self.assert_error(self.post(), 404, 'PROMOTION_NOT_FOUND')

    def test_unpublished_hides_other_reasons(self):
        """비공개면 비쿠폰형·종료·소진 여부를 드러내지 않고 404"""
        non_coupon = make_non_coupon_promotion(is_published=False)
        response = self.post(promotion_id=non_coupon.pk)
        self.assertEqual(response.json()['error']['code'], 'PROMOTION_NOT_FOUND')

        with freeze(self.promotion.ends_at):
            self.promotion.is_published = False
            self.promotion.save()
            self.assert_error(self.post(), 404, 'PROMOTION_NOT_FOUND')

    def test_non_coupon_promotion(self):
        promotion = make_non_coupon_promotion()
        response = self.post(promotion_id=promotion.pk)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'PROMOTION_COUPON_NOT_REQUIRED')
        self.assertFalse(Coupon.objects.exists())

    def test_before_start(self):
        with freeze(self.promotion.starts_at - timedelta(microseconds=1)):
            self.assert_error(self.post(), 409, 'PROMOTION_NOT_ACTIVE')

    def test_starts_at_is_inclusive(self):
        with freeze(self.promotion.starts_at):
            response = self.post()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Coupon.objects.get().issued_at, self.promotion.starts_at)

    def test_ends_at_is_exclusive(self):
        with freeze(self.promotion.ends_at):
            self.assert_error(self.post(), 409, 'PROMOTION_ENDED')

    def test_just_before_ends_at(self):
        with freeze(self.promotion.ends_at - timedelta(microseconds=1)):
            self.assertEqual(self.post().status_code, 201)

    def test_sold_out(self):
        for i in range(2):
            Coupon.objects.create(
                user=User.objects.create_user(username=f'other{i}'),
                promotion=self.promotion,
                expires_at=self.promotion.redeem_until,
            )
        self.assert_error(self.post(), 409, 'COUPON_SOLD_OUT')

    def test_used_and_expired_coupons_count_toward_quantity(self):
        Coupon.objects.create(
            user=User.objects.create_user(username='used'),
            promotion=self.promotion,
            expires_at=self.promotion.redeem_until,
            used_at=timezone.now(),
        )
        Coupon.objects.create(
            user=User.objects.create_user(username='expired'),
            promotion=self.promotion,
            expires_at=timezone.now() - timedelta(days=1),
        )
        self.assert_error(self.post(), 409, 'COUPON_SOLD_OUT')

    def test_last_coupon_can_be_issued(self):
        Coupon.objects.create(
            user=User.objects.create_user(username='other'),
            promotion=self.promotion,
            expires_at=self.promotion.redeem_until,
        )
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(self.promotion.coupons.count(), 2)

    def test_promotion_id_must_be_integer(self):
        self.assertEqual(self.client.post('/api/v1/promotions/abc/coupons/', {}, format='json').status_code, 404)


class CouponIssueConcurrencyTests(TransactionTestCase):
    """PostgreSQL에서 스레드마다 별도 DB 연결·트랜잭션으로 동시에 발급을 요청한다."""

    def concurrent_posts(self, clients, promotion_id):
        barrier = threading.Barrier(len(clients))
        results = {}

        def make_request(client):
            def request():
                barrier.wait()
                response = client.post(issue_url(promotion_id), {}, format='json')
                return response.status_code, response.json()
            return request

        threads = [run_in_thread(make_request(client), results, i) for i, client in enumerate(clients)]
        for thread in threads:
            thread.join()
        for result in results.values():
            if isinstance(result, Exception):
                raise result
        return list(results.values())

    def test_same_user_concurrent_requests_create_one_coupon(self):
        user = User.objects.create_user(username='u1')
        promotion = make_promotion(total_quantity=10)
        results = self.concurrent_posts([logged_in_client(user) for _ in range(8)], promotion.pk)

        statuses = sorted(status for status, _ in results)
        self.assertEqual(statuses, [200] * 7 + [201])
        self.assertEqual(len({body['coupon']['id'] for _, body in results}), 1)
        self.assertEqual(Coupon.objects.filter(user=user, promotion=promotion).count(), 1)

    def test_many_users_cannot_exceed_total_quantity(self):
        promotion = make_promotion(total_quantity=3)
        users = [User.objects.create_user(username=f'u{i}') for i in range(10)]
        results = self.concurrent_posts([logged_in_client(user) for user in users], promotion.pk)

        created = [body for status, body in results if status == 201]
        sold_out = [body for status, body in results if status == 409]
        self.assertEqual(len(created), 3)
        self.assertEqual(len(sold_out), 7)
        self.assertTrue(all(body['error']['code'] == 'COUPON_SOLD_OUT' for body in sold_out))
        self.assertEqual(Coupon.objects.filter(promotion=promotion).count(), 3)

    def test_last_coupon_race(self):
        promotion = make_promotion(total_quantity=1)
        users = [User.objects.create_user(username=f'u{i}') for i in range(5)]
        results = self.concurrent_posts([logged_in_client(user) for user in users], promotion.pk)

        self.assertEqual(sorted(status for status, _ in results), [201] + [409] * 4)
        self.assertEqual(Coupon.objects.filter(promotion=promotion).count(), 1)


class CouponIssueAdminRaceTests(TransactionTestCase):
    """실제 Admin 수정 경로와 첫 쿠폰 발급이 겹칠 때 Promotion 행 잠금으로 순서가 정해지는지 확인한다."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(username='admin', password='pw', email='a@example.com')
        self.user = User.objects.create_user(username='u1')
        self.promotion = make_promotion(total_quantity=10, benefit='10% 할인', featured_rank=1)

    def admin_post(self, promotion, **overrides):
        """Admin 변경 폼에 현재 값 + overrides를 그대로 제출한다."""
        client = APIClient()
        client.force_login(self.admin_user)
        values = {
            name: getattr(promotion, name)
            for name in (
                'title', 'description', 'benefit', 'terms', 'image_url', 'starts_at', 'ends_at',
                'requires_coupon', 'redeem_until', 'total_quantity', 'is_published', 'featured_rank',
            )
        }
        values.update(overrides)
        data = {
            'store': str(promotion.store_id),
            'title': values['title'],
            'description': values['description'],
            'benefit': values['benefit'],
            'terms': values['terms'],
            'image_url': values['image_url'],
            'total_quantity': '' if values['total_quantity'] is None else str(values['total_quantity']),
            'featured_rank': '' if values['featured_rank'] is None else str(values['featured_rank']),
            '_save': '저장',
        }
        if values['requires_coupon']:
            data['requires_coupon'] = 'on'
        if values['is_published']:
            data['is_published'] = 'on'
        for name in ('starts_at', 'ends_at', 'redeem_until'):
            local = timezone.localtime(values[name])
            data[f'{name}_0'] = local.strftime('%Y-%m-%d')
            data[f'{name}_1'] = local.strftime('%H:%M:%S')
        return client.post(reverse('admin:promotions_promotion_change', args=[promotion.pk]), data)

    def test_admin_change_waits_for_issue_and_is_rejected(self):
        """발급이 먼저 잠금을 잡으면 Admin 수정은 기다렸다가 발급된 쿠폰을 보고 금지 수정을 거부한다."""
        results = {}
        with transaction.atomic():
            _, created = issue_coupon(user=self.user, promotion_id=self.promotion.pk)
            self.assertTrue(created)
            thread = run_in_thread(lambda: self.admin_post(self.promotion, benefit='50% 할인'), results, 'admin')
            self.assertTrue(wait_for_lock_waiter(), 'Admin 수정이 Promotion 잠금을 기다리지 않았습니다.')
        thread.join()

        response = results['admin']
        self.assertNotIsInstance(response, Exception)
        self.assertEqual(response.status_code, 200)  # 저장 성공이면 302
        self.assertIn('쿠폰이 발급된 프로모션의 혜택은 변경할 수 없습니다.', response.content.decode())
        self.promotion.refresh_from_db()
        self.assertEqual(self.promotion.benefit, '10% 할인')

    def test_allowed_admin_change_after_issue_still_saves(self):
        issue_coupon(user=self.user, promotion_id=self.promotion.pk)
        response = self.admin_post(self.promotion, total_quantity=20, featured_rank=2, is_published=False)
        self.assertEqual(response.status_code, 302)
        self.promotion.refresh_from_db()
        self.assertEqual((self.promotion.total_quantity, self.promotion.featured_rank), (20, 2))
        self.assertFalse(self.promotion.is_published)
        self.assertEqual(Coupon.objects.count(), 1)

    def test_issue_waits_for_admin_change_and_uses_new_redeem_until(self):
        """Admin 수정이 먼저 잠금을 잡으면 발급은 기다렸다가 수정된 사용 기한으로 발급한다."""
        new_redeem_until = self.promotion.redeem_until + timedelta(days=7)
        results = {}
        with transaction.atomic():
            response = self.admin_post(self.promotion, redeem_until=new_redeem_until)
            self.assertEqual(response.status_code, 302)
            thread = run_in_thread(
                lambda: issue_coupon(user=self.user, promotion_id=self.promotion.pk), results, 'issue'
            )
            self.assertTrue(wait_for_lock_waiter(), '발급이 Promotion 잠금을 기다리지 않았습니다.')
        thread.join()

        coupon, created = results['issue']
        self.assertTrue(created)
        self.assertEqual(coupon.expires_at, new_redeem_until)

    def test_issue_waits_for_admin_unpublish_and_is_rejected(self):
        results = {}
        with transaction.atomic():
            self.assertEqual(self.admin_post(self.promotion, is_published=False).status_code, 302)
            thread = run_in_thread(
                lambda: issue_coupon(user=self.user, promotion_id=self.promotion.pk), results, 'issue'
            )
            self.assertTrue(wait_for_lock_waiter(), '발급이 Promotion 잠금을 기다리지 않았습니다.')
        thread.join()

        self.assertIsInstance(results['issue'], PromotionNotFound)
        self.assertFalse(Coupon.objects.exists())
