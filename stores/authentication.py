import re
from dataclasses import dataclass

from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from .models import Store, StoreAccessToken
from .services import InvalidStoreAccessToken, validate_store_access_token


OWNER_TOKEN_PATTERN = re.compile(r'^[A-Za-z0-9_-]{43}$')


@dataclass(frozen=True)
class OwnerPrincipal:
    store: Store

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def __str__(self):
        return f'Owner:{self.store_id}'

    @property
    def store_id(self):
        return self.store.pk


class OwnerTokenAuthentication(BaseAuthentication):
    keyword = b'owner'

    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts:
            return None
        if len(parts) != 2 or parts[0].lower() != self.keyword:
            self._fail()

        try:
            raw_token = parts[1].decode('ascii')
        except UnicodeDecodeError:
            self._fail()
        if not OWNER_TOKEN_PATTERN.fullmatch(raw_token):
            self._fail()

        try:
            access_token = validate_store_access_token(raw_token)
        except InvalidStoreAccessToken:
            self._fail()

        principal = OwnerPrincipal(store=access_token.store)
        return principal, access_token

    def authenticate_header(self, request):
        return 'Owner'

    def _fail(self):
        raise AuthenticationFailed('Owner 인증에 실패했습니다.')
