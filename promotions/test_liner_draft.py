import json
from datetime import timedelta
from unittest.mock import Mock, patch

import requests
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from coupons.models import Coupon
from promotions.models import Promotion, PromotionEvent, SavedPromotion
from promotions.services.liner import (
    LINER_CHAT_COMPLETIONS_URL,
    LINER_TIMEOUT,
    LinerDraftError,
    PromotionDraft,
    generate_promotion_draft,
)
from stores.models import Store, StoreMenu


User = get_user_model()


def successful_liner_response(title='따뜻한 한입의 휴식', description='오늘의 혜택을 만나보세요.'):
    response = Mock(status_code=200, headers={})
    response.json.return_value = {
        'choices': [
            {
                'finish_reason': 'stop',
                'message': {
                    'content': json.dumps(
                        {'title': title, 'description': description},
                        ensure_ascii=False,
                    )
                },
            }
        ]
    }
    return response


@override_settings(LINER_API_KEY='test-liner-key', LINER_MODEL='liner-mark-1.3')
class LinerDraftServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(
            name='신촌 빵집',
            category=Store.Category.BAKERY_CAFE,
            address='공개하지 않을 주소',
            business_hours='매일 10:00~20:00',
            story_title='매일 굽는 작은 빵집',
            story='신선한 재료로 매일 빵을 굽습니다.',
            story_after_image='잠깐 쉬어가기 좋은 공간입니다.',
            promotion_context='NEVER_SEND_INTERNAL_CONTEXT',
            image_url='https://secret.example/store.jpg',
            map_url='https://secret.example/map',
            instagram_url='https://secret.example/instagram',
            naver_url='https://secret.example/naver',
            usage_pin_hash='NEVER_SEND_PIN_HASH',
        )
        for index in range(12):
            StoreMenu.objects.create(
                store=cls.store,
                name=f'메뉴 {index}',
                description=f'메뉴 설명 {index}',
                price=3000 + index,
                image_url=f'https://secret.example/menu-{index}.jpg',
                sort_order=index,
            )
        now = timezone.now().replace(microsecond=0)
        cls.promotion_data = {
            'benefit': '음료 10% 할인',
            'terms': '1인 1회',
            'starts_at': now,
            'ends_at': now + timedelta(days=7),
            'requires_coupon': True,
            'redeem_until': now + timedelta(days=14),
            'total_quantity': 100,
        }

    @patch('promotions.services.liner.requests.post')
    def test_success_uses_expected_request_contract_and_returns_validated_draft(self, mock_post):
        mock_post.return_value = successful_liner_response(
            title='  따뜻한 한입의 휴식  ',
            description='  오늘의 혜택을 만나보세요.  ',
        )

        draft = generate_promotion_draft(
            store=self.store,
            promotion_data=self.promotion_data,
            ai_context='공부 중 쉬어가는 분위기 강조',
        )

        self.assertEqual(draft.title, '따뜻한 한입의 휴식')
        self.assertEqual(draft.description, '오늘의 혜택을 만나보세요.')
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], LINER_CHAT_COMPLETIONS_URL)
        self.assertEqual(kwargs['timeout'], LINER_TIMEOUT)
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer test-liner-key')
        self.assertEqual(kwargs['json']['model'], 'liner-mark-1.3')
        self.assertFalse(kwargs['json']['stream'])
        self.assertEqual(kwargs['json']['response_format']['type'], 'json_schema')
        self.assertTrue(kwargs['json']['response_format']['json_schema']['strict'])
        schema = kwargs['json']['response_format']['json_schema']['schema']
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(set(schema['required']), {'title', 'description'})

    @patch('promotions.services.liner.requests.post')
    def test_payload_uses_allowlist_caps_menus_and_excludes_internal_data(self, mock_post):
        mock_post.return_value = successful_liner_response()

        generate_promotion_draft(
            store=self.store,
            promotion_data={**self.promotion_data, 'untrusted_internal': 'NEVER_SEND_EXTRA'},
            ai_context='운영자가 입력한 문맥',
        )

        request_json = mock_post.call_args.kwargs['json']
        user_content = request_json['messages'][1]['content']
        transmitted = json.loads(user_content.split('\n', 1)[1])
        self.assertEqual(
            set(transmitted['store']),
            {
                'name',
                'category',
                'business_hours',
                'story_title',
                'story',
                'story_after_image',
                'menus',
            },
        )
        self.assertEqual(len(transmitted['store']['menus']), 10)
        self.assertEqual(set(transmitted['store']['menus'][0]), {'name', 'description'})
        self.assertEqual(transmitted['operator_context'], '운영자가 입력한 문맥')
        serialized = json.dumps(request_json, ensure_ascii=False)
        for forbidden in (
            'NEVER_SEND_INTERNAL_CONTEXT',
            'NEVER_SEND_PIN_HASH',
            'NEVER_SEND_EXTRA',
            'secret.example',
            '공개하지 않을 주소',
            'test-liner-key',
        ):
            self.assertNotIn(forbidden, serialized)

    @override_settings(LINER_API_KEY='')
    @patch('promotions.services.liner.requests.post')
    def test_missing_api_key_fails_before_external_request(self, mock_post):
        with self.assertRaises(LinerDraftError) as raised:
            generate_promotion_draft(store=self.store, promotion_data=self.promotion_data)

        self.assertEqual(raised.exception.code, 'LINER_NOT_CONFIGURED')
        mock_post.assert_not_called()

    @patch('promotions.services.liner.requests.post')
    def test_http_failures_are_distinguished_without_exposing_response_body(self, mock_post):
        expected_codes = {
            400: 'LINER_BAD_REQUEST',
            401: 'LINER_AUTH_FAILED',
            402: 'LINER_CREDIT_EXHAUSTED',
            429: 'LINER_RATE_LIMITED',
            500: 'LINER_UNAVAILABLE',
            502: 'LINER_UNAVAILABLE',
        }
        for status, expected_code in expected_codes.items():
            with self.subTest(status=status):
                response = Mock(status_code=status, headers={'x-request-id': 'safe-id'})
                response.text = 'NEVER_EXPOSE_RESPONSE_OR_KEY_test-liner-key'
                mock_post.return_value = response
                with self.assertRaises(LinerDraftError) as raised:
                    generate_promotion_draft(store=self.store, promotion_data=self.promotion_data)
                self.assertEqual(raised.exception.code, expected_code)
                self.assertNotIn('test-liner-key', str(raised.exception))
                self.assertNotIn('NEVER_EXPOSE', raised.exception.user_message)

    @patch('promotions.services.liner.requests.post')
    def test_timeout_and_connection_failures_are_distinguished(self, mock_post):
        for exception, expected_code in (
            (requests.Timeout('secret body'), 'LINER_TIMEOUT'),
            (requests.ConnectionError('secret body'), 'LINER_CONNECTION_FAILED'),
            (requests.RequestException('secret body'), 'LINER_REQUEST_FAILED'),
        ):
            with self.subTest(expected_code=expected_code):
                mock_post.side_effect = exception
                with self.assertRaises(LinerDraftError) as raised:
                    generate_promotion_draft(store=self.store, promotion_data=self.promotion_data)
                self.assertEqual(raised.exception.code, expected_code)
                self.assertNotIn('secret body', raised.exception.user_message)
        mock_post.side_effect = None

    @patch('promotions.services.liner.requests.post')
    def test_invalid_envelope_and_incomplete_output_are_rejected(self, mock_post):
        invalid_bodies = (
            {},
            {'choices': []},
            {'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]},
            {'choices': [{'finish_reason': 'stop', 'message': {}}]},
        )
        for body in invalid_bodies:
            with self.subTest(body=body):
                response = Mock(status_code=200, headers={})
                response.json.return_value = body
                mock_post.return_value = response
                with self.assertRaises(LinerDraftError) as raised:
                    generate_promotion_draft(store=self.store, promotion_data=self.promotion_data)
                self.assertEqual(raised.exception.code, 'LINER_INVALID_RESPONSE')

    @patch('promotions.services.liner.requests.post')
    def test_invalid_json_is_rejected(self, mock_post):
        response = Mock(status_code=200, headers={})
        response.json.side_effect = ValueError('invalid response body')
        mock_post.return_value = response

        with self.assertRaises(LinerDraftError) as raised:
            generate_promotion_draft(store=self.store, promotion_data=self.promotion_data)

        self.assertEqual(raised.exception.code, 'LINER_INVALID_RESPONSE')
        self.assertNotIn('invalid response body', raised.exception.user_message)

    @patch('promotions.services.liner.requests.post')
    def test_invalid_draft_schema_values_are_rejected(self, mock_post):
        invalid_contents = (
            {'title': 123, 'description': '소개'},
            {'title': '제목', 'description': 123},
            {'title': '', 'description': '소개'},
            {'title': '제목', 'description': '   '},
            {'title': '가' * 201, 'description': '소개'},
            {'title': '제목', 'description': '가' * 501},
            {'title': '제목', 'description': '소개', 'extra': '금지'},
        )
        for content in invalid_contents:
            with self.subTest(content=content):
                mock_post.return_value = successful_liner_response()
                mock_post.return_value.json.return_value['choices'][0]['message']['content'] = (
                    json.dumps(content, ensure_ascii=False)
                )
                with self.assertRaises(LinerDraftError) as raised:
                    generate_promotion_draft(store=self.store, promotion_data=self.promotion_data)
                self.assertEqual(raised.exception.code, 'LINER_INVALID_RESPONSE')


@override_settings(LINER_API_KEY='test-liner-key', LINER_MODEL='liner-mark-1.3')
class LinerDraftAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.store = Store.objects.create(
            name='Admin 매장',
            category=Store.Category.RESTAURANT,
            address='주소',
        )
        cls.admin_user = User.objects.create_superuser(
            username='liner-admin',
            password='password',
            email='admin@example.com',
        )
        cls.regular_user = User.objects.create_user(username='liner-regular')
        cls.staff_without_permission = User.objects.create_user(
            username='liner-staff',
            is_staff=True,
        )
        cls.coupon_user = User.objects.create_user(username='liner-coupon-user')

    def setUp(self):
        self.client.force_login(self.admin_user)
        self.endpoint = reverse('admin:promotions_promotion_generate_draft')
        self.starts_at = timezone.now().replace(microsecond=0) + timedelta(days=1)
        self.ends_at = self.starts_at + timedelta(days=7)
        self.redeem_until = self.ends_at + timedelta(days=7)

    @staticmethod
    def add_split_datetime(data, field_name, value):
        local_value = None if value is None else timezone.localtime(value)
        data[f'{field_name}_0'] = '' if local_value is None else local_value.strftime('%Y-%m-%d')
        data[f'{field_name}_1'] = '' if local_value is None else local_value.strftime('%H:%M:%S')

    def draft_data(self, **overrides):
        values = {
            'store': str(self.store.pk),
            'benefit': '음료 10% 할인',
            'terms': '1인 1회',
            'starts_at': self.starts_at,
            'ends_at': self.ends_at,
            'requires_coupon': True,
            'redeem_until': self.redeem_until,
            'total_quantity': '100',
            'ai_context': '편안한 분위기 강조',
        }
        values.update(overrides)
        data = {
            'store': values['store'],
            'benefit': values['benefit'],
            'terms': values['terms'],
            'total_quantity': values['total_quantity'],
            'ai_context': values['ai_context'],
        }
        if values['requires_coupon']:
            data['requires_coupon'] = 'on'
        self.add_split_datetime(data, 'starts_at', values['starts_at'])
        self.add_split_datetime(data, 'ends_at', values['ends_at'])
        self.add_split_datetime(data, 'redeem_until', values['redeem_until'])
        if 'object_id' in overrides:
            data['object_id'] = overrides['object_id']
        return data

    def make_promotion(self, **kwargs):
        values = {
            'store': self.store,
            'title': '기존 제목',
            'description': '기존 소개',
            'benefit': '음료 10% 할인',
            'terms': '1인 1회',
            'starts_at': self.starts_at,
            'ends_at': self.ends_at,
            'requires_coupon': True,
            'redeem_until': self.redeem_until,
            'total_quantity': 100,
            'is_published': False,
        }
        values.update(kwargs)
        return Promotion.objects.create(**values)

    @patch('promotions.admin.generate_promotion_draft')
    def test_new_promotion_draft_succeeds_with_empty_title_and_description(self, mock_generate):
        mock_generate.return_value = PromotionDraft('생성 제목', '생성 소개')

        response = self.client.post(self.endpoint, self.draft_data())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['draft'], {'title': '생성 제목', 'description': '생성 소개'})
        self.assertEqual(Promotion.objects.count(), 0)
        self.assertIn('no-store', response['Cache-Control'])

    @patch('promotions.admin.generate_promotion_draft')
    def test_existing_unissued_promotion_draft_does_not_modify_related_data(self, mock_generate):
        mock_generate.return_value = PromotionDraft('새 초안', '새 소개 초안')
        promotion = self.make_promotion()
        saved_user = User.objects.create_user(username='saved-user')
        saved = SavedPromotion.objects.create(user=saved_user, promotion=promotion)
        event = PromotionEvent.objects.create(promotion=promotion, event_type='VIEW')
        original_values = {
            field: getattr(promotion, field)
            for field in (
                'store_id',
                'title',
                'description',
                'benefit',
                'terms',
                'starts_at',
                'ends_at',
                'requires_coupon',
                'redeem_until',
                'total_quantity',
                'is_published',
                'featured_rank',
            )
        }

        response = self.client.post(
            self.endpoint,
            self.draft_data(object_id=str(promotion.pk)),
        )

        self.assertEqual(response.status_code, 200)
        promotion.refresh_from_db()
        self.assertEqual(
            {field: getattr(promotion, field) for field in original_values},
            original_values,
        )
        self.assertTrue(SavedPromotion.objects.filter(pk=saved.pk).exists())
        self.assertTrue(PromotionEvent.objects.filter(pk=event.pk).exists())
        self.assertEqual(Coupon.objects.count(), 0)

    @patch('promotions.admin.generate_promotion_draft')
    def test_invalid_required_and_configuration_inputs_fail_before_liner(self, mock_generate):
        invalid_cases = (
            ({'store': ''}, 'store'),
            ({'benefit': ''}, 'benefit'),
            ({'starts_at': None}, 'starts_at'),
            ({'ends_at': self.starts_at}, 'ends_at'),
            ({'total_quantity': '0'}, 'total_quantity'),
            ({'redeem_until': self.ends_at - timedelta(seconds=1)}, 'redeem_until'),
        )
        for overrides, expected_field in invalid_cases:
            with self.subTest(expected_field=expected_field):
                response = self.client.post(self.endpoint, self.draft_data(**overrides))
                self.assertEqual(response.status_code, 400)
                self.assertIn(expected_field, response.json()['error']['details'])
        mock_generate.assert_not_called()

    @patch('promotions.admin.generate_promotion_draft')
    def test_non_coupon_input_discards_coupon_fields_before_liner(self, mock_generate):
        mock_generate.return_value = PromotionDraft('비쿠폰 제목', '비쿠폰 소개')

        response = self.client.post(
            self.endpoint,
            self.draft_data(requires_coupon=False),
        )

        self.assertEqual(response.status_code, 200)
        promotion_data = mock_generate.call_args.kwargs['promotion_data']
        self.assertFalse(promotion_data['requires_coupon'])
        self.assertIsNone(promotion_data['redeem_until'])
        self.assertIsNone(promotion_data['total_quantity'])

    @patch('promotions.admin.generate_promotion_draft')
    def test_issued_coupon_blocks_existing_promotion_before_liner(self, mock_generate):
        promotion = self.make_promotion()
        Coupon.objects.create(
            user=self.coupon_user,
            promotion=promotion,
            expires_at=promotion.redeem_until,
        )

        response = self.client.post(
            self.endpoint,
            self.draft_data(object_id=str(promotion.pk)),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'COUPON_ALREADY_ISSUED')
        mock_generate.assert_not_called()

    def test_get_is_rejected(self):
        response = self.client.get(self.endpoint)
        self.assertEqual(response.status_code, 405)

    @patch('promotions.admin.generate_promotion_draft')
    def test_regular_user_owner_header_and_staff_without_permission_cannot_call(self, mock_generate):
        self.client.force_login(self.regular_user)
        response = self.client.post(self.endpoint, self.draft_data())
        self.assertEqual(response.status_code, 302)

        self.client.logout()
        response = self.client.post(
            self.endpoint,
            self.draft_data(),
            HTTP_AUTHORIZATION='Owner raw-owner-token',
        )
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.staff_without_permission)
        response = self.client.post(self.endpoint, self.draft_data())
        self.assertEqual(response.status_code, 403)
        mock_generate.assert_not_called()

    @patch('promotions.admin.generate_promotion_draft')
    def test_staff_with_add_permission_can_generate_for_new_promotion(self, mock_generate):
        self.staff_without_permission.user_permissions.add(
            Permission.objects.get(codename='add_promotion')
        )
        self.client.force_login(self.staff_without_permission)
        mock_generate.return_value = PromotionDraft('권한 제목', '권한 소개')

        response = self.client.post(self.endpoint, self.draft_data())

        self.assertEqual(response.status_code, 200)

    @patch('promotions.admin.generate_promotion_draft')
    def test_liner_failure_is_safe_and_does_not_save_form_data(self, mock_generate):
        promotion = self.make_promotion()
        mock_generate.side_effect = LinerDraftError(
            'LINER_TIMEOUT',
            '초안 생성 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.',
        )

        response = self.client.post(
            self.endpoint,
            self.draft_data(object_id=str(promotion.pk), benefit='저장되면 안 되는 혜택'),
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error']['code'], 'LINER_TIMEOUT')
        promotion.refresh_from_db()
        self.assertEqual(promotion.benefit, '음료 10% 할인')

    @patch('promotions.admin.generate_promotion_draft')
    def test_csrf_is_required(self, mock_generate):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin_user)
        response = csrf_client.post(self.endpoint, self.draft_data())
        self.assertEqual(response.status_code, 403)

        csrf_client.get(reverse('admin:promotions_promotion_add'))
        csrf_token = csrf_client.cookies['csrftoken'].value
        mock_generate.return_value = PromotionDraft('CSRF 제목', 'CSRF 소개')
        response = csrf_client.post(
            self.endpoint,
            self.draft_data(),
            HTTP_X_CSRFTOKEN=csrf_token,
        )
        self.assertEqual(response.status_code, 200)
