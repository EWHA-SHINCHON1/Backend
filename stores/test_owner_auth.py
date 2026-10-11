import hashlib
import re
import threading
from datetime import timedelta
from unittest import mock, skipUnless

from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import DatabaseError, connection
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import include, path, reverse
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework.views import APIView

from promotions.models import Promotion
from stores.authentication import OwnerTokenAuthentication
from stores.models import Store, StoreAccessToken
from stores.permissions import IsOwnerStore
from stores.services import (
    InvalidStoreAccessToken,
    StoreAccessTokenStateChanged,
    generate_raw_token,
    hash_raw_token,
    issue_store_access_token,
    revoke_store_access_token,
    validate_store_access_token,
)


User = get_user_model()


class OwnerProbeView(APIView):
    authentication_classes = [OwnerTokenAuthentication]
    permission_classes = [IsOwnerStore]

    def get(self, request):
        return Response({'store_id': request.user.store.id, 'token_id': request.auth.id})


class OwnerPromotionProbeView(APIView):
    authentication_classes = [OwnerTokenAuthentication]
    permission_classes = [IsOwnerStore]

    def get(self, request, promotion_id):
        promotion = Promotion.objects.filter(
            store=request.user.store,
            pk=promotion_id,
        ).first()
        if promotion is None:
            from rest_framework.exceptions import NotFound

            raise NotFound
        return Response({'promotion_id': promotion.id})


urlpatterns = [
    path('test/owner/', OwnerProbeView.as_view()),
    path('test/owner/promotions/<int:promotion_id>/', OwnerPromotionProbeView.as_view()),
    path('', include('config.urls')),
]


def make_store(name='매장', **kwargs):
    data = {
        'name': name,
        'category': Store.Category.BAKERY_CAFE,
        'address': '주소',
    }
    data.update(kwargs)
    return Store.objects.create(**data)


def make_promotion(store):
    now = timezone.now()
    return Promotion.objects.create(
        store=store,
        title='프로모션',
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=1),
        redeem_until=now + timedelta(days=2),
        total_quantity=10,
        is_published=True,
    )


