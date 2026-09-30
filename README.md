# Backend
신촌SW창업경진대회 백엔드 레포지토리

## 개발 환경

- Python 3.11
- Django 5.2 (LTS) + Django REST framework
- 인증: 카카오 로그인 + Django 세션 쿠키
- PostgreSQL 17

## 빠른 시작

처음 받은 뒤 아래 순서대로 실행합니다. 자세한 설명은 각 섹션을 참고하세요.

```powershell
# 1. 가상환경 생성·활성화
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 환경변수 파일 생성 후 SECRET_KEY, DB_PASSWORD 입력
Copy-Item .env.example .env

# 4. 로컬 PostgreSQL 시작 (Docker Desktop 실행 필요)
docker compose up -d

# 5. 마이그레이션 적용 및 서버 실행
python manage.py migrate
python manage.py runserver
```

## 가상환경

프로젝트 루트에 `.venv` 가상환경을 사용합니다. `.venv` 폴더는 Git에 올리지 않습니다.

### 생성

```bash
# Windows
py -3.11 -m venv .venv

# macOS / Linux
python3.11 -m venv .venv
```

### 활성화

```bash
# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# Windows (cmd)
.venv\Scripts\activate.bat

# Windows (Git Bash)
source .venv/Scripts/activate

# macOS / Linux
source .venv/bin/activate
```

PowerShell에서 스크립트 실행이 차단되면 아래 명령을 한 번 실행한 뒤 다시 활성화하세요.

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### 비활성화

```bash
deactivate
```

## 의존성 설치

가상환경을 활성화한 상태에서 실행합니다.

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 주요 패키지

| 패키지 | 버전 | 용도 |
| --- | --- | --- |
| Django | 5.2.x (LTS) | 웹 프레임워크 |
| djangorestframework | 3.18.x | REST API |
| django-cors-headers | 4.9.x | CORS 설정 |
| django-environ | 0.14.x | `.env` 환경변수 로딩 |
| psycopg[binary] | 3.3.x | PostgreSQL 드라이버 |
| requests | 2.34.x | 카카오 API 호출 |

### 패키지 추가 시

```bash
pip install <패키지명>
pip freeze --exclude pip > requirements.txt
```

Windows PowerShell 5.1에서는 `>` 리다이렉트가 UTF-16으로 저장되므로 아래처럼 인코딩을 지정하세요.

```powershell
pip freeze --exclude pip | Out-File -Encoding ascii requirements.txt
```

`requirements.txt` 변경 사항도 함께 커밋해 주세요.

## 프로젝트 구조

```
.
├── config/              # Django 프로젝트 설정
│   ├── formats/ko/      # 한국어 날짜·숫자 형식
│   ├── settings.py
│   └── urls.py
├── users/               # User, SocialAccount, 인증
│   ├── migrations/
│   └── tests/
├── stores/              # Store, StoreMenu, StoreAccessToken
├── promotions/          # Promotion, PromotionEvent
├── coupons/             # Coupon
├── manage.py
├── requirements.txt
├── docker-compose.yml   # 로컬 개발용 PostgreSQL
└── .env.example         # 환경변수 예시
```

## 환경변수

`.env.example`을 복사해 프로젝트 루트에 `.env`를 만들고 값을 채웁니다. `.env`는 Git에 올리지 않습니다.

```bash
# Windows (PowerShell)
Copy-Item .env.example .env

# macOS / Linux / Git Bash
cp .env.example .env
```

`SECRET_KEY`는 아래 명령으로 생성한 값을 `.env`에 넣으세요.

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

