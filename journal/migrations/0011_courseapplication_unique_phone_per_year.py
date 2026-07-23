from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0010_academic_year_enrollments'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='courseapplication',
            constraint=models.UniqueConstraint(
                fields=('academic_year', 'student_phone'),
                name='unique_course_app_phone_per_year',
            ),
        ),
    ]
