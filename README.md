# Backend
신촌SW창업경진대회 백엔드 레포지토리

## 개발 환경

- Python 3.11

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
| djangorestframework-simplejwt | 5.5.x | JWT 인증 |
| django-cors-headers | 4.9.x | CORS 설정 |
| django-environ | 0.14.x | `.env` 환경변수 로딩 |
| psycopg[binary] | 3.3.x | PostgreSQL 드라이버 |

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
├── users/               # 사용자 앱 (커스텀 User 모델)
├── manage.py
├── requirements.txt
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
| `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` | | `30` | Access 토큰 유효 시간(분) |
| `JWT_REFRESH_TOKEN_LIFETIME_DAYS` | | `7` | Refresh 토큰 유효 기간(일) |

이미 설정된 OS 환경변수가 `.env` 값보다 우선합니다.

## Django 기본 설정

- **언어·시간대**: `ko-kr`, `Asia/Seoul`, `USE_TZ=True` (DB에는 UTC로 저장)
- **날짜·숫자 형식**: `config/formats/ko/formats.py`에서 지정합니다. 날짜는 `Y-m-d`, 숫자는 천 단위 쉼표(`1,234,567`)를 씁니다.
- **API 날짜 형식**: DRF 응답은 ISO 8601 형식입니다. (`2026-09-26T14:30:00+0900`)
- **Media**: 업로드 파일은 `media/`에 저장되고 `/media/` URL로 제공됩니다. 개발 서버에서는 `DEBUG=True`일 때만 제공합니다.
- **사용자 모델**: `users.User` (`AbstractUser` 상속, `AUTH_USER_MODEL = 'users.User'`)
- **DRF**: JWT 인증이 기본입니다. 기본 권한은 `IsAuthenticated`이므로 공개 API는 뷰에서 `AllowAny`로 따로 지정해야 합니다.

## 인증 API (JWT)

| Method | URL | 설명 |
| --- | --- | --- |
| POST | `/api/auth/token/` | `username`, `password`로 access/refresh 토큰 발급 |
| POST | `/api/auth/token/refresh/` | `refresh`로 새 access 토큰 발급 |
| POST | `/api/auth/token/verify/` | `token` 유효성 확인 |

인증이 필요한 요청에는 `Authorization: Bearer <access 토큰>` 헤더를 붙입니다.
