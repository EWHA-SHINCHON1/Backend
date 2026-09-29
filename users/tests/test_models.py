from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from users.models import SocialAccount

User = get_user_model()


class UserNicknameTests(TestCase):
    def test_nickname_defaults_to_empty_string(self):
        user = User.objects.create_user(username='u1')
        self.assertEqual(user.nickname, '')

    def test_nickname_is_saved(self):
        user = User.objects.create_user(username='u1', nickname='연우')
        user.refresh_from_db()
        self.assertEqual(user.nickname, '연우')


class SocialAccountConstraintTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username='u1')
        self.user2 = User.objects.create_user(username='u2')

    def test_link_user_to_kakao_account(self):
        account = SocialAccount.objects.create(
            user=self.user1, provider=SocialAccount.Provider.KAKAO, provider_user_id='1001'
        )
        self.assertEqual(list(self.user1.social_accounts.all()), [account])
        self.assertIsNotNone(account.created_at)

    def test_same_provider_user_id_cannot_link_twice(self):
        """같은 카카오 ID가 서로 다른 User에 연결되면 안 된다."""
        SocialAccount.objects.create(user=self.user1, provider='kakao', provider_user_id='1001')
        with self.assertRaises(IntegrityError), transaction.atomic():
            SocialAccount.objects.create(user=self.user2, provider='kakao', provider_user_id='1001')

    def test_user_cannot_have_two_accounts_of_same_provider(self):
        """한 User에 카카오 계정은 하나만 연결된다."""
        SocialAccount.objects.create(user=self.user1, provider='kakao', provider_user_id='1001')
        with self.assertRaises(IntegrityError), transaction.atomic():
            SocialAccount.objects.create(user=self.user1, provider='kakao', provider_user_id='1002')

    def test_different_users_with_different_kakao_ids(self):
        SocialAccount.objects.create(user=self.user1, provider='kakao', provider_user_id='1001')
        SocialAccount.objects.create(user=self.user2, provider='kakao', provider_user_id='1002')
        self.assertEqual(SocialAccount.objects.count(), 2)

    def test_unsupported_provider_fails_validation(self):
        account = SocialAccount(user=self.user1, provider='naver', provider_user_id='1001')
        with self.assertRaises(ValidationError):
            account.full_clean()

    def test_deleting_user_deletes_social_account(self):
        """삭제 정책: SocialAccount.user = CASCADE"""
        SocialAccount.objects.create(user=self.user1, provider='kakao', provider_user_id='1001')
        self.user1.delete()
        self.assertFalse(SocialAccount.objects.filter(provider_user_id='1001').exists())
