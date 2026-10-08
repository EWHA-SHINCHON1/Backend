import re

from django.db import transaction
from django.utils import timezone

from coupons import exceptions
from coupons.models import Coupon
from promotions.models import Promotion

# ASCII 숫자 4자리만 허용합니다. (\d는 다른 문자권의 숫자도 허용하므로 쓰지 않음)
PIN_PATTERN = re.compile(r'[0-9]{4}')


@transaction.atomic
def issue_coupon(*, user, promotion_id):
    """쿠폰을 발급하고 (coupon, created)를 돌려줍니다.

    - Promotion 행을 먼저 잠가 같은 프로모션의 발급 요청과 Admin 수정을 한 줄로 세웁니다.
      (Admin 폼도 같은 행을 select_for_update로 잠근 뒤 발급 수를 셉니다.)
    - 이미 받은 쿠폰이 있으면 공개·기간·수량과 관계없이 기존 쿠폰을 돌려줍니다.
    - 기준 시각과 발급 수는 잠금을 얻은 뒤에 구합니다.
    """
    try:
        promotion = Promotion.objects.select_for_update().get(pk=promotion_id)
    except Promotion.DoesNotExist:
        raise exceptions.PromotionNotFound

    coupon = Coupon.objects.filter(user=user, promotion=promotion).first()
    if coupon is not None:
        return coupon, False

    now = timezone.now()
    # 비공개 프로모션은 다른 조건보다 먼저 404로 처리해 유형·기간·수량을 드러내지 않습니다.
    if not promotion.is_published:
        raise exceptions.PromotionNotFound
    if not promotion.requires_coupon:
        raise exceptions.PromotionCouponNotRequired
    if now < promotion.starts_at:
        raise exceptions.PromotionNotActive
    if now >= promotion.ends_at:
        raise exceptions.PromotionEnded
    # 사용·만료 여부와 관계없이 발급된 모든 쿠폰을 셉니다.
    if promotion.coupons.count() >= promotion.total_quantity:
        raise exceptions.CouponSoldOut

    coupon = Coupon.objects.create(
        user=user,
        promotion=promotion,
        issued_at=now,
        expires_at=promotion.redeem_until,
    )
    return coupon, True


def verify_coupon_pin(*, user, coupon_id, pin):
    """본인 쿠폰을 찾고 매장 PIN을 확인한 뒤 쿠폰을 돌려줍니다. 쿠폰은 변경하지 않습니다.

    - 쿠폰 UUID와 로그인 사용자로 함께 조회합니다. 없는 쿠폰·다른 사람의 쿠폰은 모두 COUPON_NOT_FOUND.
    - pin은 JSON 문자열이어야 하며 공백 제거나 숫자→문자열 변환을 하지 않습니다.
    - PIN 미설정 매장은 check_usage_pin()의 False와 구분하기 위해 has_usage_pin(프로퍼티)으로 먼저 확인합니다.
    - PIN 원문은 저장·로깅하지 않습니다.
    """
    coupon = (
        Coupon.objects.select_related('promotion__store')
        .filter(pk=coupon_id, user=user)
        .first()
    )
    if coupon is None:
        raise exceptions.CouponNotFound

    if not isinstance(pin, str) or not PIN_PATTERN.fullmatch(pin):
        raise exceptions.InvalidPinFormat

    store = coupon.promotion.store
    if not store.has_usage_pin:
        raise exceptions.PinNotSet
    if not store.check_usage_pin(pin):
        raise exceptions.InvalidPin
    return coupon
