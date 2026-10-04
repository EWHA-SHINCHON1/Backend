from rest_framework.pagination import PageNumberPagination


class MyCouponPagination(PageNumberPagination):
    """내 쿠폰 목록 전용. 프로젝트 전역 페이지네이션 설정은 없으므로 이 목록에만 적용합니다."""

    page_size = 20
