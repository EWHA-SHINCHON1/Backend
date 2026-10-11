from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from coupons.models import Coupon
from promotions.models import Promotion, PromotionEvent
from stores.models import Store
from stores.owner_serializers import get_period_status
from stores.services import issue_store_access_token


User = get_user_model()


def make_store(name='점주 매장', **kwargs):
    values = {
        'name': name,
        'category': Store.Category.BAKERY_CAFE,
        'address': '서울 서대문구',
    }
    values.update(kwargs)
    return Store.objects.create(**values)


def make_promotion(store, **kwargs):
    now = timezone.now()
    values = {
        'store': store,
        'title': '점주 프로모션',
        'image_url': 'https://example.com/promotion.jpg',
        'starts_at': now - timedelta(days=1),
        'ends_at': now + timedelta(days=1),
        'requires_coupon': True,
        'redeem_until': now + timedelta(days=2),
        'total_quantity': 100,
        'is_published': True,
    }
    values.update(kwargs)
    return Promotion.objects.create(**values)


def make_non_coupon_promotion(store, **kwargs):
    kwargs.update(requires_coupon=False, redeem_until=None, total_quantity=None)
    return make_promotion(store, **kwargs)


def add_events(promotion, *, views=0, instagram=0, naver=0):
    PromotionEvent.objects.bulk_create([
        PromotionEvent(promotion=promotion, event_type=PromotionEvent.EventType.VIEW)
        for _index in range(views)
    ] + [
        PromotionEvent(
            promotion=promotion,
            event_type=PromotionEvent.EventType.CHANNEL_CLICK,
            channel=PromotionEvent.Channel.INSTAGRAM,
        )
        for _index in range(instagram)
    ] + [
        PromotionEvent(
            promotion=promotion,
            event_type=PromotionEvent.EventType.CHANNEL_CLICK,
            channel=PromotionEvent.Channel.NAVER,
        )
        for _index in range(naver)
    ])


def add_coupons(promotion, *, issued, used, expired=0):
    now = timezone.now()
    coupons = []
    for index in range(issued):
        user = User.objects.create_user(username=f'user-{promotion.id}-{index}')
        is_used = index < used
        is_expired = used <= index < used + expired
        coupons.append(Coupon.objects.create(
            user=user,
            promotion=promotion,
            expires_at=now - timedelta(days=1) if is_expired else now + timedelta(days=1),
            used_at=now if is_used else None,
        ))
    return coupons


class OwnerAPITestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.store = make_store()
        self.issued_token = issue_store_access_token(store=self.store)
        self.auth = {'HTTP_AUTHORIZATION': f'Owner {self.issued_token.raw_token}'}

    def stats_url(self, promotion):
        return f'/api/v1/owner/promotions/{promotion.id}/stats/'


class OwnerAPIAuthenticationTests(OwnerAPITestBase):
    def test_valid_owner_token_accesses_dashboard_and_stats(self):
        promotion = make_promotion(self.store)

        self.assertEqual(self.client.get('/api/v1/owner/dashboard/', **self.auth).status_code, 200)
        self.assertEqual(self.client.get(self.stats_url(promotion), **self.auth).status_code, 200)

    def test_missing_and_invalid_tokens_return_401(self):
        promotion = make_promotion(self.store)
        urls = ('/api/v1/owner/dashboard/', self.stats_url(promotion))

        for url in urls:
            for headers in ({}, {'HTTP_AUTHORIZATION': f"Owner {'x' * 43}"}):
                with self.subTest(url=url, headers=bool(headers)):
                    response = self.client.get(url, **headers)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response['WWW-Authenticate'], 'Owner')

    def test_expired_inactive_token_and_inactive_store_return_401(self):
        token = self.issued_token.access_token

        token.expires_at = timezone.now()
        token.save(update_fields=['expires_at'])
        self.assertEqual(self.client.get('/api/v1/owner/dashboard/', **self.auth).status_code, 401)

        replacement = issue_store_access_token(store=self.store)
        replacement.access_token.is_active = False
        replacement.access_token.save(update_fields=['is_active'])
        headers = {'HTTP_AUTHORIZATION': f'Owner {replacement.raw_token}'}
        self.assertEqual(self.client.get('/api/v1/owner/dashboard/', **headers).status_code, 401)

        active = issue_store_access_token(store=self.store)
        self.store.is_active = False
        self.store.save(update_fields=['is_active'])
        headers = {'HTTP_AUTHORIZATION': f'Owner {active.raw_token}'}
        self.assertEqual(self.client.get('/api/v1/owner/dashboard/', **headers).status_code, 401)

    def test_consumer_session_cannot_access_owner_apis(self):
        self.client.force_login(User.objects.create_user(username='consumer'))

        self.assertEqual(self.client.get('/api/v1/owner/dashboard/').status_code, 401)


