from django.urls import path

from users import views

app_name = 'users'

# /api/v1/auth/
urlpatterns = [
    path('kakao/start/', views.kakao_start, name='kakao_start'),
    path('kakao/callback/', views.kakao_callback, name='kakao_callback'),
    path('me/', views.MeView.as_view(), name='me'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
]
