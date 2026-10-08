import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class CouponQuerySet(models.QuerySet):
    def filter_status(self, status, now):
        """get_status()와 같은 규칙으로 상태별 쿠폰만 남깁니다. now는 한 요청의 기준 시각입니다."""
        if status == Coupon.Status.USED:
            return self.filter(used_at__isnull=False)
        if status == Coupon.Status.EXPIRED:
            return self.filter(used_at__isnull=True, expires_at__lte=now)
        if status == Coupon.Status.AVAILABLE:
            return self.filter(used_at__isnull=True, expires_at__gt=now)
        raise ValueError(f'Unknown coupon status: {status!r}')


class Coupon(models.Model):
    """사용자에게 발급된 프로모션 쿠폰.

    - status는 저장하지 않고 get_status()로 계산합니다.
    - expires_at은 발급 당시 Promotion.redeem_until을 복사해 명시적으로 저장합니다.
      이후 Promotion의 사용기한이 바뀌어도 기존 쿠폰은 바뀌지 않습니다.
    - 사용 PIN은 매장(Store) 공통 PIN으로 검증하므로 쿠폰에는 PIN 컬럼이 없습니다.
    """

    class Status(models.TextChoices):
        AVAILABLE = 'available', '사용 가능'
        USED = 'used', '사용 완료'
        EXPIRED = 'expired', '기간 만료'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='coupons',
        verbose_name='사용자',
        # UNIQUE(user, promotion) 인덱스가 user 단독 조회도 처리하므로 FK 기본 인덱스는 생략
        db_index=False,
    )
    promotion = models.ForeignKey(
        'promotions.Promotion',
        on_delete=models.PROTECT,
        related_name='coupons',
        verbose_name='프로모션',
    )
    issued_at = models.DateTimeField('발급 시각', default=timezone.now)
    expires_at = models.DateTimeField('사용 기한')
    used_at = models.DateTimeField('사용 시각', null=True, blank=True)

    objects = CouponQuerySet.as_manager()

    class Meta:
        db_table = 'coupons'
        verbose_name = '쿠폰'
        verbose_name_plural = '쿠폰'
        constraints = [
            models.UniqueConstraint(fields=['user', 'promotion'], name='uniq_coupon_user_promotion'),
        ]

    def __str__(self):
        return str(self.id)

    def get_status(self, now=None):
        """used_at이 있으면 used, now >= expires_at이면 expired, 그 외 available"""
        if self.used_at is not None:
            return self.Status.USED
        now = now or timezone.now()
        if now >= self.expires_at:
            return self.Status.EXPIRED
        return self.Status.AVAILABLE


class PinAttempt(models.Model):
    """쿠폰 사용 PIN 실패 기록. 사용자+매장 단위로 한 행을 두고 실패 횟수 제한에 씁니다.

    - 매장 PIN 불일치(INVALID_PIN)만 셉니다. 형식 오류·PIN 미설정은 세지 않습니다.
    - 첫 실패부터 일정 시간 안에 허용 횟수만큼 틀리면 locked_until까지 차단합니다.
    - PIN이 맞으면 기록을 초기화합니다.
    - 서버 프로세스 간에 공유되도록 DB에 저장합니다. (로컬 메모리 캐시는 워커별로 따로라 쓰지 않음)
    - 이력 보존용이 아니라 일시적인 제한 상태이므로 사용자·매장 삭제 시 함께 지웁니다.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='pin_attempts',
        verbose_name='사용자',
    )
    store = models.ForeignKey(
        'stores.Store',
        on_delete=models.CASCADE,
        related_name='pin_attempts',
        verbose_name='매장',
    )
    failure_count = models.PositiveSmallIntegerField('연속 실패 횟수', default=0)
    first_failed_at = models.DateTimeField('첫 실패 시각', null=True, blank=True)
    locked_until = models.DateTimeField('차단 해제 시각', null=True, blank=True)
    updated_at = models.DateTimeField('수정 시각', auto_now=True)

    class Meta:
        db_table = 'coupon_pin_attempts'
        verbose_name = 'PIN 실패 기록'
        verbose_name_plural = 'PIN 실패 기록'
        constraints = [
            models.UniqueConstraint(fields=['user', 'store'], name='uniq_pin_attempt_user_store'),
        ]

    def __str__(self):
        return f'{self.user_id}@{self.store_id}: {self.failure_count}'
