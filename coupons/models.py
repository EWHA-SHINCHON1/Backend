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
