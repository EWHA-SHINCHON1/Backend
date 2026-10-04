from django.utils import timezone
from rest_framework import serializers

from coupons.models import Coupon


class CouponStatusMixin(serializers.Serializer):
    """상태는 context의 now(한 요청의 기준 시각)로 계산합니다."""

    status = serializers.SerializerMethodField()

    def get_status(self, coupon):
        now = self.context.get('now') or timezone.now()
        return coupon.get_status(now=now)


class IssuedCouponSerializer(CouponStatusMixin, serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = ('id', 'status', 'issued_at', 'expires_at')
