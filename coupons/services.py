import math
import re
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from coupons import exceptions
from coupons.models import Coupon, PinAttempt
from promotions.models import Promotion

# ASCII 숫자 4자리만 허용합니다. (\d는 다른 문자권의 숫자도 허용하므로 쓰지 않음)
PIN_PATTERN = re.compile(r'[0-9]{4}')

# PIN 실패 제한: 첫 실패부터 PIN_FAILURE_WINDOW 안에 PIN_MAX_FAILURES번 틀리면 PIN_LOCK_DURATION 동안 차단
PIN_MAX_FAILURES = 5
PIN_FAILURE_WINDOW = timedelta(minutes=10)
PIN_LOCK_DURATION = timedelta(minutes=10)


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

    result = check_pin_with_limit(user=user, store=store, pin=pin)
    raise_for_pin_result(result)
    return coupon


@dataclass(frozen=True)
class PinCheckResult:
    ok: bool
    remaining_attempts: int = 0
    retry_after_seconds: int = 0

    @property
    def locked(self):
        return self.retry_after_seconds > 0


def check_pin_with_limit(*, user, store, pin):
    """실패 횟수 제한을 적용해 매장 PIN을 확인하고 결과를 돌려줍니다. 예외는 던지지 않습니다.

    - 사용자+매장의 PinAttempt 행을 잠근 채 PIN을 확인하므로, 같은 사용자·매장의 동시 요청은
      한 줄로 처리되어 허용 횟수를 넘겨 시도할 수 없습니다.
    - 이 함수 안에서는 예외를 던지지 않고 결과만 돌려줍니다. 호출한 쪽이 트랜잭션을 마친 뒤
      raise_for_pin_result()로 오류를 내야 실패 기록이 롤백되지 않습니다.
    - 차단 중에는 PIN을 확인하지 않습니다. (맞는 PIN이어도 차단)
    """
    with transaction.atomic():
        PinAttempt.objects.get_or_create(user=user, store=store)
        attempt = PinAttempt.objects.select_for_update().get(user=user, store=store)
        # 잠금을 얻은 뒤의 시각으로 판단합니다.
        now = timezone.now()

        if attempt.locked_until is not None:
            if now < attempt.locked_until:
                return PinCheckResult(ok=False, retry_after_seconds=_seconds_until(attempt.locked_until, now))
            _reset(attempt)  # 차단 시간이 지나면 처음부터 다시 셉니다.
        elif attempt.first_failed_at is not None and now - attempt.first_failed_at >= PIN_FAILURE_WINDOW:
            _reset(attempt)  # 집계 기간이 지난 실패는 버립니다.

        if store.check_usage_pin(pin):
            _reset(attempt)
            attempt.save()
            return PinCheckResult(ok=True)

        attempt.failure_count += 1
        if attempt.first_failed_at is None:
            attempt.first_failed_at = now
        if attempt.failure_count >= PIN_MAX_FAILURES:
            attempt.locked_until = now + PIN_LOCK_DURATION
            attempt.save()
            return PinCheckResult(ok=False, retry_after_seconds=_seconds_until(attempt.locked_until, now))
        attempt.save()
        return PinCheckResult(ok=False, remaining_attempts=PIN_MAX_FAILURES - attempt.failure_count)


def raise_for_pin_result(result):
    if result.ok:
        return
    if result.locked:
        raise exceptions.PinLocked(result.retry_after_seconds)
    raise exceptions.InvalidPin(result.remaining_attempts)


def _reset(attempt):
    attempt.failure_count = 0
    attempt.first_failed_at = None
    attempt.locked_until = None


def _seconds_until(moment, now):
    return max(1, math.ceil((moment - now).total_seconds()))
