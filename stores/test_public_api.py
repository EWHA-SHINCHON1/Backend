from django.test import TestCase
from rest_framework.test import APIClient

from stores.models import Store, StoreMenu


class StorePublicDetailAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.store = Store.objects.create(
            name='페어그라운드',
            category=Store.Category.BAKERY_CAFE,
            address='이대 인근',
            business_hours='매일 10:00-20:00',
            image_url='https://example.com/store.jpg',
            story='매장 이야기',
            promotion_context='내부 운영 메모',
            map_url='https://example.com/map',
            instagram_url='https://instagram.com/example',
            naver_url='https://example.com/naver',
        )
        self.store.set_usage_pin('0428')
        self.store.save()
        StoreMenu.objects.create(store=self.store, name='두 번째', price=4000, sort_order=2)
        StoreMenu.objects.create(store=self.store, name='첫 번째', price=3500, sort_order=1)

    def url(self, store_id=None):
        return f'/api/v1/stores/{store_id or self.store.id}/'

    def test_anonymous_can_retrieve_active_store(self):
        response = self.client.get(self.url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()),
            {
                'id', 'name', 'category', 'address', 'business_hours', 'image_url',
                'story', 'map_url', 'instagram_url', 'naver_url', 'menus',
            },
        )
        self.assertEqual(response.json()['category'], 'bakery_cafe')
        self.assertEqual(
            response.json()['menus'],
            [
                {'name': '첫 번째', 'price': 3500, 'sort_order': 1},
                {'name': '두 번째', 'price': 4000, 'sort_order': 2},
            ],
        )

    def test_internal_fields_and_promotions_are_not_exposed(self):
        body = self.client.get(self.url()).json()
        serialized = str(body)

        self.assertNotIn('promotions', body)
        for secret in (
            'promotion_context', 'usage_pin_hash', 'pin_updated_at', 'access_tokens',
            'token_hash', '내부 운영 메모', self.store.usage_pin_hash,
        ):
            self.assertNotIn(secret, serialized)

    def test_inactive_store_is_not_found(self):
        self.store.is_active = False
        self.store.save(update_fields=['is_active'])

        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_unknown_store_is_not_found(self):
        self.assertEqual(self.client.get(self.url(999999)).status_code, 404)

    def test_store_detail_uses_constant_queries_for_menus(self):
        for index in range(10):
            StoreMenu.objects.create(
                store=self.store,
                name=f'메뉴 {index}',
                price=1000 + index,
                sort_order=index + 3,
            )

        with self.assertNumQueries(2):
            response = self.client.get(self.url())

        self.assertEqual(response.status_code, 200)
