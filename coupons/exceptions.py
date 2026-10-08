"""쿠폰 API 오류.

공통 예외 처리(config.exceptions)는 NotFound를 모두 NOT_FOUND로 바꾸므로,
전용 코드를 내려야 하는 404도 APIException을 직접 상속합니다.
응답 형식: {"error": {"code": default_code 대문자, "message": default_detail}}
"""

import math

from rest_framework import status
from rest_framework.exceptions import APIException


class PromotionNotFound(APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_code = 'PROMOTION_NOT_FOUND'
    default_detail = '프로모션을 찾을 수 없습니다.'


class PromotionCouponNotRequired(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = 'PROMOTION_COUPON_NOT_REQUIRED'
    default_detail = '쿠폰 없이 참여하는 프로모션입니다.'


class PromotionNotActive(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = 'PROMOTION_NOT_ACTIVE'
    default_detail = '아직 쿠폰 발급이 시작되지 않았습니다.'


class PromotionEnded(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = 'PROMOTION_ENDED'
    default_detail = '쿠폰 발급 기간이 종료되었습니다.'


class CouponSoldOut(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = 'COUPON_SOLD_OUT'
    default_detail = '쿠폰이 모두 소진되었습니다.'


class CouponNotFound(APIException):
    status_code = status.HTTP_404_NOT_FOUND
    default_code = 'COUPON_NOT_FOUND'
    default_detail = '쿠폰을 찾을 수 없습니다.'


class InvalidCouponStatus(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = 'INVALID_COUPON_STATUS'
    default_detail = '지원하지 않는 쿠폰 상태입니다. available, used, expired 중 하나를 사용하세요.'


class InvalidPinFormat(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = 'INVALID_PIN_FORMAT'
    default_detail = 'PIN은 4자리 숫자로 입력해 주세요.'


class PinNotSet(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = 'PIN_NOT_SET'
    default_detail = '매장 PIN이 설정되지 않았습니다. 매장에 문의해 주세요.'


class InvalidPin(APIException):
    """details.remaining_attempts: 차단 전까지 남은 시도 횟수"""

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = 'INVALID_PIN'
    default_detail = 'PIN이 올바르지 않습니다.'

    def __init__(self, remaining_attempts):
        super().__init__(f'{self.default_detail} 남은 시도 {remaining_attempts}회')
        self.details = {'remaining_attempts': remaining_attempts}


class PinLocked(APIException):
    """details.retry_after_seconds: 다시 시도할 수 있을 때까지 남은 초. Retry-After 헤더도 함께 내려갑니다."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_code = 'PIN_LOCKED'
    default_detail = 'PIN을 여러 번 잘못 입력해 잠시 사용할 수 없습니다.'

    def __init__(self, retry_after_seconds):
        minutes = max(1, math.ceil(retry_after_seconds / 60))
        super().__init__(f'PIN을 여러 번 잘못 입력했습니다. {minutes}분 후 다시 시도해 주세요.')
        # DRF 기본 예외 처리가 wait 값으로 Retry-After 헤더를 붙입니다.
        self.wait = retry_after_seconds
        self.details = {'retry_after_seconds': retry_after_seconds}


class CouponUseNotImplemented(APIException):
    """쿠폰 사용 API 개발 중 임시 응답. 4단계(사용 처리)에서 제거합니다."""

    status_code = status.HTTP_501_NOT_IMPLEMENTED
    default_code = 'NOT_IMPLEMENTED'
    default_detail = '쿠폰 사용 기능은 준비 중입니다.'
