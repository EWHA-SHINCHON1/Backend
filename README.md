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