| 변수 | 필수 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `SECRET_KEY` | O | - | Django 비밀 키. 없으면 서버가 시작되지 않습니다. |
| `DEBUG` | | `False` | 로컬 개발 시 `True` |
| `ALLOWED_HOSTS` | | (없음) | 허용 호스트, 쉼표로 구분 |
| `CORS_ALLOWED_ORIGINS` | | (없음) | CORS 허용 출처, 쉼표로 구분 (예: `http://localhost:3000`) |
| `CSRF_TRUSTED_ORIGINS` | | (없음) | CSRF 신뢰 출처, 쉼표로 구분 |
| `DB_NAME` | O | - | PostgreSQL 데이터베이스 이름 |
| `DB_USER` | O | - | PostgreSQL 사용자 |
| `DB_PASSWORD` | O | - | PostgreSQL 비밀번호 |
| `DB_HOST` | | `localhost` | PostgreSQL 호스트 |
| `DB_PORT` | | `5432` | PostgreSQL 포트 |
| `SESSION_COOKIE_SECURE` | | `False` | 운영(HTTPS)에서 `True` |
| `CSRF_COOKIE_SECURE` | | `False` | 운영(HTTPS)에서 `True` |
| `CSRF_COOKIE_DOMAIN` | | (없음) | 운영에서 상위 도메인 (예: `.godgoowm.com`). 개발에서는 비움 |
| `KAKAO_REST_API_KEY` | 카카오 로그인 시 | - | 카카오 앱의 **REST API 키** |
| `KAKAO_CLIENT_SECRET` | 카카오 로그인 시 | - | 카카오 Client Secret (카카오 앱 기본값이 '사용함'이라 필수) |
| `KAKAO_CLIENT_SECRET_ENABLED` | | `True` | 콘솔에서 Client Secret을 '사용 안 함'으로 바꾼 경우에만 `False` |
| `KAKAO_REDIRECT_URI` | 카카오 로그인 시 | - | 콘솔에 등록한 Redirect URI와 **정확히** 같은 값 |
| `FRONTEND_BASE_URL` | | `http://localhost:3000` | 로그인 후 돌아갈 프론트 주소 (끝에 `/` 없이) |

이미 설정된 OS 환경변수가 `.env` 값보다 우선합니다.

## Django 기본 설정

- **언어·시간대**: `ko-kr`, `Asia/Seoul`, `USE_TZ=True` (DB에는 UTC로 저장)
- **날짜·숫자 형식**: `config/formats/ko/formats.py`에서 지정합니다. 날짜는 `Y-m-d`, 숫자는 천 단위 쉼표(`1,234,567`)를 씁니다.
- **API 날짜 형식**: DRF 응답은 ISO 8601 형식입니다. (`2026-09-26T14:30:00+0900`)
- **Media**: 업로드 파일은 `media/`에 저장되고 `/media/` URL로 제공됩니다. 개발 서버에서는 `DEBUG=True`일 때만 제공합니다.
- **사용자 모델**: `users.User` (`AbstractUser` 상속, `AUTH_USER_MODEL = 'users.User'`)
- **DRF**: 세션 인증(`SessionAuthentication`)이 기본입니다. 기본 권한은 `IsAuthenticated`이므로 공개 API는 뷰에서 `AllowAny`로 따로 지정해야 합니다.
- **API 기본 경로**: `/api/v1/`

## 로컬 PostgreSQL 준비

DB 접속 정보는 모두 `.env`의 `DB_*` 변수에서 읽습니다. 먼저 `.env`에 `DB_NAME`, `DB_USER`, `DB_PASSWORD`를 채운 뒤 아래 방법 중 하나로 DB를 준비하세요.
로컬 개발용 DB만 사용하고, 운영 DB 접속 정보는 로컬 `.env`에 넣지 마세요.

### 방법 1: Docker (권장)

Docker Desktop을 실행한 뒤 프로젝트 루트에서 아래 명령을 실행합니다. `.env`의 값으로 DB와 사용자가 자동으로 만들어집니다.

```bash
docker compose up -d        # PostgreSQL 17 컨테이너 시작
docker compose ps           # 상태 확인
docker compose down         # 중지 (데이터는 볼륨에 유지)
docker compose down -v      # 중지 + 데이터 삭제
```

컨테이너는 `127.0.0.1`에만 열려 있어 외부에서 접속할 수 없습니다.

### 방법 2: 직접 설치

