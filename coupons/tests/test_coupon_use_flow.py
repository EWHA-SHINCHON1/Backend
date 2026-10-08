import threading
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from coupons.models import Coupon, PinAttempt
from coupons.tests.helpers import csrf_client, issue_url, make_promotion, make_store, run_in_thread

User = get_user_model()

PIN = '0428'
WRONG = '1111'


def use_url(coupon_id):
    return f'/api/v1/me/coupons/{coupon_id}/use/'


def detail_url(coupon_id):
    return f'/api/v1/me/coupons/{coupon_id}/'


class CouponUseFlowTests(TestCase):
    """5단계 통합: 프론트와 같은 방식(세션 + CSRF 헤더)으로 발급부터 사용까지 HTTP 요청만으로 확인."""

    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        self.user = User.objects.create_user(username='u1')
        self.client, self.token = csrf_client(self.user)
        self.store = make_store(name='갓구움')
        self.store.set_usage_pin(PIN)
        self.store.save()
        self.promotion = make_promotion(self.store, title='소금빵 1+1')

    def at(self, minutes=0):
        return mock.patch('django.utils.timezone.now', return_value=self.now + timedelta(minutes=minutes))

    def post(self, url, data, minutes=0):
        with self.at(minutes):
            return self.client.post(url, data, format='json', HTTP_X_CSRFTOKEN=self.token)

    def get(self, url, minutes=0):
        with self.at(minutes):
            return self.client.get(url)

    def use(self, coupon_id, pin, minutes=0):
        return self.post(use_url(coupon_id), {'pin': pin}, minutes)

    def error_code(self, response):
        return response.json()['error']['code']

    def test_issue_fail_lock_unlock_use_and_reuse(self):
        # 1. 발급
        issued = self.post(issue_url(self.promotion.pk), {})
        self.assertEqual(issued.status_code, 201)
        coupon_id = issued.json()['coupon']['id']
        self.assertEqual(
            [c['id'] for c in self.get('/api/v1/me/coupons/?status=available').json()['results']], [coupon_id]
        )

        # 2. 오입력 4번: 남은 횟수 표시
        for remaining in (4, 3, 2, 1):
            response = self.use(coupon_id, WRONG, minutes=1)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['error']['details'], {'remaining_attempts': remaining})

        # 3. 5번째 오입력: 10분 차단
        locked = self.use(coupon_id, WRONG, minutes=2)
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(self.error_code(locked), 'PIN_LOCKED')
        self.assertEqual(locked['Retry-After'], '600')

        # 4. 차단 중에는 맞는 PIN도 거절, 쿠폰은 그대로 사용 가능 상태
        still_locked = self.use(coupon_id, PIN, minutes=7)
        self.assertEqual(still_locked.status_code, 429)
        self.assertEqual(still_locked.json()['error']['details'], {'retry_after_seconds': 300})
        self.assertEqual(self.get(detail_url(coupon_id), minutes=7).json()['status'], 'available')

        # 5. 차단 해제 후 맞는 PIN: 사용 완료
        used = self.use(coupon_id, PIN, minutes=12)
        self.assertEqual(used.status_code, 200)
        body = used.json()['coupon']
        self.assertEqual((body['id'], body['status']), (coupon_id, 'used'))
        self.assertEqual(Coupon.objects.get(pk=coupon_id).used_at, self.now + timedelta(minutes=12))
        self.assertEqual(PinAttempt.objects.get(user=self.user, store=self.store).failure_count, 0)

        # 6. 다시 사용: 거절, 사용 시각 그대로
        again = self.use(coupon_id, PIN, minutes=13)
        self.assertEqual(again.status_code, 409)
        self.assertEqual(self.error_code(again), 'COUPON_ALREADY_USED')
        self.assertEqual(Coupon.objects.get(pk=coupon_id).used_at, self.now + timedelta(minutes=12))

        # 7. 조회·재발급에 사용 완료가 반영됨
        detail = self.get(detail_url(coupon_id), minutes=13).json()
        self.assertEqual((detail['status'], detail['used_at'] is not None), ('used', True))
        self.assertEqual(self.get('/api/v1/me/coupons/?status=available', minutes=13).json()['count'], 0)
        self.assertEqual(self.get('/api/v1/me/coupons/?status=used', minutes=13).json()['count'], 1)
        reissue = self.post(issue_url(self.promotion.pk), {}, minutes=13)
        self.assertEqual(reissue.status_code, 200)
        self.assertEqual(reissue.json()['coupon'], {**reissue.json()['coupon'], 'id': coupon_id, 'status': 'used'})
        self.assertEqual(Coupon.objects.count(), 1)

    def test_expired_coupon_cannot_be_used(self):
        coupon_id = self.post(issue_url(self.promotion.pk), {}).json()['coupon']['id']
        expires_in = (Coupon.objects.get(pk=coupon_id).expires_at - self.now).total_seconds() / 60

        response = self.use(coupon_id, PIN, minutes=expires_in)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.error_code(response), 'COUPON_EXPIRED')
        self.assertEqual(self.get(detail_url(coupon_id), minutes=expires_in).json()['status'], 'expired')

    def test_other_user_cannot_use_my_coupon(self):
        coupon_id = self.post(issue_url(self.promotion.pk), {}).json()['coupon']['id']
        other_client, other_token = csrf_client(User.objects.create_user(username='u2'))

        for pin in (PIN, WRONG):
            response = other_client.post(use_url(coupon_id), {'pin': pin}, format='json', HTTP_X_CSRFTOKEN=other_token)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(self.error_code(response), 'COUPON_NOT_FOUND')
        self.assertIsNone(Coupon.objects.get(pk=coupon_id).used_at)
        self.assertFalse(PinAttempt.objects.exists())

    def test_anonymous_and_missing_csrf_are_rejected(self):
        coupon_id = self.post(issue_url(self.promotion.pk), {}).json()['coupon']['id']

        anonymous = APIClient(enforce_csrf_checks=True).post(use_url(coupon_id), {'pin': PIN}, format='json')
        self.assertEqual(anonymous.status_code, 401)
        no_csrf = self.client.post(use_url(coupon_id), {'pin': PIN}, format='json')
        self.assertEqual((no_csrf.status_code, self.error_code(no_csrf)), (403, 'CSRF_FAILED'))
        self.assertIsNone(Coupon.objects.get(pk=coupon_id).used_at)


