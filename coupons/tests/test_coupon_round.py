from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import ProtectedError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from coupons.models import Coupon, CouponRound
from coupons.tests.helpers import make_promotion

User = get_user_model()
KST = ZoneInfo('Asia/Seoul')


def make_round(promotion, expires_at, quantity=30):
    return CouponRound.objects.create(promotion=promotion, quantity=quantity, expires_at=expires_at)


class CouponRoundModelTests(TestCase):
    def setUp(self):
        self.promotion = make_promotion()

    def test_round_date_is_seoul_date_of_expires_at(self):
        # 11/1 00:30 KST = 10/31 15:30 UTC. UTC 날짜가 아니라 한국 날짜여야 한다.
        coupon_round = make_round(self.promotion, datetime(2026, 11, 1, 0, 30, tzinfo=KST))
        self.assertEqual(coupon_round.round_date.isoformat(), '2026-11-01')

        coupon_round.expires_at = datetime(2026, 11, 2, 21, 0, tzinfo=KST)
        coupon_round.save()
        coupon_round.refresh_from_db()
        self.assertEqual(coupon_round.round_date.isoformat(), '2026-11-02')

    def test_one_round_per_promotion_per_day(self):
        make_round(self.promotion, datetime(2026, 11, 1, 12, 0, tzinfo=KST))
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_round(self.promotion, datetime(2026, 11, 1, 21, 0, tzinfo=KST))

    def test_same_day_allowed_for_different_promotions(self):
        other = make_promotion()
        make_round(self.promotion, datetime(2026, 11, 1, 21, 0, tzinfo=KST))
        make_round(other, datetime(2026, 11, 1, 21, 0, tzinfo=KST))
        self.assertEqual(CouponRound.objects.count(), 2)

    def test_quantity_must_be_positive(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_round(self.promotion, datetime(2026, 11, 1, 21, 0, tzinfo=KST), quantity=0)

    def test_coupon_round_is_optional_until_issue_logic_switches(self):
        coupon = Coupon.objects.create(
            user=User.objects.create_user(username='u1'),
            promotion=self.promotion,
            expires_at=self.promotion.redeem_until,
        )
        self.assertIsNone(coupon.round)


class CouponRoundDeletePolicyTests(TestCase):
    def setUp(self):
        self.promotion = make_promotion()
        self.coupon_round = make_round(self.promotion, timezone.now() + timedelta(hours=3))

    def test_round_with_coupon_cannot_be_deleted(self):
        """삭제 정책: Coupon.round = PROTECT"""
        Coupon.objects.create(
            user=User.objects.create_user(username='u1'),
            promotion=self.promotion,
            round=self.coupon_round,
            expires_at=self.coupon_round.expires_at,
        )
        with self.assertRaises(ProtectedError):
            self.coupon_round.delete()

    def test_round_without_coupon_can_be_deleted(self):
        self.coupon_round.delete()
        self.assertFalse(CouponRound.objects.exists())

    def test_promotion_with_round_cannot_be_deleted(self):
        """삭제 정책: CouponRound.promotion = PROTECT"""
        with self.assertRaises(ProtectedError):
            self.promotion.delete()


class BackfillCouponRoundMigrationTests(TransactionTestCase):
    """0004: 발급분이 없는 기존 쿠폰을 프로모션마다 발급분 하나로 묶는다."""

    before = [('coupons', '0003_coupon_round')]
    after = [('coupons', '0004_backfill_coupon_round')]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        apps = executor.loader.project_state(self.before).apps

        Store = apps.get_model('stores', 'Store')
        Promotion = apps.get_model('promotions', 'Promotion')
        OldCoupon = apps.get_model('coupons', 'Coupon')
        UserModel = apps.get_model('users', 'User')

        now = timezone.now().replace(microsecond=0)
        self.redeem_until = datetime(2026, 11, 18, 0, 30, tzinfo=KST)
        store = Store.objects.create(name='매장', category='BAKERY_CAFE', address='주소')

        def promotion():
            return Promotion.objects.create(
                store=store,
                title='프로모션',
                starts_at=now - timedelta(days=1),
                ends_at=now + timedelta(days=1),
                redeem_until=self.redeem_until,
                total_quantity=10,
                is_published=True,
            )

        self.with_coupons_id = promotion().pk
        self.without_coupons_id = promotion().pk
        for i in range(3):
            OldCoupon.objects.create(
                user=UserModel.objects.create(username=f'u{i}'),
                promotion_id=self.with_coupons_id,
                expires_at=self.redeem_until,
            )

    def tearDown(self):
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

    def test_backfill_and_reverse(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.after)
        apps = executor.loader.project_state(self.after).apps
        NewRound = apps.get_model('coupons', 'CouponRound')
        NewCoupon = apps.get_model('coupons', 'Coupon')

        coupon_round = NewRound.objects.get()
        self.assertEqual(coupon_round.promotion_id, self.with_coupons_id)
        self.assertEqual(coupon_round.quantity, 3)
        self.assertEqual(coupon_round.expires_at, self.redeem_until)
        self.assertEqual(coupon_round.round_date.isoformat(), '2026-11-18')
        self.assertFalse(NewRound.objects.filter(promotion_id=self.without_coupons_id).exists())
        self.assertFalse(NewCoupon.objects.filter(round__isnull=True).exists())
        # 기존 쿠폰의 사용 기한은 바뀌지 않는다.
        self.assertEqual(
            set(NewCoupon.objects.values_list('expires_at', flat=True)),
            {self.redeem_until},
        )

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.before)
        apps = executor.loader.project_state(self.before).apps
        self.assertFalse(apps.get_model('coupons', 'CouponRound').objects.exists())
        self.assertEqual(apps.get_model('coupons', 'Coupon').objects.filter(round__isnull=True).count(), 3)
