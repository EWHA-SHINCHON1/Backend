from django.urls import path

from .owner_views import OwnerDashboardView, OwnerPromotionStatsView


app_name = 'owner'

urlpatterns = [
    path('dashboard/', OwnerDashboardView.as_view(), name='dashboard'),
    path(
        'promotions/<int:promotion_id>/stats/',
        OwnerPromotionStatsView.as_view(),
        name='promotion_stats',
    ),
]
