from django.db import transaction
from django.utils import timezone

from coupons import exceptions
from coupons.models import Coupon
from promotions.models import Promotion


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
