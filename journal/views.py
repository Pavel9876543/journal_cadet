from __future__ import annotations

from collections import defaultdict
from datetime import date
from io import BytesIO
import json
from typing import Iterable
from urllib.parse import quote, urlencode
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, QuerySet
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from .services.excel_export import build_full_export_workbook

from .forms import (
    CourseApplicationPublicForm,
    GradeCreateForm,
    get_student_allowed_subjects,
    get_student_subject_teachers,
    get_students_for_group_subject,
    get_teacher_groups,
    get_teacher_subjects,
)
from .grade_options import (
    get_grade_groups,
    get_grade_students,
    get_grade_subjects,
    get_grade_teachers,
)
from .models import (
    AcademicYear,
    CourseApplication,
    CourseRegistrationSettings,
    Grade,
    GroupSubject,
    PasswordRecoveryContact,
    Student,
    StudentSubject,
    StudyGroup,
    Subject,
    SubjectResult,
    Teacher,
    TemporaryCredential,
)


# -----------------------------------------------------------------------------
# Общие helper-функции журнала
# -----------------------------------------------------------------------------


def password_help_view(request):
    contacts = PasswordRecoveryContact.objects.filter(is_active=True)
    return render(
        request,
        'registration/password_help.html',
        {'contacts': contacts},
    )


@login_required
@require_GET
def grade_options_api(request):
    teacher_profile = getattr(request.user, 'teacher_profile', None)
    can_manage_all_grades = (
        request.user.is_superuser
        or (
            teacher_profile is None
            and (
                request.user.has_perm('journal.add_grade')
                or request.user.has_perm('journal.change_grade')
            )
        )
    )
    if teacher_profile is None and not can_manage_all_grades:
        return JsonResponse(
            {'error': 'Выставление оценок недоступно для этой учетной записи.'},
            status=403,
        )

    group = _get_selected_object(
        StudyGroup.objects.filter(is_active=True),
        request.GET.get('group'),
    )
    student = _get_selected_object(
        Student.objects.filter(is_active=True).select_related('group'),
        request.GET.get('student'),
    )
    subject = _get_selected_object(
        Subject.objects.filter(is_active=True),
        request.GET.get('subject'),
    )
    academic_year = _get_selected_object(
        AcademicYear.objects.all(),
        request.GET.get('academic_year'),
    )

    if can_manage_all_grades:
        teacher = _get_selected_object(
            Teacher.objects.filter(is_active=True),
            request.GET.get('teacher'),
        )
    else:
        teacher = teacher_profile

    groups = get_grade_groups(
        student=student,
        subject=subject,
        teacher=teacher,
        academic_year=academic_year,
    )
    students = get_grade_students(
        group=group,
        subject=subject,
        teacher=teacher,
        academic_year=academic_year,
    )
    subjects = get_grade_subjects(
        group=group,
        student=student,
        teacher=teacher,
        academic_year=academic_year,
    )
    teachers = get_grade_teachers(
        group=group,
        student=student,
        subject=subject,
        academic_year=academic_year,
    )
    if not can_manage_all_grades:
        teachers = teachers.filter(pk=teacher_profile.pk)

    groups = _include_selected_option(groups, StudyGroup, group)
    students = _include_selected_option(students, Student, student)
    subjects = _include_selected_option(subjects, Subject, subject)
    teachers = _include_selected_option(teachers, Teacher, teacher)

    return JsonResponse({
        'groups': [
            {'id': group.pk, 'label': str(group)}
            for group in groups
        ],
        'students': [
            {
                'id': student.pk,
                'label': student.full_name,
                'group_id': student.group_id,
            }
            for student in students
        ],
        'subjects': [
            {'id': subject.pk, 'label': subject.name}
            for subject in subjects
        ],
        'teachers': [
            {'id': teacher.pk, 'label': teacher.full_name}
            for teacher in teachers
        ],
    })


def _calculate_average(grade_values: Iterable[str]) -> str:
    numeric_values: list[int] = []
    for value in grade_values:
        text = str(value).strip().upper()
        if text in {'1', '2', '3', '4', '5'}:
            numeric_values.append(int(text))
    if not numeric_values:
        return ''
    return f'{(sum(numeric_values) / len(numeric_values)):.2f}'


def _form_error_messages(form) -> list[str]:
    messages_by_field: list[str] = []
    for field_name, errors in form.errors.items():
        label = form.fields[field_name].label if field_name in form.fields else ''
        for error in errors:
            messages_by_field.append(f'{label}: {error}' if label else str(error))
    return messages_by_field


def _normalize_grade_value(value: str) -> str:
    return str(value or '').strip().upper()


