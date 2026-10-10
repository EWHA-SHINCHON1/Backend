from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone


class Store(models.Model):
    """매장.

    promotion_context, usage_pin_hash, pin_updated_at은 내부 정보이므로 공개 응답에 포함하지 않습니다.
    """

    class Category(models.TextChoices):
        BAKERY_CAFE = 'BAKERY_CAFE', '베이커리·카페'
        RESTAURANT = 'RESTAURANT', '음식점'

    name = models.CharField('매장명', max_length=100)
    category = models.CharField('카테고리', max_length=20, choices=Category.choices)
    address = models.CharField('주소', max_length=255)
    business_hours = models.TextField('영업시간·휴무 안내', blank=True, default='')
    image_url = models.URLField('대표 이미지 URL', max_length=500, blank=True, default='')
    story_title = models.CharField('가게 이야기 제목', max_length=200, blank=True, default='')
    story = models.TextField('매장 소개', blank=True, default='')
    story_image_url = models.URLField(
        '가게 이야기 이미지 URL',
        max_length=500,
        blank=True,
        default='',
    )
    story_after_image = models.TextField('이미지 이후 이야기', blank=True, default='')
    # Liner 초안 입력용 내부 인터뷰·운영 메모. story와 달리 공개하지 않습니다.
    promotion_context = models.TextField('내부 운영 메모', blank=True, default='')
    map_url = models.URLField('지도 링크', max_length=500, blank=True, default='')
    instagram_url = models.URLField('인스타그램 링크', max_length=500, blank=True, default='')
    naver_url = models.URLField('네이버 링크', max_length=500, blank=True, default='')
    # 매장 공통 PIN의 해시. 빈 값이면 PIN 미설정 상태이며 어떤 PIN도 통과하지 않습니다.
    usage_pin_hash = models.CharField('사용 PIN 해시', max_length=128, blank=True, default='')
    pin_updated_at = models.DateTimeField('PIN 변경 시각', null=True, blank=True)
    is_active = models.BooleanField('운영 여부', default=True)
    created_at = models.DateTimeField('생성 시각', auto_now_add=True)
    updated_at = models.DateTimeField('수정 시각', auto_now=True)

    class Meta:
        db_table = 'stores'
        verbose_name = '매장'
        verbose_name_plural = '매장'

    def __str__(self):
        return self.name

    @property
    def has_usage_pin(self):
        return bool(self.usage_pin_hash)

    def set_usage_pin(self, raw_pin):
        """PIN을 해시로 바꿔 저장할 준비를 합니다. 저장(save)은 호출한 쪽에서 합니다.

        앞자리 0을 보존하기 위해 문자열만 받습니다.
        """
        if not isinstance(raw_pin, str) or not raw_pin:
            raise ValueError('PIN은 비어 있지 않은 문자열이어야 합니다.')
        self.usage_pin_hash = make_password(raw_pin)
        self.pin_updated_at = timezone.now()

    def check_usage_pin(self, raw_pin):
        """입력한 PIN이 매장 PIN과 일치하는지 확인합니다. PIN 미설정 매장은 항상 False입니다."""
        if not self.usage_pin_hash or not isinstance(raw_pin, str) or not raw_pin:
            return False
        return check_password(raw_pin, self.usage_pin_hash)


class StoreMenu(models.Model):
    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name='menus',
        verbose_name='매장',
    )
    name = models.CharField('메뉴명', max_length=100)
    description = models.TextField('메뉴 설명', blank=True, default='')
    # 원 단위 정수. PositiveIntegerField라 DB에 0 이상 CHECK 제약이 생깁니다.
    price = models.PositiveIntegerField('가격(원)')
    image_url = models.URLField('메뉴 이미지 URL', max_length=500, blank=True, default='')
    sort_order = models.PositiveIntegerField('표시 순서', default=0)

    class Meta:
        db_table = 'store_menus'
        verbose_name = '매장 메뉴'
        verbose_name_plural = '매장 메뉴'
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f'{self.store.name} - {self.name}'


class StoreAccessToken(models.Model):
    """점주 대시보드 시크릿 링크용 토큰.

    원문 토큰은 저장하지 않고 해시만 저장합니다. 소비자 인증·쿠폰 PIN과는 관계없습니다.
    토큰 생성·검증 로직은 점주 인증 기능에서 구현합니다.
    """

    store = models.ForeignKey(
        Store,
        on_delete=models.CASCADE,
        related_name='access_tokens',
        verbose_name='매장',
    )
    # 조회용 해시(예: SHA-256 hex 64자). 형식은 점주 인증 구현과 맞춥니다.
    token_hash = models.CharField('토큰 해시', max_length=64, unique=True)
    is_active = models.BooleanField('사용 가능', default=True)
    created_at = models.DateTimeField('생성 시각', auto_now_add=True)
    expires_at = models.DateTimeField('만료 시각', null=True, blank=True)
    last_used_at = models.DateTimeField('최근 사용 시각', null=True, blank=True)

    class Meta:
        db_table = 'store_access_tokens'
        verbose_name = '매장 접근 토큰'
        verbose_name_plural = '매장 접근 토큰'

    def __str__(self):
        return f'{self.store.name} 토큰 #{self.pk}'
