from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """프로젝트 사용자 모델.

    소비자는 카카오 로그인으로만 가입하므로 username은 내부 랜덤값을 쓰고,
    화면에 보여줄 이름은 nickname에 둡니다.
    """

    # 카카오 닉네임 미동의·미제공 사용자도 있으므로 빈 값을 허용합니다.
    nickname = models.CharField('닉네임', max_length=50, blank=True, default='')

    class Meta:
        db_table = 'users'
        verbose_name = '사용자'
        verbose_name_plural = '사용자'


class SocialAccount(models.Model):
    """외부 로그인 제공자 계정과 User의 연결.

    사용자는 (provider, provider_user_id)로 식별합니다. 이메일로 계정을 병합하지 않습니다.
    """

    class Provider(models.TextChoices):
        KAKAO = 'kakao', '카카오'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='social_accounts',
        verbose_name='사용자',
    )
    provider = models.CharField('제공자', max_length=20, choices=Provider.choices)
    # 카카오 회원번호(숫자)를 문자열로 저장합니다.
    provider_user_id = models.CharField('제공자 사용자 ID', max_length=64)
    created_at = models.DateTimeField('생성 시각', auto_now_add=True)

    class Meta:
        db_table = 'social_accounts'
        verbose_name = '소셜 계정'
        verbose_name_plural = '소셜 계정'
        constraints = [
            models.UniqueConstraint(
                fields=['provider', 'provider_user_id'],
                name='uniq_social_account_provider_user_id',
            ),
            models.UniqueConstraint(
                fields=['user', 'provider'],
                name='uniq_social_account_user_provider',
            ),
        ]

    def __str__(self):
        return f'{self.get_provider_display()}:{self.provider_user_id}'
