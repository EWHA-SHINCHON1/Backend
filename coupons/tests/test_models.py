import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from coupons.models import Coupon
from promotions.models import Promotion
from stores.models import Store

User = get_user_model()

NOW = timezone.now().replace(microsecond=0)


def make_promotion(**kwargs):
    store = Store.objects.create(name='매장', category=Store.Category.BAKERY_CAFE, address='주소')
    data = {
        'store': store,
        'title': '프로모션',
        'starts_at': NOW - timedelta(days=1),
        'ends_at': NOW + timedelta(days=6),
        'redeem_until': NOW + timedelta(days=13),
        'total_quantity': 2,
        'is_published': True,
    }
    data.update(kwargs)
    return Promotion.objects.create(**data)


def issue(user, promotion, **kwargs):
    """테스트용 발급. 실제 발급 API는 후속 작업이며, expires_at을 명시적으로 복사한다."""
    return Coupon.objects.create(user=user, promotion=promotion, expires_at=promotion.redeem_until, **kwargs)


class CouponModelTests(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username='u1')
        self.user2 = User.objects.create_user(username='u2')
        self.promotion = make_promotion()

    def test_uuid_pk_and_defaults(self):
        coupon = issue(self.user1, self.promotion)
        self.assertIsInstance(coupon.pk, uuid.UUID)
        self.assertIsNone(coupon.used_at)
        self.assertTrue(timezone.is_aware(coupon.issued_at))
        self.assertEqual(coupon.expires_at, self.promotion.redeem_until)

    def test_uuid_default_is_callable(self):
        """모든 쿠폰이 같은 UUID를 공유하지 않도록 default는 callable이어야 한다."""
        self.assertIs(Coupon._meta.get_field('id').default, uuid.uuid4)
        c1 = issue(self.user1, self.promotion)
        c2 = issue(self.user2, self.promotion)
        self.assertNotEqual(c1.pk, c2.pk)

    def test_forbidden_columns_do_not_exist(self):
        field_names = {f.name for f in Coupon._meta.get_fields()}
        for name in ('pin', 'status', 'redeemed_by'):
            self.assertNotIn(name, field_names)

    def test_user_cannot_get_same_promotion_twice(self):
        issue(self.user1, self.promotion)
        with self.assertRaises(IntegrityError), transaction.atomic():
            issue(self.user1, self.promotion)

    def test_same_promotion_for_different_users(self):
        issue(self.user1, self.promotion)
        issue(self.user2, self.promotion)
        self.assertEqual(self.promotion.coupons.count(), 2)

    def test_same_user_for_different_promotions(self):
        other = make_promotion()
        issue(self.user1, self.promotion)
        issue(self.user1, other)
        self.assertEqual(self.user1.coupons.count(), 2)

    def test_expires_at_not_changed_when_promotion_redeem_until_changes(self):
        coupon = issue(self.user1, self.promotion)
        original = coupon.expires_at
        self.promotion.redeem_until = original + timedelta(days=30)
        self.promotion.save()
        coupon.refresh_from_db()
        self.assertEqual(coupon.expires_at, original)


class CouponStatusTests(TestCase):
    def setUp(self):
        self.coupon = issue(User.objects.create_user(username='u1'), make_promotion())
        self.expires_at = self.coupon.expires_at

    def test_available_before_expires_at(self):
        self.assertEqual(self.coupon.get_status(now=self.expires_at - timedelta(seconds=1)), 'available')

    def test_expired_at_expires_at_boundary(self):
        self.assertEqual(self.coupon.get_status(now=self.expires_at), 'expired')
        self.assertEqual(self.coupon.get_status(now=self.expires_at + timedelta(seconds=1)), 'expired')

    def test_used_has_priority_over_expired(self):
        self.coupon.used_at = self.expires_at - timedelta(days=1)
        self.assertEqual(self.coupon.get_status(now=self.expires_at - timedelta(days=2)), 'used')
        self.assertEqual(self.coupon.get_status(now=self.expires_at + timedelta(days=1)), 'used')


class CouponDeletePolicyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='u1')
        self.promotion = make_promotion()
        self.coupon = issue(self.user, self.promotion)

    def test_user_with_coupon_cannot_be_deleted(self):
        """삭제 정책: Coupon.user = PROTECT"""
        with self.assertRaises(ProtectedError):
            self.user.delete()

    def test_promotion_with_coupon_cannot_be_deleted(self):
        """삭제 정책: Coupon.promotion = PROTECT"""
        with self.assertRaises(ProtectedError):
            self.promotion.delete()

    def test_coupon_itself_can_be_deleted(self):
        """PROTECT는 참조 대상 삭제만 막고 쿠폰 자체 삭제는 막지 않는다."""
        self.coupon.delete()
        self.assertFalse(Coupon.objects.exists())


class PromotionQuantityTests(TestCase):
    """Coupon 연결 후 완성된 Promotion의 발급 수 기반 계산"""

    def setUp(self):
        self.promotion = make_promotion(total_quantity=2)
        self.users = [User.objects.create_user(username=f'u{i}') for i in range(3)]

    def test_issued_and_remaining_quantity(self):
        self.assertEqual(self.promotion.issued_count, 0)
        self.assertEqual(self.promotion.remaining_quantity, 2)
        issue(self.users[0], self.promotion)
        self.assertEqual(self.promotion.issued_count, 1)
        self.assertEqual(self.promotion.remaining_quantity, 1)

    def test_status_uses_coupon_count_when_not_given(self):
        self.assertEqual(self.promotion.get_status(now=NOW), 'active')
        issue(self.users[0], self.promotion)
        self.assertEqual(self.promotion.get_status(now=NOW), 'active')
        issue(self.users[1], self.promotion)
        self.assertEqual(self.promotion.get_status(now=NOW), 'sold_out')
        self.assertEqual(self.promotion.remaining_quantity, 0)

    def test_remaining_quantity_never_negative(self):
        for user in self.users:  # 수량 검사는 발급 API 몫이므로 모델은 초과 생성을 막지 않음
            issue(user, self.promotion)
        self.assertEqual(self.promotion.remaining_quantity, 0)

    def test_existing_coupon_usable_after_issue_period_ends(self):
        coupon = issue(self.users[0], self.promotion)
        after_end = self.promotion.ends_at + timedelta(days=1)
        self.assertEqual(self.promotion.get_status(now=after_end), 'ended')
        self.assertEqual(coupon.get_status(now=after_end), 'available')