def _normalize_final_grade_value(subject: Subject, value: str):
    normalized = Subject.normalize_final_grade(value)
    if normalized is None:
        return None
    if normalized not in subject.get_final_grade_allowed_values():
        raise ValidationError('Недопустимое значение для итоговой оценки по выбранному предмету.')
    return normalized


def _get_selected_object(queryset, raw_pk):
    if not raw_pk:
        return None
    try:
        return queryset.filter(pk=raw_pk).first()
    except (TypeError, ValueError):
        return None


def _include_selected_option(queryset, model, selected):
    if selected is None or not getattr(selected, 'pk', None):
        return queryset
    return model.objects.filter(
        Q(pk__in=queryset.values('pk')) | Q(pk=selected.pk),
    ).distinct()


def _current_academic_year() -> AcademicYear | None:
    return AcademicYear.get_for_date(timezone.localdate()) or AcademicYear.get_active()


def _filter_groups_by_academic_year(groups, selected_academic_year: AcademicYear | None):
    if selected_academic_year is None:
        return groups
    return groups.filter(academic_year=selected_academic_year)


def _result_year_for_student(student: Student, selected_year: AcademicYear | None = None) -> AcademicYear | None:
    if selected_year is not None:
        return selected_year
    if student and student.group_id:
        return student.group.academic_year
    return _current_academic_year()


def _redirect_journal(*, group=None, subject=None, academic_year=None):
    query = {}
    if group is not None:
        query['group'] = group.pk if hasattr(group, 'pk') else group
    if subject is not None:
        query['subject'] = subject.pk if hasattr(subject, 'pk') else subject
    if academic_year is not None:
        query['academic_year'] = academic_year.pk if hasattr(academic_year, 'pk') else academic_year

    url = reverse('journal')
    if query:
        return redirect(f'{url}?{urlencode(query)}')
    return redirect(url)


def _student_subject_allowed_for_teacher(student: Student, subject: Subject, teacher: Teacher | None = None) -> bool:
    if not student or not subject:
        return False

    if teacher is None:
        return get_student_allowed_subjects(student).filter(pk=subject.pk).exists()

    return get_student_subject_teachers(student, subject).filter(pk=teacher.pk).exists()


def _subjects_for_groups(groups, *, teacher: Teacher | None = None):
    group_ids = [group.pk for group in groups if group is not None]
    if not group_ids:
        return Subject.objects.none()

    group_assignments = GroupSubject.objects.filter(
        group_id__in=group_ids,
        is_active=True,
        subject__is_active=True,
    )
    individual_assignments = StudentSubject.objects.filter(
        student__group_id__in=group_ids,
        is_active=True,
        subject__is_active=True,
        student__is_active=True,
    )

    if teacher is not None:
        group_assignments = group_assignments.filter(teacher=teacher)
        individual_assignments = individual_assignments.filter(teacher=teacher)

    group_subject_ids = group_assignments.values_list('subject_id', flat=True)
    individual_subject_ids = individual_assignments.values_list('subject_id', flat=True)

    return (
        Subject.objects
        .filter(Q(pk__in=group_subject_ids) | Q(pk__in=individual_subject_ids))
        .distinct()
        .order_by('name')
    )


def _students_for_table(
    *,
    group: StudyGroup,
    subject: Subject,
    students_by_group: dict[int, list[Student]],
    group_subject_pairs: set[tuple[int, int]],
    individual_students_by_pair: dict[tuple[int, int], set[int]],
) -> list[Student]:
    """
    Возвращает учеников, которые должны попасть в таблицу конкретного предмета.

    Ученик попадает в таблицу, если:
    1) предмет назначен всей его группе через GroupSubject;
    2) предмет назначен ему индивидуально через StudentSubject.

    Карты назначений уже отфильтрованы по роли и выбранному преподавателю.
    """
    group_students = students_by_group.get(group.pk, [])
    if not group_students:
        return []

    assignment_key = (group.pk, subject.pk)
    table_student_ids: set[int] = set()
    if assignment_key in group_subject_pairs:
        table_student_ids.update(student.pk for student in group_students)

    table_student_ids.update(individual_students_by_pair.get(assignment_key, set()))

    if not table_student_ids:
        return []

    return [student for student in group_students if student.pk in table_student_ids]


