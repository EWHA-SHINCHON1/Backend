import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from coupons.models import Coupon
from coupons.tests.helpers import csrf_client, logged_in_client, make_promotion, make_store

User = get_user_model()


def use_url(coupon_id):
    return f'/api/v1/me/coupons/{coupon_id}/use/'


class CouponUseTestMixin:
    """쿠폰 사용 테스트 공통 준비: PIN 0428이 설정된 매장의 본인 쿠폰 1장."""

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


class CouponUseBaseTests(CouponUseTestMixin, TestCase):
    """1단계: URL·로그인·소유자 확인."""

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


class CouponUsePinTests(CouponUseTestMixin, TestCase):
    """2단계: PIN 형식 검증과 매장 PIN 연동. PIN이 맞아도 아직 사용 처리하지 않습니다(4단계)."""

    def assert_pin_rejected(self, data, code='INVALID_PIN_FORMAT', status_code=400):
        response = self.post(data=data)
        self.assert_error(response, status_code, code)
        self.assertNotIn('0428', response.content.decode())
        self.assert_not_used()

    # 형식: JSON 문자열 4자리 ASCII 숫자만 허용

    def test_valid_pin_passes_and_coupon_is_not_used_yet(self):
        self.assert_error(self.post(data={'pin': '0428'}), 501, 'NOT_IMPLEMENTED')
        self.assert_not_used()

    def test_pin_missing(self):
        self.assert_pin_rejected({})

    def test_pin_null(self):
        self.assert_pin_rejected({'pin': None})

    def test_pin_empty(self):
        self.assert_pin_rejected({'pin': ''})

    def test_integer_pin_is_not_converted(self):
        self.assert_pin_rejected({'pin': 428})
        self.assert_pin_rejected({'pin': 1234})

    def test_wrong_length(self):
        for pin in ('042', '04280', '0'):
            with self.subTest(pin=pin):
                self.assert_pin_rejected({'pin': pin})

    def test_whitespace_is_not_stripped(self):
        for pin in (' 0428', '0428 ', '04 28', '0428\n', '\t0428'):
            with self.subTest(pin=repr(pin)):
                self.assert_pin_rejected({'pin': pin})

    def test_non_ascii_digits_are_rejected(self):
        # 아랍-인도 숫자, 전각 숫자
        for pin in ('٠٤٢٨', '０４２８'):
            with self.subTest(pin=pin):
                self.assert_pin_rejected({'pin': pin})

    def test_non_digit_characters(self):
        for pin in ('04a8', '-428', '+428', '4.28'):
            with self.subTest(pin=pin):
                self.assert_pin_rejected({'pin': pin})

    def test_non_string_types(self):
        for pin in (['0428'], {'v': '0428'}, True, 4.28):
            with self.subTest(pin=pin):
                self.assert_pin_rejected({'pin': pin})

    def test_body_that_is_not_an_object(self):
        response = self.client.post(use_url(self.coupon.pk), ['0428'], format='json')
        self.assert_error(response, 400, 'INVALID_PIN_FORMAT')
        self.assert_not_used()

    def test_form_encoded_body_is_also_validated(self):
        response = self.client.post(use_url(self.coupon.pk), {'pin': '0428'})
        self.assert_error(response, 501, 'NOT_IMPLEMENTED')
        response = self.client.post(use_url(self.coupon.pk), {'pin': '42'})
        self.assert_error(response, 400, 'INVALID_PIN_FORMAT')

    # 매장 PIN 연동

    def test_wrong_pin(self):
        self.assert_pin_rejected({'pin': '1234'}, 'INVALID_PIN')

    def test_pin_not_set_is_distinguished_from_wrong_pin(self):
        self.store.usage_pin_hash = ''
        self.store.save()
        self.assert_pin_rejected({'pin': '0428'}, 'PIN_NOT_SET', 409)
        self.assert_pin_rejected({'pin': '1234'}, 'PIN_NOT_SET', 409)

    def test_other_stores_pin_does_not_work(self):
        other_store = make_store(name='다른 매장')
        other_store.set_usage_pin('9999')
        other_store.save()
        self.assert_pin_rejected({'pin': '9999'}, 'INVALID_PIN')

    def test_pin_change_takes_effect(self):
        self.store.set_usage_pin('5555')
        self.store.save()
        self.assert_pin_rejected({'pin': '0428'}, 'INVALID_PIN')
        self.assert_error(self.post(data={'pin': '5555'}), 501, 'NOT_IMPLEMENTED')
        self.assert_not_used()

    # 오류 우선순위

    def test_not_found_comes_before_pin_checks(self):
        others = self.issue(user=User.objects.create_user(username='u2'))
        for data in ({'pin': 'x'}, {'pin': '1234'}, {'pin': '0428'}):
            with self.subTest(data=data):
                self.assert_error(self.post(others.pk, data=data), 404, 'COUPON_NOT_FOUND')

    def test_format_error_comes_before_pin_not_set(self):
        self.store.usage_pin_hash = ''
        self.store.save()
        self.assert_pin_rejected({'pin': '42'})

    def test_anonymous_comes_before_pin_checks(self):
        self.assert_error(self.post(client=APIClient(), data={'pin': 'x'}), 401, 'AUTHENTICATION_REQUIRED')

    # 내부 정보 비노출

    def test_error_response_does_not_expose_pin_hash(self):
        response = self.post(data={'pin': '1234'})
        body = response.content.decode()
        self.assertNotIn(self.store.usage_pin_hash, body)
        self.assertNotIn('1234', body)
        self.assertEqual(set(response.json()['error']), {'code', 'message', 'details'})
        self.assertEqual(set(response.json()['error']['details']), {'remaining_attempts'})
