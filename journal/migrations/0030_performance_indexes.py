from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0029_assessment_element_catalog'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='studentenrollment',
            index=models.Index(
                fields=['academic_year', 'is_active', 'group'],
                name='enroll_year_active_group_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='studentsubject',
            index=models.Index(
                fields=['academic_year', 'is_active', 'student'],
                name='stud_subj_year_active_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='grade',
            index=models.Index(
                fields=['enrollment', 'subject', '-date'],
                name='grade_enroll_subj_date_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='subjectresult',
            index=models.Index(
                fields=['enrollment', 'subject'],
                name='result_enroll_subject_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='assessmentgroup',
            index=models.Index(
                fields=['academic_year', 'is_active', 'sort_order'],
                name='assess_group_year_active_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='passwordrecoverycontact',
            index=models.Index(
                fields=['is_active', 'display_order'],
                name='recovery_active_order_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='courseapplication',
            index=models.Index(
                fields=['academic_year', 'status', '-registration_date'],
                name='course_app_year_status_idx',
            ),
        ),
    ]
