import threading
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from coupons import exceptions
from coupons.models import Coupon, PinAttempt
from coupons.services import use_coupon
from coupons.tests.helpers import (
    logged_in_client,
    make_promotion,
    make_store,
    run_in_thread,
    wait_for_lock_waiter,
)

User = get_user_model()

PIN = '0428'
WRONG = '1111'


def use_url(coupon_id):
    return f'/api/v1/me/coupons/{coupon_id}/use/'


def freeze(now):
    return mock.patch('django.utils.timezone.now', return_value=now)


def make_store_with_pin(**kwargs):
    store = make_store(**kwargs)
    store.set_usage_pin(PIN)
    store.save()
    return store


class CouponUseStateTests(TestCase):
    """4단계: 사용·만료 상태 확인과 used_at 기록."""

    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        self.user = User.objects.create_user(username='u1')
        self.client = logged_in_client(self.user)
        self.store = make_store_with_pin(name='갓구움')
        self.promotion = make_promotion(self.store, title='소금빵 1+1', benefit='소금빵 1개 증정')
        self.coupon = Coupon.objects.create(
            user=self.user, promotion=self.promotion, expires_at=self.promotion.redeem_until
        )

    def post(self, pin=PIN, at=None):
        with freeze(at or self.now):
            return self.client.post(use_url(self.coupon.pk), {'pin': pin}, format='json')

    def assert_error(self, response, status_code, code):
        self.assertEqual(response.status_code, status_code)
        self.assertEqual(response.json()['error']['code'], code)

    def refreshed(self):
        self.coupon.refresh_from_db()
        return self.coupon

    # 정상 사용

    def test_use_records_used_at_and_returns_coupon(self):
        response = self.post()

        self.assertEqual(response.status_code, 200)
        coupon = response.json()['coupon']
        self.assertEqual(
            set(coupon), {'id', 'status', 'issued_at', 'expires_at', 'used_at', 'promotion'}
        )
        self.assertEqual(coupon['id'], str(self.coupon.pk))
        self.assertEqual(coupon['status'], 'used')
        self.assertEqual(coupon['promotion']['title'], '소금빵 1+1')
        self.assertEqual(coupon['promotion']['store'], {'id': self.store.pk, 'name': '갓구움'})
        self.assertEqual(self.refreshed().used_at, self.now)

    def test_response_does_not_expose_internal_fields(self):
        body = self.post().content.decode()
        for secret in (PIN, self.store.usage_pin_hash, 'usage_pin', 'promotion_context'):
            self.assertNotIn(secret, body)

    def test_used_coupon_shows_as_used_in_list_and_detail(self):
        self.post()
        with freeze(self.now):
            listed = self.client.get('/api/v1/me/coupons/?status=used').json()
            detail = self.client.get(f'/api/v1/me/coupons/{self.coupon.pk}/').json()
        self.assertEqual([c['id'] for c in listed['results']], [str(self.coupon.pk)])
        self.assertEqual(detail['status'], 'used')
        self.assertIsNotNone(detail['used_at'])

    def test_failed_attempts_do_not_set_used_at(self):
        self.assert_error(self.post(WRONG), 400, 'INVALID_PIN')
        self.assert_error(self.post('12'), 400, 'INVALID_PIN_FORMAT')
        self.assertIsNone(self.refreshed().used_at)

    # 이미 사용 / 만료

    def test_second_use_is_rejected(self):
        self.post()
        used_at = self.refreshed().used_at

        response = self.post(at=self.now + timedelta(minutes=1))
        self.assert_error(response, 409, 'COUPON_ALREADY_USED')
        self.assertEqual(self.refreshed().used_at, used_at)

    def test_used_coupon_does_not_check_or_count_pin(self):
        self.post()
        with mock.patch('stores.models.Store.check_usage_pin') as check:
            for _ in range(10):
                self.assert_error(self.post(WRONG), 409, 'COUPON_ALREADY_USED')
            check.assert_not_called()
        self.assertEqual(PinAttempt.objects.get(user=self.user, store=self.store).failure_count, 0)

    def test_expired_at_boundary(self):
        expires_at = self.coupon.expires_at
        self.assert_error(self.post(at=expires_at), 409, 'COUPON_EXPIRED')
        self.assertIsNone(self.refreshed().used_at)

    def test_just_before_expiry_can_be_used(self):
        response = self.post(at=self.coupon.expires_at - timedelta(seconds=1))
        self.assertEqual(response.status_code, 200)

    def test_expired_coupon_does_not_count_pin_failures(self):
        for _ in range(10):
            self.assert_error(self.post(WRONG, at=self.coupon.expires_at), 409, 'COUPON_EXPIRED')
        self.assertFalse(PinAttempt.objects.exists())

    def test_used_has_priority_over_expired(self):
        self.post()
        # (세션 기본 유효기간 2주를 넘기지 않도록 만료 직후로 확인)
        self.assert_error(self.post(at=self.coupon.expires_at + timedelta(minutes=1)), 409, 'COUPON_ALREADY_USED')

    def test_uses_coupon_expires_at_not_current_promotion(self):
        # 발급 후 프로모션 사용 기한이 바뀌어도 쿠폰에 복사된 expires_at 기준
        Promotion = type(self.promotion)
        Promotion.objects.filter(pk=self.promotion.pk).update(redeem_until=self.now + timedelta(days=30))
        self.assert_error(self.post(at=self.coupon.expires_at), 409, 'COUPON_EXPIRED')

    # 오류 우선순위

    def test_format_error_comes_before_state(self):
        self.post()
        self.assert_error(self.post('12'), 400, 'INVALID_PIN_FORMAT')

    def test_state_comes_before_pin_not_set(self):
        self.post()
        self.store.usage_pin_hash = ''
        self.store.save()
        self.assert_error(self.post(), 409, 'COUPON_ALREADY_USED')

    # 프로모션·매장 상태와 무관하게 기존 쿠폰은 사용 가능

    def test_inactive_store_coupon_can_be_used(self):
        self.store.is_active = False
        self.store.save()
        self.assertEqual(self.post().status_code, 200)

    def test_ended_or_unpublished_promotion_coupon_can_be_used(self):
        self.promotion.is_published = False
        self.promotion.ends_at = self.now - timedelta(minutes=1)
        self.promotion.starts_at = self.now - timedelta(days=2)
        self.promotion.save()
        self.assertEqual(self.post().status_code, 200)


