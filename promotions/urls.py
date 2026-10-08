from django.urls import path

from .views import PromotionPublicDetailView, PromotionPublicListView


app_name = 'promotions'

urlpatterns = [
    path('', PromotionPublicListView.as_view(), name='list'),
    path('<int:promotion_id>/', PromotionPublicDetailView.as_view(), name='detail'),
]