class StoreAccessTokenServiceTests(TestCase):
    def setUp(self):
        self.store = make_store()
        self.now = timezone.now().replace(microsecond=0)

    def test_generated_token_is_url_safe_and_high_entropy(self):
        raw_token = generate_raw_token()

        self.assertRegex(raw_token, r'^[A-Za-z0-9_-]{43}$')
        self.assertGreaterEqual(len(raw_token), 43)

    def test_issue_stores_only_sha256_hash_and_sets_default_expiry(self):
        issued = issue_store_access_token(store=self.store, now=self.now)
        access_token = issued.access_token

        self.assertEqual(access_token.token_hash, hashlib.sha256(issued.raw_token.encode()).hexdigest())
        self.assertNotEqual(access_token.token_hash, issued.raw_token)
        self.assertEqual(access_token.expires_at, self.now + timedelta(days=60))
        self.assertNotIn(issued.raw_token, str(access_token.__dict__))

    def test_issue_deactivates_all_existing_active_tokens(self):
        old_tokens = [
            StoreAccessToken.objects.create(store=self.store, token_hash=char * 64)
            for char in ('a', 'b')
        ]

        issued = issue_store_access_token(store=self.store, now=self.now)

        for token in old_tokens:
            token.refresh_from_db()
            self.assertFalse(token.is_active)
        self.assertTrue(issued.access_token.is_active)
        self.assertEqual(StoreAccessToken.objects.filter(store=self.store, is_active=True).count(), 1)

    def test_hash_collision_is_retried(self):
        duplicate_raw = 'a' * 43
        StoreAccessToken.objects.create(
            store=make_store('다른 매장'),
            token_hash=hash_raw_token(duplicate_raw),
        )
        unique_raw = 'b' * 43

        with mock.patch(
            'stores.services.generate_raw_token',
            side_effect=[duplicate_raw, unique_raw],
        ):
            issued = issue_store_access_token(store=self.store, now=self.now)

        self.assertEqual(issued.raw_token, unique_raw)
        self.assertEqual(issued.access_token.token_hash, hash_raw_token(unique_raw))

    def test_valid_token_returns_token_and_store(self):
        issued = issue_store_access_token(store=self.store, now=self.now)

        validated = validate_store_access_token(issued.raw_token, now=self.now)

        self.assertEqual(validated.pk, issued.access_token.pk)
        self.assertEqual(validated.store, self.store)

    def test_unknown_inactive_expired_and_inactive_store_are_rejected(self):
        with self.assertRaises(InvalidStoreAccessToken):
            validate_store_access_token('x' * 43, now=self.now)

        issued = issue_store_access_token(store=self.store, now=self.now)
        issued.access_token.is_active = False
        issued.access_token.save(update_fields=['is_active'])
        with self.assertRaises(InvalidStoreAccessToken):
            validate_store_access_token(issued.raw_token, now=self.now)

        expired = issue_store_access_token(store=self.store, now=self.now)
        with self.assertRaises(InvalidStoreAccessToken):
            validate_store_access_token(expired.raw_token, now=expired.access_token.expires_at)

        active = issue_store_access_token(store=self.store, now=self.now)
        self.store.is_active = False
        self.store.save(update_fields=['is_active'])
        with self.assertRaises(InvalidStoreAccessToken):
            validate_store_access_token(active.raw_token, now=self.now)

    def test_legacy_token_without_expiry_is_valid(self):
        raw_token = 'l' * 43
        access_token = StoreAccessToken.objects.create(
            store=self.store,
            token_hash=hash_raw_token(raw_token),
            expires_at=None,
        )

        self.assertEqual(validate_store_access_token(raw_token, now=self.now).pk, access_token.pk)

    def test_last_used_at_updates_at_most_once_per_five_minutes(self):
        issued = issue_store_access_token(store=self.store, now=self.now)

        validate_store_access_token(issued.raw_token, now=self.now)
        issued.access_token.refresh_from_db()
        self.assertEqual(issued.access_token.last_used_at, self.now)

        validate_store_access_token(issued.raw_token, now=self.now + timedelta(minutes=4, seconds=59))
        issued.access_token.refresh_from_db()
        self.assertEqual(issued.access_token.last_used_at, self.now)

        later = self.now + timedelta(minutes=5)
        validate_store_access_token(issued.raw_token, now=later)
        issued.access_token.refresh_from_db()
        self.assertEqual(issued.access_token.last_used_at, later)

    def test_rotation_and_revocation(self):
        first = issue_store_access_token(store=self.store, now=self.now)
        second = issue_store_access_token(store=self.store, now=self.now + timedelta(days=1))

        with self.assertRaises(InvalidStoreAccessToken):
            validate_store_access_token(first.raw_token, now=self.now + timedelta(days=1))
        self.assertEqual(validate_store_access_token(second.raw_token).pk, second.access_token.pk)

        self.assertTrue(revoke_store_access_token(second.access_token))
        self.assertFalse(revoke_store_access_token(second.access_token))
        with self.assertRaises(InvalidStoreAccessToken):
            validate_store_access_token(second.raw_token)

    def test_tokens_are_isolated_by_store(self):
        other_store = make_store('다른 매장')
        first = issue_store_access_token(store=self.store, now=self.now)
        second = issue_store_access_token(store=other_store, now=self.now)

        self.assertEqual(validate_store_access_token(first.raw_token).store, self.store)
        self.assertEqual(validate_store_access_token(second.raw_token).store, other_store)


