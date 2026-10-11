"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from config.health import health
from promotions.views import SavedPromotionListView

api_v1_patterns = [
    path('auth/', include('users.urls')),
    path('promotions/', include('promotions.urls')),
    path('stores/', include('stores.urls')),
    path('owner/', include('stores.owner_urls')),
    path('me/saved-promotions/', SavedPromotionListView.as_view(), name='saved_promotions'),
    path('', include('coupons.urls')),
]

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include(api_v1_patterns)),
    path("health/", health, name="health"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
