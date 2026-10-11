from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from coupons.models import Coupon
from promotions.models import Promotion
from stores.models import Store, StoreMenu


User = get_user_model()


class PromotionPublicAPITestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.now = timezone.now().replace(microsecond=0)
        self.store = Store.objects.create(
            name='페어그라운드',
            category=Store.Category.BAKERY_CAFE,
            address='이대 인근',
            business_hours='10:00-20:00',
            image_url='https://example.com/store.jpg',
            story_title='가게 이야기 제목',
            story='매장 이야기',
            story_image_url='https://example.com/story.jpg',
            story_after_image='이미지 다음 이야기',
            map_url='https://example.com/map',
            instagram_url='https://instagram.com/example',
            naver_url='https://example.com/naver',
        )

    def make_promotion(self, **kwargs):
        data = {
            'store': self.store,
            'title': '휘낭시에 할인',
            'description': '정성스럽게 구운 디저트',
            'benefit': '1,000원 할인',
            'terms': '한 사람당 한 번',
            'image_url': 'https://example.com/promotion.jpg',
            'starts_at': self.now - timedelta(days=1),
            'ends_at': self.now + timedelta(days=2),
            'requires_coupon': True,
            'redeem_until': self.now + timedelta(days=3),
            'total_quantity': 10,
            'is_published': True,
        }
        data.update(kwargs)
        return Promotion.objects.create(**data)

    def make_non_coupon_promotion(self, **kwargs):
        kwargs.setdefault('requires_coupon', False)
        kwargs.setdefault('redeem_until', None)
        kwargs.setdefault('total_quantity', None)
        return self.make_promotion(**kwargs)

    def issue(self, promotion, user=None, **kwargs):
        user = user or User.objects.create_user(username=f'user-{User.objects.count()}')
        kwargs.setdefault('expires_at', promotion.redeem_until)
        return Coupon.objects.create(user=user, promotion=promotion, **kwargs)


