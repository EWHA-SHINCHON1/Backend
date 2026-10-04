import re

from django import forms
from django.contrib import admin

from .models import Store, StoreMenu


PIN_PATTERN = re.compile(r'^[0-9]{4}$')


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
    fields = ('name', 'price', 'sort_order')
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
                    'story',
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


@admin.register(StoreMenu)
class StoreMenuAdmin(admin.ModelAdmin):
    list_display = ('id', 'store', 'name', 'price', 'sort_order')
    search_fields = ('name', 'store__name')
    list_filter = ('store',)
    list_select_related = ('store',)
    ordering = ('sort_order', 'id')
