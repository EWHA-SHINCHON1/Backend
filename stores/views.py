from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import AllowAny

from .models import Store
from .serializers import StorePublicDetailSerializer


class StorePublicDetailView(RetrieveAPIView):
    serializer_class = StorePublicDetailSerializer
    permission_classes = [AllowAny]
    lookup_url_kwarg = 'store_id'

    def get_queryset(self):
        return Store.objects.filter(is_active=True).prefetch_related('menus')
