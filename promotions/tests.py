import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from promotions.models import Promotion, PromotionEvent
from stores.models import Store

BASE = timezone.now().replace(microsecond=0)
STARTS = BASE
ENDS = BASE + timedelta(days=7)
REDEEM_UNTIL = BASE + timedelta(days=14)


def make_store():
    return Store.objects.create(name='매장', category=Store.Category.RESTAURANT, address='주소')


def make_promotion(store, **kwargs):
    data = {
        'store': store,
        'title': '오픈 기념',
        'starts_at': STARTS,
        'ends_at': ENDS,
        'redeem_until': REDEEM_UNTIL,
        'total_quantity': 10,
        'is_published': True,
    }
    data.update(kwargs)
    return Promotion.objects.create(**data)


class PromotionConstraintTests(TestCase):
    def setUp(self):
        self.store = make_store()

    def assert_rejected(self, **kwargs):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_promotion(self.store, **kwargs)

    def test_valid_promotion(self):
        promotion = make_promotion(self.store, is_published=False)
        self.assertFalse(Promotion._meta.get_field('is_published').default)
        self.assertIsNone(promotion.featured_rank)

    def test_starts_at_must_be_before_ends_at(self):
        self.assert_rejected(starts_at=ENDS, ends_at=STARTS)
        self.assert_rejected(starts_at=STARTS, ends_at=STARTS)  # 같은 시각도 거부

    def test_ends_at_must_not_be_after_redeem_until(self):
        self.assert_rejected(redeem_until=ENDS - timedelta(seconds=1))
        make_promotion(self.store, redeem_until=ENDS)  # 같은 시각은 허용

    def test_total_quantity_must_be_positive(self):
        self.assert_rejected(total_quantity=0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_promotion(self.store, total_quantity=-1)

    def test_store_with_promotion_cannot_be_deleted(self):
        """삭제 정책: Promotion.store = PROTECT"""
        make_promotion(self.store)
        with self.assertRaises(ProtectedError):
            self.store.delete()


class PromotionStatusTests(TestCase):
    def setUp(self):
        self.promotion = make_promotion(make_store())

    def status(self, now, issued_count=0):
        return self.promotion.get_status(now=now, issued_count=issued_count)

    def test_unpublished_is_hidden_first(self):
        self.promotion.is_published = False
        self.assertEqual(self.status(STARTS - timedelta(days=1)), 'hidden')
        self.assertEqual(self.status(STARTS), 'hidden')
        self.assertEqual(self.status(ENDS, issued_count=10), 'hidden')

    def test_upcoming_before_starts_at(self):
        self.assertEqual(self.status(STARTS - timedelta(seconds=1)), 'upcoming')

    def test_active_at_starts_at_boundary(self):
        self.assertEqual(self.status(STARTS), 'active')
        self.assertEqual(self.status(ENDS - timedelta(seconds=1), issued_count=9), 'active')

    def test_ended_at_ends_at_boundary(self):
        self.assertEqual(self.status(ENDS), 'ended')
        self.assertEqual(self.status(ENDS, issued_count=10), 'ended')  # 종료가 소진보다 우선

    def test_sold_out_when_issued_reaches_total(self):
        self.assertEqual(self.status(STARTS, issued_count=10), 'sold_out')
        self.assertEqual(self.status(STARTS, issued_count=11), 'sold_out')

    def test_upcoming_has_priority_over_sold_out(self):
        self.assertEqual(self.status(STARTS - timedelta(seconds=1), issued_count=10), 'upcoming')

    def test_issue_period_boundaries(self):
        self.assertFalse(self.promotion.is_in_issue_period(STARTS - timedelta(seconds=1)))
        self.assertTrue(self.promotion.is_in_issue_period(STARTS))
        self.assertTrue(self.promotion.is_in_issue_period(ENDS - timedelta(seconds=1)))
        self.assertFalse(self.promotion.is_in_issue_period(ENDS))


class PromotionEventTests(TestCase):
    def setUp(self):
        self.promotion = make_promotion(make_store())

    def create_event(self, **kwargs):
        return PromotionEvent.objects.create(promotion=self.promotion, **kwargs)

    def assert_rejected(self, **kwargs):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_event(**kwargs)

    def test_view_event_has_empty_channel(self):
        event = self.create_event(event_type='VIEW')
        self.assertEqual(event.channel, '')
        self.assertIsInstance(event.id, uuid.UUID)

    def test_view_event_with_channel_is_rejected(self):
        self.assert_rejected(event_type='VIEW', channel='instagram')

    def test_channel_click_requires_allowed_channel(self):
        self.create_event(event_type='CHANNEL_CLICK', channel='instagram')
        self.create_event(event_type='CHANNEL_CLICK', channel='naver')
        self.assert_rejected(event_type='CHANNEL_CLICK', channel='')
        self.assert_rejected(event_type='CHANNEL_CLICK', channel='kakao')

    def test_unknown_event_type_is_rejected(self):
        self.assert_rejected(event_type='CLICK', channel='')

    def test_client_event_id_is_used_as_pk_and_deduplicated(self):
        """클라이언트 event_id를 id로 저장하면 같은 요청 재시도가 DB에서 중복 차단된다."""
        event_id = uuid.uuid4()
        event = self.create_event(id=event_id, event_type='VIEW')
        self.assertEqual(event.pk, event_id)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PromotionEvent.objects.create(id=event_id, promotion=self.promotion, event_type='VIEW')

    def test_promotion_with_events_cannot_be_deleted(self):
        """삭제 정책: PromotionEvent.promotion = PROTECT"""
        self.create_event(event_type='VIEW')
        with self.assertRaises(ProtectedError):
            self.promotion.delete()
