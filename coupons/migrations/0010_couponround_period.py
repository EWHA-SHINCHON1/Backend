from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('coupons', '0009_fill_couponround_issue_starts_at'),
    ]

    operations = [
        migrations.AlterField(
            model_name='couponround',
            name='issue_starts_at',
            field=models.DateTimeField(verbose_name='발급 시작'),
        ),
        migrations.RemoveConstraint(
            model_name='couponround',
            name='uniq_coupon_round_promotion_date',
        ),
        migrations.RemoveField(
            model_name='couponround',
            name='round_date',
        ),
        migrations.AlterModelOptions(
            name='couponround',
            options={
                'ordering': ['issue_starts_at', 'id'],
                'verbose_name': '쿠폰 발급분',
                'verbose_name_plural': '쿠폰 발급분',
            },
        ),
        migrations.AddConstraint(
            model_name='couponround',
            constraint=models.CheckConstraint(
                condition=models.Q(issue_starts_at__lt=models.F('expires_at')),
                name='coupon_round_starts_before_expires',
                violation_error_message='발급 시작은 쿠폰 사용 마감보다 빨라야 합니다.',
            ),
        ),
    ]
