import uuid
from datetime import timedelta

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from coupons.models import Coupon, CouponRound
from coupons.tests.helpers import make_coupon
from promotions.admin import PromotionAdmin
from promotions.models import Promotion, PromotionEvent
from promotions.validators import validate_promotion_update
from stores.models import Store

User = get_user_model()

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


def make_non_coupon_promotion(store, **kwargs):
    data = {
        'store': store,
        'title': '매장 방문 이벤트',
        'starts_at': STARTS,
        'ends_at': ENDS,
        'requires_coupon': False,
        'redeem_until': None,
        'total_quantity': None,
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
        self.assertTrue(Promotion._meta.get_field('requires_coupon').default)
        self.assertTrue(promotion.requires_coupon)
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

    def test_coupon_promotion_requires_redeem_until_and_total_quantity(self):
        self.assert_rejected(redeem_until=None)
        self.assert_rejected(total_quantity=None)

    def test_valid_non_coupon_promotion_requires_null_coupon_fields(self):
        promotion = make_non_coupon_promotion(self.store)

        self.assertFalse(promotion.requires_coupon)
        self.assertIsNone(promotion.redeem_until)
        self.assertIsNone(promotion.total_quantity)

    def test_non_coupon_promotion_rejects_coupon_fields(self):
        for values in (
            {'redeem_until': REDEEM_UNTIL},
            {'total_quantity': 10},
            {'redeem_until': REDEEM_UNTIL, 'total_quantity': 10},
        ):
            with self.subTest(values=values):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    make_non_coupon_promotion(self.store, **values)

    def test_non_coupon_promotion_still_requires_valid_active_period(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_non_coupon_promotion(self.store, starts_at=ENDS, ends_at=STARTS)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_non_coupon_promotion(self.store, starts_at=STARTS, ends_at=STARTS)

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

    def test_active_period_boundaries(self):
        self.assertFalse(self.promotion.is_in_active_period(STARTS - timedelta(seconds=1)))
        self.assertTrue(self.promotion.is_in_active_period(STARTS))
        self.assertTrue(self.promotion.is_in_active_period(ENDS - timedelta(seconds=1)))
        self.assertFalse(self.promotion.is_in_active_period(ENDS))


class NonCouponPromotionStatusTests(TestCase):
    def setUp(self):
        self.promotion = make_non_coupon_promotion(make_store())

    def test_status_boundaries(self):
        self.assertEqual(
            self.promotion.get_status(now=STARTS - timedelta(seconds=1)),
            Promotion.Status.UPCOMING,
        )
        self.assertEqual(self.promotion.get_status(now=STARTS), Promotion.Status.ACTIVE)
        self.assertEqual(
            self.promotion.get_status(now=ENDS - timedelta(seconds=1)),
            Promotion.Status.ACTIVE,
        )
        self.assertEqual(self.promotion.get_status(now=ENDS), Promotion.Status.ENDED)

    def test_unpublished_status_has_priority(self):
        self.promotion.is_published = False

        self.assertEqual(self.promotion.get_status(now=STARTS), Promotion.Status.HIDDEN)

    def test_period_boundaries(self):
        self.assertFalse(self.promotion.is_in_active_period(STARTS - timedelta(seconds=1)))
        self.assertTrue(self.promotion.is_in_active_period(STARTS))
        self.assertTrue(self.promotion.is_in_issue_period(STARTS))
        self.assertFalse(self.promotion.is_in_active_period(ENDS))
        self.assertFalse(self.promotion.is_in_issue_period(ENDS))

    def test_remaining_quantity_is_none(self):
        self.assertIsNone(self.promotion.remaining_quantity)

    def test_sold_out_never_occurs_and_coupon_count_is_not_queried(self):
        with self.assertNumQueries(0):
            status = self.promotion.get_status(now=STARTS)
            status_with_issued_count = self.promotion.get_status(now=STARTS, issued_count=10)

        self.assertEqual(status, Promotion.Status.ACTIVE)
        self.assertEqual(status_with_issued_count, Promotion.Status.ACTIVE)


class PromotionUpdatePolicyTests(TestCase):
    def setUp(self):
        self.store = make_store()
        self.other_store = Store.objects.create(
            name='다른 매장',
            category=Store.Category.BAKERY_CAFE,
            address='다른 주소',
        )
        self.promotion = make_promotion(self.store)

    def test_protected_fields_are_rejected_after_coupon_issue(self):
        changed_values = {
            'store': self.other_store,
            'requires_coupon': False,
            'title': '변경된 제목',
            'description': '변경된 소개',
            'benefit': '변경된 혜택',
            'terms': '변경된 조건',
            'starts_at': STARTS + timedelta(hours=1),
            'ends_at': ENDS + timedelta(hours=1),
            'redeem_until': REDEEM_UNTIL + timedelta(days=1),
        }

        for field_name, changed_value in changed_values.items():
            with self.subTest(field=field_name):
                with self.assertRaises(ValidationError) as raised:
                    validate_promotion_update(
                        original=self.promotion,
                        proposed={field_name: changed_value},
                        issued_count=1,
                    )
                self.assertIn(field_name, raised.exception.message_dict)

    def test_allowed_fields_and_quantity_increase_pass_after_coupon_issue(self):
        validate_promotion_update(
            original=self.promotion,
            proposed={
                'image_url': 'https://example.com/new.jpg',
                'is_published': False,
                'featured_rank': 2,
                'total_quantity': self.promotion.total_quantity + 1,
            },
            issued_count=1,
        )

    def test_all_changes_pass_without_coupon_issue(self):
        validate_promotion_update(
            original=self.promotion,
            proposed={
                'store': self.other_store,
                'requires_coupon': False,
                'benefit': '변경된 혜택',
            },
            issued_count=0,
        )


class PromotionAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = make_store()
        cls.other_store = Store.objects.create(
            name='다른 매장',
            category=Store.Category.BAKERY_CAFE,
            address='다른 주소',
        )
        cls.admin_user = User.objects.create_superuser(
            username='promotion-admin',
            password='admin-password',
            email='admin@example.com',
        )
        cls.regular_user = User.objects.create_user(
            username='regular-user',
            password='user-password',
        )
        cls.coupon_user = User.objects.create_user(username='coupon-user')

    def setUp(self):
        self.client.force_login(self.admin_user)

    def promotion_data(self, promotion=None, **overrides):
        values = {
            'store': self.store,
            'title': 'Admin 프로모션',
            'description': 'Admin 소개',
            'benefit': '10% 할인',
            'terms': '1인 1회',
            'image_url': '',
            'starts_at': STARTS,
            'ends_at': ENDS,
            'requires_coupon': True,
            'redeem_until': REDEEM_UNTIL,
            'total_quantity': 10,
            'is_published': True,
            'featured_rank': 1,
        }
        if promotion is not None:
            for field_name in values:
                values[field_name] = getattr(promotion, field_name)
        values.update(overrides)

        data = {
            'store': str(values['store'].pk),
            'title': values['title'],
            'description': values['description'],
            'benefit': values['benefit'],
            'terms': values['terms'],
            'image_url': values['image_url'],
            'total_quantity': '' if values['total_quantity'] is None else str(values['total_quantity']),
            'featured_rank': '' if values['featured_rank'] is None else str(values['featured_rank']),
            '_save': '저장',
        }
        if values['requires_coupon']:
            data['requires_coupon'] = 'on'
        if values['is_published']:
            data['is_published'] = 'on'

        self.add_split_datetime(data, 'starts_at', values['starts_at'])
        self.add_split_datetime(data, 'ends_at', values['ends_at'])
        self.add_split_datetime(data, 'redeem_until', values['redeem_until'])
        self.add_coupon_round_rows(data, promotion)
        return data

    def add_coupon_round_rows(self, data, promotion, rows=()):
        """쿠폰 발급분 인라인 입력. 기존 발급분은 그대로 다시 보내고, rows는 새로 추가할 행이다.

        rows의 각 항목: {'expires_at': datetime, 'quantity': int}
        기존 발급분을 바꾸려면 {'round': CouponRound, ...바꿀 값, 'DELETE': True} 형식을 쓴다.
        """
        prefix = 'coupon_rounds'
        existing = list(promotion.coupon_rounds.order_by('round_date', 'id')) if promotion else []
        changes = {row['round'].pk: row for row in rows if 'round' in row}
        new_rows = [row for row in rows if 'round' not in row]

        index = 0
        for coupon_round in existing:
            row = changes.get(coupon_round.pk, {})
            data[f'{prefix}-{index}-id'] = str(coupon_round.pk)
            data[f'{prefix}-{index}-promotion'] = str(promotion.pk)
            data[f'{prefix}-{index}-quantity'] = str(row.get('quantity', coupon_round.quantity))
            self.add_split_datetime(data, f'{prefix}-{index}-expires_at', row.get('expires_at', coupon_round.expires_at))
            if row.get('DELETE'):
                data[f'{prefix}-{index}-DELETE'] = 'on'
            index += 1
        for row in new_rows:
            data[f'{prefix}-{index}-id'] = ''
            data[f'{prefix}-{index}-promotion'] = '' if promotion is None else str(promotion.pk)
            data[f'{prefix}-{index}-quantity'] = str(row['quantity'])
            self.add_split_datetime(data, f'{prefix}-{index}-expires_at', row['expires_at'])
            index += 1

        data[f'{prefix}-TOTAL_FORMS'] = str(index)
        data[f'{prefix}-INITIAL_FORMS'] = str(len(existing))
        data[f'{prefix}-MIN_NUM_FORMS'] = '0'
        data[f'{prefix}-MAX_NUM_FORMS'] = '1000'
        return data

    @staticmethod
    def add_split_datetime(data, field_name, value):
        local_value = None if value is None else timezone.localtime(value)
        data[f'{field_name}_0'] = '' if local_value is None else local_value.strftime('%Y-%m-%d')
        data[f'{field_name}_1'] = '' if local_value is None else local_value.strftime('%H:%M:%S')

    def issue_coupon(self, promotion):
        return make_coupon(
            user=self.coupon_user,
            promotion=promotion,
            expires_at=promotion.redeem_until,
        )

    def post_change(self, promotion, **overrides):
        return self.client.post(
            reverse('admin:promotions_promotion_change', args=[promotion.pk]),
            self.promotion_data(promotion, **overrides),
        )

    def assert_admin_form_error(self, response, field_name):
        self.assertEqual(response.status_code, 200)
        self.assertIn(field_name, response.context['adminform'].form.errors)

    def test_admin_creates_coupon_promotion(self):
        response = self.client.post(
            reverse('admin:promotions_promotion_add'),
            self.promotion_data(),
        )

        self.assertEqual(response.status_code, 302)
        promotion = Promotion.objects.get(title='Admin 프로모션')
        self.assertTrue(promotion.requires_coupon)
        self.assertEqual(promotion.redeem_until, REDEEM_UNTIL)
        self.assertEqual(promotion.total_quantity, 10)

    def test_admin_creates_non_coupon_promotion_with_null_coupon_fields(self):
        response = self.client.post(
            reverse('admin:promotions_promotion_add'),
            self.promotion_data(
                title='비쿠폰 Admin 프로모션',
                requires_coupon=False,
                redeem_until=REDEEM_UNTIL,
                total_quantity=10,
            ),
        )

        self.assertEqual(response.status_code, 302)
        promotion = Promotion.objects.get(title='비쿠폰 Admin 프로모션')
        self.assertFalse(promotion.requires_coupon)
        self.assertIsNone(promotion.redeem_until)
        self.assertIsNone(promotion.total_quantity)

    def test_list_status_uses_annotated_coupon_count_without_extra_query(self):
        promotion = make_promotion(self.store)
        self.issue_coupon(promotion)
        model_admin = PromotionAdmin(Promotion, admin.site)

        with self.assertNumQueries(1):
            listed_promotion = model_admin.get_queryset(request=None).get(pk=promotion.pk)
            status_label = model_admin.current_status(listed_promotion)

        self.assertEqual(listed_promotion.admin_issued_count, 1)
        self.assertEqual(status_label, Promotion.Status.ACTIVE.label)

    def test_admin_rejects_invalid_dates_and_quantity(self):
        invalid_cases = (
            ({'starts_at': ENDS, 'ends_at': STARTS}, 'ends_at'),
            ({'redeem_until': ENDS - timedelta(seconds=1)}, 'redeem_until'),
            ({'total_quantity': 0}, 'total_quantity'),
        )
        for overrides, error_field in invalid_cases:
            with self.subTest(overrides=overrides):
                response = self.client.post(
                    reverse('admin:promotions_promotion_add'),
                    self.promotion_data(**overrides),
                )
                self.assert_admin_form_error(response, error_field)

    def test_type_change_is_allowed_without_issued_coupon(self):
        promotion = make_promotion(self.store)

        response = self.post_change(promotion, requires_coupon=False)

        self.assertEqual(response.status_code, 302)
        promotion.refresh_from_db()
        self.assertFalse(promotion.requires_coupon)
        self.assertIsNone(promotion.redeem_until)
        self.assertIsNone(promotion.total_quantity)

    def test_type_change_is_rejected_after_coupon_issue(self):
        promotion = make_promotion(self.store)
        self.issue_coupon(promotion)

        response = self.post_change(promotion, requires_coupon=False)

        self.assert_admin_form_error(response, 'requires_coupon')
        promotion.refresh_from_db()
        self.assertTrue(promotion.requires_coupon)

    def test_store_benefit_and_period_changes_are_rejected_after_coupon_issue(self):
        promotion = make_promotion(self.store)
        self.issue_coupon(promotion)
        changes = (
            ({'store': self.other_store}, 'store'),
            ({'benefit': '변경된 혜택'}, 'benefit'),
            ({'starts_at': STARTS + timedelta(hours=1)}, 'starts_at'),
            ({'ends_at': ENDS + timedelta(hours=1)}, 'ends_at'),
            ({'redeem_until': REDEEM_UNTIL + timedelta(days=1)}, 'redeem_until'),
        )

        for overrides, error_field in changes:
            with self.subTest(field=error_field):
                response = self.post_change(promotion, **overrides)
                self.assert_admin_form_error(response, error_field)

    def test_quantity_decrease_is_rejected_after_coupon_issue(self):
        promotion = make_promotion(self.store, total_quantity=10)
        self.issue_coupon(promotion)

        response = self.post_change(promotion, total_quantity=9)

        self.assert_admin_form_error(response, 'total_quantity')

    def test_quantity_increase_is_allowed_after_coupon_issue(self):
        promotion = make_promotion(self.store, total_quantity=10)
        self.issue_coupon(promotion)

        response = self.post_change(promotion, total_quantity=20)

        self.assertEqual(response.status_code, 302)
        promotion.refresh_from_db()
        self.assertEqual(promotion.total_quantity, 20)

    def test_visibility_rank_and_image_changes_preserve_existing_coupon(self):
        promotion = make_promotion(self.store, is_published=True)
        coupon = self.issue_coupon(promotion)
        original_expiry = coupon.expires_at

        response = self.post_change(
            promotion,
            is_published=False,
            featured_rank=3,
            image_url='https://example.com/new.jpg',
        )

        self.assertEqual(response.status_code, 302)
        promotion.refresh_from_db()
        coupon.refresh_from_db()
        self.assertFalse(promotion.is_published)
        self.assertEqual(promotion.featured_rank, 3)
        self.assertEqual(promotion.image_url, 'https://example.com/new.jpg')
        self.assertEqual(coupon.expires_at, original_expiry)

    def test_regular_user_cannot_access_promotion_admin(self):
        self.client.force_login(self.regular_user)

        response = self.client.get(reverse('admin:promotions_promotion_changelist'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('admin:login'), response.url)


class CouponRoundAdminTests(TestCase):
    """프로모션 Admin의 쿠폰 발급분 인라인 입력·검증"""

    promotion_data = PromotionAdminTests.promotion_data
    add_split_datetime = staticmethod(PromotionAdminTests.add_split_datetime)
    add_coupon_round_rows = PromotionAdminTests.add_coupon_round_rows
    store = None

    def setUp(self):
        self.store = make_store()
        self.client.force_login(User.objects.create_superuser(
            username='round-admin', password='admin-password', email='round@example.com',
        ))
        self.promotion = make_promotion(self.store, total_quantity=100)
        self.tomorrow = timezone.now().replace(microsecond=0) + timedelta(days=1)

    def post_rounds(self, *rows, promotion=None, **promotion_overrides):
        promotion = promotion or self.promotion
        data = self.promotion_data(promotion, **promotion_overrides)
        self.add_coupon_round_rows(data, promotion, rows)
        return self.client.post(reverse('admin:promotions_promotion_change', args=[promotion.pk]), data)

    def make_round(self, expires_at=None, quantity=30):
        return CouponRound.objects.create(
            promotion=self.promotion,
            quantity=quantity,
            expires_at=expires_at or self.tomorrow,
        )

    def issue(self, coupon_round):
        return Coupon.objects.create(
            user=User.objects.create_user(username=f'u{Coupon.objects.count()}'),
            promotion=self.promotion,
            round=coupon_round,
            expires_at=coupon_round.expires_at,
        )

    def inline_errors(self, response):
        self.assertEqual(response.status_code, 200)
        formset = response.context['inline_admin_formsets'][0].formset
        form_errors = [str(error) for form in formset.forms for errors in form.errors.values() for error in errors]
        return form_errors + [str(error) for error in formset.non_form_errors()]

    def test_admin_adds_round_with_seoul_round_date(self):
        response = self.post_rounds({'expires_at': self.tomorrow, 'quantity': 30})

        self.assertEqual(response.status_code, 302)
        coupon_round = CouponRound.objects.get(promotion=self.promotion)
        self.assertEqual(coupon_round.quantity, 30)
        self.assertEqual(coupon_round.expires_at, self.tomorrow)
        self.assertEqual(coupon_round.round_date, timezone.localdate(self.tomorrow))

    def test_admin_adds_several_future_days_at_once(self):
        response = self.post_rounds(
            {'expires_at': self.tomorrow, 'quantity': 30},
            {'expires_at': self.tomorrow + timedelta(days=1), 'quantity': 50},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(CouponRound.objects.filter(promotion=self.promotion).count(), 2)

    def test_second_round_on_same_day_is_rejected(self):
        self.make_round()
        response = self.post_rounds({'expires_at': self.tomorrow + timedelta(minutes=30), 'quantity': 10})

        self.assertTrue(any('하루에 하나' in error for error in self.inline_errors(response)))
        self.assertEqual(CouponRound.objects.count(), 1)

    def test_same_day_rows_in_one_submit_are_rejected(self):
        response = self.post_rounds(
            {'expires_at': self.tomorrow, 'quantity': 30},
            {'expires_at': self.tomorrow + timedelta(minutes=30), 'quantity': 30},
        )

        self.assertTrue(any('하루에 하나' in error for error in self.inline_errors(response)))
        self.assertFalse(CouponRound.objects.exists())

    def test_invalid_new_rounds_are_rejected(self):
        now = timezone.now().replace(microsecond=0)
        cases = (
            ({'expires_at': now - timedelta(minutes=1), 'quantity': 30}, '이미 지난 시각'),
            ({'expires_at': REDEEM_UNTIL + timedelta(seconds=1), 'quantity': 30}, '쿠폰 사용 종료 시각'),
            ({'expires_at': ENDS + timedelta(days=2), 'quantity': 30}, '프로모션 기간'),
            ({'expires_at': self.tomorrow, 'quantity': 0}, ''),
        )
        for row, message in cases:
            with self.subTest(row=row):
                errors = self.inline_errors(self.post_rounds(row))
                self.assertTrue(errors)
                self.assertTrue(any(message in error for error in errors))
                self.assertFalse(CouponRound.objects.exists())

    def test_round_rejected_for_non_coupon_promotion(self):
        promotion = make_non_coupon_promotion(self.store)
        response = self.post_rounds({'expires_at': self.tomorrow, 'quantity': 30}, promotion=promotion)

        self.assertTrue(any('비쿠폰형' in error for error in self.inline_errors(response)))
        self.assertFalse(CouponRound.objects.exists())

    def test_round_without_coupon_can_be_changed_and_deleted(self):
        coupon_round = self.make_round()
        later = self.tomorrow + timedelta(hours=2)

        response = self.post_rounds({'round': coupon_round, 'expires_at': later, 'quantity': 10})
        self.assertEqual(response.status_code, 302)
        coupon_round.refresh_from_db()
        self.assertEqual((coupon_round.expires_at, coupon_round.quantity), (later, 10))

        response = self.post_rounds({'round': coupon_round, 'DELETE': True})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CouponRound.objects.exists())

    def test_round_with_coupon_is_protected(self):
        coupon_round = self.make_round(quantity=30)
        coupon = self.issue(coupon_round)
        self.issue(coupon_round)
        cases = (
            ({'DELETE': True}, ''),  # Django Admin의 PROTECT 관계 삭제 거절 메시지
            ({'expires_at': self.tomorrow + timedelta(hours=1)}, '사용 마감은 변경할 수 없습니다'),
            ({'quantity': 1}, '2장이 발급되어'),
        )
        for change, message in cases:
            with self.subTest(change=change):
                errors = self.inline_errors(self.post_rounds({'round': coupon_round, **change}))
                self.assertTrue(errors)
                self.assertTrue(any(message in error for error in errors))
        coupon_round.refresh_from_db()
        coupon.refresh_from_db()
        self.assertEqual((coupon_round.expires_at, coupon_round.quantity), (self.tomorrow, 30))
        self.assertEqual(coupon.expires_at, self.tomorrow)

    def test_quantity_increase_allowed_after_coupon_issue(self):
        coupon_round = self.make_round(quantity=30)
        self.issue(coupon_round)

        response = self.post_rounds({'round': coupon_round, 'quantity': 40})

        self.assertEqual(response.status_code, 302)
        coupon_round.refresh_from_db()
        self.assertEqual(coupon_round.quantity, 40)

    def test_past_round_kept_unchanged_does_not_block_other_edits(self):
        """지난 발급분이 있어도 그대로 두면 다른 수정은 저장된다."""
        coupon_round = self.make_round()
        CouponRound.objects.filter(pk=coupon_round.pk).update(
            expires_at=STARTS - timedelta(hours=1),
        )

        response = self.post_rounds(featured_rank=5)

        self.assertEqual(response.status_code, 302)
        self.promotion.refresh_from_db()
        self.assertEqual(self.promotion.featured_rank, 5)


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
