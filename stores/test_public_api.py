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
            story_title='익숙한 재료의 새로운 맛',
            story='매장 이야기',
            story_image_url='https://example.com/story.jpg',
            story_after_image='사진 뒤에 이어지는 이야기',
            promotion_context='내부 운영 메모',
            map_url='https://example.com/map',
            instagram_url='https://instagram.com/example',
            naver_url='https://example.com/naver',
        )
        self.store.set_usage_pin('0428')
        self.store.save()
        StoreMenu.objects.create(
            store=self.store,
            name='두 번째',
            description='두 번째 메뉴 설명',
            price=4000,
            image_url='https://example.com/menu-2.jpg',
            sort_order=2,
        )
        StoreMenu.objects.create(
            store=self.store,
            name='첫 번째',
            description='첫 번째 메뉴 설명',
            price=3500,
            image_url='https://example.com/menu-1.jpg',
            sort_order=1,
        )

    def url(self, store_id=None):
        return f'/api/v1/stores/{store_id or self.store.id}/'

    def test_anonymous_can_retrieve_active_store(self):
        response = self.client.get(self.url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()),
            {
                'id', 'name', 'category', 'address', 'business_hours', 'image_url',
                'story_title', 'story', 'story_image_url', 'story_after_image',
                'map_url', 'instagram_url', 'naver_url', 'menus',
            },
        )
        self.assertEqual(response.json()['category'], 'bakery_cafe')
        self.assertEqual(response.json()['story_title'], '익숙한 재료의 새로운 맛')
        self.assertEqual(response.json()['story_image_url'], 'https://example.com/story.jpg')
        self.assertEqual(response.json()['story_after_image'], '사진 뒤에 이어지는 이야기')
        self.assertEqual(
            response.json()['menus'],
            [
                {
                    'name': '첫 번째',
                    'description': '첫 번째 메뉴 설명',
                    'price': 3500,
                    'image_url': 'https://example.com/menu-1.jpg',
                    'sort_order': 1,
                },
                {
                    'name': '두 번째',
                    'description': '두 번째 메뉴 설명',
                    'price': 4000,
                    'image_url': 'https://example.com/menu-2.jpg',
                    'sort_order': 2,
                },
            ],
        )

    def test_empty_story_and_menu_content_are_returned_as_empty_strings(self):
        store = Store.objects.create(
            name='빈 콘텐츠 매장',
            category=Store.Category.RESTAURANT,
            address='신촌',
        )
        StoreMenu.objects.create(store=store, name='기본 메뉴', price=1000)

        body = self.client.get(self.url(store.id)).json()

        self.assertEqual(body['story_title'], '')
        self.assertEqual(body['story'], '')
        self.assertEqual(body['story_image_url'], '')
        self.assertEqual(body['story_after_image'], '')
        self.assertEqual(body['menus'][0]['description'], '')
        self.assertEqual(body['menus'][0]['image_url'], '')

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
