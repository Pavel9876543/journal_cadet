import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Q


def populate_academic_year_enrollments(apps, schema_editor):
    AcademicYear = apps.get_model('journal', 'AcademicYear')
    Grade = apps.get_model('journal', 'Grade')
    GroupSubject = apps.get_model('journal', 'GroupSubject')
    Student = apps.get_model('journal', 'Student')
    StudentEnrollment = apps.get_model('journal', 'StudentEnrollment')
    StudentSubject = apps.get_model('journal', 'StudentSubject')
    SubjectResult = apps.get_model('journal', 'SubjectResult')

    fallback_year = AcademicYear.objects.filter(is_active=True).first()
    if fallback_year is None:
        fallback_year = AcademicYear.objects.order_by('-starts_on', '-ends_on', '-pk').first()

    years_by_student = {}
    for student_id, year_id in Student.objects.exclude(group_id__isnull=True).values_list(
        'pk',
        'group__academic_year_id',
    ):
        years_by_student.setdefault(student_id, set()).add(year_id)
    for student_id, year_id in Grade.objects.exclude(academic_year_id__isnull=True).values_list(
        'student_id',
        'academic_year_id',
    ):
        years_by_student.setdefault(student_id, set()).add(year_id)
    for student_id, year_id in SubjectResult.objects.values_list('student_id', 'academic_year_id'):
        years_by_student.setdefault(student_id, set()).add(year_id)

    students = {
        student.pk: student
        for student in Student.objects.select_related('group', 'instrument').iterator()
    }
    enrollment_ids = {}
    enrollments_to_create = []
    for student_id, student in students.items():
        year_ids = years_by_student.get(student_id, set())
        if not year_ids and fallback_year is not None:
            year_ids = {fallback_year.pk}
        for year_id in year_ids:
            group_id = (
                student.group_id
                if student.group_id and student.group.academic_year_id == year_id
                else None
            )
            enrollments_to_create.append(
                StudentEnrollment(
                    student_id=student.pk,
                    academic_year_id=year_id,
                    group_id=group_id,
                    full_name=student.full_name,
                    gender=student.gender,
                    birth_date=student.birth_date,
                    city_church=student.city_church,
                    instrument_name=student.instrument.name if student.instrument_id else '',
                    music_education=student.music_education,
                    student_phone=student.student_phone,
                    parent_contacts=student.parent_contacts,
                    comments=student.comments,
                    is_active=student.is_active,
                )
            )
    StudentEnrollment.objects.bulk_create(enrollments_to_create, ignore_conflicts=True)
    enrollment_ids.update({
        (student_id, year_id): enrollment_id
        for enrollment_id, student_id, year_id in StudentEnrollment.objects.values_list(
            'pk',
            'student_id',
            'academic_year_id',
        )
    })

    for assignment in StudentSubject.objects.select_related(
        'student__group__academic_year',
        'subject',
        'teacher',
    ).iterator():
        year_id = (
            assignment.student.group.academic_year_id
            if assignment.student.group_id
            else (fallback_year.pk if fallback_year is not None else None)
        )
        assignment.academic_year_id = year_id
        assignment.subject_name_snapshot = assignment.subject.name
        assignment.teacher_name_snapshot = assignment.teacher.full_name
        assignment.final_grade_type_snapshot = assignment.subject.final_grade_type
        assignment.save(update_fields=[
            'academic_year',
            'subject_name_snapshot',
            'teacher_name_snapshot',
            'final_grade_type_snapshot',
        ])

    for assignment in GroupSubject.objects.select_related('subject', 'teacher').iterator():
        assignment.subject_name_snapshot = assignment.subject.name
        assignment.teacher_name_snapshot = assignment.teacher.full_name
        assignment.final_grade_type_snapshot = assignment.subject.final_grade_type
        assignment.save(update_fields=[
            'subject_name_snapshot',
            'teacher_name_snapshot',
            'final_grade_type_snapshot',
        ])

    for grade in Grade.objects.select_related(
        'student__group',
        'subject',
        'teacher',
    ).iterator():
        enrollment_id = enrollment_ids.get((grade.student_id, grade.academic_year_id))
        enrollment = (
            StudentEnrollment.objects.filter(pk=enrollment_id).select_related('group').first()
            if enrollment_id
            else None
        )
        grade.enrollment_id = enrollment_id
        grade.student_name_snapshot = enrollment.full_name if enrollment else grade.student.full_name
        grade.group_name_snapshot = (
            enrollment.group.name
            if enrollment and enrollment.group_id
            else (grade.student.group.name if grade.student.group_id else '')
        )
        grade.subject_name_snapshot = grade.subject.name
        grade.teacher_name_snapshot = grade.teacher.full_name
        grade.save(update_fields=[
            'enrollment',
            'student_name_snapshot',
            'group_name_snapshot',
            'subject_name_snapshot',
            'teacher_name_snapshot',
        ])

    for result in SubjectResult.objects.select_related(
        'student__group',
        'subject',
    ).iterator():
        enrollment_id = enrollment_ids.get((result.student_id, result.academic_year_id))
        enrollment = (
            StudentEnrollment.objects.filter(pk=enrollment_id).select_related('group').first()
            if enrollment_id
            else None
        )
        result.enrollment_id = enrollment_id
        result.student_name_snapshot = enrollment.full_name if enrollment else result.student.full_name
        result.group_name_snapshot = (
            enrollment.group.name
            if enrollment and enrollment.group_id
            else (result.student.group.name if result.student.group_id else '')
        )
        result.subject_name_snapshot = result.subject.name
        result.final_grade_type_snapshot = result.subject.final_grade_type
        result.save(update_fields=[
            'enrollment',
            'student_name_snapshot',
            'group_name_snapshot',
            'subject_name_snapshot',
            'final_grade_type_snapshot',
        ])


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0009_courseapplication_academic_year_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='StudentEnrollment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('full_name', models.CharField(max_length=150, verbose_name='ФИО ученика на этот год')),
                ('gender', models.CharField(blank=True, choices=[('male', 'Мужской'), ('female', 'Женский')], max_length=10, verbose_name='Пол')),
                ('birth_date', models.DateField(blank=True, null=True, verbose_name='Дата рождения')),
                ('city_church', models.CharField(blank=True, max_length=255, verbose_name='Город / Церковь')),
                ('instrument_name', models.CharField(blank=True, max_length=100, verbose_name='Инструмент')),
                ('music_education', models.CharField(blank=True, choices=[('none', 'Нет'), ('self_taught', 'Самоучка'), ('basic', 'Начальное'), ('secondary', 'Среднее'), ('higher', 'Высшее')], max_length=20, verbose_name='Музыкальное образование')),
                ('student_phone', models.CharField(blank=True, max_length=32, verbose_name='Телефон ученика')),
                ('parent_contacts', models.TextField(blank=True, verbose_name='Телефон родителей')),
                ('comments', models.TextField(blank=True, verbose_name='Комментарий')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активен в учебном году')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Изменено')),
                ('academic_year', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='student_enrollments', to='journal.academicyear', verbose_name='Учебный год')),
                ('group', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='student_enrollments', to='journal.studygroup', verbose_name='Группа')),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='enrollments', to='journal.student', verbose_name='Ученик')),
            ],
            options={
                'verbose_name': 'Зачисление ученика',
                'verbose_name_plural': 'Зачисления учеников',
                'ordering': ['-academic_year__starts_on', 'full_name'],
            },
        ),
        migrations.AddField(
            model_name='studentsubject',
            name='academic_year',
            field=models.ForeignKey(editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='student_subjects', to='journal.academicyear', verbose_name='Учебный год'),
        ),
        migrations.AddField(
            model_name='studentsubject',
            name='subject_name_snapshot',
            field=models.CharField(blank=True, editable=False, max_length=100, verbose_name='Название предмета в учебном году'),
        ),
        migrations.AddField(
            model_name='studentsubject',
            name='teacher_name_snapshot',
            field=models.CharField(blank=True, editable=False, max_length=150, verbose_name='ФИО преподавателя в учебном году'),
        ),
        migrations.AddField(
            model_name='studentsubject',
            name='final_grade_type_snapshot',
            field=models.CharField(blank=True, choices=[('numeric', 'Пятибалльная (1-5, Н)'), ('pass_fail', 'Зачет/незачет')], editable=False, max_length=20, verbose_name='Тип итоговой оценки в учебном году'),
        ),
        migrations.AddField(
            model_name='groupsubject',
            name='subject_name_snapshot',
            field=models.CharField(blank=True, editable=False, max_length=100, verbose_name='Название предмета в учебном году'),
        ),
        migrations.AddField(
            model_name='groupsubject',
            name='teacher_name_snapshot',
            field=models.CharField(blank=True, editable=False, max_length=150, verbose_name='ФИО преподавателя в учебном году'),
        ),
        migrations.AddField(
            model_name='groupsubject',
            name='final_grade_type_snapshot',
            field=models.CharField(blank=True, choices=[('numeric', 'Пятибалльная (1-5, Н)'), ('pass_fail', 'Зачет/незачет')], editable=False, max_length=20, verbose_name='Тип итоговой оценки в учебном году'),
        ),
        migrations.AddField(
            model_name='grade',
            name='enrollment',
            field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='grades', to='journal.studentenrollment', verbose_name='Зачисление ученика'),
        ),
        migrations.AddField(
            model_name='grade',
            name='student_name_snapshot',
            field=models.CharField(blank=True, editable=False, max_length=150, verbose_name='ФИО ученика в учебном году'),
        ),
        migrations.AddField(
            model_name='grade',
            name='group_name_snapshot',
            field=models.CharField(blank=True, editable=False, max_length=100, verbose_name='Группа в учебном году'),
        ),
        migrations.AddField(
            model_name='grade',
            name='subject_name_snapshot',
            field=models.CharField(blank=True, editable=False, max_length=100, verbose_name='Название предмета в учебном году'),
        ),
        migrations.AddField(
            model_name='grade',
            name='teacher_name_snapshot',
            field=models.CharField(blank=True, editable=False, max_length=150, verbose_name='ФИО преподавателя в учебном году'),
        ),
        migrations.AddField(
            model_name='subjectresult',
            name='enrollment',
            field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='subject_results', to='journal.studentenrollment', verbose_name='Зачисление ученика'),
        ),
        migrations.AddField(
            model_name='subjectresult',
            name='student_name_snapshot',
            field=models.CharField(blank=True, editable=False, max_length=150, verbose_name='ФИО ученика в учебном году'),
        ),
        migrations.AddField(
            model_name='subjectresult',
            name='group_name_snapshot',
            field=models.CharField(blank=True, editable=False, max_length=100, verbose_name='Группа в учебном году'),
        ),
        migrations.AddField(
            model_name='subjectresult',
            name='subject_name_snapshot',
            field=models.CharField(blank=True, editable=False, max_length=100, verbose_name='Название предмета в учебном году'),
        ),
        migrations.AddField(
            model_name='subjectresult',
            name='final_grade_type_snapshot',
            field=models.CharField(blank=True, choices=[('numeric', 'Пятибалльная (1-5, Н)'), ('pass_fail', 'Зачет/незачет')], editable=False, max_length=20, verbose_name='Тип итоговой оценки в учебном году'),
        ),
        migrations.RemoveConstraint(
            model_name='studentsubject',
            name='unique_student_ind_subject',
        ),
        migrations.RemoveConstraint(
            model_name='studentsubject',
            name='unique_active_specialty',
        ),
        migrations.RunPython(
            populate_academic_year_enrollments,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='studentsubject',
            name='academic_year',
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.PROTECT, related_name='student_subjects', to='journal.academicyear', verbose_name='Учебный год'),
        ),
        migrations.AddConstraint(
            model_name='studentenrollment',
            constraint=models.UniqueConstraint(fields=('student', 'academic_year'), name='unique_student_enrollment_year'),
        ),
        migrations.AddIndex(
            model_name='studentenrollment',
            index=models.Index(fields=['academic_year', 'group'], name='enroll_year_group_idx'),
        ),
        migrations.AddIndex(
            model_name='studentenrollment',
            index=models.Index(fields=['student', 'academic_year'], name='enroll_student_year_idx'),
        ),
        migrations.AddIndex(
            model_name='studentenrollment',
            index=models.Index(fields=['is_active'], name='enroll_active_idx'),
        ),
        migrations.AddConstraint(
            model_name='studentsubject',
            constraint=models.UniqueConstraint(fields=('student', 'subject', 'academic_year'), name='unique_student_ind_subject'),
        ),
        migrations.AddConstraint(
            model_name='studentsubject',
            constraint=models.UniqueConstraint(condition=Q(is_active=True, is_specialty=True), fields=('student', 'academic_year'), name='unique_active_specialty'),
        ),
    ]
