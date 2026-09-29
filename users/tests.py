from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import include, path, reverse
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework.views import APIView


class ProtectedView(APIView):
    """기본 권한(IsAuthenticated)이 적용되는 테스트용 뷰."""

    def get(self, request):
        return Response({'username': request.user.username})


urlpatterns = [
    path('', include('config.urls')),
    path('test/protected/', ProtectedView.as_view(), name='test_protected'),
]


class UserModelTests(TestCase):
    def test_auth_user_model(self):
        User = get_user_model()
        self.assertEqual(User._meta.label, 'users.User')
        self.assertEqual(User._meta.db_table, 'users')


@override_settings(ROOT_URLCONF='users.tests')
class JWTAuthTests(TestCase):
    username = 'tester'
    password = 'test-password-1234'

    def setUp(self):
        get_user_model().objects.create_user(username=self.username, password=self.password)
        self.client = APIClient()

    def obtain_tokens(self):
        response = self.client.post(
            reverse('token_obtain_pair'),
            {'username': self.username, 'password': self.password},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_obtain_token(self):
        tokens = self.obtain_tokens()
        self.assertIn('access', tokens)
        self.assertIn('refresh', tokens)

    def test_obtain_token_with_wrong_password(self):
        response = self.client.post(
            reverse('token_obtain_pair'),
            {'username': self.username, 'password': 'wrong-password'},
            format='json',
        )
        self.assertEqual(response.status_code, 401)

    def test_refresh_token(self):
        tokens = self.obtain_tokens()
        response = self.client.post(reverse('token_refresh'), {'refresh': tokens['refresh']}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.data)

    def test_verify_token(self):
        tokens = self.obtain_tokens()
        response = self.client.post(reverse('token_verify'), {'token': tokens['access']}, format='json')
        self.assertEqual(response.status_code, 200)

        response = self.client.post(reverse('token_verify'), {'token': 'invalid'}, format='json')
        self.assertEqual(response.status_code, 401)

    def test_protected_view_requires_token(self):
        response = self.client.get(reverse('test_protected'))
        self.assertEqual(response.status_code, 401)

    def test_protected_view_with_bearer_token(self):
        tokens = self.obtain_tokens()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        response = self.client.get(reverse('test_protected'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['username'], self.username)
