from django.db import migrations


def align_subject_classification(apps, schema_editor):
    Subject = apps.get_model('journal', 'Subject')
    StudentSubject = apps.get_model('journal', 'StudentSubject')
    GroupSubject = apps.get_model('journal', 'GroupSubject')

    individual_subject_ids = set(
        StudentSubject.objects.values_list('subject_id', flat=True).distinct()
    )
    group_subject_ids = set(
        GroupSubject.objects.values_list('subject_id', flat=True).distinct()
    )
    safe_individual_ids = individual_subject_ids - group_subject_ids
    Subject.objects.filter(pk__in=safe_individual_ids).update(is_specialty=True)


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0018_element_assessment_models'),
    ]

    operations = [
        migrations.RunPython(align_subject_classification, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='studentsubject',
            name='unique_active_specialty',
        ),
        migrations.RemoveField(
            model_name='studentsubject',
            name='is_specialty',
        ),
    ]
