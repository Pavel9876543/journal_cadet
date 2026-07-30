import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def copy_registration_settings_to_academic_years(apps, schema_editor):
    AcademicYear = apps.get_model('journal', 'AcademicYear')
    CourseRegistrationSettings = apps.get_model('journal', 'CourseRegistrationSettings')

    academic_years = list(
        AcademicYear.objects.order_by('-is_active', '-starts_on', '-pk')
    )
    existing = CourseRegistrationSettings.objects.order_by('pk').first()
    if not academic_years:
        CourseRegistrationSettings.objects.all().delete()
        return

    copied_values = {
        'telegram_group_url': existing.telegram_group_url if existing else '',
        'minimum_registration_age': existing.minimum_registration_age if existing else 14,
        'registration_mode': existing.registration_mode if existing else 'open',
        'application_limit': existing.application_limit if existing else None,
    }
    for index, academic_year in enumerate(academic_years):
        if index == 0 and existing is not None:
            existing.academic_year_id = academic_year.pk
            existing.save(update_fields=['academic_year'])
            continue
        CourseRegistrationSettings.objects.create(
            academic_year_id=academic_year.pk,
            **copied_values,
        )


def restore_single_registration_settings(apps, schema_editor):
    CourseRegistrationSettings = apps.get_model('journal', 'CourseRegistrationSettings')
    settings_rows = list(CourseRegistrationSettings.objects.order_by('pk'))
    if not settings_rows:
        return
    first = settings_rows[0]
    CourseRegistrationSettings.objects.exclude(pk=first.pk).delete()
    first.academic_year_id = None
    first.save(update_fields=['academic_year'])


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('journal', '0022_course_registration_availability'),
    ]

    operations = [
        migrations.CreateModel(
            name='AccountProfile',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('birth_date', models.DateField(blank=True, null=True, verbose_name='Дата рождения')),
                (
                    'user',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='journal_account_profile',
                        to=settings.AUTH_USER_MODEL,
                        verbose_name='Пользователь',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Дополнительные данные пользователя',
                'verbose_name_plural': 'Дополнительные данные пользователей',
            },
        ),
        migrations.AddField(
            model_name='courseregistrationsettings',
            name='academic_year',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='registration_settings',
                to='journal.academicyear',
                verbose_name='Учебный год',
            ),
        ),
        migrations.AlterField(
            model_name='courseregistrationsettings',
            name='id',
            field=models.BigAutoField(
                auto_created=True,
                primary_key=True,
                serialize=False,
                verbose_name='ID',
            ),
        ),
        migrations.RunPython(
            copy_registration_settings_to_academic_years,
            restore_single_registration_settings,
        ),
        migrations.AlterField(
            model_name='courseregistrationsettings',
            name='academic_year',
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='registration_settings',
                to='journal.academicyear',
                verbose_name='Учебный год',
            ),
        ),
    ]