def _table_assignment_maps(
    *,
    groups,
    subjects,
    teacher: Teacher | None = None,
) -> tuple[set[tuple[int, int]], dict[tuple[int, int], set[int]]]:
    group_ids = {group.pk for group in groups if group is not None}
    subject_ids = {subject.pk for subject in subjects if subject is not None}
    if not group_ids or not subject_ids:
        return set(), defaultdict(set)

    group_assignments = GroupSubject.objects.filter(
        group_id__in=group_ids,
        subject_id__in=subject_ids,
        is_active=True,
    )
    individual_assignments = StudentSubject.objects.filter(
        student__group_id__in=group_ids,
        subject_id__in=subject_ids,
        is_active=True,
        student__is_active=True,
    )

    if teacher is not None:
        group_assignments = group_assignments.filter(teacher=teacher)
        individual_assignments = individual_assignments.filter(teacher=teacher)

    group_subject_pairs = set(group_assignments.values_list('group_id', 'subject_id'))
    individual_students_by_pair: dict[tuple[int, int], set[int]] = defaultdict(set)
    for group_id, subject_id, student_id in individual_assignments.values_list(
        'student__group_id',
        'subject_id',
        'student_id',
    ):
        individual_students_by_pair[(group_id, subject_id)].add(student_id)

    return group_subject_pairs, individual_students_by_pair


def _build_journal_tables(
    *,
    groups,
    subjects,
    students,
    grade_qs,
    results_qs,
    selected_academic_year: AcademicYear | None = None,
    teacher: Teacher | None = None,
):
    journal_tables = []

    students_by_group: dict[int, list[Student]] = defaultdict(list)
    for student in students:
        if student.group_id:
            students_by_group[student.group_id].append(student)

    grades_map: dict[tuple[int, int], list[Grade]] = defaultdict(list)
    for grade in grade_qs:
        if grade.student and grade.student.group_id:
            grades_map[(grade.student.group_id, grade.subject_id)].append(grade)

    result_map: dict[tuple[int, int, int], SubjectResult] = {}
    for result in results_qs:
        result_map[(result.student_id, result.subject_id, result.academic_year_id)] = result

    group_subject_pairs, individual_students_by_pair = _table_assignment_maps(
        groups=groups,
        subjects=subjects,
        teacher=teacher,
    )

    for group in groups:
        if group is None:
            continue

        for subject in subjects:
            if subject is None:
                continue

            table_students = _students_for_table(
                group=group,
                subject=subject,
                students_by_group=students_by_group,
                group_subject_pairs=group_subject_pairs,
                individual_students_by_pair=individual_students_by_pair,
            )
            if not table_students:
                continue

            table_student_ids = {student.pk for student in table_students}
            subject_grades = [
                grade
                for grade in grades_map.get((group.pk, subject.pk), [])
                if grade.student_id in table_student_ids
            ]
            dates = sorted({grade.date for grade in subject_grades})
            row_map = {student.pk: {lesson_date: '' for lesson_date in dates} for student in table_students}

            for grade in subject_grades:
                if grade.student_id in row_map:
                    row_map[grade.student_id][grade.date] = str(grade.value)

            rows = []
            for student in table_students:
                grades_by_date = {}
                grade_values = []
                for lesson_date in dates:
                    value = row_map[student.pk][lesson_date]
                    grades_by_date[lesson_date] = value
                    if value:
                        grade_values.append(value)

                result_year = _result_year_for_student(student, selected_academic_year)
                subject_result = None
                if result_year is not None:
                    subject_result = result_map.get((student.pk, subject.pk, result_year.pk))

                rows.append(
                    {
                        'student': student,
                        'grades_by_date': grades_by_date,
                        'average_grade': _calculate_average(grade_values),
                        'exam_grade': '' if subject_result is None or subject_result.exam_grade is None else subject_result.exam_grade,
                        'final_grade': '' if subject_result is None or subject_result.final_grade is None else subject_result.final_grade,
                    }
                )

            journal_tables.append(
                {
                    'group': group,
                    'subject': subject,
                    'dates': dates,
                    'rows': rows,
                    'final_grade_options': sorted(subject.get_final_grade_allowed_values()),
                    'academic_year': selected_academic_year or group.academic_year,
                }
            )

    return journal_tables


