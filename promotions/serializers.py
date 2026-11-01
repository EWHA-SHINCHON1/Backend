from rest_framework import serializers

from stores.models import Store, StoreMenu

from .models import Promotion, PromotionEvent


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
        fields = ('name', 'description', 'price', 'image_url', 'sort_order')


class PromotionStoreDetailSerializer(serializers.ModelSerializer):
    category = serializers.SerializerMethodField()
    menus = PromotionStoreMenuSerializer(many=True, read_only=True)

    class Meta:
        model = Store
        fields = (
            'id',
            'name',
            'category',
            'address',
            'business_hours',
            'image_url',
            'map_url',
            'instagram_url',
            'naver_url',
            'story_title',
            'story',
            'story_image_url',
            'story_after_image',
            'menus',
        )

    def get_category(self, store):
        return store.category.lower()


class PromotionPublicListSerializer(serializers.ModelSerializer):
    starts_at = serializers.DateTimeField(format='iso-8601', read_only=True)
    ends_at = serializers.DateTimeField(format='iso-8601', read_only=True)
    # 쿠폰형은 현재 발급분(발급이 시작된 가장 최근 발급분) 기준 값입니다. 필드 이름과 형식은 발급분 도입 전과 같습니다.
    # - redeem_until: 현재 발급분의 사용 마감 (없으면 프로모션의 최종 사용 한도)
    # - total_quantity: 현재 발급분 수량 (발급이 시작된 발급분이 없으면 0)
    # - remaining_quantity: 현재 발급분에서 지금 더 받을 수 있는 수량 (닫혔거나 없으면 0)
    redeem_until = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    total_quantity = serializers.SerializerMethodField()
    remaining_quantity = serializers.SerializerMethodField()
    is_saved = serializers.BooleanField(read_only=True)
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
            'is_saved',
            'store',
        )

    def get_status(self, promotion):
        return promotion.get_status(now=self.context['now'])

    def get_redeem_until(self, promotion):
        redeem_until = promotion.redeem_until
        if promotion.requires_coupon:
            redeem_until = promotion.coupon_state(self.context['now']).current_expires_at or redeem_until
        if redeem_until is None:
            return None
        return serializers.DateTimeField(format='iso-8601').to_representation(redeem_until)

    def get_total_quantity(self, promotion):
        if not promotion.requires_coupon:
            return None
        return promotion.coupon_state(self.context['now']).display_quantity

    def get_remaining_quantity(self, promotion):
        return promotion.get_remaining_quantity(now=self.context['now'])


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


class PromotionEventInputSerializer(serializers.Serializer):
    event_id = serializers.UUIDField()
    event_type = serializers.ChoiceField(choices=('view', 'channel_click'))
    channel = serializers.CharField(required=False, allow_blank=True, default='', max_length=20)

    def validate(self, attrs):
        event_type = attrs['event_type']
        channel = attrs['channel']
        if event_type == 'view' and channel:
            raise serializers.ValidationError({'channel': ['view 이벤트에는 channel을 지정할 수 없습니다.']})
        if event_type == 'channel_click' and channel not in {
            PromotionEvent.Channel.INSTAGRAM,
            PromotionEvent.Channel.NAVER,
        }:
            raise serializers.ValidationError(
                {'channel': ['channel_click 이벤트에는 instagram 또는 naver가 필요합니다.']}
            )
        return attrs


class PromotionEventSerializer(serializers.ModelSerializer):
    event_id = serializers.UUIDField(source='id', read_only=True)
    event_type = serializers.SerializerMethodField()

    class Meta:
        model = PromotionEvent
        fields = ('event_id', 'event_type', 'channel', 'created_at')

    def get_event_type(self, event):
        return event.event_type.lower()
