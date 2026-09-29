from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """프로젝트 사용자 모델.

    나중에 필드를 추가할 수 있도록 처음부터 커스텀 모델로 둡니다.
    """

    class Meta:
        db_table = 'users'
        verbose_name = '사용자'
        verbose_name_plural = '사용자'
