import threading
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from promotions.models import Promotion, SavedPromotion
from stores.models import Store


User = get_user_model()
ME_URL = '/api/v1/auth/me/'
SAVED_LIST_URL = '/api/v1/me/saved-promotions/'


def make_store(**kwargs):
    data = {
        'name': '저장 테스트 매장',
        'category': Store.Category.BAKERY_CAFE,
        'address': '신촌',
    }
    data.update(kwargs)
    return Store.objects.create(**data)


def make_promotion(store=None, **kwargs):
    now = timezone.now().replace(microsecond=0)
    data = {
        'store': store or make_store(),
        'title': '저장할 프로모션',
        'description': '프로모션 설명',
        'starts_at': now - timedelta(days=1),
        'ends_at': now + timedelta(days=1),
        'redeem_until': now + timedelta(days=2),
        'total_quantity': 10,
        'is_published': True,
    }
    data.update(kwargs)
    return Promotion.objects.create(**data)


def bookmark_url(promotion):
    return f'/api/v1/promotions/{promotion.pk}/bookmark/'


class SavedPromotionModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='saved-user')
        self.promotion = make_promotion()

    def test_user_and_promotion_pair_is_unique(self):
        SavedPromotion.objects.create(user=self.user, promotion=self.promotion)

        with self.assertRaises(IntegrityError):
            SavedPromotion.objects.create(user=self.user, promotion=self.promotion)

    def test_different_users_can_save_the_same_promotion(self):
        other = User.objects.create_user(username='other-user')

        SavedPromotion.objects.create(user=self.user, promotion=self.promotion)
        SavedPromotion.objects.create(user=other, promotion=self.promotion)

        self.assertEqual(SavedPromotion.objects.filter(promotion=self.promotion).count(), 2)

    def test_foreign_key_deletion_cascades(self):
        saved = SavedPromotion.objects.create(user=self.user, promotion=self.promotion)
        saved_id = saved.id

        self.promotion.delete()

        self.assertFalse(SavedPromotion.objects.filter(pk=saved_id).exists())


class BookmarkAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='bookmark-user')
        self.other = User.objects.create_user(username='bookmark-other')
        self.promotion = make_promotion()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_put_creates_and_repeats_idempotently(self):
        first = self.client.put(bookmark_url(self.promotion), {}, format='json')
        second = self.client.put(bookmark_url(self.promotion), {}, format='json')

        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json(), {'saved': True, 'created': True})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json(), {'saved': True, 'created': False})
        self.assertEqual(
            SavedPromotion.objects.filter(user=self.user, promotion=self.promotion).count(),
            1,
        )

    def test_delete_is_idempotent_and_only_deletes_own_record(self):
        SavedPromotion.objects.create(user=self.user, promotion=self.promotion)
        other_saved = SavedPromotion.objects.create(user=self.other, promotion=self.promotion)

        first = self.client.delete(bookmark_url(self.promotion))
        second = self.client.delete(bookmark_url(self.promotion))

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), {'saved': False})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json(), {'saved': False})
        self.assertTrue(SavedPromotion.objects.filter(pk=other_saved.pk).exists())

    def test_anonymous_access_is_rejected(self):
        client = APIClient()

        for method in ('put', 'delete'):
            with self.subTest(method=method):
                response = getattr(client, method)(bookmark_url(self.promotion), {}, format='json')
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json()['error']['code'], 'AUTHENTICATION_REQUIRED')

        response = client.get(SAVED_LIST_URL)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error']['code'], 'AUTHENTICATION_REQUIRED')

        owner_header = APIClient(HTTP_AUTHORIZATION='Owner not-a-consumer-session')
        response = owner_header.get(SAVED_LIST_URL)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['error']['code'], 'AUTHENTICATION_REQUIRED')

    def test_put_requires_csrf_for_a_session_user(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        client.get(ME_URL)
        token = client.cookies['csrftoken'].value

        rejected = client.put(bookmark_url(self.promotion), {}, format='json')
        accepted = client.put(
            bookmark_url(self.promotion),
            {},
            format='json',
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.json()['error']['code'], 'CSRF_FAILED')
        self.assertEqual(accepted.status_code, 201)

    def test_put_rejects_unpublished_inactive_store_and_unknown_promotions(self):
        unpublished = make_promotion(is_published=False)
        inactive = make_promotion(store=make_store(is_active=False))

        for promotion_id in (unpublished.pk, inactive.pk, 999999):
            with self.subTest(promotion_id=promotion_id):
                response = self.client.put(
                    f'/api/v1/promotions/{promotion_id}/bookmark/',
                    {},
                    format='json',
                )
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()['error']['code'], 'NOT_FOUND')

    def test_delete_remains_available_after_promotion_becomes_unpublished(self):
        SavedPromotion.objects.create(user=self.user, promotion=self.promotion)
        self.promotion.is_published = False
        self.promotion.save(update_fields=['is_published'])

        response = self.client.delete(bookmark_url(self.promotion))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SavedPromotion.objects.filter(user=self.user).exists())