class OwnerDashboardAPITests(OwnerAPITestBase):
    def test_dashboard_returns_only_authenticated_store_promotions_including_hidden(self):
        own_public = make_promotion(self.store, title='공개')
        own_hidden = make_promotion(self.store, title='비공개', is_published=False)
        other_store = make_store('다른 매장')
        make_promotion(other_store, title='다른 매장 프로모션')

        response = self.client.get('/api/v1/owner/dashboard/', **self.auth)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['store'], {'id': self.store.id, 'name': self.store.name})
        self.assertEqual(
            {item['id'] for item in body['promotions']},
            {own_public.id, own_hidden.id},
        )
        hidden = next(item for item in body['promotions'] if item['id'] == own_hidden.id)
        self.assertEqual(hidden['status'], 'hidden')
        self.assertFalse(hidden['is_published'])

    def test_dashboard_period_status_is_independent_from_promotion_status(self):
        now = timezone.now()
        upcoming = make_promotion(
            self.store,
            title='시작 전',
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=2),
            redeem_until=now + timedelta(days=3),
        )
        ongoing = make_promotion(self.store, title='진행 중')
        sold_out = make_promotion(self.store, title='품절', total_quantity=1)
        add_coupons(sold_out, issued=1, used=0)
        ended = make_promotion(
            self.store,
            title='종료',
            starts_at=now - timedelta(days=3),
            ends_at=now - timedelta(days=2),
            redeem_until=now - timedelta(days=1),
        )

        body = self.client.get('/api/v1/owner/dashboard/', **self.auth).json()
        items = {item['id']: item for item in body['promotions']}

        self.assertEqual(items[upcoming.id]['period_status'], 'upcoming')
        self.assertEqual(items[ongoing.id]['period_status'], 'ongoing')
        self.assertEqual(items[sold_out.id]['status'], 'sold_out')
        self.assertEqual(items[sold_out.id]['period_status'], 'ongoing')
        self.assertEqual(items[ended.id]['period_status'], 'ended')

    def test_period_status_exact_boundaries(self):
        boundary = timezone.now()
        starts_now = make_promotion(
            self.store,
            starts_at=boundary,
            ends_at=boundary + timedelta(days=1),
            redeem_until=boundary + timedelta(days=2),
        )
        ends_now = make_promotion(
            self.store,
            starts_at=boundary - timedelta(days=1),
            ends_at=boundary,
            redeem_until=boundary + timedelta(days=1),
        )

        self.assertEqual(get_period_status(starts_now, boundary), 'ongoing')
        self.assertEqual(get_period_status(ends_now, boundary), 'ended')

    def test_dashboard_query_count_does_not_grow_with_promotions(self):
        self.issued_token.access_token.last_used_at = timezone.now()
        self.issued_token.access_token.save(update_fields=['last_used_at'])
        make_promotion(self.store, title='one')

        with CaptureQueriesContext(connection) as one_promotion_queries:
            response = self.client.get('/api/v1/owner/dashboard/', **self.auth)
        self.assertEqual(response.status_code, 200)

        for index in range(10):
            make_promotion(self.store, title=f'many-{index}')
        with CaptureQueriesContext(connection) as many_promotion_queries:
            response = self.client.get('/api/v1/owner/dashboard/', **self.auth)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(len(one_promotion_queries), len(many_promotion_queries))
        self.assertEqual(len(many_promotion_queries), 4)

    def test_dashboard_with_no_promotions_returns_empty_list(self):
        response = self.client.get('/api/v1/owner/dashboard/', **self.auth)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['promotions'], [])

    def test_dashboard_does_not_expose_internal_store_fields(self):
        self.store.promotion_context = 'internal memo'
        self.store.set_usage_pin('0428')
        self.store.save()
        make_promotion(self.store)

        content = response_content = str(
            self.client.get('/api/v1/owner/dashboard/', **self.auth).json()
        )

        self.assertNotIn('internal memo', content)
        self.assertNotIn(self.store.usage_pin_hash, response_content)
        self.assertNotIn('token_hash', content)


