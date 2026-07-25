from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0017_subject_assessment_mode_and_string_grades'),
    ]

    operations = [
        migrations.CreateModel(
            name='AssessmentGroup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=150, verbose_name='Название группы произведений')),
                ('description', models.TextField(blank=True, verbose_name='Описание')),
                ('sort_order', models.PositiveIntegerField(default=100, verbose_name='Порядок отображения')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активна')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Изменено')),
                ('academic_year', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assessment_groups', to='journal.academicyear', verbose_name='Учебный год')),
                ('subject', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assessment_groups', to='journal.subject', verbose_name='Предмет')),
            ],
            options={
                'verbose_name': 'Группа произведений',
                'verbose_name_plural': 'Группы произведений',
                'ordering': ['academic_year__starts_on', 'subject__name', 'sort_order', 'name', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='AssessmentItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=255, verbose_name='Произведение / элемент')),
                ('description', models.TextField(blank=True, verbose_name='Описание')),
                ('sort_order', models.PositiveIntegerField(default=100, verbose_name='Порядок отображения')),
                ('is_required', models.BooleanField(default=True, verbose_name='Обязательное произведение')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активно')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Изменено')),
                ('academic_year', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assessment_items', to='journal.academicyear', verbose_name='Учебный год')),
                ('group', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='items', to='journal.assessmentgroup', verbose_name='Группа произведений')),
                ('responsible_teacher', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='responsible_assessment_items', to='journal.teacher', verbose_name='Ответственный преподаватель-дирижёр')),
                ('subject', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assessment_items', to='journal.subject', verbose_name='Предмет')),
            ],
            options={
                'verbose_name': 'Произведение / элемент аттестации',
                'verbose_name_plural': 'Произведения / элементы аттестации',
                'ordering': ['group__sort_order', 'sort_order', 'title', 'pk'],
            },
        ),
        migrations.CreateModel(
            name='StudentAssessmentGroup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_active', models.BooleanField(default=True, verbose_name='Назначение активно')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Назначено')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Изменено')),
                ('academic_year', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='student_assessment_group_assignments', to='journal.academicyear', verbose_name='Учебный год')),
                ('assessment_group', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='student_assignments', to='journal.assessmentgroup', verbose_name='Группа произведений')),
                ('enrollment', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='assessment_group_assignments', to='journal.studentenrollment', verbose_name='Зачисление ученика')),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assessment_group_assignments', to='journal.student', verbose_name='Ученик')),
            ],
            options={
                'verbose_name': 'Назначение группы произведений ученику',
                'verbose_name_plural': 'Назначения групп произведений ученикам',
                'ordering': ['student__full_name', 'assessment_group__subject__name', 'assessment_group__sort_order'],
            },
        ),
        migrations.CreateModel(
            name='AssessmentResult',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('passed', 'Зачёт'), ('failed', 'Незачёт')], max_length=16, verbose_name='Результат')),
                ('comment', models.TextField(blank=True, verbose_name='Комментарий')),
                ('assessed_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='Дата результата')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Изменено')),
                ('assessed_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assessment_results_given', to='journal.teacher', verbose_name='Преподаватель, выставивший результат')),
                ('enrollment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='assessment_results', to='journal.studentenrollment', verbose_name='Зачисление ученика')),
                ('item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='results', to='journal.assessmentitem', verbose_name='Произведение / элемент')),
            ],
            options={
                'verbose_name': 'Результат сдачи произведения',
                'verbose_name_plural': 'Результаты сдачи произведений',
                'ordering': ['item__sort_order', 'enrollment__full_name'],
            },
        ),
        migrations.CreateModel(
            name='FinalGradeRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('rule_type', models.CharField(choices=[('count', 'По количеству зачётов'), ('all_required', 'Все обязательные произведения'), ('default', 'Значение по умолчанию')], max_length=20, verbose_name='Тип правила')),
                ('passed_count', models.PositiveIntegerField(blank=True, null=True, verbose_name='Количество зачётов')),
                ('condition_value', models.BooleanField(blank=True, help_text='Для правила «Все обязательные произведения»: да или нет.', null=True, verbose_name='Условие выполнено')),
                ('grade', models.CharField(max_length=64, verbose_name='Итоговая оценка')),
                ('priority', models.PositiveIntegerField(default=100, verbose_name='Приоритет')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активно')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Изменено')),
                ('academic_year', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='final_grade_rules', to='journal.academicyear', verbose_name='Учебный год')),
                ('assessment_group', models.ForeignKey(blank=True, help_text='Оставьте пустым для общего правила предмета.', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='final_grade_rules', to='journal.assessmentgroup', verbose_name='Группа произведений')),
                ('subject', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='final_grade_rules', to='journal.subject', verbose_name='Предмет')),
            ],
            options={
                'verbose_name': 'Правило итоговой оценки',
                'verbose_name_plural': 'Правила итоговых оценок',
                'ordering': ['academic_year__starts_on', 'subject__name', 'priority', 'pk'],
            },
        ),
        migrations.AddField(model_name='subjectresult', name='calculated_at', field=models.DateTimeField(blank=True, null=True, verbose_name='Дата автоматического расчёта')),
        migrations.AddField(model_name='subjectresult', name='calculation_details', field=models.JSONField(blank=True, default=dict, verbose_name='Детали расчёта')),
        migrations.AddField(model_name='subjectresult', name='is_auto_calculated', field=models.BooleanField(default=False, verbose_name='Рассчитано автоматически')),
        migrations.AddConstraint(model_name='assessmentgroup', constraint=models.UniqueConstraint(fields=('subject', 'academic_year', 'name'), name='unique_assessment_group_subject_year_name')),
        migrations.AddIndex(model_name='assessmentgroup', index=models.Index(fields=['academic_year', 'subject'], name='assess_group_year_subject_idx')),
        migrations.AddIndex(model_name='assessmentgroup', index=models.Index(fields=['is_active', 'sort_order'], name='assess_group_active_order_idx')),
        migrations.AddConstraint(model_name='assessmentitem', constraint=models.UniqueConstraint(fields=('group', 'title'), name='unique_assessment_item_group_title')),
        migrations.AddIndex(model_name='assessmentitem', index=models.Index(fields=['academic_year', 'subject', 'group'], name='assess_item_year_subj_idx')),
        migrations.AddIndex(model_name='assessmentitem', index=models.Index(fields=['responsible_teacher', 'is_active'], name='assess_item_teacher_active_idx')),
        migrations.AddIndex(model_name='assessmentitem', index=models.Index(fields=['group', 'sort_order'], name='assess_item_group_order_idx')),
        migrations.AddConstraint(model_name='studentassessmentgroup', constraint=models.UniqueConstraint(fields=('student', 'assessment_group', 'academic_year'), name='unique_student_assessment_group_year')),
        migrations.AddIndex(model_name='studentassessmentgroup', index=models.Index(fields=['student', 'academic_year', 'is_active'], name='stud_assess_group_act_idx')),
        migrations.AddIndex(model_name='studentassessmentgroup', index=models.Index(fields=['assessment_group', 'is_active'], name='assess_group_stud_act_idx')),
        migrations.AddConstraint(model_name='assessmentresult', constraint=models.UniqueConstraint(fields=('enrollment', 'item'), name='unique_assessment_result_enrollment_item')),
        migrations.AddIndex(model_name='assessmentresult', index=models.Index(fields=['item', 'status'], name='assess_result_item_status_idx')),
        migrations.AddIndex(model_name='assessmentresult', index=models.Index(fields=['enrollment', 'status'], name='assess_res_enroll_stat_idx')),
        migrations.AddIndex(model_name='assessmentresult', index=models.Index(fields=['assessed_by', '-assessed_at'], name='assess_result_teacher_date_idx')),
        migrations.AddConstraint(model_name='finalgraderule', constraint=models.UniqueConstraint(fields=('subject', 'academic_year', 'assessment_group', 'rule_type', 'passed_count', 'condition_value'), name='unique_final_grade_rule_condition', nulls_distinct=False)),
        migrations.AddIndex(model_name='finalgraderule', index=models.Index(fields=['academic_year', 'subject', 'is_active'], name='final_rule_year_subject_idx')),
    ]
