import re
import secrets
from urllib.parse import urlsplit

from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import URLValidator
from django.db import DatabaseError
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import patch_cache_control

from .models import Store, StoreAccessToken, StoreMenu
from .services import (
    StoreAccessTokenStateChanged,
    issue_store_access_token,
    revoke_store_access_token,
)


PIN_PATTERN = re.compile(r'^[0-9]{4}$')
OWNER_ISSUE_SIGNING_SALT = 'stores.owner-token-issue'
OWNER_USED_NONCES_SESSION_KEY = 'stores.owner-token-used-nonces'


def _no_store(response):
    patch_cache_control(response, no_store=True, no_cache=True, must_revalidate=True, private=True)
    return response


def _validated_frontend_base_url():
    try:
        base_url = settings.FRONTEND_BASE_URL.strip().rstrip('/')
        URLValidator(schemes=('http', 'https'))(base_url)
        parsed = urlsplit(base_url)
        # Reading the port also rejects malformed and out-of-range values.
        parsed.port
    except (AttributeError, TypeError, ValueError, ValidationError) as exc:
        raise ValidationError('Owner link configuration is invalid.') from exc

    if (
        parsed.scheme not in ('http', 'https')
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (settings.IS_PRODUCTION and parsed.scheme != 'https')
    ):
        raise ValidationError('Owner link configuration is invalid.')
    return base_url


def _issue_owner_link_response(model_admin, request, queryset, *, store_getter, action_name):
    if queryset.count() != 1:
        model_admin.message_user(request, '한 번에 한 매장만 선택해 주세요.', level=messages.ERROR)
        return None

    selected = queryset.first()
    store = store_getter(selected)
    back_url = reverse(
        f'admin:{model_admin.model._meta.app_label}_{model_admin.model._meta.model_name}_changelist'
    )
    context = {
        **model_admin.admin_site.each_context(request),
        'opts': model_admin.model._meta,
        'media': model_admin.media,
        'store': store,
        'objects': [selected],
        'action_name': action_name,
        'action_checkbox_name': ACTION_CHECKBOX_NAME,
        'back_url': back_url,
    }

    if request.POST.get('confirm_owner_token_issue') != 'yes':
        active_token_ids = list(
            StoreAccessToken.objects.filter(store=store, is_active=True)
            .order_by('pk')
            .values_list('pk', flat=True)
        )
        payload = {
            'store_id': store.pk,
            'nonce': secrets.token_urlsafe(16),
            'active_token_ids': active_token_ids,
        }
        context.update(
            title='Owner Secret Link 발급 확인',
            issue_nonce=signing.dumps(payload, salt=OWNER_ISSUE_SIGNING_SALT),
        )
        return _no_store(
            TemplateResponse(request, 'admin/stores/owner_token_issue_confirm.html', context)
        )

    try:
        payload = signing.loads(
            request.POST.get('issue_nonce', ''),
            salt=OWNER_ISSUE_SIGNING_SALT,
            max_age=600,
        )
    except signing.BadSignature as exc:
        raise PermissionDenied from exc
    active_token_ids = payload.get('active_token_ids')
    if (
        payload.get('store_id') != store.pk
        or not payload.get('nonce')
        or not isinstance(active_token_ids, list)
        or not all(isinstance(token_id, int) for token_id in active_token_ids)
    ):
        raise PermissionDenied

    used_nonces = request.session.get(OWNER_USED_NONCES_SESSION_KEY, [])
    if payload['nonce'] in used_nonces:
        context.update(
            title='이미 처리된 발급 요청',
            already_processed=True,
        )
        return _no_store(
            TemplateResponse(request, 'admin/stores/owner_token_issued.html', context)
        )

    try:
        frontend_base_url = _validated_frontend_base_url()
    except ValidationError:
        context.update(
            title='Owner Secret Link 발급 실패',
            issue_nonce=request.POST.get('issue_nonce', ''),
            issue_error='Owner link 설정을 확인한 뒤 다시 시도해 주세요.',
        )
        return _no_store(
            TemplateResponse(request, 'admin/stores/owner_token_issue_confirm.html', context)
        )

    try:
        issued = issue_store_access_token(
            store=store,
            expected_active_token_ids=active_token_ids,
        )
    except StoreAccessTokenStateChanged:
        context.update(
            title='이미 처리되었거나 변경된 발급 요청',
            already_processed=True,
        )
        return _no_store(
            TemplateResponse(request, 'admin/stores/owner_token_issued.html', context)
        )
    except (DatabaseError, RuntimeError):
        context.update(
            title='Owner Secret Link 발급 실패',
            issue_nonce=request.POST.get('issue_nonce', ''),
            issue_error='토큰을 발급하지 못했습니다. 잠시 후 다시 시도해 주세요.',
        )
        return _no_store(
            TemplateResponse(request, 'admin/stores/owner_token_issue_confirm.html', context)
        )

    secret_link = f"{frontend_base_url}/owner#token={issued.raw_token}"
    request.session[OWNER_USED_NONCES_SESSION_KEY] = (used_nonces + [payload['nonce']])[-20:]
    context.update(
        title='Owner Secret Link 발급 완료',
        secret_link=secret_link,
        access_token=issued.access_token,
    )
    return _no_store(
        TemplateResponse(request, 'admin/stores/owner_token_issued.html', context)
    )


