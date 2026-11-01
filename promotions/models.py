import uuid
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.db import models
from django.db.models import BooleanField, Case, Count, F, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone


@dataclass(frozen=True)
class CouponState:
    """쿠폰형 프로모션의 발급 상태. 기준은 now 시점의 현재 발급분(발급이 시작된 가장 최근 발급분)입니다.

    - 현재 발급분이 열려 있고, 발급분 수량과 프로모션 전체 상한(total_quantity)이 모두 남아 있어야 발급할 수 있습니다.
    - 응답의 total_quantity·redeem_until은 현재 발급분 기준으로 보여 줍니다. (API 필드는 그대로)
    """

    issued_count: int
    current_round_id: int | None
    current_quantity: int | None
    current_expires_at: datetime | None
    current_issued_count: int

    def is_available(self, now, total_quantity):
        # 현재 발급분은 이미 발급이 시작된 것이므로 마감만 확인합니다.
        if self.current_round_id is None or now >= self.current_expires_at:
            return False
        return self.current_issued_count < self.current_quantity and self.issued_count < total_quantity

    def remaining_quantity(self, now, total_quantity):
        if not self.is_available(now, total_quantity):
            return 0
        return min(self.current_quantity - self.current_issued_count, total_quantity - self.issued_count)

    @property
    def display_quantity(self):
        """현재 발급분 수량. 발급이 시작된 발급분이 없으면 0."""
        return self.current_quantity or 0


class PromotionQuerySet(models.QuerySet):
    def with_coupon_state(self, now):
        """get_status()·coupon_state()가 추가 쿼리 없이 쓰도록 쿠폰 수와 현재 발급분 정보를 붙입니다.

        coupon_available은 목록 status 필터용입니다. (기간·공개 여부는 보지 않음)
        """
        from coupons.models import Coupon, CouponRound

        current_round = CouponRound.objects.filter(promotion=OuterRef('pk')).current(now)
        issued = (
            Coupon.objects.filter(promotion=OuterRef('pk'))
            .order_by()
            .values('promotion')
            .annotate(count=Count('pk'))
            .values('count')
        )
        current_issued = (
            Coupon.objects.filter(round_id=OuterRef('current_round_id'))
            .order_by()
            .values('round')
            .annotate(count=Count('pk'))
            .values('count')
        )

        return self.annotate(
            coupon_issued_count=Coalesce(Subquery(issued), 0),
            current_round_id=Subquery(current_round.values('pk')[:1]),
            current_round_quantity=Subquery(current_round.values('quantity')[:1]),
            current_round_expires_at=Subquery(current_round.values('expires_at')[:1]),
        ).annotate(
            current_round_issued_count=Coalesce(Subquery(current_issued), 0),
        ).annotate(
            coupon_available=Case(
                When(
                    current_round_expires_at__gt=now,
                    current_round_issued_count__lt=F('current_round_quantity'),
                    coupon_issued_count__lt=F('total_quantity'),
                    then=Value(True),
                ),
                default=Value(False),
                output_field=BooleanField(),
            ),
        )


