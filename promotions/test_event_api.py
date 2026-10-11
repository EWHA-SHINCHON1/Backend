import json
import threading
import uuid
from datetime import timedelta
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from promotions.models import Promotion, PromotionEvent
from stores.models import Store


User = get_user_model()


def make_store(name='이벤트 매장', **kwargs):
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
        'title': '이벤트 프로모션',
        'starts_at': now - timedelta(days=1),
        'ends_at': now + timedelta(days=1),
        'requires_coupon': False,
        'redeem_until': None,
        'total_quantity': None,
        'is_published': True,
    }
    values.update(kwargs)
    return Promotion.objects.create(**values)


def event_url(promotion):
    return f'/api/v1/promotions/{promotion.id}/events/'


class PromotionEventAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.store = make_store()
        self.promotion = make_promotion(self.store)

    def post(self, data, promotion=None, **headers):
        return self.client.post(
            event_url(promotion or self.promotion),
            data,
            format='json',
            **headers,
        )

    def test_anonymous_view_event_is_recorded(self):
        event_id = uuid.uuid4()

        response = self.post({'event_id': str(event_id), 'event_type': 'view'})

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()['created'])
        self.assertEqual(response.json()['event']['event_id'], str(event_id))
        self.assertEqual(response.json()['event']['event_type'], 'view')
        self.assertEqual(response.json()['event']['channel'], '')
        event = PromotionEvent.objects.get(pk=event_id)
        self.assertEqual(event.event_type, PromotionEvent.EventType.VIEW)
        self.assertEqual(event.promotion, self.promotion)

    def test_anonymous_channel_click_events_are_recorded(self):
        for channel in ('instagram', 'naver'):
            with self.subTest(channel=channel):
                response = self.post({
                    'event_id': str(uuid.uuid4()),
                    'event_type': 'channel_click',
                    'channel': channel,
                })
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.json()['event']['event_type'], 'channel_click')
                self.assertEqual(response.json()['event']['channel'], channel)

        self.assertEqual(
            PromotionEvent.objects.filter(event_type=PromotionEvent.EventType.CHANNEL_CLICK).count(),
            2,
        )

    def test_invalid_uuid_type_and_channel_combinations_return_400(self):
        invalid_payloads = (
            {'event_id': 'not-a-uuid', 'event_type': 'view'},
            {'event_id': str(uuid.uuid4()), 'event_type': 'VIEW'},
            {'event_id': str(uuid.uuid4()), 'event_type': 'view', 'channel': 'instagram'},
            {'event_id': str(uuid.uuid4()), 'event_type': 'channel_click'},
            {
                'event_id': str(uuid.uuid4()),
                'event_type': 'channel_click',
                'channel': 'kakao',
            },
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                response = self.post(payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['error']['code'], 'VALIDATION_ERROR')

        self.assertEqual(PromotionEvent.objects.count(), 0)

    def test_identical_event_retry_is_idempotent(self):
        event_id = uuid.uuid4()
        payload = {'event_id': str(event_id), 'event_type': 'view', 'channel': ''}

        first = self.post(payload)
        second = self.post(payload)

        self.assertEqual(first.status_code, 201)
        self.assertTrue(first.json()['created'])
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()['created'])
        self.assertEqual(first.json()['event'], second.json()['event'])
        self.assertEqual(PromotionEvent.objects.filter(pk=event_id).count(), 1)

    def test_same_event_id_with_different_payload_returns_409(self):
        event_id = uuid.uuid4()
        self.post({'event_id': str(event_id), 'event_type': 'view'})

        response = self.post({
            'event_id': str(event_id),
            'event_type': 'channel_click',
            'channel': 'instagram',
        })

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'EVENT_ID_CONFLICT')
        self.assertEqual(PromotionEvent.objects.filter(pk=event_id).count(), 1)

    def test_same_event_id_for_another_promotion_returns_409(self):
        event_id = uuid.uuid4()
        other = make_promotion(self.store, title='다른 프로모션')
        self.post({'event_id': str(event_id), 'event_type': 'view'})

        response = self.post(
            {'event_id': str(event_id), 'event_type': 'view'},
            promotion=other,
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'EVENT_ID_CONFLICT')

    def test_unpublished_inactive_store_and_unknown_promotions_return_404(self):
        unpublished = make_promotion(self.store, title='비공개', is_published=False)
        inactive_store = make_store(name='비활성', is_active=False)
        inactive = make_promotion(inactive_store)

        for url in (
            event_url(unpublished),
            event_url(inactive),
            '/api/v1/promotions/999999/events/',
        ):
            with self.subTest(url=url):
                response = self.client.post(
                    url,
                    {'event_id': str(uuid.uuid4()), 'event_type': 'view'},
                    format='json',
                )
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()['error']['code'], 'NOT_FOUND')

    def test_upcoming_and_ended_public_promotions_allow_events(self):
        now = timezone.now()
        upcoming = make_promotion(
            self.store,
            title='시작 전',
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=2),
        )
        ended = make_promotion(
            self.store,
            title='종료',
            starts_at=now - timedelta(days=2),
            ends_at=now - timedelta(days=1),
        )

        for promotion in (upcoming, ended):
            with self.subTest(promotion=promotion.title):
                response = self.post(
                    {'event_id': str(uuid.uuid4()), 'event_type': 'view'},
                    promotion=promotion,
                )
                self.assertEqual(response.status_code, 201)

    def test_session_and_owner_headers_do_not_enable_authentication_or_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(User.objects.create_user(username='consumer'))

        response = csrf_client.post(
            event_url(self.promotion),
            data=json.dumps({'event_id': str(uuid.uuid4()), 'event_type': 'view'}),
            content_type='application/json',
            HTTP_AUTHORIZATION='Owner malformed-token',
        )

        self.assertEqual(response.status_code, 201)

    def test_ip_throttle_allows_sixty_requests_then_returns_429(self):
        for _index in range(60):
            response = self.post(
                {'event_id': str(uuid.uuid4()), 'event_type': 'view'},
                REMOTE_ADDR='198.51.100.10',
            )
            self.assertEqual(response.status_code, 201)

        blocked = self.post(
            {'event_id': str(uuid.uuid4()), 'event_type': 'view'},
            REMOTE_ADDR='198.51.100.10',
        )
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()['error']['code'], 'TOO_MANY_REQUESTS')
        self.assertIn('Retry-After', blocked)
        self.assertEqual(PromotionEvent.objects.count(), 60)


@skipUnless(connection.vendor == 'postgresql', 'Concurrent idempotency requires PostgreSQL.')
class ConcurrentPromotionEventAPITests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.store = make_store()
        self.promotion = make_promotion(self.store)

    def test_concurrent_identical_requests_create_one_event(self):
        event_id = uuid.uuid4()
        barrier = threading.Barrier(2)
        responses = []
        errors = []

        def post_event():
            try:
                client = APIClient()
                barrier.wait(timeout=10)
                responses.append(client.post(
                    event_url(self.promotion),
                    {'event_id': str(event_id), 'event_type': 'view'},
                    format='json',
                    REMOTE_ADDR='203.0.113.10',
                ))
            except Exception as exc:
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=post_event) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertFalse(errors)
        self.assertEqual(len(responses), 2)
        self.assertEqual(sorted(response.status_code for response in responses), [200, 201])
        self.assertEqual(PromotionEvent.objects.filter(pk=event_id).count(), 1)
        self.assertEqual(
            [response.json()['created'] for response in sorted(responses, key=lambda item: item.status_code)],
            [False, True],
        )
