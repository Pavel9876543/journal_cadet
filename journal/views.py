from urllib.parse import urlencode
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render

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


def _save_inline_grades(request, *, role_mode, selected_group, subjects, teacher=None):
    changed = 0

    for field_name, raw_value in request.POST.items():
        if not field_name.startswith('grade__'):
            continue

        parts = field_name.split('__')
        if len(parts) != 4:
            continue

        _, subject_id_raw, student_id_raw, grade_date_raw = parts
        try:
            subject_id = int(subject_id_raw)
            student_id = int(student_id_raw)
            grade_date = date.fromisoformat(grade_date_raw)
        except (TypeError, ValueError):
            continue

        value = raw_value.strip()
        if value and value not in {'1', '2', '3', '4', '5'}:
            messages.error(request, 'Оценка должна быть числом от 1 до 5.')
            return False

        grade = Grade.objects.filter(
            student_id=student_id,
            subject_id=subject_id,
            date=grade_date,
            student__group=selected_group,
            subject__in=subjects,
        ).select_related('teacher').first()
        if not grade:
            continue

        if role_mode == 'teacher' and (teacher is None or grade.teacher_id != teacher.id):
            continue

        if value == '':
            grade.delete()
            changed += 1
            continue

        new_value = int(value)
        if grade.value == new_value:
            continue

        grade.value = new_value
        try:
            grade.save()
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
            return False
        changed += 1

    if changed:
        messages.success(request, f'Изменения сохранены: {changed}.')
    else:
        messages.info(request, 'Изменений для сохранения нет.')
    return True


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
        grade_form = None

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

                if request.method == 'POST' and request.POST.get('action') == 'inline_edit':
                    if _save_inline_grades(
                        request,
                        role_mode=role_mode,
                        selected_group=selected_group,
                        subjects=subjects,
                    ):
                        query = {'group': selected_group.id}
                        if selected_subject_id:
                            query['subject'] = selected_subject_id
                        return redirect(f"/?{urlencode(query)}")

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
        if selected_group and request.method == 'POST' and request.POST.get('action') == 'inline_edit':
            if _save_inline_grades(
                request,
                role_mode=role_mode,
                selected_group=selected_group,
                subjects=subjects,
                teacher=teacher,
            ):
                query = {'group': selected_group.id}
                if selected_subject_id:
                    query['subject'] = selected_subject_id
                return redirect(f"/?{urlencode(query)}")

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
