from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0009_inbound_eracun_rejection'),
    ]

    operations = [
        migrations.AddField(
            model_name='inboundgatewaydocumentlink',
            name='rejection_capability_checked',
            field=models.BooleanField(
                default=False,
                help_text='True once provider_capabilities returned a definitive answer.',
            ),
        ),
        migrations.AlterField(
            model_name='inboundgatewaydocumentlink',
            name='gateway_document_id',
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
    ]