class PromotionPublicListAPITests(PromotionPublicAPITestBase):
    url = '/api/v1/promotions/'

    def results(self, params=None):
        response = self.client.get(self.url, params or {})
        self.assertEqual(response.status_code, 200)
        return response.json()['results']

    def test_anonymous_list_fields_and_coupon_quantity(self):
        promotion = self.make_promotion(featured_rank=1)
        self.issue(promotion)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {'count', 'next', 'previous', 'results'})
        item = response.json()['results'][0]
        self.assertEqual(
            set(item),
            {
                'id', 'title', 'description', 'benefit', 'image_url', 'requires_coupon',
                'starts_at', 'ends_at', 'redeem_until', 'status', 'total_quantity',
                'remaining_quantity', 'featured_rank', 'is_saved', 'store',
            },
        )
        self.assertFalse(item['is_saved'])
        self.assertEqual(item['status'], 'active')
        self.assertEqual(item['remaining_quantity'], 9)
        self.assertEqual(item['store']['category'], 'bakery_cafe')
        self.assertIn('+09:00', item['starts_at'])

    def test_non_coupon_fields_are_null(self):
        promotion = self.make_non_coupon_promotion()

        item = self.results()[0]

        self.assertEqual(item['id'], promotion.id)
        self.assertFalse(item['requires_coupon'])
        self.assertIsNone(item['redeem_until'])
        self.assertIsNone(item['total_quantity'])
        self.assertIsNone(item['remaining_quantity'])

    def test_only_published_promotions_of_active_stores_are_listed(self):
        visible = self.make_promotion(title='공개')
        self.make_promotion(title='비공개', is_published=False)
        inactive_store = Store.objects.create(
            name='비활성', category=Store.Category.RESTAURANT, address='주소', is_active=False,
        )
        self.make_promotion(store=inactive_store, title='비활성 매장 프로모션')

        self.assertEqual([item['id'] for item in self.results()], [visible.id])

    def test_statuses(self):
        active = self.make_promotion(title='진행 중')
        upcoming = self.make_promotion(
            title='예정',
            starts_at=self.now + timedelta(days=1),
            ends_at=self.now + timedelta(days=2),
            redeem_until=self.now + timedelta(days=3),
        )
        ended = self.make_promotion(
            title='종료',
            starts_at=self.now - timedelta(days=3),
            ends_at=self.now - timedelta(days=1),
            redeem_until=self.now + timedelta(days=1),
        )
        sold_out = self.make_promotion(title='소진', total_quantity=1)
        self.issue(sold_out)

        statuses = {item['id']: item['status'] for item in self.results()}

        self.assertEqual(statuses[active.id], 'active')
        self.assertEqual(statuses[upcoming.id], 'upcoming')
        self.assertEqual(statuses[ended.id], 'ended')
        self.assertEqual(statuses[sold_out.id], 'sold_out')

    def test_category_filter_uses_lowercase_api_value(self):
        bakery = self.make_promotion(title='베이커리')
        restaurant = Store.objects.create(
            name='식당', category=Store.Category.RESTAURANT, address='주소',
        )
        self.make_promotion(store=restaurant, title='식당 행사')

        self.assertEqual(
            [item['id'] for item in self.results({'category': 'bakery_cafe'})],
            [bakery.id],
        )

    def test_status_filter(self):
        active = self.make_promotion(title='진행')
        sold_out = self.make_promotion(title='소진', total_quantity=1)
        self.issue(sold_out)

        self.assertEqual(
            [item['id'] for item in self.results({'status': 'active'})],
            [active.id],
        )
        self.assertEqual(
            [item['id'] for item in self.results({'status': 'sold_out'})],
            [sold_out.id],
        )

    def test_period_filters(self):
        upcoming = self.make_promotion(
            title='예정', starts_at=self.now + timedelta(days=1),
            ends_at=self.now + timedelta(days=2), redeem_until=self.now + timedelta(days=3),
        )
        active = self.make_promotion(title='진행')
        sold_out = self.make_promotion(title='소진', total_quantity=1)
        self.issue(sold_out)
        ended = self.make_promotion(
            title='종료', starts_at=self.now - timedelta(days=3),
            ends_at=self.now - timedelta(days=1), redeem_until=self.now + timedelta(days=1),
        )

        self.assertEqual(
            {item['id'] for item in self.results({'period': 'upcoming'})}, {upcoming.id},
        )
        self.assertEqual(
            {item['id'] for item in self.results({'period': 'ongoing'})},
            {active.id, sold_out.id},
        )
        self.assertEqual(
            {item['id'] for item in self.results({'period': 'ended'})}, {ended.id},
        )
        self.assertEqual(len(self.results({'period': 'all'})), 4)

    def test_closing_today_filter(self):
        local_today = timezone.localdate(self.now)
        closes_today = timezone.make_aware(datetime.combine(local_today, time.max))
        today = self.make_promotion(ends_at=closes_today, redeem_until=closes_today + timedelta(days=1))
        later = self.make_promotion(
            title='내일 종료', ends_at=closes_today + timedelta(days=1),
            redeem_until=closes_today + timedelta(days=2),
        )

        self.assertEqual(
            [item['id'] for item in self.results({'closing_today': 'true'})], [today.id],
        )
        self.assertEqual(
            [item['id'] for item in self.results({'closing_today': 'false'})], [later.id],
        )

    def test_featured_filter(self):
        featured = self.make_promotion(title='추천', featured_rank=1)
        regular = self.make_promotion(title='일반', featured_rank=None)

        self.assertEqual(
            [item['id'] for item in self.results({'featured': 'true'})], [featured.id],
        )
        self.assertEqual(
            [item['id'] for item in self.results({'featured': 'false'})], [regular.id],
        )

    def test_searches_title_description_and_store_name(self):
        title = self.make_promotion(title='특별 휘낭시에')
        description = self.make_promotion(title='다른 행사', description='휘낭시에 소개')
        store_match = Store.objects.create(
            name='휘낭시에 상점', category=Store.Category.RESTAURANT, address='주소',
        )
        store_promotion = self.make_promotion(store=store_match, title='매장 행사')
        self.make_promotion(title='검색 제외', description='다른 설명')

        self.assertEqual(
            {item['id'] for item in self.results({'q': '휘낭시에'})},
            {title.id, description.id, store_promotion.id},
        )

    def test_default_and_explicit_orderings(self):
        regular = self.make_promotion(title='일반')
        rank_two = self.make_promotion(title='추천 2', featured_rank=2)
        rank_one = self.make_promotion(title='추천 1', featured_rank=1)

        self.assertEqual(
            [item['id'] for item in self.results()],
            [rank_one.id, rank_two.id, regular.id],
        )
        self.assertEqual(
            [item['id'] for item in self.results({'ordering': 'latest'})],
            [rank_one.id, rank_two.id, regular.id],
        )

    def test_ending_soon_orders_unended_first_and_keeps_ended_last(self):
        later = self.make_promotion(title='나중', ends_at=self.now + timedelta(days=3), redeem_until=self.now + timedelta(days=4))
        soon = self.make_promotion(title='곧', ends_at=self.now + timedelta(hours=1), redeem_until=self.now + timedelta(days=1))
        ended = self.make_promotion(
            title='종료', starts_at=self.now - timedelta(days=3),
            ends_at=self.now - timedelta(days=1), redeem_until=self.now + timedelta(days=1),
        )

        self.assertEqual(
            [item['id'] for item in self.results({'ordering': 'ending_soon'})],
            [soon.id, later.id, ended.id],
        )
        self.assertEqual(
            [item['id'] for item in self.results({'ordering': 'ending_soon', 'status': 'ended'})],
            [ended.id],
        )

    def test_pagination_uses_twenty_items(self):
        for index in range(21):
            self.make_promotion(title=f'프로모션 {index}')

        first = self.client.get(self.url).json()
        second = self.client.get(self.url, {'page': 2}).json()

        self.assertEqual(first['count'], 21)
        self.assertEqual(len(first['results']), 20)
        self.assertEqual(len(second['results']), 1)

    def test_invalid_filter_values_return_400(self):
        invalid = {
            'category': 'cafe',
            'status': 'unknown',
            'closing_today': 'yes',
            'period': 'current',
            'featured': '1',
            'ordering': 'oldest',
        }
        for name, value in invalid.items():
            with self.subTest(name=name):
                response = self.client.get(self.url, {name: value})
                self.assertEqual(response.status_code, 400)
                self.assertIn(name, response.json()['error']['details'])

    def test_list_query_count_does_not_grow(self):
        promotions = [self.make_promotion(title=f'프로모션 {index}') for index in range(10)]
        for promotion in promotions:
            self.issue(promotion)

        with self.assertNumQueries(2):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['results']), 10)


