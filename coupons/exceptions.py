"""쿠폰 API 오류.

공통 예외 처리(config.exceptions)는 NotFound를 모두 NOT_FOUND로 바꾸므로,
전용 코드를 내려야 하는 404도 APIException을 직접 상속합니다.
응답 형식: {"error": {"code": default_code 대문자, "message": default_detail}}
"""

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
