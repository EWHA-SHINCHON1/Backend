from rest_framework.throttling import UserRateThrottle


class CouponIssueRateThrottle(UserRateThrottle):
    """쿠폰 발급 요청 횟수 제한. 로그인 사용자별로 셉니다. (한도: settings DEFAULT_THROTTLE_RATES['coupon_issue'])

    발급 API는 로그인이 필요하므로 비로그인 요청은 throttle 전에 401로 끝납니다.
    이미 받은 쿠폰을 다시 요청한 경우(200)도 요청 횟수에 포함합니다.
    """

    scope = 'coupon_issue'
