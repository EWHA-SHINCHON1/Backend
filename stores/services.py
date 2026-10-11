import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .models import Store, StoreAccessToken


TOKEN_BYTES = 32
TOKEN_LIFETIME = timedelta(days=60)
LAST_USED_UPDATE_INTERVAL = timedelta(minutes=5)
TOKEN_CREATE_MAX_ATTEMPTS = 5


class InvalidStoreAccessToken(Exception):
    """Owner token이 유효하지 않을 때 세부 사유를 노출하지 않고 사용하는 예외."""


class StoreAccessTokenStateChanged(Exception):
    """The active-token state changed after the admin confirmation page."""


@dataclass(frozen=True)
class IssuedStoreAccessToken:
    raw_token: str
    access_token: StoreAccessToken


def generate_raw_token():
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_raw_token(raw_token):
    if not isinstance(raw_token, str) or not raw_token:
        raise ValueError('토큰은 비어 있지 않은 문자열이어야 합니다.')
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


@transaction.atomic
def issue_store_access_token(*, store, now=None, expected_active_token_ids=None):
    """Store의 기존 활성 토큰을 폐기하고 새 raw token을 한 번만 반환합니다."""
    if store.pk is None:
        raise ValueError('저장된 매장에만 토큰을 발급할 수 있습니다.')

    now = now or timezone.now()
    locked_store = Store.objects.select_for_update().get(pk=store.pk)

    if expected_active_token_ids is not None:
        current_active_token_ids = list(
            StoreAccessToken.objects.filter(store=locked_store, is_active=True)
            .order_by('pk')
            .values_list('pk', flat=True)
        )
        if current_active_token_ids != sorted(expected_active_token_ids):
            raise StoreAccessTokenStateChanged

    StoreAccessToken.objects.filter(store=locked_store, is_active=True).update(is_active=False)

    for _attempt in range(TOKEN_CREATE_MAX_ATTEMPTS):
        raw_token = generate_raw_token()
        token_hash = hash_raw_token(raw_token)
        try:
            # unique 충돌로 트랜잭션이 깨져도 다음 난수로 재시도할 수 있도록 savepoint를 둡니다.
            with transaction.atomic():
                access_token = StoreAccessToken.objects.create(
                    store=locked_store,
                    token_hash=token_hash,
                    expires_at=now + TOKEN_LIFETIME,
                )
        except IntegrityError:
            continue
        return IssuedStoreAccessToken(raw_token=raw_token, access_token=access_token)

    raise RuntimeError('고유한 Store access token을 생성하지 못했습니다.')


@transaction.atomic
def validate_store_access_token(raw_token, *, now=None):
    """raw token을 검증하고 성공 시 StoreAccessToken을 반환합니다."""
    try:
        token_hash = hash_raw_token(raw_token)
    except ValueError as exc:
        raise InvalidStoreAccessToken from exc

    now = now or timezone.now()
    try:
        access_token = (
            StoreAccessToken.objects.select_for_update()
            .select_related('store')
            .get(token_hash=token_hash)
        )
    except StoreAccessToken.DoesNotExist as exc:
        raise InvalidStoreAccessToken from exc

    if (
        not access_token.is_active
        or not access_token.store.is_active
        or (access_token.expires_at is not None and now >= access_token.expires_at)
    ):
        raise InvalidStoreAccessToken

    last_used_cutoff = now - LAST_USED_UPDATE_INTERVAL
    if access_token.last_used_at is None or access_token.last_used_at <= last_used_cutoff:
        updated = (
            StoreAccessToken.objects.filter(pk=access_token.pk, is_active=True)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .update(last_used_at=now)
        )
        if updated:
            access_token.last_used_at = now

    return access_token


def revoke_store_access_token(access_token):
    """원본 row는 보존하고 즉시 비활성화합니다."""
    if access_token.pk is None:
        return False
    updated = StoreAccessToken.objects.filter(pk=access_token.pk, is_active=True).update(is_active=False)
    if updated:
        access_token.is_active = False
    return bool(updated)
