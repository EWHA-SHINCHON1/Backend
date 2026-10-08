from django.urls import path

from coupons import views

app_name = 'coupons'

# /api/v1/
urlpatterns = [
    path('promotions/<int:promotion_id>/coupons/', views.PromotionCouponIssueView.as_view(), name='issue'),
    path('me/coupons/', views.MyCouponListView.as_view(), name='my_coupons'),
    path('me/coupons/<uuid:coupon_id>/', views.MyCouponDetailView.as_view(), name='my_coupon_detail'),
    path('me/coupons/<uuid:coupon_id>/use/', views.MyCouponUseView.as_view(), name='my_coupon_use'),
]