class OwnerPromotionStatsAPITests(OwnerAPITestBase):
    def test_owner_responses_prevent_caching_and_stats_hide_internal_fields(self):
        self.store.promotion_context = 'private owner memo'
        self.store.set_usage_pin('0428')
        self.store.save()
        promotion = make_promotion(self.store)

        dashboard = self.client.get('/api/v1/owner/dashboard/', **self.auth)
        stats = self.client.get(self.stats_url(promotion), **self.auth)

        for response in (dashboard, stats):
            with self.subTest(path=response.request['PATH_INFO']):
                self.assertIn('no-store', response['Cache-Control'])
                self.assertIn('private', response['Cache-Control'])

        serialized = str(stats.json())
        for secret in (
            'private owner memo',
            self.store.usage_pin_hash,
            self.issued_token.access_token.token_hash,
            'usage_pin_hash',
            'token_hash',
            'promotion_context',
        ):
            self.assertNotIn(secret, serialized)

    def test_stats_query_count_does_not_grow_with_events_and_coupons(self):
        self.issued_token.access_token.last_used_at = timezone.now()
        self.issued_token.access_token.save(update_fields=['last_used_at'])
        promotion = make_promotion(self.store)

        with CaptureQueriesContext(connection) as empty_stats_queries:
            response = self.client.get(self.stats_url(promotion), **self.auth)
        self.assertEqual(response.status_code, 200)

        add_events(promotion, views=10, instagram=5, naver=5)
        add_coupons(promotion, issued=10, used=5)
        with CaptureQueriesContext(connection) as populated_stats_queries:
            response = self.client.get(self.stats_url(promotion), **self.auth)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(len(empty_stats_queries), len(populated_stats_queries))
        self.assertEqual(len(populated_stats_queries), 6)

    def test_stats_are_isolated_per_promotion_and_avoid_join_multiplication(self):
        promotion_a = make_promotion(self.store, title='A')
        promotion_b = make_promotion(self.store, title='B')
        add_events(promotion_a, views=10, instagram=3, naver=1)
        add_events(promotion_b, views=7, instagram=2, naver=2)
        add_coupons(promotion_a, issued=5, used=2, expired=1)
        add_coupons(promotion_b, issued=3, used=1)

        stats_a = self.client.get(self.stats_url(promotion_a), **self.auth).json()['stats']
        stats_b = self.client.get(self.stats_url(promotion_b), **self.auth).json()['stats']

        self.assertEqual(stats_a, {
            'views': 10,
            'channel_clicks': 4,
            'channel_clicks_by_channel': {'instagram': 3, 'naver': 1},
            'coupons_issued': 5,
            'coupons_used': 2,
            'usage_rate_percent': 40.0,
        })
        self.assertEqual(stats_b['views'], 7)
        self.assertEqual(stats_b['channel_clicks'], 4)
        self.assertEqual(stats_b['coupons_issued'], 3)
        self.assertEqual(stats_b['coupons_used'], 1)

        add_events(promotion_b, views=5, instagram=1)
        unchanged = self.client.get(self.stats_url(promotion_a), **self.auth).json()['stats']
        self.assertEqual(unchanged, stats_a)

    def test_stats_include_events_regardless_of_promotion_period(self):
        now = timezone.now()
        upcoming = make_promotion(
            self.store,
            title='시작 전',
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=2),
            redeem_until=now + timedelta(days=3),
        )
        ended = make_promotion(
            self.store,
            title='종료',
            starts_at=now - timedelta(days=3),
            ends_at=now - timedelta(days=2),
            redeem_until=now - timedelta(days=1),
        )
        add_events(upcoming, views=2, naver=1)
        add_events(ended, views=3, instagram=2)

        upcoming_stats = self.client.get(self.stats_url(upcoming), **self.auth).json()['stats']
        ended_stats = self.client.get(self.stats_url(ended), **self.auth).json()['stats']

        self.assertEqual((upcoming_stats['views'], upcoming_stats['channel_clicks']), (2, 1))
        self.assertEqual((ended_stats['views'], ended_stats['channel_clicks']), (3, 2))

    def test_coupon_stats_include_used_expired_and_available_issued_coupons(self):
        promotion = make_promotion(self.store)
        add_coupons(promotion, issued=4, used=1, expired=1)

        stats = self.client.get(self.stats_url(promotion), **self.auth).json()['stats']

        self.assertEqual(stats['coupons_issued'], 4)
        self.assertEqual(stats['coupons_used'], 1)
        self.assertEqual(stats['usage_rate_percent'], 25.0)

    def test_coupon_promotion_with_no_issues_returns_zero_metrics(self):
        promotion = make_promotion(self.store)

        stats = self.client.get(self.stats_url(promotion), **self.auth).json()['stats']

        self.assertEqual(stats['coupons_issued'], 0)
        self.assertEqual(stats['coupons_used'], 0)
        self.assertEqual(stats['usage_rate_percent'], 0.0)

    def test_non_coupon_promotion_returns_null_coupon_metrics(self):
        promotion = make_non_coupon_promotion(self.store)
        add_events(promotion, views=2, instagram=1)

        response = self.client.get(self.stats_url(promotion), **self.auth)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['promotion']['store'], {'id': self.store.id, 'name': self.store.name})
        self.assertEqual(body['stats']['views'], 2)
        self.assertEqual(body['stats']['channel_clicks'], 1)
        self.assertIsNone(body['stats']['coupons_issued'])
        self.assertIsNone(body['stats']['coupons_used'])
        self.assertIsNone(body['stats']['usage_rate_percent'])

    def test_other_store_promotion_stats_return_404_even_when_hidden(self):
        other_store = make_store('다른 매장')
        other = make_promotion(other_store, is_published=False)
        add_events(other, views=5, instagram=2)
        add_coupons(other, issued=2, used=1)

        response = self.client.get(self.stats_url(other), **self.auth)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error']['code'], 'NOT_FOUND')

    def test_owner_can_view_hidden_promotion_stats_for_own_store(self):
        hidden = make_promotion(self.store, is_published=False)
        add_events(hidden, views=1)

        response = self.client.get(self.stats_url(hidden), **self.auth)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['promotion']['is_published'])
        self.assertEqual(response.json()['stats']['views'], 1)
