from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponseNotAllowed, JsonResponse
from django.forms.models import BaseInlineFormSet
from django.urls import path, reverse
from django.utils import timezone

from coupons.models import Coupon, CouponRound
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


class CouponRoundInlineFormSet(BaseInlineFormSet):
    """쿠폰 발급분 입력 검증.

    - Admin change view는 PromotionAdminForm.clean()에서 Promotion 행을 잠근 뒤 이 검증을 실행하므로
      발급 수 확인과 저장 사이에 같은 프로모션의 쿠폰 발급이 끼어들지 않습니다.
    - self.instance에는 같은 요청에서 입력한 프로모션 값(기간·사용 기한)이 반영되어 있습니다.
    - 한 프로모션의 발급분 기간(발급 시작 ~ 쿠폰 사용 마감)은 서로 겹칠 수 없습니다.
    - 모든 발급분의 사용 마감은 프로모션의 쿠폰 사용 종료(최종 한도)를 넘을 수 없습니다.
    - 쿠폰이 발급된 발급분은 기간 변경·발급 수 미만으로 수량 축소를 막습니다. (사용자와의 약속 보호)
      삭제는 Coupon.round = PROTECT를 보고 Django Admin이 막습니다.
    - 발급 시작 < 사용 마감은 CouponRound의 CheckConstraint가 폼 검증에서 막습니다.
    """

    def clean(self):
        super().clean()
        promotion = self.instance
        now = timezone.now()
        periods = []

        for form in self.forms:
            if not hasattr(form, 'cleaned_data'):
                continue
            coupon_round = form.instance
            is_existing = coupon_round.pk is not None
            if not is_existing and not form.has_changed():
                continue
            # 쿠폰이 연결된 발급분의 삭제는 Django Admin이 PROTECT 관계를 보고 거절합니다.
            if self.can_delete and self._should_delete_form(form):
                continue
            issued_count = coupon_round.coupons.count() if is_existing else 0

            if not promotion.requires_coupon:
                form.add_error(None, '비쿠폰형 프로모션에는 쿠폰 발급분을 둘 수 없습니다.')
                continue

            starts_at = form.cleaned_data.get('issue_starts_at')
            expires_at = form.cleaned_data.get('expires_at')
            quantity = form.cleaned_data.get('quantity')
            if starts_at is None or expires_at is None or starts_at >= expires_at:
                continue

            if quantity is not None and quantity < issued_count:
                form.add_error(
                    'quantity',
                    f'이미 {issued_count}장이 발급되어 그보다 적게 줄일 수 없습니다.',
                )

            if promotion.redeem_until is not None and expires_at > promotion.redeem_until:
                form.add_error('expires_at', '프로모션의 쿠폰 사용 종료 시각보다 늦을 수 없습니다.')

            changed = [name for name in ('issue_starts_at', 'expires_at') if name in form.changed_data]
            if changed:
                if issued_count:
                    for name in changed:
                        form.add_error(name, '쿠폰이 발급된 발급분의 기간은 변경할 수 없습니다.')
                elif expires_at <= now:
                    form.add_error('expires_at', '이미 지난 시각으로는 발급분을 만들 수 없습니다.')
                elif promotion.starts_at is not None and promotion.ends_at is not None and not (
                    starts_at < promotion.ends_at and expires_at > promotion.starts_at
                ):
                    form.add_error('issue_starts_at', '프로모션 기간과 겹치지 않아 발급이 열리지 않습니다.')

            periods.append((starts_at, expires_at, form))

        periods.sort(key=lambda period: period[0])
        for (_, previous_expires_at, previous_form), (starts_at, _, form) in zip(periods, periods[1:]):
            if starts_at < previous_expires_at:
                previous = previous_form.cleaned_data
                form.add_error(
                    'issue_starts_at',
                    '다른 발급분({} ~ {})과 기간이 겹칩니다.'.format(
                        timezone.localtime(previous['issue_starts_at']).strftime('%m/%d %H:%M'),
                        timezone.localtime(previous['expires_at']).strftime('%m/%d %H:%M'),
                    ),
                )


def _rate(numerator, denominator):
    """사용률(%) 표시. 분모가 0이면 0.0%"""
    return f'{numerator / denominator * 100:.1f}%' if denominator else '0.0%'


def _coupon_stat_annotations(now):
    """프로모션 목록용 쿠폰 통계. 발급분 합계와 쿠폰 수를 각각 Subquery로 구해 JOIN 중복 집계를 피합니다.

    - 뿌린 양: 발급분 quantity 합계 / 가져간 양: with_coupon_state()의 coupon_issued_count
    - 실제 사용·미사용 만료·사용 대기: 쿠폰의 used_at·expires_at 기준 (가져간 양 = 세 값의 합)
    - 안 가져가고 소멸: 마감된 발급분의 quantity 합계 - 그 발급분들에서 나간 쿠폰 수
      (발급분 수량을 넘겨 발급하지 않고, Admin도 발급 수 미만으로 줄일 수 없으므로 발급분별로 음수가 되지 않음)
    """

    def coupon_count(**filters):
        coupons = (
            Coupon.objects.filter(promotion=OuterRef('pk'), **filters)
            .order_by()
            .values('promotion')
            .annotate(count=Count('pk'))
            .values('count')
        )
        return Coalesce(Subquery(coupons), 0)

    def round_quantity(**filters):
        rounds = (
            CouponRound.objects.filter(promotion=OuterRef('pk'), **filters)
            .order_by()
            .values('promotion')
            .annotate(total=Sum('quantity'))
            .values('total')
        )
        return Coalesce(Subquery(rounds), 0)

    return {
        'stat_distributed': round_quantity(),
        'stat_used': coupon_count(used_at__isnull=False),
        'stat_expired': coupon_count(used_at__isnull=True, expires_at__lte=now),
        'stat_waiting': coupon_count(used_at__isnull=True, expires_at__gt=now),
        'stat_closed_quantity': round_quantity(expires_at__lte=now),
        'stat_closed_issued': coupon_count(round__expires_at__lte=now),
    }