1. [PostgreSQL 공식 사이트](https://www.postgresql.org/download/)에서 설치합니다. (Windows: `winget install PostgreSQL.PostgreSQL.17`)
2. `psql -U postgres`로 접속해 `.env`와 같은 값으로 사용자와 DB를 만듭니다.

```sql
CREATE USER shinchon WITH PASSWORD '<비밀번호>';
CREATE DATABASE shinchon_dev OWNER shinchon;
ALTER USER shinchon CREATEDB;  -- 테스트 실행 시 테스트 DB 생성에 필요
```

### 연결 확인

```bash
python manage.py shell -c "from django.db import connection; connection.ensure_connection(); print(connection.settings_dict['HOST'], connection.pg_version)"
```

## 마이그레이션

모델을 바꾼 뒤에는 마이그레이션 파일을 만들고 적용합니다. 마이그레이션 파일(`*/migrations/*.py`)은 커밋합니다.

```bash
python manage.py makemigrations   # 마이그레이션 파일 생성
python manage.py migrate          # DB에 적용
python manage.py showmigrations   # 적용 상태 확인
```

## 실행

```bash
python manage.py createsuperuser  # 관리자 계정 생성 (최초 1회)
python manage.py runserver        # http://localhost:8000
```

관리자 페이지는 http://localhost:8000/admin/ 입니다.

## 테스트

```bash
python manage.py check   # 설정 검사
python manage.py test    # 전체 테스트
```

테스트는 PostgreSQL에 임시 DB(`test_<DB_NAME>`)를 만들어 실행하고, 끝나면 삭제합니다. 개발 DB 데이터에는 영향이 없습니다.

## 인증 (카카오 로그인 + 세션)

### 결정 사항

| 항목 | 결정 |
| --- | --- |
| 배포 구조 | 프론트·백엔드가 같은 도메인 아래 (예: `godgoowm.com` + `api.godgoowm.com`) |
| 로그인 유지 | Django 세션 쿠키 (HttpOnly, SameSite=Lax). JWT는 사용하지 않음 |
| 소비자 가입·로그인 | 카카오 로그인만. 아이디·비밀번호 로그인 API 없음 |
| 운영자 로그인 | Django Admin (`/admin/`) |
| 로그인 성공 후 | 프론트의 원래 페이지로 복귀. 허용 경로는 `/`, `/promotions/{id}`만 |
| 로그인 실패·취소 | 프론트 `/login?error=<코드>`로 이동 (코드는 구현 시 문서화 — 제안) |
| 비로그인 `GET /api/v1/auth/me/` | `200` `{"id": null, "nickname": null, "is_authenticated": false}` |
| 로그아웃 | `POST /api/v1/auth/logout/` → `204`. 카카오 연결 해제(탈퇴)와 별개 |

### API

Base URL: `/api/v1/`

| Method | 경로 | 설명 | 상태 |
| --- | --- | --- | --- |
| GET | `auth/kakao/start/` | 카카오 로그인 시작 (브라우저 이동) | 구현 |
| GET | `auth/kakao/callback/` | 카카오가 돌려보내는 주소. 로그인 처리 후 프론트로 이동 | 구현 |
| GET | `auth/me/` | 현재 로그인 사용자 | 예정 |
| POST | `auth/logout/` | 로그아웃 | 예정 |

### 카카오 로그인 흐름

1. 프론트의 '쿠폰 받기' 등에서 브라우저를 아래 주소로 **이동**시킵니다. (fetch/axios 호출이 아니라 `window.location.href` 이동)
   ```
   {API 주소}/api/v1/auth/kakao/start/?next=/promotions/12
   ```
   - `next`: 로그인 후 돌아갈 프론트 경로. `/` 또는 `/promotions/{id}`만 허용하며, 그 외 값은 `/`로 바뀝니다.
2. 카카오 로그인·동의 화면을 거쳐 백엔드 `auth/kakao/callback/`으로 돌아옵니다.
3. 백엔드가 사용자를 확인하고 **세션 쿠키로 로그인**시킨 뒤 프론트로 이동시킵니다.
   - 성공: `{FRONTEND_BASE_URL}{next}` (예: `/promotions/12`)
   - 실패: `{FRONTEND_BASE_URL}/login?error=<오류 코드>`
4. 쿠폰은 자동으로 발급되지 않습니다. 돌아온 뒤 프론트가 쿠폰 발급 API를 따로 호출합니다.

**로그인 실패 오류 코드 (제안)**

| 코드 | 상황 |
| --- | --- |
| `LOGIN_CANCELLED` | 사용자가 카카오 로그인·동의를 취소 |
| `INVALID_STATE` | 로그인 요청 검증 실패 (만료 10분, 재사용, 다른 브라우저, 위조) → 다시 시도 |
| `INVALID_REQUEST` | 카카오 응답에 인가 코드가 없음 |
| `KAKAO_AUTH_FAILED` | 카카오 토큰·사용자 정보 조회 실패 (네트워크, 카카오 오류) |
| `INACTIVE_USER` | 비활성화된 계정 |
| `KAKAO_NOT_CONFIGURED` | 서버에 카카오 키 설정이 없음 (개발 환경 확인) |

**계정 연결 규칙**

- 카카오 회원번호로 사용자를 식별합니다. 같은 카카오 계정이면 항상 같은 사용자로 로그인됩니다.
- 처음 로그인하면 사용자를 자동으로 만듭니다. 비밀번호는 없고(`has_usable_password() == False`), 관리자 권한도 없습니다.
- 닉네임은 카카오 닉네임을 가져오며, 동의하지 않았으면 빈 값입니다. 이미 닉네임이 있으면 덮어쓰지 않습니다.
- 카카오 토큰은 저장하지 않습니다.

### 프론트에서 지켜야 할 것

- API 요청에 **쿠키를 포함**해야 합니다. (`fetch(url, { credentials: 'include' })`, axios는 `withCredentials: true`)
- POST·PUT·PATCH·DELETE 요청에는 `csrftoken` 쿠키 값을 **`X-CSRFToken` 헤더**로 보내야 합니다.
- 개발 시 프론트와 API 주소의 호스트를 맞추세요. 프론트가 `localhost:3000`이면 API도 `localhost:8000`으로 부릅니다. (`localhost`와 `127.0.0.1`은 쿠키가 따로 관리됩니다)

### 카카오 개발자 콘솔 설정

[카카오 디벨로퍼스](https://developers.kakao.com/) > 내 애플리케이션에서 설정합니다.

1. **앱 키**: `REST API 키`를 `.env`의 `KAKAO_REST_API_KEY`에 넣습니다. (JavaScript 키·Admin 키가 아님)
2. **카카오 로그인 활성화**: 제품 설정 > 카카오 로그인 > 활성화 ON
3. **Redirect URI 등록**: 아래 주소를 등록하고, `.env`의 `KAKAO_REDIRECT_URI`에 똑같이 넣습니다.
   - 개발: `http://localhost:8000/api/v1/auth/kakao/callback/`
   - 운영: `https://api.<도메인>/api/v1/auth/kakao/callback/`
4. **동의항목**: `닉네임`을 선택 동의로 설정합니다. 이메일은 필요하지 않습니다.
5. **Client Secret** (보안 메뉴): 기본값이 '사용함'입니다. 코드를 확인해 `KAKAO_CLIENT_SECRET`에 넣습니다. '사용 안 함'으로 바꿨다면 `KAKAO_CLIENT_SECRET_ENABLED=False`로 둡니다.

키와 Client Secret은 `.env`에만 넣고 커밋하거나 공유하지 마세요.

### 운영 배포 시 설정

```dotenv
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
CSRF_COOKIE_DOMAIN=.godgoowm.com
CORS_ALLOWED_ORIGINS=https://godgoowm.com
CSRF_TRUSTED_ORIGINS=https://godgoowm.com
FRONTEND_BASE_URL=https://godgoowm.com
KAKAO_REDIRECT_URI=https://api.godgoowm.com/api/v1/auth/kakao/callback/
```

(도메인은 예시입니다)
