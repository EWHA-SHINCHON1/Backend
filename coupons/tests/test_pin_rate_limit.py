import threading
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from coupons import exceptions
from coupons.models import Coupon, PinAttempt
from coupons.services import PIN_MAX_FAILURES, use_coupon
from coupons.tests.helpers import logged_in_client, make_promotion, make_store, run_in_thread

User = get_user_model()

PIN = '0428'
WRONG = '1111'


def use_url(coupon_id):
    return f'/api/v1/me/coupons/{coupon_id}/use/'


def freeze(now):
    return mock.patch('django.utils.timezone.now', return_value=now)


class PinRateLimitTests(TestCase):
    """3단계: 사용자+매장 기준 10분 안에 5번 틀리면 10분 차단."""

    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        self.user = User.objects.create_user(username='u1')
        self.client = logged_in_client(self.user)
        self.store = self.make_store_with_pin('매장')
        self.coupon = self.issue(self.user, self.store)

    def make_store_with_pin(self, name, pin=PIN):
        store = make_store(name=name)
        store.set_usage_pin(pin)
        store.save()
        return store

    def issue(self, user, store):
        promotion = make_promotion(store)
        return Coupon.objects.create(user=user, promotion=promotion, expires_at=promotion.redeem_until)

    def post(self, pin, coupon=None, client=None, at=None):
        coupon = coupon or self.coupon
        with freeze(at or self.now):
            return (client or self.client).post(use_url(coupon.pk), {'pin': pin}, format='json')

    def fail(self, times, **kwargs):
        return [self.post(WRONG, **kwargs) for _ in range(times)]

    def assert_invalid(self, response, remaining):
        self.assertEqual(response.status_code, 400)
        error = response.json()['error']
        self.assertEqual(error['code'], 'INVALID_PIN')
        self.assertEqual(error['details'], {'remaining_attempts': remaining})
        self.assertIn(f'남은 시도 {remaining}회', error['message'])

    def assert_locked(self, response, retry_after):
        self.assertEqual(response.status_code, 429)
        error = response.json()['error']
        self.assertEqual(error['code'], 'PIN_LOCKED')
        self.assertEqual(error['details'], {'retry_after_seconds': retry_after})
        self.assertEqual(response['Retry-After'], str(retry_after))

    def assert_passes(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['coupon']['status'], 'used')

    def assert_not_used(self):
        self.coupon.refresh_from_db()
        self.assertIsNone(self.coupon.used_at)

    # 횟수와 차단

    def test_remaining_attempts_count_down(self):
        responses = self.fail(PIN_MAX_FAILURES - 1)
        for response, remaining in zip(responses, [4, 3, 2, 1]):
            self.assert_invalid(response, remaining)
        self.assert_not_used()

    def test_fifth_failure_locks_for_ten_minutes(self):
        self.fail(4)
        response = self.post(WRONG)
        self.assert_locked(response, 600)
        self.assertIn('10분 후 다시 시도', response.json()['error']['message'])

        attempt = PinAttempt.objects.get(user=self.user, store=self.store)
        self.assertEqual(attempt.failure_count, 5)
        self.assertEqual(attempt.locked_until, self.now + timedelta(minutes=10))
        self.assert_not_used()

    def test_correct_pin_is_rejected_while_locked(self):
        self.fail(5)
        with mock.patch('stores.models.Store.check_usage_pin') as check:
            self.assert_locked(self.post(PIN, at=self.now + timedelta(minutes=3)), 420)
            check.assert_not_called()
        self.assert_not_used()

    def test_lock_expires_and_counting_restarts(self):
        self.fail(5)
        unlocked_at = self.now + timedelta(minutes=10)
        self.assert_locked(self.post(PIN, at=unlocked_at - timedelta(seconds=1)), 1)

        self.assert_invalid(self.post(WRONG, at=unlocked_at), 4)
        self.assert_passes(self.post(PIN, at=unlocked_at))

    def test_lock_expiry_allows_correct_pin(self):
        self.fail(5)
        self.assert_passes(self.post(PIN, at=self.now + timedelta(minutes=10)))
        attempt = PinAttempt.objects.get(user=self.user, store=self.store)
        self.assertEqual((attempt.failure_count, attempt.locked_until), (0, None))

    def test_success_resets_failures(self):
        second = self.issue(self.user, self.store)
        self.fail(3)
        self.assert_passes(self.post(PIN))
        # 같은 매장의 다른 쿠폰에서 다시 5번의 기회
        self.assert_invalid(self.post(WRONG, coupon=second), 4)

    def test_failures_older_than_window_are_dropped(self):
        self.fail(4)
        self.assert_invalid(self.post(WRONG, at=self.now + timedelta(minutes=10)), 4)

    def test_failures_within_window_accumulate(self):
        self.fail(4)
        self.assert_locked(self.post(WRONG, at=self.now + timedelta(minutes=9, seconds=59)), 600)

    # 세지 않는 오류

    def test_format_errors_do_not_count(self):
        for _ in range(10):
            self.assertEqual(self.post('12').json()['error']['code'], 'INVALID_PIN_FORMAT')
        self.assert_invalid(self.post(WRONG), 4)

    def test_pin_not_set_does_not_count(self):
        self.store.usage_pin_hash = ''
        self.store.save()
        for _ in range(10):
            self.assertEqual(self.post(WRONG).json()['error']['code'], 'PIN_NOT_SET')
        self.assertFalse(PinAttempt.objects.filter(failure_count__gt=0).exists())

    def test_other_users_coupon_does_not_count(self):
        others = self.issue(User.objects.create_user(username='u2'), self.store)
        for _ in range(10):
            self.assertEqual(self.post(WRONG, coupon=others).status_code, 404)
        self.assertFalse(PinAttempt.objects.exists())

    # 집계 단위: 사용자 + 매장

    def test_same_store_coupons_share_the_limit(self):
        second = self.issue(self.user, self.store)
        self.fail(4)
        self.assert_locked(self.post(WRONG, coupon=second), 600)
        self.assert_locked(self.post(PIN, coupon=self.coupon), 600)

    def test_other_store_is_not_locked(self):
        other_store = self.make_store_with_pin('다른 매장', '9999')
        other_coupon = self.issue(self.user, other_store)
        self.fail(5)
        self.assert_passes(self.post('9999', coupon=other_coupon))

    def test_other_user_is_not_locked(self):
        other = User.objects.create_user(username='u2')
        other_coupon = self.issue(other, self.store)
        self.fail(5)
        self.assert_passes(self.post(PIN, coupon=other_coupon, client=logged_in_client(other)))

    # 실패 기록은 응답 후에도 DB에 남음

    def test_failure_is_persisted(self):
        self.post(WRONG)
        attempt = PinAttempt.objects.get(user=self.user, store=self.store)
        self.assertEqual(attempt.failure_count, 1)
        self.assertEqual(attempt.first_failed_at, self.now)


