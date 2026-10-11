"""DRF 공통 오류 응답 형식.

모든 API 오류를 아래 형식으로 통일합니다.
    {"error": {"code": "AUTHENTICATION_REQUIRED", "message": "로그인이 필요합니다."}}
입력값 오류는 필드별 내용을 details에 담습니다.
"""

from rest_framework import exceptions, status
from rest_framework.views import exception_handler


def _error(code, message, details=None):
    body = {'code': code, 'message': message}
    if details is not None:
        body['details'] = details
    return {'error': body}


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, (exceptions.NotAuthenticated, exceptions.AuthenticationFailed)):
        # 세션 인증은 기본적으로 403을 주지만, 비로그인은 401로 구분합니다.
        response.status_code = status.HTTP_401_UNAUTHORIZED
        response.data = _error('AUTHENTICATION_REQUIRED', '로그인이 필요합니다.')
    elif isinstance(exc, exceptions.PermissionDenied):
        if str(exc.detail).startswith('CSRF Failed'):
            response.data = _error('CSRF_FAILED', '요청을 확인할 수 없습니다. 페이지를 새로고침한 뒤 다시 시도해 주세요.')
        else:
            response.data = _error('PERMISSION_DENIED', '권한이 없습니다.')
    elif isinstance(exc, exceptions.ValidationError):
        response.data = _error('VALIDATION_ERROR', '입력값을 확인해 주세요.', details=response.data)
    elif isinstance(exc, exceptions.NotFound):
        response.data = _error('NOT_FOUND', '요청한 대상을 찾을 수 없습니다.')
    elif isinstance(exc, exceptions.MethodNotAllowed):
        response.data = _error('METHOD_NOT_ALLOWED', '허용되지 않은 요청 방식입니다.')
    elif isinstance(exc, exceptions.Throttled):
        response.data = _error('TOO_MANY_REQUESTS', '요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.')
    elif isinstance(exc, exceptions.ParseError):
        response.data = _error('BAD_REQUEST', '요청 형식이 올바르지 않습니다.')
    else:
        code = getattr(exc, 'default_code', 'error').upper()
        # 예외가 details(dict)를 가지면 함께 내려줍니다. (예: 남은 PIN 시도 횟수)
        response.data = _error(
            code,
            str(getattr(exc, 'detail', '요청을 처리할 수 없습니다.')),
            details=getattr(exc, 'details', None),
        )
    return response
