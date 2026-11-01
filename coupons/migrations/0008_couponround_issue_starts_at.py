from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('coupons', '0007_coupon_round_required'),
    ]

    operations = [
        migrations.AddField(
            model_name='couponround',
            name='issue_starts_at',
            field=models.DateTimeField(null=True, verbose_name='발급 시작'),
        ),
    ]