def _save_inline_grades(
    request,
    *,
    role_mode: str,
    students: QuerySet[Student],
    subjects: QuerySet[Subject],
    teacher: Teacher | None = None,
    selected_academic_year: AcademicYear | None = None,
) -> bool:
    changed = 0
    student_map = {
        student.pk: student
        for student in students.select_related('group', 'group__academic_year')
    }
    subject_map = {
        subject.pk: subject
        for subject in subjects
    }
    student_ids = set(student_map)
    subject_ids = set(subject_map)
    teacher_group_subject_pairs: set[tuple[int, int]] = set()
    teacher_individual_subject_pairs: set[tuple[int, int]] = set()

    if role_mode == 'teacher' and teacher is not None and student_ids and subject_ids:
        teacher_group_subject_pairs = set(
            GroupSubject.objects
            .filter(
                group_id__in={student.group_id for student in student_map.values()},
                subject_id__in=subject_ids,
                teacher=teacher,
                is_active=True,
            )
            .values_list('group_id', 'subject_id')
        )
        teacher_individual_subject_pairs = set(
            StudentSubject.objects
            .filter(
                student_id__in=student_ids,
                subject_id__in=subject_ids,
                teacher=teacher,
                is_active=True,
            )
            .values_list('student_id', 'subject_id')
        )

    with transaction.atomic():
        for field_name, raw_value in request.POST.items():
            if not (
                field_name.startswith('grade__')
                or field_name.startswith('exam__')
                or field_name.startswith('final__')
            ):
                continue

            value = str(raw_value or '').strip()

            if field_name.startswith('exam__') or field_name.startswith('final__'):
                field_mode = 'exam' if field_name.startswith('exam__') else 'final'
                parts = field_name.split('__')
                if len(parts) != 3:
                    continue

                _, subject_id_raw, student_id_raw = parts
                try:
                    subject_id = int(subject_id_raw)
                    student_id = int(student_id_raw)
                except (TypeError, ValueError):
                    continue

                if student_id not in student_ids or subject_id not in subject_ids:
                    continue

                student = student_map.get(student_id)
                subject = subject_map.get(subject_id)
                if student is None or subject is None:
                    continue

                if (
                    role_mode == 'teacher'
                    and (
                        (student.group_id, subject_id) not in teacher_group_subject_pairs
                        and (student_id, subject_id) not in teacher_individual_subject_pairs
                    )
                ):
                    continue

                try:
                    normalized_value = _normalize_final_grade_value(subject, value)
                except ValidationError as exc:
                    messages.error(request, '; '.join(exc.messages))
                    transaction.set_rollback(True)
                    return False

                academic_year = _result_year_for_student(student, selected_academic_year)
                if academic_year is None:
                    messages.error(request, 'Не удалось определить учебный год для итоговой оценки.')
                    transaction.set_rollback(True)
                    return False

                result, _ = SubjectResult.objects.get_or_create(
                    student=student,
                    subject=subject,
                    academic_year=academic_year,
                )

                if field_mode == 'exam':
                    if result.exam_grade == normalized_value:
                        continue
                    result.exam_grade = normalized_value
                else:
                    if result.final_grade == normalized_value:
                        continue
                    result.final_grade = normalized_value

                try:
                    result.save()
                except ValidationError as exc:
                    messages.error(request, '; '.join(exc.messages))
                    transaction.set_rollback(True)
                    return False
                changed += 1
                continue

            normalized_grade_value = _normalize_grade_value(value)
            if normalized_grade_value and normalized_grade_value not in Grade.ALLOWED_VALUES:
                messages.error(request, 'Оценка должна быть 1-5 или Н.')
                transaction.set_rollback(True)
                return False

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

            if student_id not in student_ids or subject_id not in subject_ids:
                continue

            grade = (
                Grade.objects
                .filter(
                    student_id=student_id,
                    subject_id=subject_id,
                    date=grade_date,
                    student_id__in=student_ids,
                    subject_id__in=subject_ids,
                )
                .select_related('teacher', 'student', 'subject')
                .first()
            )
            if grade is None:
                continue

            if role_mode == 'teacher' and (teacher is None or grade.teacher_id != teacher.pk):
                continue

            if normalized_grade_value == '':
                grade.delete()
                changed += 1
                continue

            if grade.value == normalized_grade_value:
                continue

            grade.value = normalized_grade_value
            try:
                grade.save()
            except ValidationError as exc:
                messages.error(request, '; '.join(exc.messages))
                transaction.set_rollback(True)
                return False
            changed += 1

    if changed:
        messages.success(request, f'Изменения сохранены: {changed}.')
    else:
        messages.info(request, 'Изменений для сохранения нет.')
    return True


# -----------------------------------------------------------------------------
# Основной журнал
# -----------------------------------------------------------------------------


@login_required
def journal_view(request):
    if (
        not request.user.is_superuser
        and TemporaryCredential.objects.filter(login=request.user.username).exists()
    ):
        messages.info(request, 'Смените временный пароль перед работой с журналом.')
        return redirect('password_change')

    selected_group_id = request.GET.get('group')
    selected_subject_id = request.GET.get('subject')
    selected_year_id = request.GET.get('academic_year') or request.GET.get('year')

    academic_years = AcademicYear.objects.all().order_by('-starts_on')
    selected_academic_year = _get_selected_object(academic_years, selected_year_id) if selected_year_id else _current_academic_year()

    if request.user.is_superuser:
        return _journal_for_admin(
            request,
            selected_group_id=selected_group_id,
            selected_subject_id=selected_subject_id,
            academic_years=academic_years,
            selected_academic_year=selected_academic_year,
        )

    teacher = getattr(request.user, 'teacher_profile', None)
    student_profile = getattr(request.user, 'student_profile', None)

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
                'selected_group': None,
                'selected_group_id': '',
                'selected_subject_id': '',
                'selected_academic_year': selected_academic_year,
                'academic_years': academic_years,
                'grade_form': None,
                'role_mode': '',
            },
        )

    if teacher is not None:
        return _journal_for_teacher(
            request,
            teacher=teacher,
            selected_group_id=selected_group_id,
            selected_subject_id=selected_subject_id,
            academic_years=academic_years,
            selected_academic_year=selected_academic_year,
        )

    return _journal_for_student(
        request,
        student=student_profile,
        selected_subject_id=selected_subject_id,
        academic_years=academic_years,
        selected_academic_year=selected_academic_year,
    )


