from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from coupons.serializers import IssuedCouponSerializer
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
