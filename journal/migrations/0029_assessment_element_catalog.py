from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q


def create_catalog_entries(apps, schema_editor):
    AssessmentElement = apps.get_model('journal', 'AssessmentElement')
    AssessmentItem = apps.get_model('journal', 'AssessmentItem')

    for item in AssessmentItem.objects.select_related('subject').order_by('pk').iterator():
        element, created = AssessmentElement.objects.get_or_create(
            subject_id=item.subject_id,
            title=item.title.strip(),
            defaults={
                'description': (item.description or '').strip(),
                'is_active': item.is_active,
            },
        )
        if not created and not element.description and item.description:
            element.description = item.description.strip()
            element.save(update_fields=['description'])
        item.element_id = element.pk
        item.save(update_fields=['element'])


def clear_catalog_links(apps, schema_editor):
    AssessmentItem = apps.get_model('journal', 'AssessmentItem')
    AssessmentItem.objects.update(element=None)


class Migration(migrations.Migration):

    dependencies = [
        ('journal', '0028_passwordrecoverycontact_messenger_username'),
    ]

    operations = [
        migrations.CreateModel(
            name='AssessmentElement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=255, verbose_name='Произведение / элемент')),
                ('description', models.TextField(blank=True, verbose_name='Описание')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активно')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Изменено')),
                ('subject', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assessment_element_catalog', to='journal.subject', verbose_name='Предмет')),
            ],
            options={
                'verbose_name': 'Произведение / элемент',
                'verbose_name_plural': 'Произведения / элементы',
                'ordering': ['subject__name', 'title', 'pk'],
            },
        ),
        migrations.AddConstraint(
            model_name='assessmentelement',
            constraint=models.UniqueConstraint(fields=('subject', 'title'), name='unique_assessment_element_subject_title'),
        ),
        migrations.AddIndex(
            model_name='assessmentelement',
            index=models.Index(fields=['subject', 'is_active', 'title'], name='assess_element_subject_idx'),
        ),
        migrations.AddField(
            model_name='assessmentitem',
            name='element',
            field=models.ForeignKey(blank=True, help_text='Выберите значение только из справочника произведений / элементов.', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='group_placements', to='journal.assessmentelement', verbose_name='Произведение / элемент'),
        ),
        migrations.RunPython(create_catalog_entries, clear_catalog_links),
        migrations.AlterField(
            model_name='assessmentitem',
            name='title',
            field=models.CharField(editable=False, max_length=255, verbose_name='Название произведения (снимок)'),
        ),
        migrations.AlterField(
            model_name='assessmentitem',
            name='description',
            field=models.TextField(blank=True, editable=False, verbose_name='Описание (снимок)'),
        ),
        migrations.AddConstraint(
            model_name='assessmentitem',
            constraint=models.UniqueConstraint(condition=Q(('element__isnull', False)), fields=('group', 'element'), name='unique_assessment_item_group_element'),
        ),
        migrations.AddIndex(
            model_name='assessmentitem',
            index=models.Index(fields=['group', 'element'], name='assess_item_group_element_idx'),
        ),
        migrations.AlterModelOptions(
            name='assessmentitem',
            options={'ordering': ['group__sort_order', 'sort_order', 'title', 'pk'], 'verbose_name': 'Произведение в группе', 'verbose_name_plural': 'Произведения в группах'},
        ),
    ]
