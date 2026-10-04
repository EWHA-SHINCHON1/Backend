from django.urls import path

from coupons import views

app_name = 'coupons'

# /api/v1/
urlpatterns = [
    path('promotions/<int:promotion_id>/coupons/', views.PromotionCouponIssueView.as_view(), name='issue'),
]
