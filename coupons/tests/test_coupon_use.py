import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from coupons.models import Coupon
from coupons.tests.helpers import csrf_client, logged_in_client, make_promotion, make_store

User = get_user_model()


def use_url(coupon_id):
    return f'/api/v1/me/coupons/{coupon_id}/use/'


class CouponUseBaseTests(TestCase):
    """1단계: URL·로그인·소유자 확인. PIN 검증과 사용 처리는 이후 단계에서 추가합니다."""

    def setUp(self):
        self.user = User.objects.create_user(username='u1')
        self.client = logged_in_client(self.user)
        self.store = make_store()
        self.store.set_usage_pin('0428')
        self.store.save()
        self.promotion = make_promotion(self.store)
        self.coupon = self.issue()

    def issue(self, user=None):
        return Coupon.objects.create(
            user=user or self.user,
            promotion=self.promotion,
            expires_at=self.promotion.redeem_until,
        )

    def post(self, coupon_id=None, client=None, data=None):
        return (client or self.client).post(
            use_url(coupon_id or self.coupon.pk), data if data is not None else {'pin': '0428'}, format='json'
        )

    def assert_error(self, response, status_code, code):
        self.assertEqual(response.status_code, status_code)
        self.assertEqual(response.json()['error']['code'], code)

    def assert_not_used(self, coupon=None):
        coupon = coupon or self.coupon
        coupon.refresh_from_db()
        self.assertIsNone(coupon.used_at)

    # 인증·CSRF·메서드

    def test_anonymous_is_rejected(self):
        self.assert_error(self.post(client=APIClient()), 401, 'AUTHENTICATION_REQUIRED')
        self.assert_not_used()

    def test_csrf_token_is_required(self):
        client, token = csrf_client(self.user)
        response = client.post(use_url(self.coupon.pk), {'pin': '0428'}, format='json')
        self.assert_error(response, 403, 'CSRF_FAILED')

        response = client.post(use_url(self.coupon.pk), {'pin': '0428'}, format='json', HTTP_X_CSRFTOKEN=token)
        self.assertNotEqual(response.status_code, 403)
        self.assert_not_used()

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(use_url(self.coupon.pk)).status_code, 405)

    # 소유자

    def test_other_users_coupon_is_not_found(self):
        others = self.issue(user=User.objects.create_user(username='u2'))
        self.assert_error(self.post(others.pk), 404, 'COUPON_NOT_FOUND')
        self.assert_not_used(others)

    def test_unknown_uuid_is_not_found(self):
        self.assert_error(self.post(uuid.uuid4()), 404, 'COUPON_NOT_FOUND')

    def test_non_uuid_path_is_not_routed(self):
        self.assertEqual(self.client.post('/api/v1/me/coupons/123/use/', {}, format='json').status_code, 404)

    # 1단계 임시 응답: 본인 쿠폰이어도 아직 사용 처리하지 않음

    def test_own_coupon_returns_not_implemented_and_is_not_used(self):
        self.assert_error(self.post(), 501, 'NOT_IMPLEMENTED')
        self.assert_not_used()
