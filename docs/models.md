# 모델 계약 (핵심 8개)

Store/Promotion 공개 API, Admin, 점주 인증·성과, Liner 기능을 구현할 때 참고하는 모델 명세입니다.
모델 필드·관계·제약을 바꿔야 하면 migration 충돌을 막기 위해 먼저 공유해 주세요.

## 앱 구성

| 앱 라벨 | 모델 | 테이블 |
| --- | --- | --- |
| `users` | `User`, `SocialAccount` | `users`, `social_accounts` |
| `stores` | `Store`, `StoreMenu`, `StoreAccessToken` | `stores`, `store_menus`, `store_access_tokens` |
| `promotions` | `Promotion`, `PromotionEvent` | `promotions`, `promotion_events` |
| `coupons` | `Coupon` | `coupons` |

User는 `settings.AUTH_USER_MODEL` 또는 `get_user_model()`로 참조합니다.

## 관계와 삭제 정책

| 부모 | 자식 (FK 필드) | related_name | on_delete |
| --- | --- | --- | --- |
| User | SocialAccount.user | `social_accounts` | CASCADE |
| User | Coupon.user | `coupons` | PROTECT |
| Store | StoreMenu.store | `menus` | CASCADE |
| Store | StoreAccessToken.store | `access_tokens` | CASCADE |
| Store | Promotion.store | `promotions` | PROTECT |
| Promotion | Coupon.promotion | `coupons` | PROTECT |
| Promotion | PromotionEvent.promotion | `events` | PROTECT |

- PROTECT: 자식이 있으면 부모를 삭제할 수 없습니다(`ProtectedError`). 쿠폰·성과 기록 보존을 위해 **삭제 대신 `is_active=False` / `is_published=False`로 운영**합니다.
- PROTECT는 부모 삭제만 막습니다. 자식(예: Coupon) 자체의 삭제는 막지 않습니다.

## Migration 적용 순서

```
users.0001 → stores.0001 → promotions.0001 → coupons.0001 → users.0002
```

`python manage.py migrate`가 의존성에 따라 자동으로 이 순서로 적용합니다.

---

## users

### User (`AbstractUser` 상속)

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `nickname` | CharField(50) | 빈 값 허용, 기본 `''` |
| (상속) | | `id`, `username`, `password`, `is_active`, `is_staff`, `is_superuser`, `date_joined`, `last_login` 등 |

- 소비자는 카카오 로그인으로만 가입합니다. `username`은 내부 랜덤값이며 화면 표시에는 `nickname`을 씁니다.
- 카카오 가입 사용자는 비밀번호가 없고(`has_usable_password() == False`), 관리자 권한도 없습니다.

### SocialAccount

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `user` | FK → User | CASCADE |
| `provider` | CharField(20) | `kakao` |
| `provider_user_id` | CharField(64) | 카카오 회원번호(문자열) |
| `created_at` | DateTime | 자동 |

제약: `UNIQUE(provider, provider_user_id)`, `UNIQUE(user, provider)`

## stores

### Store

| 필드 | 타입 | 필수 | 공개 |
| --- | --- | --- | --- |
| `name` | CharField(100) | O | O |
| `category` | CharField(20) | O | O |
| `address` | CharField(255) | O | O |
| `business_hours` | Text | | O |
| `image_url` | URL(500) | | O |
| `story` | Text | | O |
| `map_url`, `instagram_url`, `naver_url` | URL(500) | | O |
| `is_active` | Bool, 기본 True | | |
| `created_at`, `updated_at` | DateTime | 자동 | |
| `promotion_context` | Text | | ❌ 내부 전용 (Liner 입력용) |
| `usage_pin_hash` | CharField(128) | | ❌ 비공개 |
| `pin_updated_at` | DateTime, null | | ❌ 비공개 |

- `category` 값: `BAKERY_CAFE`, `RESTAURANT` (DB는 **대문자**). API에서 소문자로 보여주려면 serializer에서 명시적으로 변환하세요.
- 선택 필드는 빈 문자열 `''`이 기본값입니다(null 아님).

**PIN 메서드**

```python
store.set_usage_pin('0428')      # 해시로 저장 준비 + pin_updated_at 갱신. 이후 store.save() 필요
store.check_usage_pin('0428')    # True / False
store.has_usage_pin              # PIN 설정 여부
```

- PIN은 **문자열만** 받습니다(앞자리 0 보존). 숫자 타입이면 `ValueError`.
- 원문은 저장하지 않고 Django `make_password` 해시로 저장합니다.
- PIN 미설정 매장(`usage_pin_hash == ''`)은 어떤 입력도 통과하지 않습니다. 기본 PIN은 없습니다.
- PIN 자릿수·형식 제한은 없습니다. PIN 설정 기능에서 정해 주세요.

### StoreMenu

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `store` | FK → Store | CASCADE |
| `name` | CharField(100) | |
| `price` | PositiveInteger | 원 단위, **필수**, DB `CHECK (price >= 0)` |
| `sort_order` | PositiveInteger | 기본 0 |

기본 정렬: `sort_order`, `id`

