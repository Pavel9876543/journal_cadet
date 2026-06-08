from django.db import migrations


def create_settings(apps, schema_editor):
    CourseRegistrationSettings = apps.get_model('journal', 'CourseRegistrationSettings')
    CourseRegistrationSettings.objects.get_or_create(
        pk=1,
        defaults={'telegram_group_url': ''},
    )


def remove_settings(apps, schema_editor):
    CourseRegistrationSettings = apps.get_model('journal', 'CourseRegistrationSettings')
    CourseRegistrationSettings.objects.filter(pk=1).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('journal', '0010_courseregistrationsettings_courseapplication'),
    ]

    operations = [
        migrations.RunPython(create_settings, remove_settings),
    ]
