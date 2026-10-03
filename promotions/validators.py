from django.core.exceptions import ValidationError


PROTECTED_AFTER_ISSUE_FIELDS = (
    'store',
    'requires_coupon',
    'title',
    'description',
    'benefit',
    'terms',
    'starts_at',
    'ends_at',
    'redeem_until',
)

PROTECTED_FIELD_MESSAGES = {
    'store': '쿠폰이 발급된 프로모션의 매장은 변경할 수 없습니다.',
    'requires_coupon': '쿠폰이 발급된 프로모션의 유형은 변경할 수 없습니다.',
    'title': '쿠폰이 발급된 프로모션의 제목은 변경할 수 없습니다.',
    'description': '쿠폰이 발급된 프로모션의 소개는 변경할 수 없습니다.',
    'benefit': '쿠폰이 발급된 프로모션의 혜택은 변경할 수 없습니다.',
    'terms': '쿠폰이 발급된 프로모션의 이용 조건은 변경할 수 없습니다.',
    'starts_at': '쿠폰이 발급된 프로모션의 시작 시각은 변경할 수 없습니다.',
    'ends_at': '쿠폰이 발급된 프로모션의 종료 시각은 변경할 수 없습니다.',
    'redeem_until': '쿠폰이 발급된 프로모션의 쿠폰 사용 종료 시각은 변경할 수 없습니다.',
}


def validate_promotion_configuration(
    *,
    starts_at,
    ends_at,
    requires_coupon,
    redeem_until,
    total_quantity,
):
    """Promotion 필드 조합을 DB 제약과 같은 규칙으로 검증합니다."""
    errors = {}

    if starts_at is not None and ends_at is not None and starts_at >= ends_at:
        errors['ends_at'] = ['종료 시각은 시작 시각보다 늦어야 합니다.']

    if requires_coupon:
        if redeem_until is None:
            errors['redeem_until'] = ['쿠폰형 프로모션은 쿠폰 사용 종료 시각이 필요합니다.']
        elif ends_at is not None and ends_at > redeem_until:
            errors['redeem_until'] = ['쿠폰 사용 종료 시각은 프로모션 종료 시각보다 빠를 수 없습니다.']

        if total_quantity is None:
            errors['total_quantity'] = ['쿠폰형 프로모션은 총 발급 수량이 필요합니다.']
        elif total_quantity <= 0:
            errors['total_quantity'] = ['총 발급 수량은 1 이상이어야 합니다.']
    else:
        if redeem_until is not None:
            errors['redeem_until'] = ['비쿠폰형 프로모션의 쿠폰 사용 종료 시각은 비어 있어야 합니다.']
        if total_quantity is not None:
            errors['total_quantity'] = ['비쿠폰형 프로모션의 총 발급 수량은 비어 있어야 합니다.']

    if errors:
        raise ValidationError(errors)


def validate_promotion_update(*, original, proposed, issued_count):
    """쿠폰 발급 이후 변경할 수 없는 약속을 검증합니다.

    ``proposed``는 변경 후 값을 담은 mapping입니다. 호출자는 동시 발급과의 경쟁을
    막아야 한다면 원본 Promotion 행을 잠근 뒤 발급량을 계산해야 합니다.
    """
    if issued_count <= 0:
        return

    errors = {}
    for field_name in PROTECTED_AFTER_ISSUE_FIELDS:
        if field_name not in proposed:
            continue
        proposed_value = proposed[field_name]
        if field_name == 'store':
            proposed_value = getattr(proposed_value, 'pk', proposed_value)
            original_value = original.store_id
        else:
            original_value = getattr(original, field_name)
        if proposed_value != original_value:
            errors[field_name] = [PROTECTED_FIELD_MESSAGES[field_name]]

    if 'total_quantity' in proposed:
        proposed_quantity = proposed['total_quantity']
        original_quantity = original.total_quantity
        if (
            original_quantity is not None
            and (proposed_quantity is None or proposed_quantity < original_quantity)
        ):
            errors['total_quantity'] = [
                '쿠폰이 발급된 프로모션의 총 발급 수량은 줄일 수 없습니다.'
            ]

    if errors:
        raise ValidationError(errors)
