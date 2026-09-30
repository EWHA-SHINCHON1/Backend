import logging
import re
import secrets
import time
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth import login
from django.http import HttpResponseRedirect
from django.views.decorators.http import require_GET

from users.services import kakao

logger = logging.getLogger(__name__)

# 로그인 시도 정보를 세션에 보관하는 키와 유효시간
KAKAO_LOGIN_SESSION_KEY = 'kakao_login'
KAKAO_STATE_TTL_SECONDS = 10 * 60

# 로그인 후 돌아갈 수 있는 프론트 경로 (서버가 허용한 것만)
# fullmatch로 비교합니다. ($는 끝의 줄바꿈을 허용하므로 쓰지 않음)
ALLOWED_NEXT_PATTERNS = [
    re.compile(r'/'),
    re.compile(r'/promotions/[1-9]\d{0,17}/?'),
]
DEFAULT_NEXT = '/'


def get_safe_next_path(value):
    """허용된 프론트 경로만 돌려주고, 그 외(외부 URL, //host, 알 수 없는 경로)는 기본 경로로 바꿉니다."""
    if isinstance(value, str) and any(p.fullmatch(value) for p in ALLOWED_NEXT_PATTERNS):
        return value
    return DEFAULT_NEXT


def _frontend_redirect(path):
    return HttpResponseRedirect(f'{settings.FRONTEND_BASE_URL}{path}')


def _login_failed(code):
    return _frontend_redirect(f'/login?{urlencode({"error": code})}')


@require_GET
def kakao_start(request):
    """카카오 로그인 시작: state를 만들어 세션에 보관하고 카카오 인가 화면으로 보냅니다.

    query: next (선택) — 로그인 후 돌아갈 프론트 경로. 예: /promotions/12
    """
    if not kakao.is_configured():
        logger.error('Kakao login is not configured (check KAKAO_* settings).')
        return _login_failed('KAKAO_NOT_CONFIGURED')

    state = secrets.token_urlsafe(32)
    request.session[KAKAO_LOGIN_SESSION_KEY] = {
        'state': state,
        'next': get_safe_next_path(request.GET.get('next')),
        'created_at': time.time(),
    }
    return HttpResponseRedirect(kakao.build_authorize_url(state))


@require_GET
def kakao_callback(request):
    """카카오 인가 후 돌아오는 주소. 사용자 연결·세션 로그인 후 프론트로 이동합니다.

    쿠폰은 발급하지 않습니다. (쿠폰 발급은 프론트의 별도 POST 요청)
    """
    # 한 번 쓴 state는 재사용할 수 없도록 먼저 꺼내서 지웁니다.
    pending = request.session.pop(KAKAO_LOGIN_SESSION_KEY, None)

    if request.GET.get('error'):
        if request.GET['error'] == 'access_denied':
            return _login_failed('LOGIN_CANCELLED')
        logger.warning('Kakao authorize returned error=%s', request.GET['error'][:50])
        return _login_failed('KAKAO_AUTH_FAILED')

    state = request.GET.get('state')
    if (
        not isinstance(pending, dict)
        or not state
        or not secrets.compare_digest(str(pending.get('state', '')), state)
    ):
        return _login_failed('INVALID_STATE')
    if time.time() - pending.get('created_at', 0) > KAKAO_STATE_TTL_SECONDS:
        return _login_failed('INVALID_STATE')

    code = request.GET.get('code')
    if not code:
        return _login_failed('INVALID_REQUEST')

    # 외부 HTTP 호출은 사용자 생성 트랜잭션 밖에서 합니다.
    try:
        access_token = kakao.exchange_code_for_token(code)
        kakao_user = kakao.fetch_user(access_token)
    except kakao.KakaoLoginError as exc:
        logger.warning('Kakao login failed: %s', exc)
        return _login_failed(exc.code)

    user = kakao.get_or_create_user(kakao_user)
    if not user.is_active:
        return _login_failed('INACTIVE_USER')

    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    return _frontend_redirect(get_safe_next_path(pending.get('next')))
