from rest_framework import serializers

from stores.models import Store, StoreMenu

from .models import Promotion


class PromotionStoreSummarySerializer(serializers.ModelSerializer):
    category = serializers.SerializerMethodField()

    class Meta:
        model = Store
        fields = ('id', 'name', 'category', 'address')

    def get_category(self, store):
        return store.category.lower()


class PromotionStoreMenuSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreMenu
        fields = ('name', 'price', 'sort_order')


class PromotionStoreDetailSerializer(serializers.ModelSerializer):
    category = serializers.SerializerMethodField()
    menus = PromotionStoreMenuSerializer(many=True, read_only=True)

    class Meta:
        model = Store
        fields = ('id', 'name', 'category', 'address', 'business_hours', 'story', 'menus')

    def get_category(self, store):
        return store.category.lower()


class PromotionPublicListSerializer(serializers.ModelSerializer):
    starts_at = serializers.DateTimeField(format='iso-8601', read_only=True)
    ends_at = serializers.DateTimeField(format='iso-8601', read_only=True)
    redeem_until = serializers.DateTimeField(format='iso-8601', allow_null=True, read_only=True)
    status = serializers.SerializerMethodField()
    remaining_quantity = serializers.SerializerMethodField()
    store = PromotionStoreSummarySerializer(read_only=True)

    class Meta:
        model = Promotion
        fields = (
            'id',
            'title',
            'description',
            'benefit',
            'image_url',
            'requires_coupon',
            'starts_at',
            'ends_at',
            'redeem_until',
            'status',
            'total_quantity',
            'remaining_quantity',
            'featured_rank',
            'store',
        )

    def get_status(self, promotion):
        return promotion.get_status(
            issued_count=getattr(promotion, 'api_issued_count', None),
            now=self.context['now'],
        )

    def get_remaining_quantity(self, promotion):
        if not promotion.requires_coupon:
            return None
        issued_count = getattr(promotion, 'api_issued_count', None)
        if issued_count is None:
            return promotion.remaining_quantity
        return max(promotion.total_quantity - issued_count, 0)


class PromotionPublicDetailSerializer(PromotionPublicListSerializer):
    store = PromotionStoreDetailSerializer(read_only=True)
    my_coupon_id = serializers.SerializerMethodField()

    class Meta(PromotionPublicListSerializer.Meta):
        fields = PromotionPublicListSerializer.Meta.fields + ('terms', 'my_coupon_id')

    def get_my_coupon_id(self, promotion):
        if not promotion.requires_coupon:
            return None
        coupon_id = getattr(promotion, 'api_my_coupon_id', None)
        return str(coupon_id) if coupon_id is not None else None
