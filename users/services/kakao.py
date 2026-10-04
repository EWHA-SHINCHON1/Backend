"""카카오 로그인 연동.

- 카카오 API 호출(토큰 교환, 사용자 조회)과 우리 서비스 User 연결을 담당합니다.
- 카카오 토큰은 사용자 식별에만 쓰고 저장하지 않습니다. 우리 서비스 인증은 Django 세션으로 합니다.
- 인가 코드·토큰 원문은 로그에 남기지 않습니다.

공식 문서: https://developers.kakao.com/docs/ko/kakaologin/rest-api
"""

import logging
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from users.models import SocialAccount

logger = logging.getLogger(__name__)

AUTHORIZE_URL = 'https://kauth.kakao.com/oauth/authorize'
TOKEN_URL = 'https://kauth.kakao.com/oauth/token'
USER_INFO_URL = 'https://kapi.kakao.com/v2/user/me'

# (연결 timeout, 응답 timeout) 초
REQUEST_TIMEOUT = (3, 5)

NICKNAME_MAX_LENGTH = 50


class KakaoLoginError(Exception):
    """카카오 연동 실패. code는 프론트로 전달하는 오류 코드입니다."""

    def __init__(self, code, log_message=''):
        super().__init__(log_message or code)
        self.code = code


@dataclass(frozen=True)
class KakaoUser:
    id: str
    nickname: str


def is_configured():
    """카카오 로그인에 필요한 설정이 모두 있는지 확인합니다."""
    if not settings.KAKAO_REST_API_KEY or not settings.KAKAO_REDIRECT_URI:
        return False
    if settings.KAKAO_CLIENT_SECRET_ENABLED and not settings.KAKAO_CLIENT_SECRET:
        return False
    return True


def build_authorize_url(state):
    query = urlencode({
        'client_id': settings.KAKAO_REST_API_KEY,
        'redirect_uri': settings.KAKAO_REDIRECT_URI,
        'response_type': 'code',
        'state': state,
    })
    return f'{AUTHORIZE_URL}?{query}'


def _parse_json(response, step):
    try:
        return response.json()
    except ValueError:
        raise KakaoLoginError('KAKAO_AUTH_FAILED', f'{step}: invalid JSON (status={response.status_code})')


def exchange_code_for_token(code):
    """인가 코드를 카카오 access token으로 교환합니다.

    인가 코드는 한 번만 쓸 수 있으므로 실패해도 재시도하지 않습니다.
    """
    data = {
        'grant_type': 'authorization_code',
        'client_id': settings.KAKAO_REST_API_KEY,
        'redirect_uri': settings.KAKAO_REDIRECT_URI,
        'code': code,
    }
    if settings.KAKAO_CLIENT_SECRET_ENABLED:
        data['client_secret'] = settings.KAKAO_CLIENT_SECRET

    try:
        response = requests.post(
            TOKEN_URL,
            data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded;charset=utf-8'},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise KakaoLoginError('KAKAO_AUTH_FAILED', f'token request failed: {type(exc).__name__}')

    body = _parse_json(response, 'token')
    if response.status_code != 200:
        # 카카오 오류 코드(KOE...)만 기록합니다.
        error_code = body.get('error_code', '') if isinstance(body, dict) else ''
        raise KakaoLoginError(
            'KAKAO_AUTH_FAILED', f'token request status={response.status_code} error_code={error_code}'
        )
    access_token = body.get('access_token') if isinstance(body, dict) else None
    if not access_token or not isinstance(access_token, str):
        raise KakaoLoginError('KAKAO_AUTH_FAILED', 'token response has no access_token')
    return access_token


def fetch_user(access_token):
    """카카오 사용자 정보를 조회합니다. 닉네임은 동의하지 않았으면 빈 문자열입니다."""
    try:
        response = requests.get(
            USER_INFO_URL,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise KakaoLoginError('KAKAO_AUTH_FAILED', f'user info request failed: {type(exc).__name__}')

    body = _parse_json(response, 'user info')
    if response.status_code != 200:
        raise KakaoLoginError('KAKAO_AUTH_FAILED', f'user info status={response.status_code}')
    if not isinstance(body, dict):
        raise KakaoLoginError('KAKAO_AUTH_FAILED', 'user info is not an object')

    kakao_id = body.get('id')
    # bool은 int의 하위 타입이므로 따로 제외합니다.
    if not isinstance(kakao_id, int) or isinstance(kakao_id, bool) or kakao_id <= 0:
        raise KakaoLoginError('KAKAO_AUTH_FAILED', 'user info has no valid id')

    account = body.get('kakao_account')
    profile = account.get('profile') if isinstance(account, dict) else None
    nickname = profile.get('nickname') if isinstance(profile, dict) else None
    if not isinstance(nickname, str):
        nickname = ''

    return KakaoUser(id=str(kakao_id), nickname=nickname.strip()[:NICKNAME_MAX_LENGTH])


def _find_user(provider_user_id):
    account = (
        SocialAccount.objects.select_related('user')
        .filter(provider=SocialAccount.Provider.KAKAO, provider_user_id=provider_user_id)
        .first()
    )
    return account.user if account else None


def get_or_create_user(kakao_user):
    """카카오 회원번호로 User를 찾고, 없으면 User와 SocialAccount를 함께 만듭니다.

    동시에 같은 카카오 계정으로 첫 로그인이 들어오면 한쪽의 SocialAccount 생성이
    unique 제약에 걸립니다. 이때 트랜잭션 전체가 롤백되어 User가 고아로 남지 않고,
    먼저 만들어진 계정을 다시 조회해 사용합니다.
    """
    user = _find_user(kakao_user.id)
    if user is not None:
        if not user.nickname and kakao_user.nickname:
            # 비어 있을 때만 채우고, 기존 닉네임은 덮어쓰지 않습니다.
            user.nickname = kakao_user.nickname
            user.save(update_fields=['nickname'])
        return user

    User = get_user_model()
    try:
        with transaction.atomic():
            # username은 로그인에 쓰지 않는 내부 랜덤값입니다. (닉네임·이메일로 식별하지 않음)
            user = User(username=f'kakao_{uuid.uuid4().hex}', nickname=kakao_user.nickname)
            user.set_unusable_password()
            user.save()
            SocialAccount.objects.create(
                user=user,
                provider=SocialAccount.Provider.KAKAO,
                provider_user_id=kakao_user.id,
            )
        return user
    except IntegrityError:
        user = _find_user(kakao_user.id)
        if user is None:
            raise
        return user