class StoreAdminForm(forms.ModelForm):
    usage_pin = forms.CharField(
        label='새 PIN',
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={'autocomplete': 'new-password', 'inputmode': 'numeric'},
        ),
        help_text='정확히 4자리 숫자를 입력하세요. 수정 시 비워 두면 기존 PIN을 유지합니다.',
        error_messages={'required': '매장 생성 시 PIN을 입력해야 합니다.'},
    )
    usage_pin_confirmation = forms.CharField(
        label='새 PIN 확인',
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={'autocomplete': 'new-password', 'inputmode': 'numeric'},
        ),
        error_messages={'required': 'PIN 확인값을 입력해야 합니다.'},
    )

    class Meta:
        model = Store
        exclude = ('usage_pin_hash',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance._state.adding:
            self.fields['usage_pin'].required = True
            self.fields['usage_pin_confirmation'].required = True

    def clean(self):
        cleaned_data = super().clean()
        pin = cleaned_data.get('usage_pin')
        confirmation = cleaned_data.get('usage_pin_confirmation')

        if not self.instance._state.adding and not pin and not confirmation:
            return cleaned_data

        if pin and not confirmation:
            self.add_error('usage_pin_confirmation', 'PIN 확인값을 입력해야 합니다.')
        if confirmation and not pin:
            self.add_error('usage_pin', '새 PIN을 입력해야 합니다.')
        if pin and not PIN_PATTERN.fullmatch(pin):
            self.add_error('usage_pin', 'PIN은 정확히 4자리 숫자여야 합니다.')
        if pin and confirmation and pin != confirmation:
            self.add_error('usage_pin_confirmation', 'PIN 확인값이 일치하지 않습니다.')

        return cleaned_data

    def save(self, commit=True):
        store = super().save(commit=False)
        pin = self.cleaned_data.get('usage_pin')
        if pin:
            store.set_usage_pin(pin)

        if commit:
            store.save()
            self.save_m2m()
        return store


class StoreMenuInline(admin.TabularInline):
    model = StoreMenu
    fields = ('name', 'description', 'price', 'image_url', 'sort_order')
    extra = 1
    ordering = ('sort_order', 'id')


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    form = StoreAdminForm
    list_display = (
        'id',
        'name',
        'category',
        'is_active',
        'pin_is_set',
        'updated_at',
    )
    search_fields = ('name', 'address')
    list_filter = ('category', 'is_active')
    readonly_fields = ('pin_is_set', 'pin_updated_at', 'created_at', 'updated_at')
    inlines = (StoreMenuInline,)
    actions = ('issue_owner_link',)
    fieldsets = (
        (
            '공개 매장 정보',
            {
                'fields': (
                    'name',
                    'category',
                    'address',
                    'business_hours',
                    'image_url',
                    'story_title',
                    'story',
                    'story_image_url',
                    'story_after_image',
                    'map_url',
                    'instagram_url',
                    'naver_url',
                ),
            },
        ),
        (
            '내부 운영 정보',
            {
                'fields': ('promotion_context', 'is_active'),
                'description': '내부 운영 메모는 운영자만 확인하며 공개 응답에 포함하지 않습니다.',
            },
        ),
        (
            '매장 PIN',
            {
                'fields': (
                    'usage_pin',
                    'usage_pin_confirmation',
                    'pin_is_set',
                    'pin_updated_at',
                ),
            },
        ),
        (
            '시스템 정보',
            {
                'fields': ('created_at', 'updated_at'),
                'classes': ('collapse',),
            },
        ),
    )

    @admin.display(boolean=True, description='PIN 설정 여부')
    def pin_is_set(self, obj):
        return bool(obj and obj.has_usage_pin)

    @admin.action(description='선택한 매장의 Owner Secret Link 발급·재발급', permissions=['issue_owner_link'])
    def issue_owner_link(self, request, queryset):
        return _issue_owner_link_response(
            self,
            request,
            queryset,
            store_getter=lambda store: store,
            action_name='issue_owner_link',
        )

    def has_issue_owner_link_permission(self, request):
        return (
            self.has_change_permission(request)
            and request.user.has_perm('stores.add_storeaccesstoken')
            and request.user.has_perm('stores.change_storeaccesstoken')
        )


@admin.register(StoreMenu)
class StoreMenuAdmin(admin.ModelAdmin):
    list_display = ('id', 'store', 'name', 'price', 'sort_order')
    fields = ('store', 'name', 'description', 'price', 'image_url', 'sort_order')
    search_fields = ('name', 'store__name')
    list_filter = ('store',)
    list_select_related = ('store',)
    ordering = ('sort_order', 'id')


@admin.register(StoreAccessToken)
class StoreAccessTokenAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'store',
        'is_active',
        'is_expired',
        'expires_at',
        'created_at',
        'last_used_at',
    )
    list_filter = ('is_active', 'store')
    search_fields = ('store__name',)
    list_select_related = ('store',)
    fields = ('store', 'is_active', 'is_expired', 'expires_at', 'created_at', 'last_used_at')
    readonly_fields = fields
    actions = ('rotate_owner_link', 'revoke_tokens')

    def has_add_permission(self, request):
        # raw token을 거치지 않는 직접 row 생성을 막습니다.
        return False

    def has_delete_permission(self, request, obj=None):
        # 감사 이력을 남기기 위해 삭제 대신 비활성화합니다.
        return False

    @admin.display(boolean=True, description='만료 여부')
    def is_expired(self, obj):
        return obj.expires_at is not None and timezone.now() >= obj.expires_at

    @admin.action(description='선택한 토큰의 매장에 새 Secret Link 재발급', permissions=['rotate_owner_link'])
    def rotate_owner_link(self, request, queryset):
        return _issue_owner_link_response(
            self,
            request,
            queryset.select_related('store'),
            store_getter=lambda access_token: access_token.store,
            action_name='rotate_owner_link',
        )

    def has_rotate_owner_link_permission(self, request):
        return (
            self.has_change_permission(request)
            and request.user.has_perm('stores.add_storeaccesstoken')
        )

    @admin.action(description='선택한 Owner Token 비활성화', permissions=['change'])
    def revoke_tokens(self, request, queryset):
        revoked = sum(revoke_store_access_token(access_token) for access_token in queryset)
        self.message_user(request, f'{revoked}개의 토큰을 비활성화했습니다.', level=messages.SUCCESS)
