from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounting', '0027_official_document_posting'),
        ('tenants', '0007_create_finestar_tenant'),
    ]

    operations = [
        migrations.CreateModel(
            name='CostCenter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=32, verbose_name='Šifra')),
                ('name', models.CharField(max_length=200, verbose_name='Naziv')),
                ('kind', models.CharField(
                    choices=[
                        ('location', 'Lokacijsko'),
                        ('object', 'Objektno'),
                        ('overhead', 'Režijsko'),
                        ('group', 'Grupa'),
                    ],
                    max_length=16,
                    verbose_name='Vrsta',
                )),
                ('is_active', models.BooleanField(default=True, verbose_name='Aktivno')),
                ('notes', models.TextField(blank=True, verbose_name='Napomene')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Kreirano')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Ažurirano')),
                ('parent', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='children',
                    to='accounting.costcenter',
                    verbose_name='Nadređeno MT',
                )),
                ('tenant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='%(app_label)s_%(class)s_set',
                    to='tenants.tenant',
                    verbose_name='Tenant',
                )),
            ],
            options={
                'verbose_name': 'Mjesto troška',
                'verbose_name_plural': 'Mjesta troška',
                'ordering': ['code'],
            },
        ),
        migrations.AddConstraint(
            model_name='costcenter',
            constraint=models.UniqueConstraint(
                fields=('tenant', 'code'),
                name='unique_cost_center_code_per_tenant',
            ),
        ),
        migrations.AddField(
            model_name='journalentryline',
            name='cost_center',
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                help_text='Kanonski računovodstveni trag nakon knjiženja. Samo RDG klase 4/5/7.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='journal_lines',
                to='accounting.costcenter',
                verbose_name='Mjesto troška',
            ),
        ),
        migrations.AddField(
            model_name='fixedasset',
            name='cost_center',
            field=models.ForeignKey(
                blank=True,
                help_text='Input za resolver amortizacije. Kanonski trag je JournalEntryLine.cost_center.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='fixed_assets',
                to='accounting.costcenter',
                verbose_name='Mjesto troška',
            ),
        ),
        migrations.AddField(
            model_name='vehicle',
            name='cost_center',
            field=models.OneToOneField(
                blank=True,
                help_text='Objektno MT vozila. Input za resolver; kanonski trag je JournalEntryLine.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='vehicle',
                to='accounting.costcenter',
                verbose_name='Mjesto troška',
            ),
        ),
        migrations.AddField(
            model_name='officialdocument',
            name='cost_center',
            field=models.ForeignKey(
                blank=True,
                help_text='Input za resolver. Kanonski trag nakon knjiženja je JournalEntryLine.cost_center.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='official_documents',
                to='accounting.costcenter',
                verbose_name='Mjesto troška',
            ),
        ),
    ]