class PromotionPublicDetailAPITests(PromotionPublicAPITestBase):
    def url(self, promotion_id):
        return f'/api/v1/promotions/{promotion_id}/'

    def test_anonymous_detail_includes_terms_store_and_ordered_menus(self):
        promotion = self.make_promotion()
        StoreMenu.objects.create(
            store=self.store,
            name='두 번째',
            description='두 번째 설명',
            price=4000,
            image_url='https://example.com/menu-2.jpg',
            sort_order=2,
        )
        StoreMenu.objects.create(
            store=self.store,
            name='첫 번째',
            description='첫 번째 설명',
            price=3500,
            image_url='https://example.com/menu-1.jpg',
            sort_order=1,
        )

        response = self.client.get(self.url(promotion.id))

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['terms'], '한 사람당 한 번')
        self.assertIsNone(body['my_coupon_id'])
        self.assertEqual(body['store']['category'], 'bakery_cafe')
        self.assertEqual(body['store']['image_url'], 'https://example.com/store.jpg')
        self.assertEqual(body['store']['map_url'], 'https://example.com/map')
        self.assertEqual(body['store']['instagram_url'], 'https://instagram.com/example')
        self.assertEqual(body['store']['naver_url'], 'https://example.com/naver')
        self.assertEqual(body['store']['story_title'], '가게 이야기 제목')
        self.assertEqual(body['store']['story'], '매장 이야기')
        self.assertEqual(body['store']['story_image_url'], 'https://example.com/story.jpg')
        self.assertEqual(body['store']['story_after_image'], '이미지 다음 이야기')
        self.assertEqual([menu['name'] for menu in body['store']['menus']], ['첫 번째', '두 번째'])
        self.assertEqual(body['store']['menus'][0]['description'], '첫 번째 설명')
        self.assertEqual(body['store']['menus'][0]['image_url'], 'https://example.com/menu-1.jpg')

    def test_detail_query_count_does_not_grow_with_nested_menus(self):
        promotion = self.make_promotion()
        for index in range(10):
            StoreMenu.objects.create(
                store=self.store,
                name=f'메뉴 {index}',
                description=f'설명 {index}',
                price=1000 + index,
                image_url=f'https://example.com/menu-{index}.jpg',
                sort_order=index,
            )

        with self.assertNumQueries(2):
            response = self.client.get(self.url(promotion.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['store']['menus']), 10)

    def test_unpublished_inactive_store_and_unknown_are_not_found(self):
        unpublished = self.make_promotion(is_published=False)
        inactive_store = Store.objects.create(
            name='비활성', category=Store.Category.RESTAURANT, address='주소', is_active=False,
        )
        inactive = self.make_promotion(store=inactive_store)

        for promotion_id in (unpublished.id, inactive.id, 999999):
            with self.subTest(promotion_id=promotion_id):
                self.assertEqual(self.client.get(self.url(promotion_id)).status_code, 404)

    def test_authenticated_users_my_coupon_id(self):
        promotion = self.make_promotion()
        user = User.objects.create_user(username='owner')
        other = User.objects.create_user(username='other')
        other_coupon = self.issue(promotion, other)
        self.client.force_login(user)

        self.assertIsNone(self.client.get(self.url(promotion.id)).json()['my_coupon_id'])

        other_coupon.delete()
        coupon = self.issue(promotion, user, used_at=self.now)
        self.assertEqual(
            self.client.get(self.url(promotion.id)).json()['my_coupon_id'], str(coupon.id),
        )

        promotion.starts_at = self.now - timedelta(days=2)
        promotion.ends_at = self.now - timedelta(days=1)
        promotion.save(update_fields=['starts_at', 'ends_at'])
        self.assertEqual(
            self.client.get(self.url(promotion.id)).json()['my_coupon_id'], str(coupon.id),
        )

        coupon.used_at = None
        coupon.expires_at = self.now - timedelta(seconds=1)
        coupon.save(update_fields=['used_at', 'expires_at'])
        self.assertEqual(
            self.client.get(self.url(promotion.id)).json()['my_coupon_id'], str(coupon.id),
        )

    def test_non_coupon_detail_has_no_coupon_id(self):
        promotion = self.make_non_coupon_promotion()
        user = User.objects.create_user(username='user')
        self.client.force_login(user)

        body = self.client.get(self.url(promotion.id)).json()

        self.assertIsNone(body['my_coupon_id'])
        self.assertIsNone(body['remaining_quantity'])

    def test_internal_store_fields_are_not_exposed(self):
        self.store.promotion_context = '내부 메모'
        self.store.set_usage_pin('0428')
        self.store.save()
        promotion = self.make_promotion()

        serialized = str(self.client.get(self.url(promotion.id)).json())

        for secret in ('promotion_context', 'usage_pin_hash', 'pin_updated_at', 'token_hash', '내부 메모', self.store.usage_pin_hash):
            self.assertNotIn(secret, serialized)
