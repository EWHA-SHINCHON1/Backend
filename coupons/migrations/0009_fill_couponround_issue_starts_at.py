from datetime import datetime, time, timedelta

from django.db import migrations
from django.utils import timezone


def fill_issue_starts_at(apps, schema_editor):
    """기존 발급분은 발급일(round_date) 00:00(Asia/Seoul)부터 열린 것으로 채워 지금까지와 같게 동작시킵니다.

    사용 마감이 자정 정각이라 발급일 00:00과 같으면, 발급 시작 < 사용 마감이 되도록 하루 앞당깁니다.
    """
    CouponRound = apps.get_model('coupons', 'CouponRound')
    for coupon_round in CouponRound.objects.filter(issue_starts_at__isnull=True):
        starts_at = timezone.make_aware(datetime.combine(coupon_round.round_date, time.min))
        if starts_at >= coupon_round.expires_at:
            starts_at = coupon_round.expires_at - timedelta(days=1)
        coupon_round.issue_starts_at = starts_at
        coupon_round.save(update_fields=['issue_starts_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('coupons', '0008_couponround_issue_starts_at'),
    ]

    operations = [
        migrations.RunPython(fill_issue_starts_at, migrations.RunPython.noop),
    ]
