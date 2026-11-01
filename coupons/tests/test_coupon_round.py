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


def make_round(promotion, expires_at, quantity=30, issue_starts_at=None):
    """발급 시작을 주지 않으면 사용 마감 하루 전부터 연다."""
    return CouponRound.objects.create(
        promotion=promotion,
        quantity=quantity,
        issue_starts_at=issue_starts_at or expires_at - timedelta(days=1),
        expires_at=expires_at,
    )


class CouponRoundModelTests(TestCase):
    def setUp(self):
        self.promotion = make_promotion(with_round=False)

    def test_issue_starts_at_must_be_before_expires_at(self):
        expires_at = datetime(2026, 11, 3, 20, 0, tzinfo=KST)
        for issue_starts_at in (expires_at, expires_at + timedelta(seconds=1)):
            with self.subTest(issue_starts_at=issue_starts_at), self.assertRaises(IntegrityError), transaction.atomic():
                make_round(self.promotion, expires_at, issue_starts_at=issue_starts_at)

    def test_multi_day_round_is_open_for_whole_period(self):
        coupon_round = make_round(
            self.promotion,
            datetime(2026, 11, 3, 20, 0, tzinfo=KST),
            issue_starts_at=datetime(2026, 11, 1, 10, 0, tzinfo=KST),
        )
        self.assertFalse(coupon_round.is_open(datetime(2026, 11, 1, 9, 59, 59, tzinfo=KST)))
        self.assertTrue(coupon_round.is_open(datetime(2026, 11, 1, 10, 0, tzinfo=KST)))
        self.assertTrue(coupon_round.is_open(datetime(2026, 11, 2, 23, 0, tzinfo=KST)))
        self.assertFalse(coupon_round.is_open(datetime(2026, 11, 3, 20, 0, tzinfo=KST)))

    def test_current_round_is_latest_started(self):
        first = make_round(
            self.promotion,
            datetime(2026, 11, 3, 20, 0, tzinfo=KST),
            issue_starts_at=datetime(2026, 11, 1, 10, 0, tzinfo=KST),
        )
        second = make_round(
            self.promotion,
            datetime(2026, 11, 8, 20, 0, tzinfo=KST),
            issue_starts_at=datetime(2026, 11, 5, 10, 0, tzinfo=KST),
        )

        def current(*args):
            return CouponRound.objects.filter(promotion=self.promotion).current(datetime(*args, tzinfo=KST)).first()

        self.assertIsNone(current(2026, 10, 31, 23, 0))  # 아직 시작한 발급분 없음
        self.assertEqual(current(2026, 11, 2, 12, 0), first)
        self.assertEqual(current(2026, 11, 4, 12, 0), first)  # 마감됐지만 다음 발급분 시작 전
        self.assertEqual(current(2026, 11, 5, 10, 0), second)

    def test_quantity_must_be_positive(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_round(self.promotion, datetime(2026, 11, 1, 21, 0, tzinfo=KST), quantity=0)

    def test_coupon_requires_round(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Coupon.objects.create(
                user=User.objects.create_user(username='u1'),
                promotion=self.promotion,
                expires_at=self.promotion.redeem_until,
            )

    def test_same_user_once_per_round_but_again_in_next_round(self):
        user = User.objects.create_user(username='u1')
        today = make_round(self.promotion, timezone.now() + timedelta(hours=1))
        tomorrow = make_round(self.promotion, timezone.now() + timedelta(days=1))

        def create(coupon_round):
            return Coupon.objects.create(
                user=user, promotion=self.promotion, round=coupon_round, expires_at=coupon_round.expires_at,
            )

        create(today)
        with self.assertRaises(IntegrityError), transaction.atomic():
            create(today)
        create(tomorrow)
        self.assertEqual(user.coupons.filter(promotion=self.promotion).count(), 2)


class CouponRoundDeletePolicyTests(TestCase):
    def setUp(self):
        self.promotion = make_promotion(with_round=False)
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

    @staticmethod
    def state_targets(executor, coupons_target):
        """coupons만 지정한 마이그레이션으로 두고 다른 앱은 최신 상태로 둔 모델 상태 기준."""
        others = [node for node in executor.loader.graph.leaf_nodes() if node[0] != 'coupons']
        return others + coupons_target

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        apps = executor.loader.project_state(self.state_targets(executor, self.before)).apps

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
        apps = executor.loader.project_state(self.state_targets(executor, self.after)).apps
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
        apps = executor.loader.project_state(self.state_targets(executor, self.before)).apps
        self.assertFalse(apps.get_model('coupons', 'CouponRound').objects.exists())
        self.assertEqual(apps.get_model('coupons', 'Coupon').objects.filter(round__isnull=True).count(), 3)

    def test_existing_round_opens_from_its_date_after_period_migration(self):
        """0009·0010: 기존 발급분은 발급일 00:00(Asia/Seoul)부터 열린 것으로 채워지고 round_date는 없어진다."""
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        target = [('coupons', '0010_couponround_period')]
        executor.migrate(target)
        apps = executor.loader.project_state(self.state_targets(executor, target)).apps
        coupon_round = apps.get_model('coupons', 'CouponRound').objects.get()

        self.assertEqual(coupon_round.issue_starts_at, datetime(2026, 11, 18, 0, 0, tzinfo=KST))
        self.assertEqual(coupon_round.expires_at, self.redeem_until)
        self.assertFalse(hasattr(coupon_round, 'round_date'))
