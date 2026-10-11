from django.urls import path

from .views import (
    PromotionBookmarkView,
    PromotionEventCreateView,
    PromotionPublicDetailView,
    PromotionPublicListView,
)


app_name = 'promotions'

urlpatterns = [
    path('', PromotionPublicListView.as_view(), name='list'),
    path('<int:promotion_id>/bookmark/', PromotionBookmarkView.as_view(), name='bookmark'),
    path('<int:promotion_id>/events/', PromotionEventCreateView.as_view(), name='event_create'),
    path('<int:promotion_id>/', PromotionPublicDetailView.as_view(), name='detail'),
]
