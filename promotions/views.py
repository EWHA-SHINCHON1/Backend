from django.db import IntegrityError, models, transaction
from django.db.models import Case, Count, F, OuterRef, Q, Subquery, Value, When
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from coupons.models import Coupon
from stores.models import Store

from .models import Promotion, PromotionEvent
from .serializers import (
    PromotionEventInputSerializer,
    PromotionEventSerializer,
    PromotionPublicDetailSerializer,
    PromotionPublicListSerializer,
)
from .throttles import PromotionEventIPThrottle


class PromotionEventConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_code = 'EVENT_ID_CONFLICT'
    default_detail = '이미 다른 이벤트에 사용된 event_id입니다.'


class PromotionPublicPagination(PageNumberPagination):
    page_size = 20


class PromotionPublicQueryMixin:
    permission_classes = [AllowAny]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.now = timezone.now()

    def public_queryset(self):
        return (
            Promotion.objects.filter(is_published=True, store__is_active=True)
            .select_related('store')
            .annotate(api_issued_count=Count('coupons'))
        )

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'now': self.now}


class PromotionPublicListView(PromotionPublicQueryMixin, ListAPIView):
    serializer_class = PromotionPublicListSerializer
    pagination_class = PromotionPublicPagination

    def get_queryset(self):
        queryset = self.public_queryset()
        params = self.request.query_params

        category = params.get('category')
        if category is not None:
            categories = {value.lower(): value for value, _label in Store.Category.choices}
            if category not in categories:
                self._invalid('category')
            queryset = queryset.filter(store__category=categories[category])

        status_value = params.get('status')
        if status_value is not None:
            if status_value not in Promotion.Status.values:
                self._invalid('status')
            queryset = self._filter_status(queryset, status_value)

        closing_today = params.get('closing_today')
        if closing_today is not None:
            closing_today = self._boolean('closing_today', closing_today)
            lookup = {'ends_at__date': timezone.localdate(self.now)}
            queryset = queryset.filter(**lookup) if closing_today else queryset.exclude(**lookup)

        period = params.get('period')
        if period is not None:
            if period not in {'all', 'upcoming', 'ongoing', 'ended'}:
                self._invalid('period')
            if period == 'upcoming':
                queryset = queryset.filter(starts_at__gt=self.now)
            elif period == 'ongoing':
                queryset = queryset.filter(starts_at__lte=self.now, ends_at__gt=self.now)
            elif period == 'ended':
                queryset = queryset.filter(ends_at__lte=self.now)

        featured = params.get('featured')
        if featured is not None:
            featured = self._boolean('featured', featured)
            queryset = queryset.filter(featured_rank__isnull=not featured)

        query = params.get('q')
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query)
                | Q(description__icontains=query)
                | Q(store__name__icontains=query)
            )

        ordering = params.get('ordering')
        if ordering is not None and ordering not in {'latest', 'ending_soon', 'featured'}:
            self._invalid('ordering')
        return self._order(queryset, ordering or 'featured')

    def _filter_status(self, queryset, status_value):
        if status_value == Promotion.Status.HIDDEN:
            return queryset.none()
        if status_value == Promotion.Status.UPCOMING:
            return queryset.filter(starts_at__gt=self.now)
        if status_value == Promotion.Status.ENDED:
            return queryset.filter(ends_at__lte=self.now)

        in_period = Q(starts_at__lte=self.now, ends_at__gt=self.now)
        if status_value == Promotion.Status.SOLD_OUT:
            return queryset.filter(
                in_period,
                requires_coupon=True,
                api_issued_count__gte=F('total_quantity'),
            )
        return queryset.filter(in_period).filter(
            Q(requires_coupon=False) | Q(api_issued_count__lt=F('total_quantity'))
        )

    def _order(self, queryset, ordering):
        if ordering == 'latest':
            return queryset.order_by('-created_at', '-id')
        if ordering == 'ending_soon':
            return queryset.annotate(
                api_has_ended=Case(
                    When(ends_at__lte=self.now, then=Value(1)),
                    default=Value(0),
                    output_field=models.IntegerField(),
                )
            ).order_by('api_has_ended', 'ends_at', '-created_at', '-id')
        return queryset.order_by(F('featured_rank').asc(nulls_last=True), '-created_at', '-id')

    def _boolean(self, name, value):
        if value not in {'true', 'false'}:
            self._invalid(name)
        return value == 'true'

    def _invalid(self, name):
        raise ValidationError({name: ['허용되지 않은 값입니다.']})


class PromotionPublicDetailView(PromotionPublicQueryMixin, RetrieveAPIView):
    serializer_class = PromotionPublicDetailSerializer
    lookup_url_kwarg = 'promotion_id'

    def get_queryset(self):
        queryset = self.public_queryset().prefetch_related('store__menus')
        if self.request.user.is_authenticated:
            coupon_id = Coupon.objects.filter(
                user=self.request.user,
                promotion_id=OuterRef('pk'),
            ).values('id')[:1]
            return queryset.annotate(
                api_my_coupon_id=Subquery(coupon_id, output_field=models.UUIDField())
            )
        return queryset.annotate(
            api_my_coupon_id=Value(None, output_field=models.UUIDField())
        )


class PromotionEventCreateView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PromotionEventIPThrottle]

    EVENT_TYPE_MAP = {
        'view': PromotionEvent.EventType.VIEW,
        'channel_click': PromotionEvent.EventType.CHANNEL_CLICK,
    }

    def post(self, request, promotion_id):
        promotion = Promotion.objects.filter(
            pk=promotion_id,
            is_published=True,
            store__is_active=True,
        ).first()
        if promotion is None:
            raise NotFound
        input_serializer = PromotionEventInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data
        event_values = {
            'id': data['event_id'],
            'promotion': promotion,
            'event_type': self.EVENT_TYPE_MAP[data['event_type']],
            'channel': data['channel'],
        }

        try:
            with transaction.atomic():
                event = PromotionEvent.objects.create(**event_values)
            created = True
        except IntegrityError:
            event = PromotionEvent.objects.filter(pk=data['event_id']).first()
            if event is None:
                raise
            created = False

        if not created and (
            event.promotion_id != promotion.id
            or event.event_type != event_values['event_type']
            or event.channel != event_values['channel']
        ):
            raise PromotionEventConflict

        return Response(
            {
                'created': created,
                'event': PromotionEventSerializer(event).data,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
