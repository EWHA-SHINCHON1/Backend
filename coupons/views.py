from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from coupons.exceptions import InvalidCouponStatus
from coupons.models import Coupon
from coupons.pagination import MyCouponPagination
from coupons.serializers import IssuedCouponSerializer, MyCouponSerializer
from coupons.services import issue_coupon


class PromotionCouponIssueView(APIView):
    """쿠폰 발급: POST /api/v1/promotions/{promotion_id}/coupons/

    본문은 {}이며 사용자는 세션의 로그인 사용자로 정합니다. CSRF 토큰(X-CSRFToken)이 필요합니다.
    최초 발급 201(created=true), 이미 받은 쿠폰이 있으면 200(created=false)과 기존 쿠폰을 돌려줍니다.
    """

    def post(self, request, promotion_id):
        coupon, created = issue_coupon(user=request.user, promotion_id=promotion_id)
        serializer = IssuedCouponSerializer(coupon, context={'now': timezone.now()})
        return Response(
            {'created': created, 'coupon': serializer.data},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class MyCouponListView(ListAPIView):
    """내 쿠폰 목록: GET /api/v1/me/coupons/?status=available|used|expired&page=N

    status를 생략하면 전체, 그 외 값은 400(INVALID_COUPON_STATUS).
    최신 발급순(동률은 id)이며, 종료·비공개 프로모션의 쿠폰도 포함합니다.
    필터와 응답의 status는 같은 기준 시각(self.now)으로 계산합니다.
    """

    serializer_class = MyCouponSerializer
    pagination_class = MyCouponPagination

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.now = timezone.now()

    def get_queryset(self):
        queryset = (
            Coupon.objects.filter(user=self.request.user)
            .select_related('promotion__store')
            .order_by('-issued_at', '-id')
        )
        status_param = self.request.query_params.get('status')
        if status_param is None:
            return queryset
        if status_param not in Coupon.Status.values:
            raise InvalidCouponStatus
        return queryset.filter_status(status_param, self.now)

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'now': self.now}
