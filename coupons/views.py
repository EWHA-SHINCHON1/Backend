from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from coupons.exceptions import CouponNotFound, CouponUseNotImplemented, InvalidCouponStatus
from coupons.models import Coupon
from coupons.pagination import MyCouponPagination
from coupons.serializers import IssuedCouponSerializer, MyCouponDetailSerializer, MyCouponSerializer
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


class MyCouponDetailView(RetrieveAPIView):
    """내 쿠폰 상세: GET /api/v1/me/coupons/{coupon_id}/

    쿠폰 UUID와 로그인 사용자로 함께 조회하므로 없는 쿠폰과 다른 사람의 쿠폰은 모두 404(COUPON_NOT_FOUND).
    종료·비공개 프로모션의 쿠폰도 조회할 수 있습니다.
    """

    serializer_class = MyCouponDetailSerializer

    def get_object(self):
        try:
            return Coupon.objects.select_related('promotion__store').get(
                pk=self.kwargs['coupon_id'],
                user=self.request.user,
            )
        except Coupon.DoesNotExist:
            raise CouponNotFound

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'now': timezone.now()}


class MyCouponUseView(APIView):
    """쿠폰 사용: POST /api/v1/me/coupons/{coupon_id}/use/

    요청: {"pin": "0428"} — 점주가 사용자 휴대폰에서 매장 PIN을 입력합니다. CSRF 토큰(X-CSRFToken)이 필요합니다.
    쿠폰 UUID와 로그인 사용자로 함께 조회하므로 없는 쿠폰과 다른 사람의 쿠폰은 모두 404(COUPON_NOT_FOUND).

    개발 단계: 현재는 로그인·소유자 확인까지만 하고 501을 돌려줍니다.
    PIN 검증(2단계), 실패 횟수 제한(3단계), 사용 처리와 완료 응답(4단계)은 이후 단계에서 추가합니다.
    """

    def post(self, request, coupon_id):
        if not Coupon.objects.filter(pk=coupon_id, user=request.user).exists():
            raise CouponNotFound
        raise CouponUseNotImplemented
