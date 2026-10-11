from rest_framework import serializers

from .models import Store, StoreMenu


class StoreMenuPublicSerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreMenu
        fields = ('name', 'description', 'price', 'image_url', 'sort_order')


class StorePublicDetailSerializer(serializers.ModelSerializer):
    category = serializers.SerializerMethodField()
    menus = StoreMenuPublicSerializer(many=True, read_only=True)

    class Meta:
        model = Store
        fields = (
            'id',
            'name',
            'category',
            'address',
            'business_hours',
            'image_url',
            'story_title',
            'story',
            'story_image_url',
            'story_after_image',
            'map_url',
            'instagram_url',
            'naver_url',
            'menus',
        )

    def get_category(self, store):
        return store.category.lower()