class CouponUseConcurrencyTests(TransactionTestCase):
    """PostgreSQL 실제 동시 연결: 쿠폰 행 잠금으로 한 번만 사용, 실패 기록은 커밋됨."""

    def setUp(self):
        self.user = User.objects.create_user(username='u1')
        self.store = make_store_with_pin()
        promotion = make_promotion(self.store)
        self.coupon = Coupon.objects.create(user=self.user, promotion=promotion, expires_at=promotion.redeem_until)

    def use(self, pin=PIN):
        return use_coupon(user=self.user, coupon_id=self.coupon.pk, pin=pin)

    def test_concurrent_correct_pins_use_coupon_once(self):
        count = 8
        results = {}
        barrier = threading.Barrier(count)

        def call():
            barrier.wait(timeout=30)
            return self.use()

        threads = [run_in_thread(call, results, i) for i in range(count)]
        for thread in threads:
            thread.join(timeout=120)

        succeeded = [r for r in results.values() if isinstance(r, Coupon)]
        already_used = [r for r in results.values() if isinstance(r, exceptions.CouponAlreadyUsed)]
        self.assertEqual(len(succeeded), 1)
        self.assertEqual(len(already_used), count - 1)
        self.coupon.refresh_from_db()
        self.assertEqual(self.coupon.used_at, succeeded[0].used_at)

    def test_waiting_request_rechecks_state_after_lock(self):
        """다른 트랜잭션이 쿠폰을 잠그고 사용 처리하는 동안 기다린 요청은, 잠금 후 다시 확인해 거절된다."""
        results = {}
        with transaction.atomic():
            locked = Coupon.objects.select_for_update().get(pk=self.coupon.pk)
            thread = run_in_thread(self.use, results, 'waiter')
            self.assertTrue(wait_for_lock_waiter(), '쿠폰 사용 요청이 쿠폰 잠금을 기다리지 않았습니다.')
            locked.used_at = timezone.now()
            locked.save(update_fields=['used_at'])
        thread.join(timeout=120)

        self.assertIsInstance(results['waiter'], exceptions.CouponAlreadyUsed)

    def test_failure_record_is_committed_even_though_use_fails(self):
        with self.assertRaises(exceptions.InvalidPin):
            self.use(WRONG)

        # 별도 연결(스레드)에서 읽어 실제로 커밋됐는지 확인
        results = {}
        thread = run_in_thread(
            lambda: PinAttempt.objects.get(user=self.user, store=self.store).failure_count, results, 'count'
        )
        thread.join(timeout=60)
        self.assertEqual(results['count'], 1)
        self.coupon.refresh_from_db()
        self.assertIsNone(self.coupon.used_at)
