from django.db import migrations
from django.db.models import Count, Max
from django.utils import timezone


def backfill_coupon_rounds(apps, schema_editor):
    """발급분이 없는 기존 쿠폰을 프로모션마다 발급분 하나로 묶습니다.

    - 발급분 도입 전 쿠폰은 모두 같은 사용 기한(발급 당시 redeem_until)을 가지므로 그 값을 씁니다.
    - quantity는 이미 발급된 수로 둡니다. 이 발급분에서 추가 발급이 일어나지 않게 하기 위해서입니다.
    - 다시 실행해도 round가 비어 있는 쿠폰만 처리합니다.
    """
    Coupon = apps.get_model('coupons', 'Coupon')
    CouponRound = apps.get_model('coupons', 'CouponRound')

    rows = (
        Coupon.objects.filter(round__isnull=True)
        .values('promotion_id')
        .annotate(issued=Count('id'), expires_at=Max('expires_at'))
    )
    for row in rows:
        coupon_round = CouponRound.objects.create(
            promotion_id=row['promotion_id'],
            quantity=row['issued'],
            expires_at=row['expires_at'],
            round_date=timezone.localdate(row['expires_at']),
        )
        Coupon.objects.filter(
            promotion_id=row['promotion_id'],
            round__isnull=True,
        ).update(round=coupon_round)


def unlink_coupon_rounds(apps, schema_editor):
    Coupon = apps.get_model('coupons', 'Coupon')
    CouponRound = apps.get_model('coupons', 'CouponRound')
    Coupon.objects.update(round=None)
    CouponRound.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('coupons', '0003_coupon_round'),
    ]

    operations = [
        migrations.RunPython(backfill_coupon_rounds, unlink_coupon_rounds),
    ]
