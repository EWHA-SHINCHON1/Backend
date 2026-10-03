import threading
import time
from unittest import mock
from urllib.parse import parse_qs, urlparse

import requests
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings

from coupons.models import Coupon
from users.models import SocialAccount
from users.services import kakao
from users.views import KAKAO_LOGIN_SESSION_KEY, KAKAO_STATE_TTL_SECONDS, get_safe_next_path

User = get_user_model()

START_URL = '/api/v1/auth/kakao/start/'
CALLBACK_URL = '/api/v1/auth/kakao/callback/'
FRONTEND = 'http://front.test'

KAKAO_SETTINGS = {
    'KAKAO_REST_API_KEY': 'test-rest-key',
    'KAKAO_CLIENT_SECRET': 'test-client-secret',
    'KAKAO_CLIENT_SECRET_ENABLED': True,
    'KAKAO_REDIRECT_URI': 'http://api.test/api/v1/auth/kakao/callback/',
    'FRONTEND_BASE_URL': FRONTEND,
}


def fake_response(status=200, json_data=None, invalid_json=False):
    response = mock.Mock()
    response.status_code = status
    if invalid_json:
        response.json.side_effect = ValueError('not json')
    else:
        response.json.return_value = json_data
    return response


def token_ok():
    return fake_response(json_data={'token_type': 'bearer', 'access_token': 'kakao-access-token'})


def user_ok(kakao_id=1001, nickname='연우'):
    data = {'id': kakao_id, 'kakao_account': {}}
    if nickname is not None:
        data['kakao_account'] = {'profile': {'nickname': nickname}}
    return fake_response(json_data=data)


def login_error_redirect(code):
    return f'{FRONTEND}/login?error={code}'


class GetSafeNextPathTests(TestCase):
    def test_allowed_paths(self):
        for path in ('/', '/promotions/12', '/promotions/12/'):
            self.assertEqual(get_safe_next_path(path), path)

    def test_disallowed_paths_fall_back_to_root(self):
        for path in (
            None, '', 'https://evil.com', '//evil.com', '/\\evil.com', 'javascript:alert(1)',
            '/promotions/abc', '/promotions/0', '/promotions/12/../../admin', '/admin/', 'promotions/12',
            '/promotions/12?x=https://evil.com', '/promotions/12\n',
        ):
            self.assertEqual(get_safe_next_path(path), '/', msg=repr(path))


@override_settings(**KAKAO_SETTINGS)
class KakaoStartTests(TestCase):
    def test_redirects_to_kakao_with_state(self):
        response = self.client.get(START_URL, {'next': '/promotions/7'})
        self.assertEqual(response.status_code, 302)

        location = urlparse(response['Location'])
        self.assertEqual(f'{location.scheme}://{location.netloc}{location.path}', kakao.AUTHORIZE_URL)
        query = parse_qs(location.query)
        self.assertEqual(query['client_id'], ['test-rest-key'])
        self.assertEqual(query['redirect_uri'], [KAKAO_SETTINGS['KAKAO_REDIRECT_URI']])
        self.assertEqual(query['response_type'], ['code'])

        pending = self.client.session[KAKAO_LOGIN_SESSION_KEY]
        self.assertEqual(query['state'], [pending['state']])
        self.assertGreaterEqual(len(pending['state']), 32)
        self.assertEqual(pending['next'], '/promotions/7')
        # client secret은 브라우저로 가는 URL에 포함되지 않는다.
        self.assertNotIn('test-client-secret', response['Location'])

    def test_state_is_unpredictable(self):
        self.client.get(START_URL)
        first = self.client.session[KAKAO_LOGIN_SESSION_KEY]['state']
        self.client.get(START_URL)
        second = self.client.session[KAKAO_LOGIN_SESSION_KEY]['state']
        self.assertNotEqual(first, second)

    def test_disallowed_next_is_replaced(self):
        self.client.get(START_URL, {'next': 'https://evil.com'})
        self.assertEqual(self.client.session[KAKAO_LOGIN_SESSION_KEY]['next'], '/')

    @override_settings(KAKAO_REST_API_KEY='')
    def test_missing_rest_api_key(self):
        response = self.client.get(START_URL)
        self.assertRedirects(response, login_error_redirect('KAKAO_NOT_CONFIGURED'), fetch_redirect_response=False)

    @override_settings(KAKAO_CLIENT_SECRET='')
    def test_missing_client_secret_when_enabled(self):
        response = self.client.get(START_URL)
        self.assertRedirects(response, login_error_redirect('KAKAO_NOT_CONFIGURED'), fetch_redirect_response=False)

    @override_settings(KAKAO_CLIENT_SECRET='', KAKAO_CLIENT_SECRET_ENABLED=False)
    def test_client_secret_can_be_disabled(self):
        response = self.client.get(START_URL)
        self.assertTrue(response['Location'].startswith(kakao.AUTHORIZE_URL))

    def test_post_not_allowed(self):
        self.assertEqual(self.client.post(START_URL).status_code, 405)


