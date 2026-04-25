from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .forms import GradeCreateForm
from .models import Grade, Group, Student, Subject, Teacher


def _build_journal_tables(students, table_subjects, grade_qs):
    journal_tables = []

    # Формируем таблицы в формате: строки=ученики, столбцы=даты, ячейки=оценки.
    for subject in table_subjects:
        subject_grades = grade_qs.filter(subject=subject).order_by('date')
        dates = sorted({grade.date for grade in subject_grades})

        row_map = {student.id: {lesson_date: '' for lesson_date in dates} for student in students}
        for grade in subject_grades:
            row_map[grade.student_id][grade.date] = str(grade.value)

        rows = []
        for student in students:
            grades_by_date = {}
            for lesson_date in dates:
                grades_by_date[lesson_date] = row_map[student.id][lesson_date]
            rows.append({'student': student, 'grades_by_date': grades_by_date})

        journal_tables.append(
            {
                'subject': subject,
                'dates': dates,
                'rows': rows,
            }
        )

    return journal_tables


@login_required
def journal_view(request):
    selected_group_id = request.GET.get('group')
    selected_subject_id = request.GET.get('subject')

    if request.user.is_superuser:
        role_mode = 'superuser'
        groups = Group.objects.all().order_by('name')
        subjects = Subject.objects.all().order_by('name')
        selected_group = None
        students = []
        journal_tables = []

        if selected_group_id:
            selected_group = groups.filter(pk=selected_group_id).first()
            if selected_group:
                subjects = selected_group.subjects.all().order_by('name')
                grade_qs = Grade.objects.filter(student__group=selected_group).select_related(
                    'student',
                    'subject',
                    'teacher',
                )

                if selected_subject_id:
                    grade_qs = grade_qs.filter(subject_id=selected_subject_id)
                    table_subjects = list(subjects.filter(pk=selected_subject_id))
                else:
                    table_subjects = list(subjects)

                students = list(selected_group.students.all())
                journal_tables = _build_journal_tables(students, table_subjects, grade_qs)

        context = {
            'role_mode': role_mode,
            'groups': groups,
            'subjects': subjects,
            'students': students,
            'journal_tables': journal_tables,
            'single_subject_mode': bool(selected_subject_id),
            'selected_group': selected_group,
            'selected_group_id': str(selected_group_id or ''),
            'selected_subject_id': str(selected_subject_id or ''),
            'grade_form': None,
        }
        return render(request, 'journal.html', context)

    try:
        teacher = request.user.teacher_profile
    except Teacher.DoesNotExist:
        teacher = None

    try:
        student_profile = request.user.student_profile
    except Student.DoesNotExist:
        student_profile = None

    if teacher is None and student_profile is None:
        return render(
            request,
            'journal.html',
            {
                'access_error': 'У вашей учетной записи нет профиля преподавателя или ученика. Обратитесь к администратору.',
                'groups': [],
                'subjects': [],
                'students': [],
                'journal_tables': [],
                'single_subject_mode': False,
                'selected_group_id': '',
                'selected_subject_id': '',
                'grade_form': None,
                'role_mode': '',
            },
        )

    if teacher is not None:
        role_mode = 'teacher'

        groups = Group.objects.filter(subjects__in=teacher.subjects.all()).distinct().order_by('name')
        subjects = teacher.subjects.all().order_by('name')

        journal_tables = []
        students = []
        selected_group = None

        if selected_group_id:
            selected_group = groups.filter(pk=selected_group_id).first()
            if selected_group:
                subjects = subjects.filter(groups=selected_group).distinct()

                grade_qs = Grade.objects.filter(
                    teacher=teacher,
                    student__group=selected_group,
                    subject__in=subjects,
                ).select_related('student', 'subject', 'teacher')

                if selected_subject_id:
                    grade_qs = grade_qs.filter(subject_id=selected_subject_id)
                    table_subjects = list(subjects.filter(pk=selected_subject_id))
                else:
                    table_subjects = list(subjects)

                students = list(selected_group.students.all())
                journal_tables = _build_journal_tables(students, table_subjects, grade_qs)

        grade_form = None
        if selected_group:
            grade_form = GradeCreateForm(
                request.POST or None,
                teacher=teacher,
                group=selected_group,
            )

            if request.method == 'POST':
                if grade_form.is_valid():
                    grade_form.save()
                    messages.success(request, 'Оценка успешно добавлена.')

                    query = {'group': selected_group.id}
                    saved_subject_id = str(grade_form.cleaned_data['subject'].id)
                    if selected_subject_id:
                        query['subject'] = saved_subject_id
                    return redirect(f"/?{urlencode(query)}")
                messages.error(request, 'Не удалось сохранить оценку. Проверьте данные формы.')

        context = {
            'role_mode': role_mode,
            'groups': groups,
            'subjects': subjects,
            'students': students,
            'journal_tables': journal_tables,
            'single_subject_mode': bool(selected_subject_id),
            'selected_group': selected_group,
            'selected_group_id': str(selected_group_id or ''),
            'selected_subject_id': str(selected_subject_id or ''),
            'grade_form': grade_form,
        }
        return render(request, 'journal.html', context)

    role_mode = 'student'

    selected_group = student_profile.group
    students = [student_profile]
    groups = [selected_group]

    subjects = selected_group.subjects.order_by('name')
    grade_qs = Grade.objects.filter(student=student_profile, subject__in=subjects).select_related(
        'student',
        'subject',
        'teacher',
    )

    if selected_subject_id:
        grade_qs = grade_qs.filter(subject_id=selected_subject_id)
        table_subjects = list(subjects.filter(pk=selected_subject_id))
    else:
        table_subjects = list(subjects)

    journal_tables = _build_journal_tables(students, table_subjects, grade_qs)

    if request.method == 'POST':
        messages.error(request, 'Ученику недоступно редактирование оценок.')
        return redirect(f"/?{urlencode({'subject': selected_subject_id} if selected_subject_id else {})}")

    context = {
        'role_mode': role_mode,
        'groups': groups,
        'subjects': subjects,
        'students': students,
        'journal_tables': journal_tables,
        'single_subject_mode': bool(selected_subject_id),
        'selected_group': selected_group,
        'selected_group_id': str(selected_group.id),
        'selected_subject_id': str(selected_subject_id or ''),
        'grade_form': None,
    }
    return render(request, 'journal.html', context)