class CouponRoundInline(admin.TabularInline):
    model = CouponRound
    formset = CouponRoundInlineFormSet
    fields = (
        'issue_starts_at',
        'expires_at',
        'quantity',
        'issued_count',
        'used_count',
        'expired_count',
        'waiting_count',
        'lapsed_count',
        'usage_rate_of_issued',
        'usage_rate_of_quantity',
    )
    readonly_fields = fields[3:]
    extra = 0
    verbose_name = '쿠폰 발급분'
    verbose_name_plural = '쿠폰 발급분 (발급 시작부터 쿠폰 사용 마감까지 발급, 기간은 서로 겹칠 수 없음)'

    def get_queryset(self, request):
        self.stats_now = timezone.now()
        return super().get_queryset(request).annotate(
            stat_issued=Count('coupons'),
            stat_used=Count('coupons', filter=Q(coupons__used_at__isnull=False)),
            stat_expired=Count(
                'coupons',
                filter=Q(coupons__used_at__isnull=True, coupons__expires_at__lte=self.stats_now),
            ),
            stat_waiting=Count(
                'coupons',
                filter=Q(coupons__used_at__isnull=True, coupons__expires_at__gt=self.stats_now),
            ),
        )

    @staticmethod
    def _stat(obj, name):
        """저장 전 새 행이거나 통계가 붙지 않은 객체는 None"""
        if obj is None or obj.pk is None:
            return None
        return getattr(obj, name, None)

    @admin.display(description='가져간 양')
    def issued_count(self, obj):
        value = self._stat(obj, 'stat_issued')
        return '-' if value is None else value

    @admin.display(description='실제 사용')
    def used_count(self, obj):
        value = self._stat(obj, 'stat_used')
        return '-' if value is None else value

    @admin.display(description='미사용 만료')
    def expired_count(self, obj):
        value = self._stat(obj, 'stat_expired')
        return '-' if value is None else value

    @admin.display(description='사용 대기')
    def waiting_count(self, obj):
        value = self._stat(obj, 'stat_waiting')
        return '-' if value is None else value

    @admin.display(description='안 가져가고 소멸')
    def lapsed_count(self, obj):
        issued = self._stat(obj, 'stat_issued')
        if issued is None or obj.expires_at > getattr(self, 'stats_now', timezone.now()):
            return '-'
        return max(obj.quantity - issued, 0)

    @admin.display(description='사용률(받은 대비)')
    def usage_rate_of_issued(self, obj):
        issued = self._stat(obj, 'stat_issued')
        return '-' if issued is None else _rate(obj.stat_used, issued)

    @admin.display(description='사용률(뿌린 대비)')
    def usage_rate_of_quantity(self, obj):
        if self._stat(obj, 'stat_used') is None:
            return '-'
        return _rate(obj.stat_used, obj.quantity)


def _draft_json(data, *, status=200):
    response = JsonResponse(data, status=status)
    response['Cache-Control'] = 'no-store'
    return response


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    form = PromotionAdminForm
    inlines = (CouponRoundInline,)
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
        'distributed_count',
        'issued_count',
        'used_count',
        'expired_count',
        'waiting_count',
        'lapsed_count',
        'usage_rate_of_issued',
        'usage_rate_of_distributed',
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
        now = timezone.now()
        return (
            super().get_queryset(request)
            .with_coupon_state(now)
            .annotate(**_coupon_stat_annotations(now))
        )

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
        status = obj.get_status()
        return Promotion.Status(status).label

    # 쿠폰 통계 (행사 전체 누적). 비쿠폰형은 '-'

    @staticmethod
    def _coupon_stat(obj, value):
        return value if obj.requires_coupon else '-'

    @admin.display(description='뿌린 양')
    def distributed_count(self, obj):
        return self._coupon_stat(obj, obj.stat_distributed)

    @admin.display(description='가져간 양')
    def issued_count(self, obj):
        return self._coupon_stat(obj, obj.coupon_issued_count)

    @admin.display(description='실제 사용')
    def used_count(self, obj):
        return self._coupon_stat(obj, obj.stat_used)

    @admin.display(description='미사용 만료')
    def expired_count(self, obj):
        return self._coupon_stat(obj, obj.stat_expired)

    @admin.display(description='사용 대기')
    def waiting_count(self, obj):
        return self._coupon_stat(obj, obj.stat_waiting)

    @admin.display(description='안 가져가고 소멸')
    def lapsed_count(self, obj):
        return self._coupon_stat(obj, max(obj.stat_closed_quantity - obj.stat_closed_issued, 0))

    @admin.display(description='사용률(받은 대비)')
    def usage_rate_of_issued(self, obj):
        return self._coupon_stat(obj, _rate(obj.stat_used, obj.coupon_issued_count))

    @admin.display(description='사용률(뿌린 대비)')
    def usage_rate_of_distributed(self, obj):
        return self._coupon_stat(obj, _rate(obj.stat_used, obj.stat_distributed))
