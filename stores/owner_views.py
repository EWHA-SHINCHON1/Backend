from django.db.models import Count, Q
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from coupons.models import Coupon
from promotions.models import Promotion, PromotionEvent

from .authentication import OwnerTokenAuthentication
from .owner_serializers import (
    OwnerDashboardPromotionSerializer,
    OwnerPromotionMetricsSerializer,
    OwnerStatsPromotionSerializer,
    OwnerStoreSummarySerializer,
)
from .permissions import IsOwnerStore


@method_decorator(never_cache, name='dispatch')
class OwnerAPIView(APIView):
    authentication_classes = [OwnerTokenAuthentication]
    permission_classes = [IsOwnerStore]


class OwnerDashboardView(OwnerAPIView):
    def get(self, request):
        now = timezone.now()
        promotions = (
            Promotion.objects.filter(store=request.user.store)
            .annotate(owner_issued_count=Count('coupons'))
            .order_by('-created_at', '-id')
        )
        return Response({
            'store': OwnerStoreSummarySerializer(request.user.store).data,
            'promotions': OwnerDashboardPromotionSerializer(
                promotions,
                many=True,
                context={'now': now},
            ).data,
        })


class OwnerPromotionStatsView(OwnerAPIView):
    def get(self, request, promotion_id):
        promotion = (
            Promotion.objects.filter(
                pk=promotion_id,
                store=request.user.store,
            )
            .select_related('store')
            .first()
        )
        if promotion is None:
            raise NotFound

        event_counts = PromotionEvent.objects.filter(promotion=promotion).aggregate(
            views=Count('id', filter=Q(event_type=PromotionEvent.EventType.VIEW)),
            channel_clicks=Count(
                'id',
                filter=Q(event_type=PromotionEvent.EventType.CHANNEL_CLICK),
            ),
            instagram=Count(
                'id',
                filter=Q(
                    event_type=PromotionEvent.EventType.CHANNEL_CLICK,
                    channel=PromotionEvent.Channel.INSTAGRAM,
                ),
            ),
            naver=Count(
                'id',
                filter=Q(
                    event_type=PromotionEvent.EventType.CHANNEL_CLICK,
                    channel=PromotionEvent.Channel.NAVER,
                ),
            ),
        )

        if promotion.requires_coupon:
            coupon_counts = Coupon.objects.filter(promotion=promotion).aggregate(
                issued=Count('id'),
                used=Count('id', filter=Q(used_at__isnull=False)),
            )
            coupons_issued = coupon_counts['issued']
            coupons_used = coupon_counts['used']
            usage_rate_percent = (
                round(coupons_used / coupons_issued * 100, 1)
                if coupons_issued
                else 0.0
            )
        else:
            coupons_issued = None
            coupons_used = None
            usage_rate_percent = None

        metrics = {
            'views': event_counts['views'],
            'channel_clicks': event_counts['channel_clicks'],
            'channel_clicks_by_channel': {
                'instagram': event_counts['instagram'],
                'naver': event_counts['naver'],
            },
            'coupons_issued': coupons_issued,
            'coupons_used': coupons_used,
            'usage_rate_percent': usage_rate_percent,
        }
        return Response({
            'promotion': OwnerStatsPromotionSerializer(promotion).data,
            'stats': OwnerPromotionMetricsSerializer(metrics).data,
        })
