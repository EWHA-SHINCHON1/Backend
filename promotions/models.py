import uuid

from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class Promotion(models.Model):
    """매장 프로모션.

    status는 저장하지 않고 get_status()로 계산합니다.
    - 신규 쿠폰 발급 기간: starts_at <= now < ends_at
    - 이미 발급된 쿠폰은 발급 기간이 끝나도 자신의 expires_at까지 사용합니다.
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
    starts_at = models.DateTimeField('발급 시작')
    ends_at = models.DateTimeField('발급 종료')
    redeem_until = models.DateTimeField('사용 종료')
    total_quantity = models.PositiveIntegerField('총 발급 수량')
    # 운영자 검수 전 노출을 막기 위해 기본값은 비공개입니다.
    is_published = models.BooleanField('공개 여부', default=False)
    featured_rank = models.PositiveIntegerField('추천 노출 순위', null=True, blank=True)
    created_at = models.DateTimeField('생성 시각', auto_now_add=True)
    updated_at = models.DateTimeField('수정 시각', auto_now=True)

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
                condition=Q(ends_at__lte=F('redeem_until')),
                name='promotion_ends_before_redeem_until',
            ),
            models.CheckConstraint(
                condition=Q(total_quantity__gt=0),
                name='promotion_total_quantity_positive',
            ),
        ]

    def __str__(self):
        return self.title

    def is_in_issue_period(self, now=None):
        """신규 쿠폰 발급 기간인지 (수량·공개 여부는 보지 않음)."""
        now = now or timezone.now()
        return self.starts_at <= now < self.ends_at

    @property
    def issued_count(self):
        """발급된 쿠폰 수. 별도 카운터 컬럼 없이 연결된 Coupon 수로 계산합니다."""
        return self.coupons.count()

    @property
    def remaining_quantity(self):
        return max(self.total_quantity - self.issued_count, 0)

    def get_status(self, *, issued_count=None, now=None):
        """우선순위: hidden → upcoming → ended → sold_out → active

        목록 조회에서 annotate로 발급 수를 미리 구했다면 issued_count로 넘겨 추가 쿼리를 피할 수 있습니다.
        넘기지 않으면 소진 여부를 판단할 때만 Coupon 수를 셉니다.
        """
        now = now or timezone.now()
        if not self.is_published:
            return self.Status.HIDDEN
        if now < self.starts_at:
            return self.Status.UPCOMING
        if now >= self.ends_at:
            return self.Status.ENDED
        if issued_count is None:
            issued_count = self.issued_count
        if issued_count >= self.total_quantity:
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
