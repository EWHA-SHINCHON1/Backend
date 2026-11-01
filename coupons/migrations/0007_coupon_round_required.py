import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('coupons', '0006_attach_remaining_coupons'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='coupon',
            name='uniq_coupon_user_promotion',
        ),
        migrations.AlterField(
            model_name='coupon',
            name='round',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='coupons',
                to='coupons.couponround',
                verbose_name='발급분',
            ),
        ),
        migrations.AddConstraint(
            model_name='coupon',
            constraint=models.UniqueConstraint(fields=('user', 'round'), name='uniq_coupon_user_round'),
        ),
    ]
