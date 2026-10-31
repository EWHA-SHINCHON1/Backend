from django.db import migrations
from django.db.models import Count, Max
from django.utils import timezone


def attach_remaining_coupons(apps, schema_editor):
    """0004 이후 발급 로직 전환 전까지 round 없이 발급된 쿠폰을 발급분에 연결합니다.

    - 같은 프로모션·같은 날짜의 발급분이 이미 있으면(관리자가 Admin에서 만든 경우) 그 발급분에 연결합니다.
    - 없으면 0004와 같은 방식으로 발급 수만큼의 발급분을 만듭니다.
    """
    Coupon = apps.get_model('coupons', 'Coupon')
    CouponRound = apps.get_model('coupons', 'CouponRound')

    rows = (
        Coupon.objects.filter(round__isnull=True)
        .values('promotion_id')
        .annotate(issued=Count('id'), expires_at=Max('expires_at'))
    )
    for row in rows:
        round_date = timezone.localdate(row['expires_at'])
        coupon_round = CouponRound.objects.filter(
            promotion_id=row['promotion_id'],
            round_date=round_date,
        ).first()
        if coupon_round is None:
            coupon_round = CouponRound.objects.create(
                promotion_id=row['promotion_id'],
                quantity=row['issued'],
                expires_at=row['expires_at'],
                round_date=round_date,
            )
        Coupon.objects.filter(
            promotion_id=row['promotion_id'],
            round__isnull=True,
        ).update(round=coupon_round)


class Migration(migrations.Migration):

    dependencies = [
        ('coupons', '0005_coupon_round_quantity_min'),
    ]

    operations = [
        migrations.RunPython(attach_remaining_coupons, migrations.RunPython.noop),
    ]