class Promotion(models.Model):
    """매장 프로모션.

    status는 저장하지 않고 get_status()로 계산합니다.
    - 프로모션 유효 기간: starts_at <= now < ends_at
    - 쿠폰형 프로모션의 신규 발급 가능 기간도 프로모션 유효 기간과 같고, 그 안에서 관리자가 연 발급분 기간에만 발급합니다.
    - redeem_until은 발급분 사용 마감의 최종 한도이고, total_quantity는 모든 발급분을 합한 전체 발급 상한입니다.
    - 이미 발급된 쿠폰은 유효 기간이 끝나도 자신의 expires_at까지 사용합니다.
    """

    class Status(models.TextChoices):
        HIDDEN = 'hidden', '비공개'
        UPCOMING = 'upcoming', '예정'
        ENDED = 'ended', '종료'
        SOLD_OUT = 'sold_out', '소진'
        ACTIVE = 'active', '진행 중'

    store = models.ForeignKey(
        'stores.Store',
        on_delete=models.PROTECT,
        related_name='promotions',
        verbose_name='매장',
    )
    title = models.CharField('제목', max_length=200)
    description = models.TextField('소개', blank=True, default='')
    # benefit, terms는 운영자가 확정하는 값입니다. (Liner는 title/description 초안만 작성)
    benefit = models.CharField('혜택', max_length=200, blank=True, default='')
    terms = models.TextField('이용 조건', blank=True, default='')
    image_url = models.URLField('대표 이미지 URL', max_length=500, blank=True, default='')
    starts_at = models.DateTimeField('프로모션 시작')
    ends_at = models.DateTimeField('프로모션 종료')
    requires_coupon = models.BooleanField('쿠폰 필요 여부', default=True)
    redeem_until = models.DateTimeField('쿠폰 사용 종료', null=True, blank=True)
    total_quantity = models.PositiveIntegerField('총 쿠폰 발급 수량', null=True, blank=True)
    # 운영자 검수 전 노출을 막기 위해 기본값은 비공개입니다.
    is_published = models.BooleanField('공개 여부', default=False)
    featured_rank = models.PositiveIntegerField('추천 노출 순위', null=True, blank=True)
    created_at = models.DateTimeField('생성 시각', auto_now_add=True)
    updated_at = models.DateTimeField('수정 시각', auto_now=True)

    objects = PromotionQuerySet.as_manager()

    class Meta:
        db_table = 'promotions'
        verbose_name = '프로모션'
        verbose_name_plural = '프로모션'
        constraints = [
            models.CheckConstraint(
                condition=Q(starts_at__lt=F('ends_at')),
                name='promotion_starts_before_ends',
            ),
            models.CheckConstraint(
                condition=(
                    Q(requires_coupon=False)
                    | Q(ends_at__lte=F('redeem_until'))
                ),
                name='promotion_ends_before_redeem_until',
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        requires_coupon=True,
                        redeem_until__isnull=False,
                        total_quantity__isnull=False,
                        total_quantity__gt=0,
                    )
                    | Q(
                        requires_coupon=False,
                        redeem_until__isnull=True,
                        total_quantity__isnull=True,
                    )
                ),
                name='promotion_coupon_fields_valid',
            ),
        ]

    def __str__(self):
        return self.title

    def is_in_active_period(self, now=None):
        """프로모션 유효 기간인지 (공개 여부는 보지 않음)."""
        now = now or timezone.now()
        return self.starts_at <= now < self.ends_at

    def is_in_issue_period(self, now=None):
        """신규 쿠폰 발급 기간인지 (수량·공개 여부는 보지 않음)."""
        return self.is_in_active_period(now)

    @property
    def issued_count(self):
        """발급된 쿠폰 수(모든 발급분 합계). 별도 카운터 컬럼 없이 연결된 Coupon 수로 계산합니다."""
        return self.coupons.count()

    def coupon_state(self, now=None):
        """now 시점의 현재 발급분 기준 쿠폰 상태.

        with_coupon_state(now)로 조회한 객체는 붙어 있는 값을 쓰고(같은 now 기준이어야 함),
        아니면 필요한 값을 직접 조회합니다.
        """
        if hasattr(self, 'current_round_id'):
            return CouponState(
                issued_count=self.coupon_issued_count,
                current_round_id=self.current_round_id,
                current_quantity=self.current_round_quantity,
                current_expires_at=self.current_round_expires_at,
                current_issued_count=self.current_round_issued_count,
            )
        now = now or timezone.now()
        current_round = self.coupon_rounds.current(now).first()
        return CouponState(
            issued_count=self.issued_count,
            current_round_id=current_round.pk if current_round else None,
            current_quantity=current_round.quantity if current_round else None,
            current_expires_at=current_round.expires_at if current_round else None,
            current_issued_count=current_round.coupons.count() if current_round else 0,
        )

    def get_remaining_quantity(self, now=None):
        """현재 발급분에서 지금 더 받을 수 있는 수량. 발급분이 닫혔거나 없으면 0, 비쿠폰형은 None."""
        if not self.requires_coupon:
            return None
        now = now or timezone.now()
        return self.coupon_state(now).remaining_quantity(now, self.total_quantity)

    def get_status(self, *, now=None):
        """우선순위: hidden → upcoming → ended → sold_out → active

        쿠폰형은 현재 발급분이 없거나, 마감됐거나, 발급분 수량·전체 상한에 도달하면 sold_out입니다.
        with_coupon_state(now)로 조회했다면 추가 쿼리 없이 계산합니다.
        """
        now = now or timezone.now()
        if not self.is_published:
            return self.Status.HIDDEN
        if now < self.starts_at:
            return self.Status.UPCOMING
        if now >= self.ends_at:
            return self.Status.ENDED
        if not self.requires_coupon:
            return self.Status.ACTIVE
        if not self.coupon_state(now).is_available(now, self.total_quantity):
            return self.Status.SOLD_OUT
        return self.Status.ACTIVE


