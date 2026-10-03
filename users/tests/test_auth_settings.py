from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework.views import APIView

User = get_user_model()


class ProtectedView(APIView):
    """기본 인증·권한 설정이 적용되는 테스트용 뷰."""

    def get(self, request):
        return Response({'id': request.user.id})

    def post(self, request):
        return Response({'ok': True})


urlpatterns = [
    path('', include('config.urls')),
    path('test/protected/', ProtectedView.as_view()),
]


@override_settings(ROOT_URLCONF='users.tests.test_auth_settings')
class SessionAuthSettingsTests(TestCase):
    """소비자 인증 = Django 세션 쿠키 + 쓰기 요청 CSRF"""

    def setUp(self):
        self.user = User.objects.create_user(username='u1')

    def test_anonymous_request_is_rejected(self):
        response = APIClient().get('/test/protected/')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {
            'error': {'code': 'AUTHENTICATION_REQUIRED', 'message': '로그인이 필요합니다.'},
        })

    def test_session_login_is_authenticated(self):
        client = APIClient()
        client.force_login(self.user)
        response = client.get('/test/protected/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], self.user.id)

    def test_write_request_requires_csrf_token(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post('/test/protected/')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error']['code'], 'CSRF_FAILED')

    def test_write_request_with_csrf_token_succeeds(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        client.get('/admin/login/')  # csrftoken 쿠키 발급
        token = client.cookies['csrftoken'].value
        response = client.post('/test/protected/', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)

    def test_bearer_token_is_not_accepted(self):
        """JWT 방식은 제거됐으므로 Authorization 헤더로는 인증되지 않는다."""
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION='Bearer anything')
        response = client.get('/test/protected/')
        self.assertEqual(response.status_code, 401)

    def test_old_jwt_endpoint_is_removed(self):
        response = APIClient().post('/api/auth/token/', {'username': 'u1', 'password': 'x'})
        self.assertEqual(response.status_code, 404)