class SavedPromotionListAPITests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='list-user')
        self.other = User.objects.create_user(username='list-other')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_returns_only_own_public_active_store_promotions_newest_first(self):
        first = make_promotion(title='먼저 저장')
        second = make_promotion(title='나중 저장')
        hidden = make_promotion(title='비공개', is_published=False)
        inactive = make_promotion(title='비활성 매장', store=make_store(is_active=False))
        SavedPromotion.objects.create(user=self.user, promotion=first)
        SavedPromotion.objects.create(user=self.user, promotion=second)
        SavedPromotion.objects.create(user=self.user, promotion=hidden)
        SavedPromotion.objects.create(user=self.user, promotion=inactive)
        SavedPromotion.objects.create(user=self.other, promotion=make_promotion(title='타인 저장'))

        response = self.client.get(SAVED_LIST_URL)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['count'], 2)
        self.assertEqual([item['id'] for item in body['results']], [second.id, first.id])
        self.assertTrue(all(item['is_saved'] for item in body['results']))
        self.assertEqual(SavedPromotion.objects.filter(user=self.user).count(), 4)

    def test_empty_and_paginated_list(self):
        empty = self.client.get(SAVED_LIST_URL).json()
        self.assertEqual(empty, {'count': 0, 'next': None, 'previous': None, 'results': []})

        for index in range(21):
            SavedPromotion.objects.create(
                user=self.user,
                promotion=make_promotion(title=f'프로모션 {index}'),
            )
        first = self.client.get(SAVED_LIST_URL).json()
        second = self.client.get(SAVED_LIST_URL, {'page': 2}).json()
        self.assertEqual(first['count'], 21)
        self.assertEqual(len(first['results']), 20)
        self.assertEqual(len(second['results']), 1)

    def test_list_query_count_does_not_grow(self):
        SavedPromotion.objects.create(user=self.user, promotion=make_promotion())
        with self.assertNumQueries(2):
            one_response = self.client.get(SAVED_LIST_URL)

        for index in range(9):
            SavedPromotion.objects.create(
                user=self.user,
                promotion=make_promotion(title=f'추가 {index}'),
            )
        with self.assertNumQueries(2):
            many_response = self.client.get(SAVED_LIST_URL)

        self.assertEqual(one_response.status_code, 200)
        self.assertEqual(many_response.status_code, 200)

    def test_response_is_private_and_not_stored(self):
        response = self.client.get(SAVED_LIST_URL)

        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('Cookie', response['Vary'])


class PublicPromotionSavedStateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='public-user')
        self.other = User.objects.create_user(username='public-other')
        self.promotions = [make_promotion(title=f'공개 {index}') for index in range(10)]
        SavedPromotion.objects.create(user=self.user, promotion=self.promotions[0])
        SavedPromotion.objects.create(user=self.other, promotion=self.promotions[1])

    def test_anonymous_list_and_detail_are_false(self):
        client = APIClient()

        listing = client.get('/api/v1/promotions/')
        detail = client.get(f'/api/v1/promotions/{self.promotions[0].pk}/')

        self.assertTrue(all(not item['is_saved'] for item in listing.json()['results']))
        self.assertFalse(detail.json()['is_saved'])

    def test_authenticated_list_and_detail_expose_only_own_state(self):
        client = APIClient()
        # 실제 소비자 인증과 같은 Django 세션을 통해 사용자별 상태를 계산합니다.
        client.force_login(self.user)

        listing = client.get('/api/v1/promotions/').json()['results']
        states = {item['id']: item['is_saved'] for item in listing}
        detail = client.get(f'/api/v1/promotions/{self.promotions[0].pk}/').json()

        self.assertTrue(states[self.promotions[0].pk])
        self.assertFalse(states[self.promotions[1].pk])
        self.assertTrue(detail['is_saved'])

    def test_personalized_public_responses_are_private_and_vary_on_cookie(self):
        client = APIClient()
        client.force_authenticate(self.user)

        for url in ('/api/v1/promotions/', f'/api/v1/promotions/{self.promotions[0].pk}/'):
            with self.subTest(url=url):
                response = client.get(url)
                self.assertIn('private', response['Cache-Control'])
                self.assertIn('no-store', response['Cache-Control'])
                self.assertIn('Cookie', response['Vary'])

    def test_list_query_count_does_not_grow_with_saved_state(self):
        client = APIClient()
        client.force_authenticate(self.user)

        with CaptureQueriesContext(connection) as queries:
            response = client.get('/api/v1/promotions/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(queries), 2)
        self.assertNotIn('SELECT COUNT(*) FROM "saved_promotions"', ' '.join(q['sql'] for q in queries))


@skipUnlessDBFeature('has_select_for_update')
class BookmarkConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_user(username='concurrent-user')
        self.promotion = make_promotion()

    def test_concurrent_puts_create_one_saved_promotion(self):
        barrier = threading.Barrier(2)
        results = [None, None]

        def request(index):
            try:
                client = APIClient()
                client.force_authenticate(self.user)
                barrier.wait(timeout=10)
                results[index] = client.put(bookmark_url(self.promotion), {}, format='json')
            except Exception as exc:
                results[index] = exc
            finally:
                connections.close_all()

        threads = [threading.Thread(target=request, args=(index,)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertTrue(all(hasattr(result, 'status_code') for result in results), results)
        self.assertEqual(sorted(result.status_code for result in results), [200, 201])
        self.assertEqual(
            SavedPromotion.objects.filter(user=self.user, promotion=self.promotion).count(),
            1,
        )