class CouponUseHttpConcurrencyTests(TransactionTestCase):
    """PostgreSQL 실제 동시 HTTP 요청: 같은 쿠폰에 맞는 PIN을 동시에 보내도 한 번만 성공."""

    def setUp(self):
        self.user = User.objects.create_user(username='u1')
        store = make_store()
        store.set_usage_pin(PIN)
        store.save()
        promotion = make_promotion(store)
        self.coupon = Coupon.objects.create(user=self.user, promotion=promotion, expires_at=promotion.redeem_until)

    def test_concurrent_use_requests_succeed_once(self):
        count = 6
        clients = [csrf_client(self.user) for _ in range(count)]
        barrier = threading.Barrier(count)
        results = {}

        def call(client, token):
            def func():
                barrier.wait(timeout=30)
                return client.post(use_url(self.coupon.pk), {'pin': PIN}, format='json', HTTP_X_CSRFTOKEN=token)
            return func

        threads = [run_in_thread(call(*clients[i]), results, i) for i in range(count)]
        for thread in threads:
            thread.join(timeout=120)

        statuses = sorted(r.status_code for r in results.values())
        self.assertEqual(statuses, [200] + [409] * (count - 1))
        conflicts = [r.json()['error']['code'] for r in results.values() if r.status_code == 409]
        self.assertEqual(set(conflicts), {'COUPON_ALREADY_USED'})
        self.coupon.refresh_from_db()
        self.assertIsNotNone(self.coupon.used_at)