@override_settings(ROOT_URLCONF=__name__)
class OwnerAuthenticationTests(TestCase):
    def setUp(self):
        self.store = make_store()
        self.issued = issue_store_access_token(store=self.store)
        self.client = APIClient()

    def auth(self, raw_token=None):
        token = raw_token or self.issued.raw_token
        return {'HTTP_AUTHORIZATION': f'Owner {token}'}

    def test_valid_owner_header_sets_principal_store_and_auth(self):
        response = self.client.get('/test/owner/', **self.auth())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'store_id': self.store.id,
            'token_id': self.issued.access_token.id,
        })

    def test_missing_invalid_and_malformed_headers_return_same_401(self):
        responses = [
            self.client.get('/test/owner/'),
            self.client.get('/test/owner/', HTTP_AUTHORIZATION='Bearer anything'),
            self.client.get('/test/owner/', HTTP_AUTHORIZATION='Owner'),
            self.client.get('/test/owner/', HTTP_AUTHORIZATION='Owner not-valid!'),
            self.client.get('/test/owner/', **self.auth('x' * 43)),
        ]

        for response in responses:
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response['WWW-Authenticate'], 'Owner')
            self.assertNotIn('not-valid', str(response.json()))
            self.assertNotIn('x' * 43, str(response.json()))

    def test_other_store_promotion_is_hidden_with_404(self):
        own = make_promotion(self.store)
        other = make_promotion(make_store('다른 매장'))

        self.assertEqual(
            self.client.get(f'/test/owner/promotions/{own.id}/', **self.auth()).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(f'/test/owner/promotions/{other.id}/', **self.auth()).status_code,
            404,
        )

    def test_consumer_session_is_not_owner_authentication(self):
        user = User.objects.create_user(username='consumer')
        self.client.force_login(user)

        self.assertEqual(self.client.get('/test/owner/').status_code, 401)

    def test_owner_token_is_not_consumer_coupon_authentication(self):
        promotion = make_promotion(self.store)

        response = self.client.post(
            f'/api/v1/promotions/{promotion.id}/coupons/',
            {},
            format='json',
            **self.auth(),
        )

        self.assertEqual(response.status_code, 401)


