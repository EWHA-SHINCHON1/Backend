from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from coupons.models import Coupon
from coupons.tests.helpers import issue_url, logged_in_client, make_promotion

User = get_user_model()

LIMIT = 10


class CouponIssueThrottleTests(TestCase):
    """쿠폰 발급: 로그인 사용자별 분당 10회. 초과 시 429 TOO_MANY_REQUESTS."""

    def setUp(self):
        # DB 캐시는 테스트 트랜잭션 안에서 롤백되지만, 다른 테스트의 기록이 섞이지 않도록 비웁니다.
        cache.clear()
        self.user = User.objects.create_user(username='u1')
        self.client = logged_in_client(self.user)
        self.promotion = make_promotion(total_quantity=100)

    def post(self, client=None, promotion=None):
        return (client or self.client).post(issue_url((promotion or self.promotion).pk), {}, format='json')

    def test_uses_database_cache(self):
        from django.conf import settings

        self.assertEqual(settings.CACHES['default']['BACKEND'], 'django.core.cache.backends.db.DatabaseCache')

    def test_eleventh_request_in_a_minute_is_throttled(self):
        statuses = [self.post().status_code for _ in range(LIMIT)]
        self.assertEqual(statuses, [201] + [200] * (LIMIT - 1))

        response = self.post()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()['error']['code'], 'TOO_MANY_REQUESTS')
        self.assertTrue(response.has_header('Retry-After'))
        self.assertLessEqual(int(response['Retry-After']), 60)
        self.assertEqual(Coupon.objects.count(), 1)

    def test_limit_is_per_user_not_per_promotion(self):
        for _ in range(LIMIT):
            self.post()
        other_promotion = make_promotion()
        self.assertEqual(self.post(promotion=other_promotion).status_code, 429)
        self.assertFalse(Coupon.objects.filter(promotion=other_promotion).exists())

    def test_other_user_is_not_throttled(self):
        for _ in range(LIMIT):
            self.post()
        other = logged_in_client(User.objects.create_user(username='u2'))
        self.assertEqual(self.post(client=other).status_code, 201)

    def test_limit_resets_after_a_minute(self):
        from unittest import mock

        start = 1_000_000.0
        with mock.patch('rest_framework.throttling.SimpleRateThrottle.timer', return_value=start):
            for _ in range(LIMIT):
                self.post()
            self.assertEqual(self.post().status_code, 429)
        with mock.patch('rest_framework.throttling.SimpleRateThrottle.timer', return_value=start + 61):
            self.assertEqual(self.post().status_code, 200)

    def test_anonymous_gets_401_not_429(self):
        anonymous = APIClient()
        for _ in range(LIMIT + 2):
            self.assertEqual(self.post(client=anonymous).status_code, 401)

    def test_other_coupon_apis_are_not_throttled(self):
        for _ in range(LIMIT):
            self.post()
        for _ in range(LIMIT + 2):
            self.assertEqual(self.client.get('/api/v1/me/coupons/').status_code, 200)
