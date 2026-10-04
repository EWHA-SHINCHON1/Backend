from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count

from .models import Promotion
from .validators import validate_promotion_configuration, validate_promotion_update


class PromotionAdminForm(forms.ModelForm):
    total_quantity = forms.IntegerField(
        label='총 쿠폰 발급 수량',
        required=False,
        help_text='쿠폰형은 1 이상이어야 합니다. 비쿠폰형은 비워 두세요.',
    )

    class Meta:
        model = Promotion
        fields = '__all__'

    def clean(self):
        cleaned_data = super().clean()
        requires_coupon = cleaned_data.get('requires_coupon', False)

        # 비쿠폰형에 남아 있는 입력값은 저장하지 않아 DB 계약을 일관되게 유지합니다.
        if not requires_coupon:
            cleaned_data['redeem_until'] = None
            cleaned_data['total_quantity'] = None

        self._add_validation_errors(
            validate_promotion_configuration,
            starts_at=cleaned_data.get('starts_at'),
            ends_at=cleaned_data.get('ends_at'),
            requires_coupon=requires_coupon,
            redeem_until=cleaned_data.get('redeem_until'),
            total_quantity=cleaned_data.get('total_quantity'),
        )

        if self.instance.pk:
            queryset = Promotion.objects
            # Admin의 POST change view는 transaction.atomic() 안에서 실행됩니다.
            # 행 잠금으로 검증부터 저장까지 동시 쿠폰 발급과의 경쟁을 줄입니다.
            if transaction.get_connection().in_atomic_block:
                queryset = queryset.select_for_update()
            original = queryset.get(pk=self.instance.pk)
            self._add_validation_errors(
                validate_promotion_update,
                original=original,
                proposed=cleaned_data,
                issued_count=original.coupons.count(),
            )

        return cleaned_data

    def _add_validation_errors(self, validator, **kwargs):
        try:
            validator(**kwargs)
        except ValidationError as exc:
            for field_name, messages in exc.message_dict.items():
                for message in messages:
                    self.add_error(field_name, message)


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    form = PromotionAdminForm
    list_display = (
        'id',
        'title',
        'store',
        'requires_coupon',
        'current_status',
        'starts_at',
        'ends_at',
        'is_published',
        'featured_rank',
    )
    search_fields = ('title', 'store__name')
    list_filter = ('store', 'requires_coupon', 'is_published')
    list_select_related = ('store',)
    autocomplete_fields = ('store',)
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (
            '기본 정보',
            {
                'fields': (
                    'store',
                    'title',
                    'description',
                    'benefit',
                    'terms',
                    'image_url',
                ),
            },
        ),
        (
            '운영 정보',
            {'fields': ('starts_at', 'ends_at', 'requires_coupon')},
        ),
        (
            '쿠폰 정보',
            {
                'fields': ('redeem_until', 'total_quantity'),
                'description': '비쿠폰형으로 저장하면 두 값은 자동으로 비워집니다.',
            },
        ),
        (
            '노출 정보',
            {'fields': ('is_published', 'featured_rank')},
        ),
        (
            '시스템 정보',
            {
                'fields': ('created_at', 'updated_at'),
                'classes': ('collapse',),
            },
        ),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(admin_issued_count=Count('coupons'))

    @admin.display(description='현재 상태')
    def current_status(self, obj):
        status = obj.get_status(issued_count=getattr(obj, 'admin_issued_count', None))
        return Promotion.Status(status).label
