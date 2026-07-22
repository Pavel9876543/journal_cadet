import django.db.models.deletion
from datetime import date, timedelta
from django.conf import settings
from django.db import migrations, models


def academic_year_name_for_dates(starts_on, ends_on):
    if starts_on.year == ends_on.year:
        return str(starts_on.year)
    return f'{starts_on.year}/{ends_on.year}'


def activate_latest_academic_year(AcademicYear):
    latest_year = AcademicYear.objects.order_by('-starts_on', '-ends_on', '-id').first()
    if latest_year is None:
        return None

    AcademicYear.objects.exclude(pk=latest_year.pk).update(is_active=False)
    AcademicYear.objects.filter(pk=latest_year.pk).update(is_active=True)
    return latest_year


def default_academic_year(AcademicYear, CourseRegistrationSettings):
    latest_year = activate_latest_academic_year(AcademicYear)
    if latest_year is not None:
        return latest_year

    settings_obj = CourseRegistrationSettings.objects.filter(pk=1).first()
    if settings_obj is not None:
        starts_on = settings_obj.course_starts_on
        ends_on = settings_obj.course_ends_on
    else:
        today = date.today()
        starts_on = today
        ends_on = today + timedelta(days=365)

    academic_year = AcademicYear.objects.create(
        name=academic_year_name_for_dates(starts_on, ends_on),
        starts_on=starts_on,
        ends_on=ends_on,
        is_active=False,
    )
    activate_latest_academic_year(AcademicYear)
    return academic_year


def populate_academic_year_links(apps, schema_editor):
    AcademicYear = apps.get_model('journal', 'AcademicYear')
    CourseApplication = apps.get_model('journal', 'CourseApplication')
    CourseRegistrationSettings = apps.get_model('journal', 'CourseRegistrationSettings')
    Grade = apps.get_model('journal', 'Grade')

    has_applications_without_year = CourseApplication.objects.filter(academic_year__isnull=True).exists()
    has_grades_without_year = Grade.objects.filter(academic_year__isnull=True).exists()
    if not has_applications_without_year and not has_grades_without_year:
        activate_latest_academic_year(AcademicYear)
        return

    fallback_year = default_academic_year(AcademicYear, CourseRegistrationSettings)

    for academic_year in AcademicYear.objects.all().only('id', 'starts_on', 'ends_on'):
        Grade.objects.filter(
            academic_year__isnull=True,
            date__gte=academic_year.starts_on,
            date__lte=academic_year.ends_on,
        ).update(academic_year=academic_year)

        CourseApplication.objects.filter(
            academic_year__isnull=True,
            student__group__academic_year=academic_year,
        ).update(academic_year=academic_year)

    Grade.objects.filter(academic_year__isnull=True).update(academic_year=fallback_year)
    CourseApplication.objects.filter(academic_year__isnull=True).update(academic_year=fallback_year)


class Migration(migrations.Migration):

    dependencies = [
        ('journal', '0008_courseregistrationratelimit_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='courseapplication',
            name='academic_year',
            field=models.ForeignKey(blank=True, editable=False, help_text='Учебный год, в рамках которого подана заявка.', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='course_applications', to='journal.academicyear', verbose_name='Учебный год'),
        ),
        migrations.RunPython(
            populate_academic_year_links,
            migrations.RunPython.noop,
        ),
        migrations.AddIndex(
            model_name='courseapplication',
            index=models.Index(fields=['academic_year', 'student_phone'], name='course_app_year_phone_idx'),
        ),
    ]
