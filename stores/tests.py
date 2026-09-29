from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from stores.models import Store, StoreAccessToken, StoreMenu


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