@override_settings(**KAKAO_SETTINGS)
class KakaoCallbackTestBase(TestCase):
    def start(self, next_path=None):
        params = {'next': next_path} if next_path else {}
        self.client.get(START_URL, params)
        return self.client.session[KAKAO_LOGIN_SESSION_KEY]['state']

    def callback(self, token_response=None, user_response=None, **params):
        with mock.patch('users.services.kakao.requests.post') as post, \
                mock.patch('users.services.kakao.requests.get') as get:
            post.return_value = token_response or token_ok()
            get.return_value = user_response or user_ok()
            response = self.client.get(CALLBACK_URL, params)
        self.post_mock, self.get_mock = post, get
        return response

    def assert_logged_in_as(self, user):
        self.assertEqual(str(self.client.session.get('_auth_user_id')), str(user.pk))

    def assert_not_logged_in(self):
        self.assertNotIn('_auth_user_id', self.client.session)


class KakaoCallbackSuccessTests(KakaoCallbackTestBase):
    def test_new_user_is_created_and_logged_in(self):
        state = self.start('/promotions/3')
        response = self.callback(code='auth-code', state=state)

        self.assertRedirects(response, f'{FRONTEND}/promotions/3', fetch_redirect_response=False)
        account = SocialAccount.objects.get(provider='kakao', provider_user_id='1001')
        user = account.user
        self.assertEqual(user.nickname, '연우')
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.username.startswith('kakao_'))
        self.assertNotIn('1001', user.username)
        self.assert_logged_in_as(user)

    def test_token_request_parameters(self):
        state = self.start()
        self.callback(code='auth-code', state=state)

        _, kwargs = self.post_mock.call_args
        self.assertEqual(self.post_mock.call_args.args[0], kakao.TOKEN_URL)
        self.assertEqual(kwargs['data'], {
            'grant_type': 'authorization_code',
            'client_id': 'test-rest-key',
            'redirect_uri': KAKAO_SETTINGS['KAKAO_REDIRECT_URI'],
            'code': 'auth-code',
            'client_secret': 'test-client-secret',
        })
        self.assertIsNotNone(kwargs.get('timeout'))
        _, get_kwargs = self.get_mock.call_args
        self.assertEqual(get_kwargs['headers']['Authorization'], 'Bearer kakao-access-token')
        self.assertIsNotNone(get_kwargs.get('timeout'))

    @override_settings(KAKAO_CLIENT_SECRET='', KAKAO_CLIENT_SECRET_ENABLED=False)
    def test_client_secret_not_sent_when_disabled(self):
        state = self.start()
        self.callback(code='auth-code', state=state)
        self.assertNotIn('client_secret', self.post_mock.call_args.kwargs['data'])

    def test_same_kakao_id_logs_into_same_user(self):
        self.callback(code='c1', state=self.start())
        first_user = SocialAccount.objects.get(provider_user_id='1001').user
        self.client.logout()

        self.callback(code='c2', state=self.start(), user_response=user_ok(nickname='다른닉네임'))
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(SocialAccount.objects.count(), 1)
        self.assert_logged_in_as(first_user)
        first_user.refresh_from_db()
        self.assertEqual(first_user.nickname, '연우')  # 기존 닉네임을 덮어쓰지 않음

    def test_existing_user_with_empty_nickname_is_filled(self):
        self.callback(code='c1', state=self.start(), user_response=user_ok(nickname=None))
        self.client.logout()
        self.callback(code='c2', state=self.start(), user_response=user_ok(nickname='연우'))
        self.assertEqual(User.objects.get().nickname, '연우')

    def test_nickname_not_provided(self):
        response = self.callback(code='c', state=self.start(), user_response=user_ok(nickname=None))
        self.assertRedirects(response, f'{FRONTEND}/', fetch_redirect_response=False)
        self.assertEqual(User.objects.get().nickname, '')

    def test_long_nickname_is_truncated(self):
        self.callback(code='c', state=self.start(), user_response=user_ok(nickname='가' * 80))
        self.assertEqual(len(User.objects.get().nickname), 50)

    def test_callback_does_not_issue_coupon(self):
        self.callback(code='c', state=self.start('/promotions/3'))
        self.assertEqual(Coupon.objects.count(), 0)

    def test_redirect_does_not_leak_tokens(self):
        response = self.callback(code='auth-code', state=self.start())
        for secret in ('auth-code', 'kakao-access-token', 'test-client-secret'):
            self.assertNotIn(secret, response['Location'])

    def test_new_session_key_after_login(self):
        self.start()
        before = self.client.session.session_key
        self.callback(code='c', state=self.client.session[KAKAO_LOGIN_SESSION_KEY]['state'])
        self.assertNotEqual(self.client.session.session_key, before)


