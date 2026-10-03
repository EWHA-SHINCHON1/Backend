# Backend
신촌SW창업경진대회 백엔드 레포지토리

## 개발 환경

- Python 3.11
- Django 5.2 (LTS) + Django REST framework + Simple JWT
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
│   ├── migrations/
│   └── tests.py         # 사용자 모델·JWT 인증 테스트
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
| `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` | | `30` | Access 토큰 유효 시간(분) |
| `JWT_REFRESH_TOKEN_LIFETIME_DAYS` | | `7` | Refresh 토큰 유효 기간(일) |
| `DB_NAME` | O | - | PostgreSQL 데이터베이스 이름 |
| `DB_USER` | O | - | PostgreSQL 사용자 |
| `DB_PASSWORD` | O | - | PostgreSQL 비밀번호 |
| `DB_HOST` | | `localhost` | PostgreSQL 호스트 |
| `DB_PORT` | | `5432` | PostgreSQL 포트 |

이미 설정된 OS 환경변수가 `.env` 값보다 우선합니다.

## Django 기본 설정

- **언어·시간대**: `ko-kr`, `Asia/Seoul`, `USE_TZ=True` (DB에는 UTC로 저장)
- **날짜·숫자 형식**: `config/formats/ko/formats.py`에서 지정합니다. 날짜는 `Y-m-d`, 숫자는 천 단위 쉼표(`1,234,567`)를 씁니다.
- **API 날짜 형식**: DRF 응답은 ISO 8601 형식입니다. (`2026-09-26T14:30:00+0900`)
- **Media**: 업로드 파일은 `media/`에 저장되고 `/media/` URL로 제공됩니다. 개발 서버에서는 `DEBUG=True`일 때만 제공합니다.
- **사용자 모델**: `users.User` (`AbstractUser` 상속, `AUTH_USER_MODEL = 'users.User'`)
- **DRF**: JWT 인증이 기본입니다. 기본 권한은 `IsAuthenticated`이므로 공개 API는 뷰에서 `AllowAny`로 따로 지정해야 합니다.

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
python manage.py runserver        # http://127.0.0.1:8000
```

관리자 페이지는 http://127.0.0.1:8000/admin/ 입니다.

## 테스트

```bash
python manage.py check   # 설정 검사
python manage.py test    # 전체 테스트
```

테스트는 PostgreSQL에 임시 DB(`test_<DB_NAME>`)를 만들어 실행하고, 끝나면 삭제합니다. 개발 DB 데이터에는 영향이 없습니다.
```
