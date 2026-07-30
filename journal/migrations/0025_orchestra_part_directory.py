import django.db.models.deletion
from django.db import migrations, models


DEFAULT_PARTS = {
    'Домра': ('Малая первая', 'Малая вторая', 'Альтовая первая', 'Альтовая вторая'),
    'Балалайка': ('Прима', 'Альт', 'Секунда'),
    'Баян': ('Первый', 'Второй', 'Третий'),
}


def migrate_orchestra_parts(apps, schema_editor):
    Instrument = apps.get_model('journal', 'Instrument')
    OrchestraPart = apps.get_model('journal', 'OrchestraPart')
    Student = apps.get_model('journal', 'Student')
    CourseApplication = apps.get_model('journal', 'CourseApplication')

    instruments_by_name = {
        instrument.name: instrument
        for instrument in Instrument.objects.all()
    }
    for instrument_name, part_names in DEFAULT_PARTS.items():
        instrument = instruments_by_name.get(instrument_name)
        if instrument is None:
            continue
        for part_name in part_names:
            OrchestraPart.objects.get_or_create(
                instrument_id=instrument.pk,
                name=part_name,
            )

    for Model, instrument_field in (
        (Student, 'instrument_id'),
        (CourseApplication, 'instrument_reference_id'),
    ):
        for item in Model.objects.exclude(orchestra_part_legacy='').iterator():
            part_name = (item.orchestra_part_legacy or '').strip()
            instrument_id = getattr(item, instrument_field)
            if not part_name or not instrument_id or item.custom_instrument:
                continue
            part, _created = OrchestraPart.objects.get_or_create(
                instrument_id=instrument_id,
                name=part_name,
            )
            Model.objects.filter(pk=item.pk).update(orchestra_part_id=part.pk)


def restore_orchestra_part_text(apps, schema_editor):
    Student = apps.get_model('journal', 'Student')
    CourseApplication = apps.get_model('journal', 'CourseApplication')
    for Model in (Student, CourseApplication):
        for item in Model.objects.select_related('orchestra_part').iterator():
            part_name = item.orchestra_part.name if item.orchestra_part_id else ''
            Model.objects.filter(pk=item.pk).update(orchestra_part_legacy=part_name)


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0024_orchestra_part_and_birthday_indexes'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrchestraPart',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255, verbose_name='Название партии')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активна')),
                (
                    'instrument',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='orchestra_parts',
                        to='journal.instrument',
                        verbose_name='Инструмент',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Партия оркестра',
                'verbose_name_plural': 'Партии оркестра',
                'ordering': ['instrument__name', 'name'],
            },
        ),
        migrations.RenameField(
            model_name='student',
            old_name='orchestra_part',
            new_name='orchestra_part_legacy',
        ),
        migrations.RenameField(
            model_name='courseapplication',
            old_name='orchestra_part',
            new_name='orchestra_part_legacy',
        ),
        migrations.AddField(
            model_name='student',
            name='orchestra_part',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='students',
                to='journal.orchestrapart',
                verbose_name='Партия в оркестре',
            ),
        ),
        migrations.AddField(
            model_name='courseapplication',
            name='orchestra_part',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='course_applications',
                to='journal.orchestrapart',
                verbose_name='Партия в оркестре',
            ),
        ),
        migrations.AddIndex(
            model_name='orchestrapart',
            index=models.Index(
                fields=['instrument', 'is_active', 'name'],
                name='orch_part_instrument_idx',
            ),
        ),
        migrations.AddConstraint(
            model_name='orchestrapart',
            constraint=models.UniqueConstraint(
                fields=('instrument', 'name'),
                name='unique_orchestra_part_instrument_name',
            ),
        ),
        migrations.RunPython(
            migrate_orchestra_parts,
            restore_orchestra_part_text,
        ),
        migrations.RemoveField(
            model_name='student',
            name='orchestra_part_legacy',
        ),
        migrations.RemoveField(
            model_name='courseapplication',
            name='orchestra_part_legacy',
        ),
        migrations.AddConstraint(
            model_name='student',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(orchestra_part__isnull=True)
                    | models.Q(instrument__isnull=False, custom_instrument='')
                ),
                name='student_part_requires_reference_instrument',
            ),
        ),
        migrations.AddConstraint(
            model_name='courseapplication',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(orchestra_part__isnull=True)
                    | models.Q(instrument_reference__isnull=False, custom_instrument='')
                ),
                name='course_app_part_requires_reference',
            ),
        ),
    ]