class KakaoCallbackRejectTests(KakaoCallbackTestBase):
    def assert_failed(self, response, code):
        self.assertRedirects(response, login_error_redirect(code), fetch_redirect_response=False)
        self.assert_not_logged_in()
        self.assertEqual(User.objects.count(), 0)

    def test_user_cancelled(self):
        self.start()
        response = self.callback(error='access_denied', error_description='User denied access')
        self.assert_failed(response, 'LOGIN_CANCELLED')
        self.post_mock.assert_not_called()

    def test_other_authorize_error(self):
        self.start()
        self.assert_failed(self.callback(error='server_error'), 'KAKAO_AUTH_FAILED')

    def test_state_missing(self):
        self.start()
        self.assert_failed(self.callback(code='c'), 'INVALID_STATE')
        self.post_mock.assert_not_called()

    def test_state_mismatch(self):
        self.start()
        self.assert_failed(self.callback(code='c', state='wrong-state'), 'INVALID_STATE')

    def test_no_login_attempt_in_session(self):
        self.assert_failed(self.callback(code='c', state='anything'), 'INVALID_STATE')

    def test_state_expired(self):
        state = self.start()
        session = self.client.session
        session[KAKAO_LOGIN_SESSION_KEY]['created_at'] = time.time() - KAKAO_STATE_TTL_SECONDS - 1
        session.save()
        self.assert_failed(self.callback(code='c', state=state), 'INVALID_STATE')

    def test_state_cannot_be_reused(self):
        state = self.start()
        self.callback(code='c', state=state)
        self.assertEqual(User.objects.count(), 1)
        self.client.logout()

        response = self.callback(code='c', state=state)
        self.assertRedirects(response, login_error_redirect('INVALID_STATE'), fetch_redirect_response=False)
        self.assert_not_logged_in()

    def test_state_from_other_browser_is_rejected(self):
        state = self.start()
        self.client = self.client_class()  # 다른 브라우저(세션 없음)
        self.assert_failed(self.callback(code='c', state=state), 'INVALID_STATE')

    def test_code_missing(self):
        state = self.start()
        self.assert_failed(self.callback(state=state), 'INVALID_REQUEST')
        self.post_mock.assert_not_called()

    def test_token_request_http_error(self):
        response = self.callback(
            code='c', state=self.start(),
            token_response=fake_response(400, {'error': 'invalid_grant', 'error_code': 'KOE320'}),
        )
        self.assert_failed(response, 'KAKAO_AUTH_FAILED')
        self.get_mock.assert_not_called()

    def test_token_request_timeout(self):
        state = self.start()
        with mock.patch('users.services.kakao.requests.post', side_effect=requests.Timeout):
            response = self.client.get(CALLBACK_URL, {'code': 'c', 'state': state})
        self.assert_failed(response, 'KAKAO_AUTH_FAILED')

    def test_token_request_not_retried(self):
        state = self.start()
        with mock.patch('users.services.kakao.requests.post', side_effect=requests.ConnectionError) as post:
            self.client.get(CALLBACK_URL, {'code': 'c', 'state': state})
        self.assertEqual(post.call_count, 1)

    def test_token_invalid_json(self):
        response = self.callback(code='c', state=self.start(), token_response=fake_response(invalid_json=True))
        self.assert_failed(response, 'KAKAO_AUTH_FAILED')

    def test_token_missing_access_token(self):
        response = self.callback(code='c', state=self.start(), token_response=fake_response(json_data={}))
        self.assert_failed(response, 'KAKAO_AUTH_FAILED')

    def test_user_info_http_error(self):
        response = self.callback(code='c', state=self.start(), user_response=fake_response(401, {'code': -401}))
        self.assert_failed(response, 'KAKAO_AUTH_FAILED')

    def test_user_info_timeout(self):
        state = self.start()
        with mock.patch('users.services.kakao.requests.post', return_value=token_ok()), \
                mock.patch('users.services.kakao.requests.get', side_effect=requests.Timeout):
            response = self.client.get(CALLBACK_URL, {'code': 'c', 'state': state})
        self.assert_failed(response, 'KAKAO_AUTH_FAILED')

    def test_user_info_invalid_json(self):
        response = self.callback(code='c', state=self.start(), user_response=fake_response(invalid_json=True))
        self.assert_failed(response, 'KAKAO_AUTH_FAILED')

    def test_user_info_missing_or_invalid_id(self):
        for body in ({}, {'id': None}, {'id': '1001'}, {'id': True}, {'id': 0}, []):
            with self.subTest(body=body):
                response = self.callback(code='c', state=self.start(), user_response=fake_response(json_data=body))
                self.assert_failed(response, 'KAKAO_AUTH_FAILED')

    def test_inactive_user_is_not_logged_in(self):
        self.callback(code='c1', state=self.start())
        user = User.objects.get()
        user.is_active = False
        user.save()
        self.client.logout()

        response = self.callback(code='c2', state=self.start())
        self.assertRedirects(response, login_error_redirect('INACTIVE_USER'), fetch_redirect_response=False)
        self.assert_not_logged_in()

    def test_post_not_allowed(self):
        self.assertEqual(self.client.post(CALLBACK_URL).status_code, 405)


