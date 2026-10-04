from django.utils import timezone
from rest_framework import serializers

from coupons.models import Coupon
from promotions.models import Promotion
from stores.models import Store


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


# 내 쿠폰 조회. 내부 정보(운영 메모, PIN 해시, 접근 토큰)가 섞이지 않도록 필드를 명시합니다.

class CouponStoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = ('id', 'name')


class CouponPromotionSerializer(serializers.ModelSerializer):
    store = CouponStoreSerializer()

    class Meta:
        model = Promotion
        fields = ('id', 'title', 'benefit', 'image_url', 'store')


class MyCouponSerializer(CouponStatusMixin, serializers.ModelSerializer):
    promotion = CouponPromotionSerializer()

    class Meta:
        model = Coupon
        fields = ('id', 'status', 'issued_at', 'expires_at', 'used_at', 'promotion')