def _journal_for_admin(
    request,
    *,
    selected_group_id: str | None,
    selected_subject_id: str | None,
    academic_years,
    selected_academic_year: AcademicYear | None,
):
    role_mode = 'superuser'

    groups = (
        StudyGroup.objects
        .filter(is_active=True)
        .select_related('academic_year')
        .order_by('academic_year__name', 'name')
    )
    groups = _filter_groups_by_academic_year(groups, selected_academic_year)
    subjects = Subject.objects.filter(is_active=True).order_by('name')

    selected_group = _get_selected_object(groups, selected_group_id)
    selected_subject = _get_selected_object(subjects, selected_subject_id)

    groups_to_show = [selected_group] if selected_group else list(groups)

    if selected_subject:
        subjects_to_show = [selected_subject]
    elif selected_group:
        subjects_to_show = list(_subjects_for_groups(groups_to_show))
    else:
        subjects_to_show = list(subjects)

    students_qs = (
        Student.objects
        .filter(is_active=True, group__in=groups_to_show)
        .select_related('group', 'group__academic_year', 'instrument', 'user')
        .order_by('full_name')
    )
    students = list(students_qs)

    grade_qs = (
        Grade.objects
        .filter(student__in=students_qs, subject__in=subjects_to_show)
        .select_related('student', 'student__group', 'subject', 'teacher', 'academic_year')
    )
    if selected_academic_year is not None:
        grade_qs = grade_qs.filter(Q(academic_year=selected_academic_year) | Q(academic_year__isnull=True))

    result_year_ids = _result_year_ids(groups_to_show, selected_academic_year)
    results_qs = (
        SubjectResult.objects
        .filter(student__in=students_qs, subject__in=subjects_to_show, academic_year_id__in=result_year_ids)
        .select_related('student', 'student__group', 'subject', 'academic_year')
    )

    journal_tables = _build_journal_tables(
        groups=groups_to_show,
        subjects=subjects_to_show,
        students=students,
        grade_qs=grade_qs,
        results_qs=results_qs,
        selected_academic_year=selected_academic_year,
    )

    grade_form = _handle_grade_form(
        request,
        role_mode=role_mode,
        groups=groups,
        subjects=subjects,
        selected_group=selected_group,
        selected_subject=selected_subject,
        selected_academic_year=selected_academic_year,
    )

    if request.method == 'POST' and request.POST.get('action') == 'inline_edit':
        if _save_inline_grades(
            request,
            role_mode=role_mode,
            students=students_qs,
            subjects=Subject.objects.filter(pk__in=[subject.pk for subject in subjects_to_show]),
            selected_academic_year=selected_academic_year,
        ):
            return _redirect_journal(
                group=selected_group,
                subject=selected_subject,
                academic_year=selected_academic_year,
            )

    if isinstance(grade_form, HttpResponse):
        return grade_form

    return render(
        request,
        'journal.html',
        _journal_context(
            role_mode=role_mode,
            groups=groups,
            subjects=subjects,
            students=students,
            journal_tables=journal_tables,
            selected_group=selected_group,
            selected_group_id=selected_group_id,
            selected_subject_id=selected_subject_id,
            academic_years=academic_years,
            selected_academic_year=selected_academic_year,
            grade_form=grade_form,
        ),
    )