### StoreAccessToken (점주 대시보드 전용)

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `store` | FK → Store | CASCADE |
| `token_hash` | CharField(64) | **UNIQUE**. 제안 형식: SHA-256 hex |
| `is_active` | Bool | 기본 True |
| `created_at` | DateTime | 자동 |
| `expires_at` | DateTime, null | |
| `last_used_at` | DateTime, null | |

- 원문 토큰 컬럼은 없습니다. 토큰 생성·해시·검증 로직은 점주 인증 기능에서 구현합니다.
- 조회 방식이 `token_hash`로 바로 찾는 구조라면, 매 요청마다 salt가 달라지는 `make_password`는 쓸 수 없습니다. SHA-256 같은 결정적 해시를 권장합니다.
- 소비자 인증이나 쿠폰 PIN과는 관계없습니다.

## promotions

### Promotion

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `store` | FK → Store | PROTECT |
| `title` | CharField(200) | 필수 |
| `description` | Text | Liner 초안 가능 |
| `benefit` | CharField(200) | 운영자 확정 값 |
| `terms` | Text | 운영자 확정 값 |
| `image_url` | URL(500) | |
| `starts_at` | DateTime | 신규 발급 시작 |
| `ends_at` | DateTime | 신규 발급 종료 |
| `redeem_until` | DateTime | 쿠폰 사용 종료 기준 |
| `total_quantity` | PositiveInteger | 총 발급 수량 |
| `is_published` | Bool | **기본 False** (검수 후 게시) |
| `featured_rank` | PositiveInteger, null | 추천 노출 순위 |
| `created_at`, `updated_at` | DateTime | 자동 |

DB 제약: `starts_at < ends_at`, `ends_at <= redeem_until`, `total_quantity > 0`

**계산값 (저장하지 않음)**

```python
promotion.get_status()             # 'hidden' | 'upcoming' | 'ended' | 'sold_out' | 'active'
promotion.get_status(issued_count=n)  # 발급 수를 미리 구했으면 전달 (추가 쿼리 없음)
promotion.issued_count             # 발급된 쿠폰 수 (COUNT 쿼리)
promotion.remaining_quantity       # max(total_quantity - issued_count, 0)
promotion.is_in_issue_period()     # starts_at <= now < ends_at
```

상태 우선순위: `hidden`(비공개) → `upcoming`(now < starts_at) → `ended`(now >= ends_at) → `sold_out`(발급 수 >= 총 수량) → `active`

- 상태값은 **소문자**입니다.
- 목록 API에서 프로모션마다 `issued_count`를 부르면 N+1 쿼리가 됩니다. `annotate(issued=Count('coupons'))` 후 `get_status(issued_count=p.issued)`로 넘기세요.
- 발급 기간이 끝나도 이미 발급된 쿠폰은 자신의 `expires_at`까지 사용할 수 있습니다.

### PromotionEvent

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `id` | UUID PK | **클라이언트 `event_id`를 그대로 저장** |
| `promotion` | FK → Promotion | PROTECT |
| `event_type` | CharField(20) | `VIEW`, `CHANNEL_CLICK` (대문자) |
| `channel` | CharField(20) | `''`, `instagram`, `naver` (소문자) |
| `created_at` | DateTime | 자동 |

- DB 제약: `VIEW`는 `channel=''`만, `CHANNEL_CLICK`은 `instagram`/`naver`만 허용합니다.
- 인덱스: `(promotion, created_at)` — 기간별 집계용
- VIEW는 순방문자가 아니라 페이지 조회 횟수입니다.
- 재시도 중복 방지: 클라이언트가 보낸 `event_id`를 `id`로 저장하면 같은 요청 재전송 시 PK 충돌(IntegrityError)이 납니다. 이벤트 API에서 이를 잡아 이미 기록된 요청으로 처리하면 됩니다.

## coupons

### Coupon

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `id` | UUID PK | 기본값 `uuid.uuid4` |
| `user` | FK → User | PROTECT |
| `promotion` | FK → Promotion | PROTECT |
| `issued_at` | DateTime | 기본 현재 시각 |
| `expires_at` | DateTime | 발급 당시 `Promotion.redeem_until` 복사 |
| `used_at` | DateTime, null | 미사용이면 NULL |

- 제약: `UNIQUE(user, promotion)` — 한 사용자는 프로모션당 쿠폰 1장
- `pin`, `status`, `redeemed_by` 컬럼은 없습니다. 사용 PIN은 `Store.check_usage_pin()`으로 검증합니다.
- Promotion의 `redeem_until`을 수정해도 이미 발급된 쿠폰의 `expires_at`은 바뀌지 않습니다.

```python
coupon.get_status()   # 'used' | 'expired' | 'available'
```

상태: `used_at`이 있으면 `used` → `now >= expires_at`이면 `expired` → 그 외 `available`

---

## 공개 응답 주의사항

- 공개 API에 `usage_pin_hash`, `pin_updated_at`, `promotion_context`, `token_hash`, 점주 접근 토큰을 포함하지 마세요.
- Serializer는 필요한 필드를 명시하고 `fields = "__all__"`을 쓰지 마세요.
- 시각은 DB에 UTC로 저장되고, API 응답은 `Asia/Seoul` 기준 ISO 8601(`+0900`)로 나갑니다. 비교에는 `django.utils.timezone.now()`를 쓰세요.
