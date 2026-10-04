from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework.views import APIView

from users.tests.test_kakao_login import KAKAO_SETTINGS, token_ok, user_ok
from users.views import KAKAO_LOGIN_SESSION_KEY

User = get_user_model()

ME_URL = '/api/v1/auth/me/'
LOGOUT_URL = '/api/v1/auth/logout/'


def csrf_client(user=None):
    """CSRF 검사를 실제로 하는 클라이언트. me를 호출해 csrftoken 쿠키를 받아 둔다."""
    client = APIClient(enforce_csrf_checks=True)
    if user is not None:
        client.force_login(user)
    client.get(ME_URL)
    return client, client.cookies['csrftoken'].value


class MeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='kakao_x', nickname='연우', password='secret-pw')

    def test_anonymous(self):
        response = APIClient().get(ME_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'id': None, 'nickname': None, 'is_authenticated': False})

    def test_authenticated(self):
        client = APIClient()
        client.force_login(self.user)
        response = client.get(ME_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'id': self.user.id, 'nickname': '연우', 'is_authenticated': True})

    def test_returns_only_own_info(self):
        other = User.objects.create_user(username='kakao_y', nickname='아령')
        client = APIClient()
        client.force_login(other)
        self.assertEqual(client.get(ME_URL).json()['id'], other.id)

    def test_no_internal_fields(self):
        client = APIClient()
        client.force_login(self.user)
        body = client.get(ME_URL).json()
        self.assertEqual(set(body), {'id', 'nickname', 'is_authenticated'})
        text = client.get(ME_URL).content.decode()
        for secret in ('password', 'kakao_x', 'is_staff', 'secret-pw', self.user.password):
            self.assertNotIn(secret, text)

    def test_sets_csrf_cookie_and_no_cache(self):
        response = APIClient().get(ME_URL)
        self.assertIn('csrftoken', response.cookies)
        self.assertIn('no-cache', response['Cache-Control'])

    def test_post_not_allowed(self):
        response = APIClient().post(ME_URL)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()['error']['code'], 'METHOD_NOT_ALLOWED')


class LogoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='kakao_x', nickname='연우')

    def test_logout_ends_session(self):
        client, token = csrf_client(self.user)
        response = client.post(LOGOUT_URL, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b'')
        self.assertFalse(client.get(ME_URL).json()['is_authenticated'])

    def test_logout_requires_csrf_token_when_logged_in(self):
        client, _ = csrf_client(self.user)
        response = client.post(LOGOUT_URL)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'CSRF_FAILED')
        self.assertTrue(client.get(ME_URL).json()['is_authenticated'])  # 로그아웃되지 않음

    def test_logout_when_already_logged_out(self):
        client, token = csrf_client()
        self.assertEqual(client.post(LOGOUT_URL, HTTP_X_CSRFTOKEN=token).status_code, 204)

    def test_get_not_allowed(self):
        response = APIClient().get(LOGOUT_URL)
        self.assertEqual(response.status_code, 405)

    def test_logout_does_not_call_kakao(self):
        client, token = csrf_client(self.user)
        with mock.patch('users.services.kakao.requests.post') as post, \
                mock.patch('users.services.kakao.requests.get') as get:
            client.post(LOGOUT_URL, HTTP_X_CSRFTOKEN=token)
        post.assert_not_called()
        get.assert_not_called()

    def test_user_and_social_account_remain_after_logout(self):
        client, token = csrf_client(self.user)
        client.post(LOGOUT_URL, HTTP_X_CSRFTOKEN=token)
        self.assertTrue(User.objects.filter(pk=self.user.pk, is_active=True).exists())


@override_settings(**KAKAO_SETTINGS)
class KakaoLoginFlowTests(TestCase):
    """카카오 로그인(mock) → me → logout → me 전체 흐름"""

    def test_full_flow(self):
        client = APIClient(enforce_csrf_checks=True)
        self.assertFalse(client.get(ME_URL).json()['is_authenticated'])

        client.get('/api/v1/auth/kakao/start/', {'next': '/promotions/5'})
        state = client.session[KAKAO_LOGIN_SESSION_KEY]['state']
        with mock.patch('users.services.kakao.requests.post', return_value=token_ok()), \
                mock.patch('users.services.kakao.requests.get', return_value=user_ok(nickname='연우')):
            response = client.get('/api/v1/auth/kakao/callback/', {'code': 'c', 'state': state})
        self.assertEqual(response['Location'], 'http://front.test/promotions/5')

        me = client.get(ME_URL).json()
        self.assertTrue(me['is_authenticated'])
        self.assertEqual(me['nickname'], '연우')

        token = client.cookies['csrftoken'].value
        self.assertEqual(client.post(LOGOUT_URL, HTTP_X_CSRFTOKEN=token).status_code, 204)
        self.assertEqual(client.get(ME_URL).json(), {'id': None, 'nickname': None, 'is_authenticated': False})


class InputSerializer(serializers.Serializer):
    name = serializers.CharField()


class ErrorView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        raise NotFound

    def post(self, request):
        InputSerializer(data=request.data).is_valid(raise_exception=True)
        return Response({})


urlpatterns = [
    path('', include('config.urls')),
    path('test/error/', ErrorView.as_view()),
]


@override_settings(ROOT_URLCONF='users.tests.test_me_logout')
class ErrorFormatTests(TestCase):
    def test_not_found(self):
        response = APIClient().get('/test/error/')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error']['code'], 'NOT_FOUND')

    def test_validation_error_has_details(self):
        response = APIClient().post('/test/error/', {}, format='json')
        self.assertEqual(response.status_code, 400)
        error = response.json()['error']
        self.assertEqual(error['code'], 'VALIDATION_ERROR')
        self.assertIn('name', error['details'])

    def test_parse_error(self):
        response = APIClient().post('/test/error/', '{bad json', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'BAD_REQUEST')