class PromotionEvent(models.Model):
    """프로모션 조회·외부 채널 클릭 기록.

    VIEW는 순방문자 수가 아니라 페이지 조회 횟수입니다.
    id는 클라이언트가 보낸 event_id(UUID)를 그대로 저장해 재시도 중복을 막는 용도로 씁니다.
    """

    class EventType(models.TextChoices):
        VIEW = 'VIEW', '조회'
        CHANNEL_CLICK = 'CHANNEL_CLICK', '채널 클릭'

    class Channel(models.TextChoices):
        NONE = '', '없음'
        INSTAGRAM = 'instagram', '인스타그램'
        NAVER = 'naver', '네이버'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.PROTECT,
        related_name='events',
        verbose_name='프로모션',
        # (promotion, created_at) 복합 인덱스가 promotion 단독 조회도 처리하므로 FK 기본 인덱스는 생략
        db_index=False,
    )
    event_type = models.CharField('이벤트 종류', max_length=20, choices=EventType.choices)
    channel = models.CharField('채널', max_length=20, choices=Channel.choices, blank=True, default='')
    created_at = models.DateTimeField('생성 시각', auto_now_add=True)

    class Meta:
        db_table = 'promotion_events'
        verbose_name = '프로모션 이벤트'
        verbose_name_plural = '프로모션 이벤트'
        indexes = [
            models.Index(fields=['promotion', 'created_at'], name='promo_event_promo_created_idx'),
        ]
        constraints = [
            # VIEW는 channel이 빈 문자열, CHANNEL_CLICK은 허용된 채널만 가능
            models.CheckConstraint(
                condition=(
                    Q(event_type='VIEW', channel='')
                    | Q(event_type='CHANNEL_CLICK', channel__in=['instagram', 'naver'])
                ),
                name='promotion_event_type_channel_valid',
            ),
        ]

    def __str__(self):
        return f'{self.promotion_id} {self.event_type} {self.channel}'.strip()


class SavedPromotion(models.Model):
    """사용자가 저장한 프로모션.

    저장 여부는 이 관계의 존재로 판단하며 별도의 캐시 컬럼을 두지 않습니다.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='saved_promotions',
        verbose_name='사용자',
    )
    promotion = models.ForeignKey(
        Promotion,
        on_delete=models.CASCADE,
        related_name='saved_by',
        verbose_name='프로모션',
    )
    created_at = models.DateTimeField('저장 시각', auto_now_add=True)

    class Meta:
        db_table = 'saved_promotions'
        verbose_name = '저장한 프로모션'
        verbose_name_plural = '저장한 프로모션'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'promotion'],
                name='uniq_saved_promotion_user_promotion',
            ),
        ]
        indexes = [
            models.Index(
                fields=['user', '-created_at'],
                name='saved_promo_user_created_idx',
            ),
        ]

    def __str__(self):
        return f'{self.user_id}:{self.promotion_id}'
