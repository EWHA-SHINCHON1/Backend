from rest_framework.permissions import BasePermission

from .authentication import OwnerPrincipal
from .models import StoreAccessToken


class IsOwnerStore(BasePermission):
    message = 'Owner 인증이 필요합니다.'

    def has_permission(self, request, view):
        return (
            isinstance(request.user, OwnerPrincipal)
            and request.user.is_authenticated
            and isinstance(request.auth, StoreAccessToken)
            and request.auth.store_id == request.user.store_id
        )
