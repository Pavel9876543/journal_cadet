from django.db import migrations, models


def copy_student_credentials(apps, schema_editor):
    TemporaryCredential = apps.get_model('journal', 'TemporaryCredential')
    TemporaryStudentCredential = apps.get_model('journal', 'TemporaryStudentCredential')

    for credential in TemporaryStudentCredential.objects.order_by('id'):
        TemporaryCredential.objects.create(
            login=credential.login,
            temporary_password=credential.temporary_password,
            student_phone=credential.student_phone,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('journal', '0015_update_temporary_student_credential'),
    ]

    operations = [
        migrations.AddField(
            model_name='temporarycredential',
            name='student_phone',
            field=models.CharField(blank=True, max_length=32, verbose_name='Номер телефона ученика'),
        ),
        migrations.RunPython(copy_student_credentials, migrations.RunPython.noop),
        migrations.DeleteModel(
            name='TemporaryStudentCredential',
        ),
    ]