@override_settings(FRONTEND_BASE_URL='https://frontend.example')
class StoreAccessTokenAdminTests(TestCase):
    def setUp(self):
        self.store = make_store()
        self.superuser = User.objects.create_superuser(
            username='admin',
            password='password',
            email='admin@example.com',
        )
        self.client.force_login(self.superuser)
        self.store_changelist = reverse('admin:stores_store_changelist')
        self.token_changelist = reverse('admin:stores_storeaccesstoken_changelist')

    def confirmation(self, *, url, action, selected_id):
        response = self.client.post(url, {
            'action': action,
            ACTION_CHECKBOX_NAME: [selected_id],
        })
        self.assertEqual(response.status_code, 200)
        return response

    def confirm_issue(self, *, url, action, selected_id, nonce):
        return self.client.post(url, {
            'action': action,
            ACTION_CHECKBOX_NAME: [selected_id],
            'confirm_owner_token_issue': 'yes',
            'issue_nonce': nonce,
        })

    def test_store_admin_issues_one_time_secret_link_without_persisting_raw(self):
        confirmation = self.confirmation(
            url=self.store_changelist,
            action='issue_owner_link',
            selected_id=self.store.id,
        )
        nonce = confirmation.context['issue_nonce']

        response = self.confirm_issue(
            url=self.store_changelist,
            action='issue_owner_link',
            selected_id=self.store.id,
            nonce=nonce,
        )

        self.assertEqual(response.status_code, 200)
        secret_link = response.context['secret_link']
        self.assertTrue(secret_link.startswith('https://frontend.example/owner#token='))
        raw_token = secret_link.split('#token=', 1)[1]
        access_token = StoreAccessToken.objects.get(store=self.store)
        self.assertEqual(access_token.token_hash, hash_raw_token(raw_token))
        self.assertNotIn(raw_token, str(access_token.__dict__))
        self.assertIn('no-store', response['Cache-Control'])
        self.assertNotIn(raw_token, str(dict(self.client.session)))
        self.assertFalse(LogEntry.objects.filter(object_repr__contains=raw_token).exists())

        repeated = self.confirm_issue(
            url=self.store_changelist,
            action='issue_owner_link',
            selected_id=self.store.id,
            nonce=nonce,
        )
        self.assertTrue(repeated.context['already_processed'])
        self.assertNotContains(repeated, raw_token)
        self.assertEqual(StoreAccessToken.objects.filter(store=self.store).count(), 1)

    def test_token_admin_rotates_and_revokes(self):
        first = issue_store_access_token(store=self.store)
        confirmation = self.confirmation(
            url=self.token_changelist,
            action='rotate_owner_link',
            selected_id=first.access_token.id,
        )
        response = self.confirm_issue(
            url=self.token_changelist,
            action='rotate_owner_link',
            selected_id=first.access_token.id,
            nonce=confirmation.context['issue_nonce'],
        )
        self.assertEqual(response.status_code, 200)
        first.access_token.refresh_from_db()
        self.assertFalse(first.access_token.is_active)
        second = StoreAccessToken.objects.get(store=self.store, is_active=True)

        response = self.client.post(self.token_changelist, {
            'action': 'revoke_tokens',
            ACTION_CHECKBOX_NAME: [second.id],
        })
        self.assertEqual(response.status_code, 302)
        second.refresh_from_db()
        self.assertFalse(second.is_active)

    def test_token_hash_is_not_rendered_and_direct_add_is_disabled(self):
        issued = issue_store_access_token(store=self.store)

        response = self.client.get(self.token_changelist)

        self.assertNotContains(response, issued.access_token.token_hash)
        self.assertEqual(
            self.client.get(reverse('admin:stores_storeaccesstoken_add')).status_code,
            403,
        )

    def test_staff_without_model_permissions_cannot_manage_tokens(self):
        staff = User.objects.create_user(username='staff', password='password', is_staff=True)
        self.client.force_login(staff)

        self.assertEqual(self.client.get(self.token_changelist).status_code, 403)

    def test_issue_action_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.superuser)

        response = client.post(self.store_changelist, {
            'action': 'issue_owner_link',
            ACTION_CHECKBOX_NAME: [self.store.id],
        })

        self.assertEqual(response.status_code, 403)
        self.assertEqual(StoreAccessToken.objects.count(), 0)

    def test_issue_failure_does_not_consume_nonce_and_can_be_retried(self):
        confirmation = self.confirmation(
            url=self.store_changelist,
            action='issue_owner_link',
            selected_id=self.store.id,
        )
        nonce = confirmation.context['issue_nonce']

        with mock.patch('stores.admin.issue_store_access_token', side_effect=DatabaseError):
            failed = self.confirm_issue(
                url=self.store_changelist,
                action='issue_owner_link',
                selected_id=self.store.id,
                nonce=nonce,
            )

        self.assertEqual(failed.status_code, 200)
        self.assertTrue(failed.context['issue_error'])
        self.assertEqual(StoreAccessToken.objects.count(), 0)
        self.assertNotIn(
            signing.loads(nonce, salt='stores.owner-token-issue')['nonce'],
            self.client.session.get('stores.owner-token-used-nonces', []),
        )

        retried = self.confirm_issue(
            url=self.store_changelist,
            action='issue_owner_link',
            selected_id=self.store.id,
            nonce=nonce,
        )
        self.assertEqual(retried.status_code, 200)
        self.assertIn('secret_link', retried.context)
        self.assertEqual(StoreAccessToken.objects.filter(store=self.store, is_active=True).count(), 1)

    def test_invalid_frontend_urls_do_not_change_existing_token(self):
        existing = issue_store_access_token(store=self.store).access_token
        confirmation = self.confirmation(
            url=self.store_changelist,
            action='issue_owner_link',
            selected_id=self.store.id,
        )
        nonce = confirmation.context['issue_nonce']

        invalid_settings = (
            {'FRONTEND_BASE_URL': ''},
            {'FRONTEND_BASE_URL': '/frontend', 'DEBUG': True},
            {'FRONTEND_BASE_URL': 'http://frontend.example', 'IS_PRODUCTION': True},
            {'FRONTEND_BASE_URL': 'https://user:secret@frontend.example'},
            {'FRONTEND_BASE_URL': 'https://frontend.example?source=admin'},
            {'FRONTEND_BASE_URL': 'https://frontend.example#fragment'},
        )
        for setting_values in invalid_settings:
            with self.subTest(setting_values=setting_values), override_settings(**setting_values):
                response = self.confirm_issue(
                    url=self.store_changelist,
                    action='issue_owner_link',
                    selected_id=self.store.id,
                    nonce=nonce,
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['issue_error'])
                if setting_values['FRONTEND_BASE_URL']:
                    self.assertNotContains(response, setting_values['FRONTEND_BASE_URL'])
                existing.refresh_from_db()
                self.assertTrue(existing.is_active)
                self.assertEqual(StoreAccessToken.objects.filter(store=self.store).count(), 1)

    def test_owner_link_issue_does_not_change_store_pin(self):
        self.store.set_usage_pin('0428')
        self.store.save()
        original_hash = self.store.usage_pin_hash
        confirmation = self.confirmation(
            url=self.store_changelist,
            action='issue_owner_link',
            selected_id=self.store.id,
        )

        self.confirm_issue(
            url=self.store_changelist,
            action='issue_owner_link',
            selected_id=self.store.id,
            nonce=confirmation.context['issue_nonce'],
        )

        self.store.refresh_from_db()
        self.assertEqual(self.store.usage_pin_hash, original_hash)
        self.assertTrue(self.store.check_usage_pin('0428'))


class ConcurrentStoreAccessTokenIssueTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.store = make_store()

    def test_concurrent_issue_leaves_only_one_active_token(self):
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def issue():
            try:
                barrier.wait(timeout=10)
                results.append(issue_store_access_token(store=self.store))
            except Exception as exc:
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=issue) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertFalse(errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(
            StoreAccessToken.objects.filter(store=self.store, is_active=True).count(),
            1,
        )
        valid_count = 0
        for issued in results:
            try:
                validate_store_access_token(issued.raw_token)
            except InvalidStoreAccessToken:
                continue
            valid_count += 1
        self.assertEqual(valid_count, 1)

    def test_concurrent_conditional_issue_allows_only_one_success(self):
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def issue():
            try:
                barrier.wait(timeout=10)
                results.append(issue_store_access_token(
                    store=self.store,
                    expected_active_token_ids=[],
                ))
            except StoreAccessTokenStateChanged:
                errors.append(StoreAccessTokenStateChanged)
            finally:
                connection.close()

        threads = [threading.Thread(target=issue) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertEqual(len(results), 1)
        self.assertEqual(errors, [StoreAccessTokenStateChanged])
        self.assertEqual(StoreAccessToken.objects.filter(store=self.store, is_active=True).count(), 1)


@override_settings(FRONTEND_BASE_URL='https://frontend.example')
@skipUnless(connection.vendor == 'postgresql', 'Row-lock concurrency requires PostgreSQL.')
class ConcurrentStoreAccessTokenAdminTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.store = make_store()
        self.admins = [
            User.objects.create_superuser(
                username=f'admin{index}',
                password='password',
                email=f'admin{index}@example.com',
            )
            for index in range(2)
        ]
        self.clients = [Client(), Client()]
        for client, admin_user in zip(self.clients, self.admins):
            client.force_login(admin_user)
        self.store_changelist = reverse('admin:stores_store_changelist')

    def test_same_confirmation_nonce_concurrently_issues_only_one_valid_link(self):
        confirmation = self.clients[0].post(self.store_changelist, {
            'action': 'issue_owner_link',
            ACTION_CHECKBOX_NAME: [self.store.id],
        })
        nonce = confirmation.context['issue_nonce']
        barrier = threading.Barrier(2)
        responses = []
        errors = []

        def confirm(client):
            try:
                barrier.wait(timeout=10)
                responses.append(client.post(self.store_changelist, {
                    'action': 'issue_owner_link',
                    ACTION_CHECKBOX_NAME: [self.store.id],
                    'confirm_owner_token_issue': 'yes',
                    'issue_nonce': nonce,
                }))
            except Exception as exc:
                errors.append(exc)
            finally:
                connection.close()

        threads = [
            threading.Thread(target=confirm, args=(client,))
            for client in self.clients
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertFalse(errors)
        self.assertEqual(len(responses), 2)
        # Django's template-rendered signal is process-global, so response.context can
        # be cross-contaminated by concurrent test clients. Response bodies are local.
        successful = [response for response in responses if b'id="owner-secret-link"' in response.content]
        rejected = [response for response in responses if b'id="owner-secret-link"' not in response.content]
        self.assertEqual(len(successful), 1)
        self.assertEqual(len(rejected), 1)
        self.assertEqual(StoreAccessToken.objects.filter(store=self.store, is_active=True).count(), 1)

        token_match = re.search(rb'#token=([A-Za-z0-9_-]{43})', successful[0].content)
        self.assertIsNotNone(token_match)
        raw_token = token_match.group(1).decode('ascii')
        validated = validate_store_access_token(raw_token)
        self.assertEqual(validated.store_id, self.store.id)