class PinRateLimitConcurrencyTests(TransactionTestCase):
    """PostgreSQL 실제 동시 연결: 같은 사용자·매장의 동시 오입력이 허용 횟수를 넘지 못함."""

    def setUp(self):
        self.user = User.objects.create_user(username='u1')
        store = make_store()
        store.set_usage_pin(PIN)
        store.save()
        self.store = store
        promotion = make_promotion(store)
        self.coupon = Coupon.objects.create(user=self.user, promotion=promotion, expires_at=promotion.redeem_until)

    def run_concurrently(self, pins):
        results = {}
        barrier = threading.Barrier(len(pins))

        def call(pin):
            def func():
                barrier.wait(timeout=30)
                return use_coupon(user=self.user, coupon_id=self.coupon.pk, pin=pin)
            return func

        threads = [run_in_thread(call(pin), results, i) for i, pin in enumerate(pins)]
        for thread in threads:
            thread.join(timeout=120)
        return [results[i] for i in range(len(pins))]

    def test_concurrent_wrong_pins_cannot_exceed_limit(self):
        results = self.run_concurrently([WRONG] * 12)

        invalid = [r for r in results if isinstance(r, exceptions.InvalidPin)]
        locked = [r for r in results if isinstance(r, exceptions.PinLocked)]
        self.assertEqual(len(invalid), PIN_MAX_FAILURES - 1)
        self.assertEqual(len(locked), 12 - (PIN_MAX_FAILURES - 1))
        self.assertEqual(
            sorted(r.details['remaining_attempts'] for r in invalid), [1, 2, 3, 4]
        )
        attempt = PinAttempt.objects.get(user=self.user, store=self.store)
        self.assertEqual(attempt.failure_count, PIN_MAX_FAILURES)
        self.assertIsNotNone(attempt.locked_until)
