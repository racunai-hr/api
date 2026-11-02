from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0026_official_document'),
        ('tenants', '0007_create_finestar_tenant'),
    ]

    operations = [
        migrations.AlterField(
            model_name='postingrule',
            name='document_type',
            field=models.CharField(
                choices=[
                    ('invoice_issued', 'Izdani račun'),
                    ('invoice_paid', 'Naplata računa'),
                    ('expense_approved', 'Odobren trošak'),
                    ('expense_paid', 'Plaćen trošak'),
                    ('payment_manual', 'Ručno plaćanje'),
                    ('official_document_posted', 'Knjižen službeni dokument'),
                ],
                max_length=40,
                verbose_name='Tip dokumenta',
            ),
        ),
        migrations.CreateModel(
            name='OfficialDocumentPostingProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=64, verbose_name='Kod')),
                ('name', models.CharField(max_length=100, verbose_name='Naziv')),
                ('economic_effect', models.CharField(
                    choices=[('capitalize', 'Kapitalizacija'), ('expense', 'Rashod')],
                    max_length=20,
                    verbose_name='Ekonomski učinak',
                )),
                ('allowed_kinds', models.JSONField(default=list, verbose_name='Dopušteni kind')),
                ('requires_fixed_asset', models.BooleanField(default=False, verbose_name='Zahtijeva imovinu')),
                ('is_active', models.BooleanField(default=True, verbose_name='Aktivno')),
                ('tenant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='%(app_label)s_%(class)s_set',
                    to='tenants.tenant',
                    verbose_name='Tenant',
                )),
            ],
            options={
                'verbose_name': 'Profil knjiženja službenog dokumenta',
                'verbose_name_plural': 'Profili knjiženja službenih dokumenata',
                'ordering': ['code'],
            },
        ),
        migrations.AddConstraint(
            model_name='officialdocumentpostingprofile',
            constraint=models.UniqueConstraint(
                fields=('tenant', 'code'),
                name='unique_official_document_posting_profile',
            ),
        ),
        migrations.AddField(
            model_name='officialdocument',
            name='posting_profile',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='official_documents',
                to='accounting.officialdocumentpostingprofile',
                verbose_name='Profil knjiženja',
            ),
        ),
    ]