class GetOrCreateUserConflictTests(TestCase):
    def test_conflict_rolls_back_new_user_and_returns_existing(self):
        """조회 직후 다른 요청이 먼저 계정을 만든 상황: unique 충돌 → 롤백 → 재조회"""
        existing = User.objects.create_user(username='existing')
        SocialAccount.objects.create(user=existing, provider='kakao', provider_user_id='7007')

        real_find = kakao._find_user
        with mock.patch('users.services.kakao._find_user', side_effect=[None, real_find('7007')]):
            user = kakao.get_or_create_user(kakao.KakaoUser(id='7007', nickname='x'))

        self.assertEqual(user, existing)
        self.assertEqual(User.objects.count(), 1)  # 새로 만들던 User는 롤백됨
        self.assertEqual(SocialAccount.objects.count(), 1)


class ConcurrentFirstLoginTests(TransactionTestCase):
    """같은 카카오 계정의 첫 로그인이 동시에 들어와도 User·SocialAccount가 하나만 남는다 (PostgreSQL)."""

    def test_concurrent_first_login_creates_single_user(self):
        kakao_user = kakao.KakaoUser(id='5005', nickname='동시')
        barrier = threading.Barrier(4)
        results, errors = [], []

        def worker():
            try:
                barrier.wait()
                results.append(kakao.get_or_create_user(kakao_user).pk)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(SocialAccount.objects.filter(provider_user_id='5005').count(), 1)
        self.assertEqual(User.objects.count(), 1)  # 고아 User 없음
