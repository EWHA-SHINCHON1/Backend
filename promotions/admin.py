from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponseNotAllowed, JsonResponse
from django.urls import path, reverse

from .models import Promotion
from .services import LinerDraftError, generate_promotion_draft
from .validators import validate_promotion_configuration, validate_promotion_update
from stores.models import Store


class PromotionAdminForm(forms.ModelForm):
    ai_context = forms.CharField(
        label='AI 추가 문맥',
        required=False,
        max_length=300,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text=(
            'DB에는 저장되지 않으며, 입력한 경우에만 문구 초안을 위해 외부 Liner API로 전송됩니다. '
            '개인정보나 내부 비밀은 입력하지 마세요.'
        ),
    )
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


class PromotionDraftInputForm(forms.Form):
    """Admin의 미저장 입력 중 초안 생성에 필요한 값만 검증합니다."""

    store = forms.ModelChoiceField(queryset=Store.objects.all())
    benefit = forms.CharField(max_length=200)
    terms = forms.CharField(required=False, max_length=2000)
    starts_at = forms.SplitDateTimeField()
    ends_at = forms.SplitDateTimeField()
    requires_coupon = forms.BooleanField(required=False)
    redeem_until = forms.SplitDateTimeField(required=False)
    total_quantity = forms.IntegerField(required=False)
    ai_context = forms.CharField(required=False, max_length=300)

    def clean(self):
        cleaned_data = super().clean()
        requires_coupon = cleaned_data.get('requires_coupon', False)
        if not requires_coupon:
            cleaned_data['redeem_until'] = None
            cleaned_data['total_quantity'] = None

        try:
            validate_promotion_configuration(
                starts_at=cleaned_data.get('starts_at'),
                ends_at=cleaned_data.get('ends_at'),
                requires_coupon=requires_coupon,
                redeem_until=cleaned_data.get('redeem_until'),
                total_quantity=cleaned_data.get('total_quantity'),
            )
        except ValidationError as exc:
            for field_name, messages in exc.message_dict.items():
                for message in messages:
                    self.add_error(field_name, message)
        return cleaned_data


def _draft_json(data, *, status=200):
    response = JsonResponse(data, status=status)
    response['Cache-Control'] = 'no-store'
    return response


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    form = PromotionAdminForm
    change_form_template = 'admin/promotions/promotion/change_form.html'
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
            'AI 문구 초안',
            {
                'fields': ('ai_context',),
                'description': '입력값을 저장하지 않고 제목·소개 초안 생성 요청에만 사용합니다.',
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

    def get_urls(self):
        custom_urls = [
            path(
                'generate-draft/',
                self.admin_site.admin_view(self.generate_draft_view),
                name='promotions_promotion_generate_draft',
            ),
        ]
        return custom_urls + super().get_urls()

    def render_change_form(
        self,
        request,
        context,
        add=False,
        change=False,
        form_url='',
        obj=None,
    ):
        context.update(
            liner_draft_url=reverse('admin:promotions_promotion_generate_draft'),
            liner_draft_object_id='' if obj is None else obj.pk,
            liner_draft_disabled=bool(obj and obj.coupons.exists()),
        )
        return super().render_change_form(
            request,
            context,
            add=add,
            change=change,
            form_url=form_url,
            obj=obj,
        )

    def generate_draft_view(self, request):
        if request.method != 'POST':
            return HttpResponseNotAllowed(['POST'])

        object_id = request.POST.get('object_id', '').strip()
        promotion = None
        if object_id:
            promotion = self.get_object(request, object_id)
            if promotion is None:
                return _draft_json(
                    {'error': {'code': 'NOT_FOUND', 'message': '프로모션을 찾을 수 없습니다.'}},
                    status=404,
                )
            if not self.has_change_permission(request, promotion):
                return _draft_json(
                    {'error': {'code': 'PERMISSION_DENIED', 'message': '변경 권한이 없습니다.'}},
                    status=403,
                )
            if promotion.coupons.exists():
                return _draft_json(
                    {
                        'error': {
                            'code': 'COUPON_ALREADY_ISSUED',
                            'message': '쿠폰 발급 이력이 있는 프로모션은 AI 초안을 생성할 수 없습니다.',
                        }
                    },
                    status=409,
                )
        elif not self.has_add_permission(request):
            return _draft_json(
                {'error': {'code': 'PERMISSION_DENIED', 'message': '등록 권한이 없습니다.'}},
                status=403,
            )

        form = PromotionDraftInputForm(request.POST)
        if not form.is_valid():
            details = {
                field_name: [str(message) for message in messages]
                for field_name, messages in form.errors.items()
            }
            return _draft_json(
                {
                    'error': {
                        'code': 'INVALID_DRAFT_INPUT',
                        'message': '초안 생성 입력값을 확인해 주세요.',
                        'details': details,
                    }
                },
                status=400,
            )

        cleaned_data = form.cleaned_data
        promotion_data = {
            field_name: cleaned_data.get(field_name)
            for field_name in (
                'benefit',
                'terms',
                'starts_at',
                'ends_at',
                'requires_coupon',
                'redeem_until',
                'total_quantity',
            )
        }
        try:
            draft = generate_promotion_draft(
                store=cleaned_data['store'],
                promotion_data=promotion_data,
                ai_context=cleaned_data.get('ai_context', ''),
            )
        except LinerDraftError as exc:
            return _draft_json(
                {'error': {'code': exc.code, 'message': exc.user_message}},
                status=503,
            )

        return _draft_json(
            {'draft': {'title': draft.title, 'description': draft.description}},
        )

    class Media:
        js = ('promotions/promotion_draft.js',)

    @admin.display(description='현재 상태')
    def current_status(self, obj):
        status = obj.get_status(issued_count=getattr(obj, 'admin_issued_count', None))
        return Promotion.Status(status).label
