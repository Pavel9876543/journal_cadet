from django.db import migrations, models


def make_logins_unique(apps, schema_editor):
    TemporaryStudentCredential = apps.get_model('journal', 'TemporaryStudentCredential')
    seen = set()

    for credential in TemporaryStudentCredential.objects.order_by('id'):
        base = credential.login or 'student'
        candidate = base
        suffix = 2
        while candidate in seen:
            candidate = f'{base}-{suffix}'
            suffix += 1

        if candidate != credential.login:
            credential.login = candidate
            credential.save(update_fields=['login'])
        seen.add(candidate)


class Migration(migrations.Migration):

    dependencies = [
        ('journal', '0014_temporarystudentcredential'),
    ]

    operations = [
        migrations.RenameField(
            model_name='temporarystudentcredential',
            old_name='phone_number',
            new_name='student_phone',
        ),
        migrations.RunPython(make_logins_unique, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='temporarystudentcredential',
            name='login',
            field=models.CharField(max_length=150, unique=True, verbose_name='Логин'),
        ),
        migrations.AlterField(
            model_name='temporarystudentcredential',
            name='student_phone',
            field=models.CharField(max_length=32, verbose_name='Номер телефона ученика'),
        ),
    ]
