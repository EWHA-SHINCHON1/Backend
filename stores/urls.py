from django.urls import path

from .views import StorePublicDetailView


app_name = 'stores'

urlpatterns = [
    path('<int:store_id>/', StorePublicDetailView.as_view(), name='detail'),
]
