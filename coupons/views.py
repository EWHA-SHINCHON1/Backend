from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from coupons.exceptions import CouponNotFound, InvalidCouponStatus
from coupons.models import Coupon
from coupons.pagination import MyCouponPagination
from coupons.serializers import IssuedCouponSerializer, MyCouponDetailSerializer, MyCouponSerializer
from coupons.services import issue_coupon, use_coupon
from coupons.throttles import CouponIssueRateThrottle


class PromotionCouponIssueView(APIView):
    """쿠폰 발급: POST /api/v1/promotions/{promotion_id}/coupons/

    본문은 {}이며 사용자는 세션의 로그인 사용자로 정합니다. CSRF 토큰(X-CSRFToken)이 필요합니다.
    최초 발급 201(created=true), 이미 받은 쿠폰이 있으면 200(created=false)과 기존 쿠폰을 돌려줍니다.
    로그인 사용자별 분당 10회까지 요청할 수 있습니다. 초과 시 429 TOO_MANY_REQUESTS + Retry-After 헤더.
    """

    throttle_classes = [CouponIssueRateThrottle]

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
    성공: 200 {"coupon": 내 쿠폰 목록 항목과 같은 구조(status=used, used_at 포함)}

    오류 확인 순서: 쿠폰 없음(404 COUPON_NOT_FOUND) → PIN 형식(400 INVALID_PIN_FORMAT)
    → 이미 사용(409 COUPON_ALREADY_USED) → 만료(409 COUPON_EXPIRED) → 매장 PIN 미설정(409 PIN_NOT_SET)
    → 차단 중(429 PIN_LOCKED) → PIN 불일치(400 INVALID_PIN, details.remaining_attempts).
    사용자+매장 기준 10분 안에 5번 틀리면 10분간 차단합니다(429, details.retry_after_seconds, Retry-After 헤더).
    """

    def post(self, request, coupon_id):
        data = request.data if isinstance(request.data, dict) else {}
        coupon = use_coupon(user=request.user, coupon_id=coupon_id, pin=data.get('pin'))
        serializer = MyCouponSerializer(coupon, context={'now': timezone.now()})
        return Response({'coupon': serializer.data})