def _journal_for_teacher(
    request,
    *,
    teacher: Teacher,
    selected_group_id: str | None,
    selected_subject_id: str | None,
    academic_years,
    selected_academic_year: AcademicYear | None,
):
    role_mode = 'teacher'

    groups = get_teacher_groups(teacher).filter(is_active=True).select_related('academic_year')
    groups = _filter_groups_by_academic_year(groups, selected_academic_year)
    selected_group = _get_selected_object(groups, selected_group_id)
    groups_to_show = [selected_group] if selected_group else list(groups)

    subjects = get_teacher_subjects(teacher, selected_group) if selected_group else _subjects_for_groups(groups_to_show, teacher=teacher)
    selected_subject = _get_selected_object(subjects, selected_subject_id)
    subjects_to_show = [selected_subject] if selected_subject else list(subjects)

    students_qs = (
        Student.objects
        .filter(is_active=True, group__in=groups_to_show)
        .filter(
            Q(group__group_subjects__teacher=teacher, group__group_subjects__is_active=True)
            | Q(individual_subjects__teacher=teacher, individual_subjects__is_active=True)
        )
        .select_related('group', 'group__academic_year', 'instrument', 'user')
        .distinct()
        .order_by('full_name')
    )
    students = list(students_qs)

    grade_qs = (
        Grade.objects
        .filter(teacher=teacher, student__in=students_qs, subject__in=subjects_to_show)
        .select_related('student', 'student__group', 'subject', 'teacher', 'academic_year')
    )
    if selected_academic_year is not None:
        grade_qs = grade_qs.filter(Q(academic_year=selected_academic_year) | Q(academic_year__isnull=True))

    result_year_ids = _result_year_ids(groups_to_show, selected_academic_year)
    results_qs = (
        SubjectResult.objects
        .filter(student__in=students_qs, subject__in=subjects_to_show, academic_year_id__in=result_year_ids)
        .select_related('student', 'student__group', 'subject', 'academic_year')
    )

    journal_tables = _build_journal_tables(
        groups=groups_to_show,
        subjects=subjects_to_show,
        students=students,
        grade_qs=grade_qs,
        results_qs=results_qs,
        selected_academic_year=selected_academic_year,
        teacher=teacher,
    )

    grade_form = _handle_grade_form(
        request,
        role_mode=role_mode,
        groups=groups,
        subjects=subjects,
        selected_group=selected_group,
        selected_subject=selected_subject,
        selected_academic_year=selected_academic_year,
        teacher=teacher,
    )

    if request.method == 'POST' and request.POST.get('action') == 'inline_edit':
        if _save_inline_grades(
            request,
            role_mode=role_mode,
            students=students_qs,
            subjects=Subject.objects.filter(pk__in=[subject.pk for subject in subjects_to_show]),
            teacher=teacher,
            selected_academic_year=selected_academic_year,
        ):
            return _redirect_journal(
                group=selected_group,
                subject=selected_subject,
                academic_year=selected_academic_year,
            )

    if isinstance(grade_form, HttpResponse):
        return grade_form

    return render(
        request,
        'journal.html',
        _journal_context(
            role_mode=role_mode,
            groups=groups,
            subjects=subjects,
            students=students,
            journal_tables=journal_tables,
            selected_group=selected_group,
            selected_group_id=selected_group_id,
            selected_subject_id=selected_subject_id,
            academic_years=academic_years,
            selected_academic_year=selected_academic_year,
            grade_form=grade_form,
        ),
    )


def _journal_for_student(
    request,
    *,
    student: Student,
    selected_subject_id: str | None,
    academic_years,
    selected_academic_year: AcademicYear | None,
):
    role_mode = 'student'
    selected_group = student.group if student.group_id else None
    groups = [selected_group] if selected_group is not None else []
    students = [student]

    subjects = get_student_allowed_subjects(student)
    selected_subject = _get_selected_object(subjects, selected_subject_id)
    subjects_to_show = [selected_subject] if selected_subject else list(subjects)

    if request.method == 'POST':
        messages.error(request, 'Ученику недоступно редактирование оценок.')
        return _redirect_journal(subject=selected_subject, academic_year=selected_academic_year)

    grade_qs = (
        Grade.objects
        .filter(student=student, subject__in=subjects_to_show)
        .select_related('student', 'student__group', 'subject', 'teacher', 'academic_year')
    )
    if selected_academic_year is not None:
        grade_qs = grade_qs.filter(Q(academic_year=selected_academic_year) | Q(academic_year__isnull=True))

    result_year_ids = _result_year_ids(groups, selected_academic_year)
    results_qs = (
        SubjectResult.objects
        .filter(student=student, subject__in=subjects_to_show, academic_year_id__in=result_year_ids)
        .select_related('student', 'student__group', 'subject', 'academic_year')
    )

    journal_tables = _build_journal_tables(
        groups=groups,
        subjects=subjects_to_show,
        students=students,
        grade_qs=grade_qs,
        results_qs=results_qs,
        selected_academic_year=selected_academic_year,
    )

    return render(
        request,
        'journal.html',
        _journal_context(
            role_mode=role_mode,
            groups=groups,
            subjects=subjects,
            students=students,
            journal_tables=journal_tables,
            selected_group=selected_group,
            selected_group_id=str(selected_group.pk) if selected_group is not None else '',
            selected_subject_id=selected_subject_id,
            academic_years=academic_years,
            selected_academic_year=selected_academic_year,
            grade_form=None,
        ),
    )


