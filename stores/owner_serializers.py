from rest_framework import serializers

from promotions.models import Promotion

from .models import Store


def get_period_status(promotion, now):
    if now < promotion.starts_at:
        return 'upcoming'
    if now >= promotion.ends_at:
        return 'ended'
    return 'ongoing'


class OwnerStoreSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = Store
        fields = ('id', 'name')


class OwnerDashboardPromotionSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()
    period_status = serializers.SerializerMethodField()

    class Meta:
        model = Promotion
        fields = (
            'id',
            'title',
            'image_url',
            'starts_at',
            'ends_at',
            'is_published',
            'requires_coupon',
            'status',
            'period_status',
        )

    def get_status(self, promotion):
        return promotion.get_status(
            issued_count=promotion.owner_issued_count,
            now=self.context['now'],
        )

    def get_period_status(self, promotion):
        return get_period_status(promotion, self.context['now'])


class OwnerStatsPromotionSerializer(serializers.ModelSerializer):
    store = OwnerStoreSummarySerializer(read_only=True)

    class Meta:
        model = Promotion
        fields = (
            'id',
            'title',
            'image_url',
            'starts_at',
            'ends_at',
            'store',
            'requires_coupon',
            'is_published',
        )


class OwnerChannelClicksSerializer(serializers.Serializer):
    instagram = serializers.IntegerField(min_value=0)
    naver = serializers.IntegerField(min_value=0)


class OwnerPromotionMetricsSerializer(serializers.Serializer):
    views = serializers.IntegerField(min_value=0)
    channel_clicks = serializers.IntegerField(min_value=0)
    channel_clicks_by_channel = OwnerChannelClicksSerializer()
    coupons_issued = serializers.IntegerField(min_value=0, allow_null=True)
    coupons_used = serializers.IntegerField(min_value=0, allow_null=True)
    usage_rate_percent = serializers.FloatField(min_value=0, allow_null=True)
