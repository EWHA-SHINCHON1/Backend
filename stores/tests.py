from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from stores.admin import StoreAdminForm
from stores.models import Store, StoreAccessToken, StoreMenu


User = get_user_model()


def make_store(**kwargs):
    data = {'name': '갓구움 베이커리', 'category': Store.Category.BAKERY_CAFE, 'address': '서울 서대문구'}
    data.update(kwargs)
    return Store.objects.create(**data)


class StoreTests(TestCase):
    def test_create_store_with_defaults(self):
        store = make_store()
        self.assertTrue(store.is_active)
        self.assertEqual(store.promotion_context, '')
        self.assertEqual(store.usage_pin_hash, '')
        self.assertIsNone(store.pin_updated_at)
        self.assertFalse(store.has_usage_pin)

    def test_invalid_category_fails_validation(self):
        store = Store(name='x', category='CAFE', address='y')
        with self.assertRaises(ValidationError):
            store.full_clean()


class StorePinTests(TestCase):
    def setUp(self):
        self.store = make_store()

    def test_raw_pin_is_not_stored(self):
        self.store.set_usage_pin('0428')
        self.store.save()
        self.store.refresh_from_db()
        self.assertNotIn('0428', self.store.usage_pin_hash)
        self.assertTrue(self.store.has_usage_pin)
        self.assertIsNotNone(self.store.pin_updated_at)

    def test_correct_and_wrong_pin(self):
        self.store.set_usage_pin('0428')
        self.store.save()
        self.store.refresh_from_db()
        self.assertTrue(self.store.check_usage_pin('0428'))
        self.assertFalse(self.store.check_usage_pin('0429'))
        self.assertFalse(self.store.check_usage_pin(''))
        self.assertFalse(self.store.check_usage_pin(None))

    def test_leading_zero_is_preserved(self):
        self.store.set_usage_pin('0123')
        self.assertTrue(self.store.check_usage_pin('0123'))
        self.assertFalse(self.store.check_usage_pin('123'))

    def test_non_string_pin_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.set_usage_pin(123)
        with self.assertRaises(ValueError):
            self.store.set_usage_pin('')
        self.store.set_usage_pin('0123')
        self.assertFalse(self.store.check_usage_pin(123))

    def test_store_without_pin_never_matches(self):
        self.assertFalse(self.store.check_usage_pin(''))
        self.assertFalse(self.store.check_usage_pin('0000'))

    def test_changing_pin_invalidates_old_pin(self):
        self.store.set_usage_pin('1111')
        self.store.set_usage_pin('2222')
        self.assertFalse(self.store.check_usage_pin('1111'))
        self.assertTrue(self.store.check_usage_pin('2222'))