def _result_year_ids(groups, selected_academic_year: AcademicYear | None) -> list[int]:
    if selected_academic_year is not None:
        return [selected_academic_year.pk]

    ids = [group.academic_year_id for group in groups if group is not None and group.academic_year_id]
    if ids:
        return list(set(ids))

    current_year = _current_academic_year()
    return [current_year.pk] if current_year is not None else []


def _handle_grade_form(
    request,
    *,
    role_mode: str,
    groups,
    subjects,
    selected_group: StudyGroup | None,
    selected_subject: Subject | None,
    selected_academic_year: AcademicYear | None,
    teacher: Teacher | None = None,
):
    if request.method == 'POST' and request.POST.get('action') == 'add_grade':
        posted_group = selected_group or _get_selected_object(groups, request.POST.get('group'))
        posted_subject = selected_subject or _get_selected_object(subjects, request.POST.get('subject'))

        if posted_group is None:
            messages.error(request, 'Выберите группу перед сохранением оценки.')
            return GradeCreateForm(
                request.POST,
                teacher=teacher,
                academic_year=selected_academic_year,
            )

        students_queryset = None
        if posted_subject is not None:
            students_queryset = get_students_for_group_subject(
                group=posted_group,
                subject=posted_subject,
                teacher=teacher,
            )

        grade_form = GradeCreateForm(
            request.POST,
            teacher=teacher,
            group=posted_group,
            students_queryset=students_queryset,
            academic_year=selected_academic_year,
        )
        if grade_form.is_valid():
            grade_form.save()
            messages.success(request, 'Оценка успешно добавлена.')
            return _redirect_journal(
                group=posted_group,
                subject=grade_form.cleaned_data.get('subject') or posted_subject,
                academic_year=selected_academic_year,
            )

        messages.error(request, ' '.join(_form_error_messages(grade_form)))
        return grade_form

    if selected_group is None:
        return None

    students_queryset = None
    if selected_subject is not None:
        students_queryset = get_students_for_group_subject(
            group=selected_group,
            subject=selected_subject,
            teacher=teacher,
        )

    return GradeCreateForm(
        None,
        teacher=teacher,
        group=selected_group,
        students_queryset=students_queryset,
        academic_year=selected_academic_year,
    )


def _journal_context(
    *,
    role_mode: str,
    groups,
    subjects,
    students,
    journal_tables,
    selected_group,
    selected_group_id,
    selected_subject_id,
    academic_years,
    selected_academic_year,
    grade_form,
):
    return {
        'role_mode': role_mode,
        'groups': groups,
        'subjects': subjects,
        'students': students,
        'journal_tables': journal_tables,
        'selected_group': selected_group,
        'selected_group_id': str(selected_group_id or ''),
        'selected_subject_id': str(selected_subject_id or ''),
        'academic_years': academic_years,
        'selected_academic_year': selected_academic_year,
        'selected_academic_year_id': str(selected_academic_year.pk) if selected_academic_year else '',
        'grade_form': grade_form,
    }


# -----------------------------------------------------------------------------
# Регистрация на курсы
# -----------------------------------------------------------------------------

COURSE_REGISTRATION_API_THROTTLE_LIMIT = 10
COURSE_REGISTRATION_API_THROTTLE_WINDOW = 60


def _load_registration_payload(request):
    if 'application/json' in request.headers.get('Content-Type', ''):
        try:
            return json.loads(request.body.decode('utf-8') or '{}')
        except json.JSONDecodeError:
            return None
    return request.POST


def _get_client_ip(request) -> str:
    if not getattr(settings, 'TRUST_X_FORWARDED_FOR', False):
        return request.META.get('REMOTE_ADDR', '') or 'unknown'

    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded_for:
        return forwarded_for.split(',', 1)[0].strip()
    return request.META.get('REMOTE_ADDR', '') or 'unknown'


def _registration_api_is_throttled(request) -> bool:
    cache_key = f'course_registration_api:{_get_client_ip(request)}'
    added = cache.add(cache_key, 1, COURSE_REGISTRATION_API_THROTTLE_WINDOW)
    if added:
        return False

    try:
        attempts = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, COURSE_REGISTRATION_API_THROTTLE_WINDOW)
        return False

    return attempts > COURSE_REGISTRATION_API_THROTTLE_LIMIT


def _get_registration_settings() -> CourseRegistrationSettings:
    return CourseRegistrationSettings.load()


def _get_telegram_redirect_url(settings_obj: CourseRegistrationSettings | None = None) -> str:
    settings_obj = settings_obj or _get_registration_settings()
    return settings_obj.telegram_group_url.strip()


def _get_application_credential(application: CourseApplication):
    if application is None or not application.pk:
        return None

    credential = getattr(application, 'temporary_credential', None)
    if credential is not None:
        return credential

    if application.generated_login:
        credential = TemporaryCredential.objects.filter(login=application.generated_login).first()
        if credential is not None:
            return credential

    return TemporaryCredential.objects.filter(course_application=application).first()


