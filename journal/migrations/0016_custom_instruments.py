from django.db import migrations, models
import django.db.models.deletion


def populate_structured_instruments(apps, schema_editor):
    Instrument = apps.get_model('journal', 'Instrument')
    CourseApplication = apps.get_model('journal', 'CourseApplication')

    instruments_by_name = {
        name.strip(): pk
        for pk, name in Instrument.objects.values_list('pk', 'name')
        if name and name.strip()
    }
    for application in CourseApplication.objects.all().iterator():
        value = (application.instrument or '').strip()
        reference_id = instruments_by_name.get(value)
        if reference_id:
            application.instrument_reference_id = reference_id
            application.custom_instrument = ''
        else:
            application.instrument_reference_id = None
            application.custom_instrument = value or 'Не указан'
            if not value:
                application.instrument = application.custom_instrument
        application.save(
            update_fields=['instrument', 'instrument_reference', 'custom_instrument'],
        )


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0015_alter_courseregistrationsettings_minimum_registration_age'),
    ]

    operations = [
        migrations.AlterField(
            model_name='student',
            name='instrument',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='students',
                to='journal.instrument',
                verbose_name='Инструмент из справочника',
            ),
        ),
        migrations.AddField(
            model_name='student',
            name='custom_instrument',
            field=models.CharField(
                blank=True,
                help_text='Заполняется только когда подходящего инструмента нет в справочнике.',
                max_length=255,
                verbose_name='Собственный инструмент',
            ),
        ),
        migrations.AddField(
            model_name='courseapplication',
            name='custom_instrument',
            field=models.CharField(blank=True, max_length=255, verbose_name='Собственный инструмент'),
        ),
        migrations.AddField(
            model_name='courseapplication',
            name='instrument_reference',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='course_applications',
                to='journal.instrument',
                verbose_name='Инструмент из справочника',
            ),
        ),
        migrations.RunPython(populate_structured_instruments, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='student',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(instrument__isnull=False, custom_instrument='')
                    | (models.Q(instrument__isnull=True) & ~models.Q(custom_instrument=''))
                ),
                name='student_exactly_one_instrument_source',
            ),
        ),
        migrations.AddConstraint(
            model_name='courseapplication',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(instrument_reference__isnull=False, custom_instrument='')
                    | (models.Q(instrument_reference__isnull=True) & ~models.Q(custom_instrument=''))
                ),
                name='course_app_exactly_one_instrument_source',
            ),
        ),
    ]