class StoreMenuTests(TestCase):
    def setUp(self):
        self.store = make_store()

    def test_menus_ordered_by_sort_order(self):
        StoreMenu.objects.create(store=self.store, name='B', price=3000, sort_order=2)
        StoreMenu.objects.create(store=self.store, name='A', price=0, sort_order=1)
        self.assertEqual([m.name for m in self.store.menus.all()], ['A', 'B'])

    def test_negative_price_is_rejected_by_db(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            StoreMenu.objects.create(store=self.store, name='x', price=-1)

    def test_price_is_required(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            StoreMenu.objects.create(store=self.store, name='x', price=None)


class StoreAccessTokenTests(TestCase):
    def setUp(self):
        self.store = make_store()

    def test_no_raw_token_field(self):
        field_names = {f.name for f in StoreAccessToken._meta.get_fields()}
        self.assertIn('token_hash', field_names)
        self.assertNotIn('token', field_names)
        self.assertNotIn('raw_token', field_names)

    def test_token_hash_is_unique(self):
        StoreAccessToken.objects.create(store=self.store, token_hash='a' * 64)
        with self.assertRaises(IntegrityError), transaction.atomic():
            StoreAccessToken.objects.create(store=self.store, token_hash='a' * 64)

    def test_defaults(self):
        token = StoreAccessToken.objects.create(store=self.store, token_hash='b' * 64)
        self.assertTrue(token.is_active)
        self.assertIsNone(token.expires_at)
        self.assertIsNone(token.last_used_at)


class StoreDeletePolicyTests(TestCase):
    def test_deleting_store_deletes_menus_and_tokens(self):
        """삭제 정책: StoreMenu.store, StoreAccessToken.store = CASCADE"""
        store = make_store()
        StoreMenu.objects.create(store=store, name='x', price=1000)
        StoreAccessToken.objects.create(store=store, token_hash='c' * 64)
        store.delete()
        self.assertEqual(StoreMenu.objects.count(), 0)
        self.assertEqual(StoreAccessToken.objects.count(), 0)


class StoreAdminFormTests(TestCase):
    def form_data(self, **overrides):
        data = {
            'name': '관리자 등록 매장',
            'category': Store.Category.BAKERY_CAFE,
            'address': '서울 서대문구 연세로',
            'usage_pin': '0428',
            'usage_pin_confirmation': '0428',
        }
        data.update(overrides)
        return data

    def test_pin_confirmation_must_match(self):
        form = StoreAdminForm(data=self.form_data(usage_pin_confirmation='0429'))

        self.assertFalse(form.is_valid())
        self.assertIn('usage_pin_confirmation', form.errors)

    def test_pin_must_be_four_ascii_digits(self):
        for invalid_pin in ('123', '12345', '12a4', '１２３４'):
            with self.subTest(pin=invalid_pin):
                form = StoreAdminForm(
                    data=self.form_data(
                        usage_pin=invalid_pin,
                        usage_pin_confirmation=invalid_pin,
                    ),
                )
                self.assertFalse(form.is_valid())
                self.assertIn('usage_pin', form.errors)

    def test_pin_is_required_when_creating_store(self):
        form = StoreAdminForm(
            data=self.form_data(usage_pin='', usage_pin_confirmation=''),
        )

        self.assertFalse(form.is_valid())
        self.assertIn('usage_pin', form.errors)
        self.assertIn('usage_pin_confirmation', form.errors)

    def test_change_requires_both_pin_fields_or_neither(self):
        store = make_store()
        store.set_usage_pin('1111')
        store.save()

        for pin, confirmation, error_field in (
            ('2222', '', 'usage_pin_confirmation'),
            ('', '2222', 'usage_pin'),
        ):
            with self.subTest(pin=pin, confirmation=confirmation):
                form = StoreAdminForm(
                    instance=store,
                    data=self.form_data(
                        usage_pin=pin,
                        usage_pin_confirmation=confirmation,
                    ),
                )
                self.assertFalse(form.is_valid())
                self.assertIn(error_field, form.errors)


class StoreAdminIntegrationTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username='store-admin',
            password='admin-password',
            email='admin@example.com',
        )
        self.client.force_login(self.admin_user)

    def store_data(self, **overrides):
        data = {
            'name': '관리자 등록 매장',
            'category': Store.Category.BAKERY_CAFE,
            'address': '서울 서대문구 연세로',
            'business_hours': '매일 10:00~20:00',
            'image_url': '',
            'story': '매장 소개',
            'promotion_context': '외부에 공개하지 않는 운영 메모',
            'map_url': '',
            'instagram_url': '',
            'naver_url': '',
            'is_active': 'on',
            'usage_pin': '0428',
            'usage_pin_confirmation': '0428',
            'menus-TOTAL_FORMS': '0',
            'menus-INITIAL_FORMS': '0',
            'menus-MIN_NUM_FORMS': '0',
            'menus-MAX_NUM_FORMS': '1000',
            '_save': '저장',
        }
        data.update(overrides)
        return data

    def test_admin_can_create_store_and_inline_menu_with_hashed_pin(self):
        response = self.client.post(
            reverse('admin:stores_store_add'),
            self.store_data(
                **{
                    'menus-TOTAL_FORMS': '1',
                    'menus-0-name': '소금빵',
                    'menus-0-price': '3500',
                    'menus-0-sort_order': '2',
                },
            ),
        )

        self.assertEqual(response.status_code, 302)
        store = Store.objects.get(name='관리자 등록 매장')
        self.assertEqual(store.address, '서울 서대문구 연세로')
        self.assertTrue(store.has_usage_pin)
        self.assertTrue(store.check_usage_pin('0428'))
        self.assertNotEqual(store.usage_pin_hash, '0428')
        self.assertNotIn('0428', store.usage_pin_hash)
        self.assertIsNotNone(store.pin_updated_at)
        menu = store.menus.get()
        self.assertEqual((menu.name, menu.price, menu.sort_order), ('소금빵', 3500, 2))

    def test_blank_pin_on_change_preserves_existing_pin_and_updates_store(self):
        store = make_store()
        store.set_usage_pin('0428')
        store.save()
        original_hash = store.usage_pin_hash
        original_pin_updated_at = store.pin_updated_at

        response = self.client.post(
            reverse('admin:stores_store_change', args=[store.pk]),
            self.store_data(
                name='수정된 매장명',
                usage_pin='',
                usage_pin_confirmation='',
            ),
        )

        self.assertEqual(response.status_code, 302)
        store.refresh_from_db()
        self.assertEqual(store.name, '수정된 매장명')
        self.assertEqual(store.usage_pin_hash, original_hash)
        self.assertEqual(store.pin_updated_at, original_pin_updated_at)
        self.assertTrue(store.check_usage_pin('0428'))

    def test_changing_pin_invalidates_previous_pin(self):
        store = make_store()
        store.set_usage_pin('1111')
        store.save()

        response = self.client.post(
            reverse('admin:stores_store_change', args=[store.pk]),
            self.store_data(
                usage_pin='0123',
                usage_pin_confirmation='0123',
            ),
        )

        self.assertEqual(response.status_code, 302)
        store.refresh_from_db()
        self.assertFalse(store.check_usage_pin('1111'))
        self.assertTrue(store.check_usage_pin('0123'))

    def test_change_page_does_not_expose_existing_pin_or_hash(self):
        store = make_store()
        store.set_usage_pin('0428')
        store.save()

        response = self.client.get(reverse('admin:stores_store_change', args=[store.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '0428')
        self.assertNotContains(response, store.usage_pin_hash)
        self.assertNotContains(response, 'usage_pin_hash')
        self.assertContains(response, 'type="password"', count=2)

    def test_non_staff_user_cannot_access_store_admin(self):
        regular_user = User.objects.create_user(
            username='regular-user',
            password='user-password',
        )
        self.client.force_login(regular_user)

        response = self.client.get(reverse('admin:stores_store_changelist'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('admin:login'), response.url)