def course_registration_view(request):
    if request.method not in {'GET', 'POST'}:
        return HttpResponseNotAllowed(['GET', 'POST'])

    registration_settings = _get_registration_settings()
    form = CourseApplicationPublicForm(
        request.POST or None,
        registration_settings=registration_settings,
    )
    redirect_url = _get_telegram_redirect_url(registration_settings)

    if request.method == 'POST' and form.is_valid():
        application = form.save()
        credential = _get_application_credential(application)
        return render(
            request,
            'journal/course_registration.html',
            {
                'submitted': True,
                'application': application,
                'credential': credential,
                'redirect_url': redirect_url,
            },
        )

    return render(
        request,
        'journal/course_registration.html',
        {
            'form': form,
            'submitted': False,
            'redirect_url': redirect_url,
        },
    )


@csrf_exempt
def course_registration_api(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    if _registration_api_is_throttled(request):
        return JsonResponse(
            {
                'success': False,
                'message': 'Слишком много попыток регистрации. Попробуйте позже.',
            },
            status=429,
        )

    payload = _load_registration_payload(request)
    if payload is None:
        return JsonResponse({'success': False, 'message': 'Неверный формат запроса.'}, status=400)

    registration_settings = _get_registration_settings()
    form = CourseApplicationPublicForm(payload, registration_settings=registration_settings)
    redirect_url = _get_telegram_redirect_url(registration_settings)

    if form.is_valid():
        application = form.save()
        credential = _get_application_credential(application)
        return JsonResponse(
            {
                'success': True,
                'message': 'Заявка успешно отправлена.',
                'redirect_url': redirect_url,
                'application_id': application.pk,
                'status': application.status,
                'status_display': application.get_status_display(),
                'login': credential.login if credential else '',
                'temporary_password': credential.temporary_password if credential else '',
            },
            status=201,
        )

    return JsonResponse(
        {
            'success': False,
            'message': ' '.join(_form_error_messages(form)) or 'Форма не содержит данных для проверки.',
            'errors': form.errors,
        },
        status=400,
    )


# -----------------------------------------------------------------------------
# Экспорт временных учетных данных
# -----------------------------------------------------------------------------


def _xlsx_cell(value):
    return f'<c t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'


def _xlsx_row(values, row_number):
    cells = ''.join(_xlsx_cell(value) for value in values)
    return f'<row r="{row_number}">{cells}</row>'


def _build_student_credentials_xlsx(rows):
    sheet_rows = [
        _xlsx_row(['Логин', 'Временный пароль', 'Телефон ученика', 'Заявка'], 1),
    ]
    for index, row in enumerate(rows, start=2):
        sheet_rows.append(_xlsx_row(row, index))

    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<cols><col min="1" max="1" width="28" customWidth="1"/>'
        '<col min="2" max="2" width="24" customWidth="1"/>'
        '<col min="3" max="3" width="22" customWidth="1"/>'
        '<col min="4" max="4" width="34" customWidth="1"/></cols>'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        '</worksheet>'
    )

    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        archive.writestr(
            '[Content_Types].xml',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>',
        )
        archive.writestr(
            '_rels/.rels',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>',
        )
        archive.writestr(
            'xl/workbook.xml',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Учетные данные" sheetId="1" r:id="rId1"/></sheets>'
            '</workbook>',
        )
        archive.writestr(
            'xl/_rels/workbook.xml.rels',
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>',
        )
        archive.writestr('xl/worksheets/sheet1.xml', worksheet)

    return output.getvalue()


@user_passes_test(lambda user: user.is_active and user.is_superuser)
def export_student_credentials_xlsx(request):
    rows = (
        TemporaryCredential.objects
        .select_related('course_application')
        .order_by('id')
    )

    data_rows = []
    for credential in rows:
        application_name = credential.course_application.full_name if credential.course_application_id else ''
        data_rows.append(
            [
                credential.login,
                credential.temporary_password,
                credential.student_phone,
                application_name,
            ]
        )

    content = _build_student_credentials_xlsx(data_rows)
    response = HttpResponse(
        content,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    filename = f'student_credentials_{timezone.localdate():%Y_%m_%d}.xlsx'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response

@user_passes_test(lambda user: user.is_active and user.is_superuser)
def export_all_data_excel(request):
    workbook = build_full_export_workbook()

    now = timezone.localtime()
    filename = f'journal_export_{now:%Y-%m-%d_%H-%M}.xlsx'
    encoded_filename = quote(filename)

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )

    response['Content-Disposition'] = (
        f"attachment; filename={filename}; filename*=UTF-8''{encoded_filename}"
    )

    workbook.save(response)

    return response
