from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from io import BytesIO
import json
from typing import Iterable
from urllib.parse import quote, urlencode
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import Max, Prefetch, Q, QuerySet
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .services.excel_export import build_full_export_workbook

from .account_utils import user_has_temporary_credential
from .assignment_options import (
    active_group_queryset,
    active_student_queryset,
    assignment_teacher_queryset,
    group_subject_queryset,
    is_default_specialty_assignment,
    student_subject_queryset,
)
from .forms import (
    CourseApplicationPublicForm,
    GradeCreateForm,
    get_student_allowed_subjects,
    get_student_subject_teachers,
    get_students_for_group_subject,
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
    StudentEnrollment,
    StudentSubject,
    StudyGroup,
    Subject,
    SubjectResult,
    Teacher,
    TemporaryCredential,
    CourseRegistrationRateLimit,
)


async def _run_db_sync(func, *args, **kwargs):
    database_engine = settings.DATABASES['default']['ENGINE']
    # SQLite test transactions are connection/thread-bound. PostgreSQL, used in
    # production, can execute independent requests concurrently in the pool.
    thread_sensitive = database_engine.endswith('sqlite3')
    return await sync_to_async(func, thread_sensitive=thread_sensitive)(*args, **kwargs)


@require_GET
def healthcheck_view(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({'status': 'unavailable'}, status=503)
    return JsonResponse({'status': 'ok'})


# -----------------------------------------------------------------------------
# Общие helper-функции журнала
# -----------------------------------------------------------------------------


async def password_help_view(request):
    return await _run_db_sync(_password_help_view_sync, request)


def _password_help_view_sync(request):
    contacts = PasswordRecoveryContact.objects.filter(is_active=True)
    return render(
        request,
        'registration/password_help.html',
        {'contacts': contacts},
    )


@login_required
@require_GET
async def grade_options_api(request):
    return await _run_db_sync(_grade_options_api_sync, request)


def _grade_options_api_sync(request):
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
    ) or AcademicYear.get_active()

    if can_manage_all_grades:
        teacher = _get_selected_object(
            Teacher.objects.filter(is_active=True),
            request.GET.get('teacher'),
        )
    else:
        teacher = teacher_profile

    changed_field = request.GET.get('changed') or ''
    strict_options = request.GET.get('strict') == '1'

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

    if not strict_options or changed_field == 'group':
        groups = _include_selected_option(groups, StudyGroup, group)
    if not strict_options or changed_field == 'student':
        students = _include_selected_option(students, Student, student)
    if not strict_options or changed_field == 'subject':
        subjects = _include_selected_option(subjects, Subject, subject)
    if not strict_options or changed_field == 'teacher':
        teachers = _include_selected_option(teachers, Teacher, teacher)

    groups = groups.select_related('academic_year')
    students = (
        students
        .select_related('group', 'group__academic_year')
        .prefetch_related(None)
        .prefetch_related(
            Prefetch(
                'enrollments',
                queryset=StudentEnrollment.objects.filter(
                    academic_year=academic_year,
                ).select_related('group', 'academic_year'),
                to_attr='journal_enrollments',
            ),
        )
    )
    defaults = {}
    enrollment = student.enrollment_for_year(academic_year) if student is not None else None
    if enrollment is not None:
        defaults['group_id'] = enrollment.group_id
        defaults['academic_year_id'] = enrollment.academic_year_id
    elif group is not None and group.academic_year_id:
        defaults['academic_year_id'] = group.academic_year_id

    student_options = []
    for option_student in students:
        option_enrollment = option_student.enrollment_for_year(academic_year)
        student_options.append({
            'id': option_student.pk,
            'label': option_student.full_name,
            'group_id': option_enrollment.group_id if option_enrollment is not None else None,
            'academic_year_id': academic_year.pk if academic_year is not None else None,
        })

    return JsonResponse({
        'groups': [
            {
                'id': group.pk,
                'label': str(group),
                'academic_year_id': group.academic_year_id,
            }
            for group in groups
        ],
        'students': student_options,
        'subjects': [
            {'id': subject.pk, 'label': subject.name}
            for subject in subjects
        ],
        'teachers': [
            {'id': teacher.pk, 'label': teacher.full_name}
            for teacher in teachers
        ],
        'defaults': defaults,
    })


@login_required
@user_passes_test(lambda user: user.is_active and user.is_staff)
@require_GET
async def assignment_options_api(request):
    return await _run_db_sync(_assignment_options_api_sync, request)


def _assignment_options_api_sync(request):
    assignment_type = request.GET.get('type')
    if assignment_type not in {'group_subject', 'student_subject'}:
        return JsonResponse(
            {'error': 'Не удалось определить тип таблицы для связанных полей.'},
            status=400,
        )

    group = _get_selected_object(
        StudyGroup.objects.filter(is_active=True).select_related('academic_year'),
        request.GET.get('group'),
    )
    student = _get_selected_object(
        Student.objects.filter(is_active=True).select_related('group', 'group__academic_year'),
        request.GET.get('student'),
    )
    subject = _get_selected_object(
        Subject.objects.filter(is_active=True),
        request.GET.get('subject'),
    )
    teacher = _get_selected_object(
        Teacher.objects.filter(is_active=True),
        request.GET.get('teacher'),
    )

    if assignment_type == 'group_subject':
        subjects = group_subject_queryset()
        defaults = _group_subject_defaults(group)
    else:
        subjects = student_subject_queryset()
        defaults = _student_subject_defaults(student, subject)

    groups = active_group_queryset()
    students = active_student_queryset()
    teachers = assignment_teacher_queryset(subject)

    groups = _include_selected_option(groups, StudyGroup, group)
    students = _include_selected_option(students, Student, student)
    subjects = _include_selected_option(subjects, Subject, subject)
    teachers = _include_selected_option(teachers, Teacher, teacher)

    groups = groups.select_related('academic_year')
    students = students.select_related('group', 'group__academic_year')
    if teacher is None and teachers.count() == 1:
        defaults['teacher_id'] = teachers.values_list('pk', flat=True).first()

    return JsonResponse({
        'groups': [
            {
                'id': item.pk,
                'label': str(item),
                'academic_year_id': item.academic_year_id,
            }
            for item in groups
        ],
        'students': [
            {
                'id': item.pk,
                'label': item.full_name,
                'group_id': item.group_id,
                'academic_year_id': item.group.academic_year_id if item.group_id else None,
            }
            for item in students
        ],
        'subjects': [
            {
                'id': item.pk,
                'label': item.name,
                'is_individual': item.is_specialty,
                'default_is_specialty': is_default_specialty_assignment(item),
                'final_grade_type': item.final_grade_type,
            }
            for item in subjects
        ],
        'teachers': [
            {'id': item.pk, 'label': item.full_name}
            for item in teachers
        ],
        'defaults': defaults,
    })


def _group_subject_defaults(group: StudyGroup | None) -> dict:
    defaults = {}
    if group is None:
        return defaults

    if group.academic_year_id:
        defaults['academic_year_id'] = group.academic_year_id

    max_sort_order = (
        GroupSubject.objects
        .filter(group=group)
        .aggregate(value=Max('sort_order'))['value']
    )
    defaults['sort_order'] = (max_sort_order or 0) + 10
    return defaults


def _student_subject_defaults(student: Student | None, subject: Subject | None) -> dict:
    defaults = {}
    if student is not None and student.group_id:
        defaults['group_id'] = student.group_id
        defaults['academic_year_id'] = student.group.academic_year_id
    if subject is not None:
        defaults['is_specialty'] = is_default_specialty_assignment(subject)
        defaults['subject_is_individual'] = subject.is_specialty
        defaults['final_grade_type'] = subject.final_grade_type
    return defaults


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
    return AcademicYear.get_active() or AcademicYear.get_for_date(timezone.localdate())


def _can_edit_academic_year(academic_year: AcademicYear | None) -> bool:
    return academic_year is not None and academic_year.is_active


def _reject_archived_academic_year_post(
    request,
    selected_academic_year: AcademicYear | None,
    *,
    selected_group=None,
    selected_subject=None,
):
    if request.method != 'POST' or _can_edit_academic_year(selected_academic_year):
        return None

    messages.error(
        request,
        'Архивный учебный год доступен только для просмотра. Изменения можно вносить только в активном учебном году.',
    )
    return _redirect_journal(
        group=selected_group,
        subject=selected_subject,
        academic_year=selected_academic_year,
    )


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


def _student_subject_allowed_for_teacher(
    student: Student,
    subject: Subject,
    teacher: Teacher | None = None,
    academic_year: AcademicYear | None = None,
) -> bool:
    if not student or not subject:
        return False

    if teacher is None:
        return get_student_allowed_subjects(
            student,
            academic_year,
        ).filter(pk=subject.pk).exists()

    return get_student_subject_teachers(
        student,
        subject,
        academic_year,
    ).filter(pk=teacher.pk).exists()


def _subjects_for_groups(
    groups,
    *,
    teacher: Teacher | None = None,
    academic_year: AcademicYear | None = None,
):
    group_ids = [group.pk for group in groups if group is not None]
    if not group_ids:
        return Subject.objects.none()

    academic_year = academic_year or groups[0].academic_year
    group_assignments = GroupSubject.objects.filter(
        group_id__in=group_ids,
        is_active=True,
    )
    enrollment_student_ids = StudentEnrollment.objects.filter(
        academic_year=academic_year,
        group_id__in=group_ids,
    ).values_list('student_id', flat=True)
    individual_assignments = StudentSubject.objects.filter(
        academic_year=academic_year,
        student_id__in=enrollment_student_ids,
        is_active=True,
    )
    if academic_year.is_active:
        group_assignments = group_assignments.filter(subject__is_active=True)
        individual_assignments = individual_assignments.filter(
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
    enrollments_by_group: dict[int, list[StudentEnrollment]],
    group_subject_pairs: set[tuple[int, int]],
    individual_students_by_pair: dict[tuple[int, int], set[int]],
) -> list[StudentEnrollment]:
    """
    Возвращает учеников, которые должны попасть в таблицу конкретного предмета.

    Ученик попадает в таблицу, если:
    1) предмет назначен всей его группе через GroupSubject;
    2) предмет назначен ему индивидуально через StudentSubject.

    Карты назначений уже отфильтрованы по роли и выбранному преподавателю.
    """
    group_enrollments = enrollments_by_group.get(group.pk, [])
    if not group_enrollments:
        return []

    assignment_key = (group.pk, subject.pk)
    table_student_ids: set[int] = set()
    if assignment_key in group_subject_pairs:
        table_student_ids.update(
            enrollment.student_id
            for enrollment in group_enrollments
        )

    table_student_ids.update(individual_students_by_pair.get(assignment_key, set()))

    if not table_student_ids:
        return []

    return [
        enrollment
        for enrollment in group_enrollments
        if enrollment.student_id in table_student_ids
    ]


def _table_assignment_maps(
    *,
    groups,
    subjects,
    academic_year: AcademicYear,
    enrollment_group_by_student: dict[int, int | None],
    teacher: Teacher | None = None,
) -> tuple[
    set[tuple[int, int]],
    dict[tuple[int, int], set[int]],
    dict[tuple[int, int], tuple[str, str]],
]:
    group_ids = {group.pk for group in groups if group is not None}
    subject_ids = {subject.pk for subject in subjects if subject is not None}
    if not group_ids or not subject_ids:
        return set(), defaultdict(set), {}

    group_assignments = GroupSubject.objects.filter(
        group_id__in=group_ids,
        subject_id__in=subject_ids,
        is_active=True,
    )
    individual_assignments = StudentSubject.objects.filter(
        academic_year=academic_year,
        student_id__in=enrollment_group_by_student,
        subject_id__in=subject_ids,
        is_active=True,
    )

    if teacher is not None:
        group_assignments = group_assignments.filter(teacher=teacher)
        individual_assignments = individual_assignments.filter(teacher=teacher)

    group_assignment_rows = list(group_assignments.values_list(
        'group_id',
        'subject_id',
        'subject_name_snapshot',
        'final_grade_type_snapshot',
    ))
    group_subject_pairs = {
        (group_id, subject_id)
        for group_id, subject_id, _subject_name, _final_grade_type in group_assignment_rows
    }
    individual_students_by_pair: dict[tuple[int, int], set[int]] = defaultdict(set)
    assignment_metadata: dict[tuple[int, int], tuple[str, str]] = {
        (group_id, subject_id): (subject_name, final_grade_type)
        for group_id, subject_id, subject_name, final_grade_type in group_assignment_rows
    }
    for student_id, subject_id, subject_name, final_grade_type in individual_assignments.values_list(
        'student_id',
        'subject_id',
        'subject_name_snapshot',
        'final_grade_type_snapshot',
    ):
        group_id = enrollment_group_by_student.get(student_id)
        if group_id not in group_ids:
            continue
        individual_students_by_pair[(group_id, subject_id)].add(student_id)
        assignment_metadata.setdefault(
            (group_id, subject_id),
            (subject_name, final_grade_type),
        )

    return group_subject_pairs, individual_students_by_pair, assignment_metadata


def _final_grade_options(final_grade_type: str) -> list[str]:
    if final_grade_type == Subject.FINAL_GRADE_TYPE_PASS_FAIL:
        return ['Зачет', 'Незачет']
    return ['1', '2', '3', '4', '5', 'Н']


def _build_journal_tables(
    *,
    groups,
    subjects,
    enrollments,
    grade_qs,
    results_qs,
    selected_academic_year: AcademicYear | None = None,
    teacher: Teacher | None = None,
):
    journal_tables = []

    enrollments_by_group: dict[int, list[StudentEnrollment]] = defaultdict(list)
    enrollment_group_by_student: dict[int, int | None] = {}
    for enrollment in enrollments:
        enrollment_group_by_student[enrollment.student_id] = enrollment.group_id
        if enrollment.group_id:
            enrollments_by_group[enrollment.group_id].append(enrollment)

    grades_map: dict[tuple[int, int], list[Grade]] = defaultdict(list)
    for grade in grade_qs:
        if grade.enrollment_id and grade.enrollment.group_id:
            grades_map[(grade.enrollment.group_id, grade.subject_id)].append(grade)

    result_map: dict[tuple[int, int, int], SubjectResult] = {}
    for result in results_qs:
        result_map[(result.student_id, result.subject_id, result.academic_year_id)] = result

    if selected_academic_year is None:
        return []
    (
        group_subject_pairs,
        individual_students_by_pair,
        assignment_metadata,
    ) = _table_assignment_maps(
        groups=groups,
        subjects=subjects,
        academic_year=selected_academic_year,
        enrollment_group_by_student=enrollment_group_by_student,
        teacher=teacher,
    )

    for group in groups:
        if group is None:
            continue

        for subject in subjects:
            if subject is None:
                continue

            table_enrollments = _students_for_table(
                group=group,
                subject=subject,
                enrollments_by_group=enrollments_by_group,
                group_subject_pairs=group_subject_pairs,
                individual_students_by_pair=individual_students_by_pair,
            )
            if not table_enrollments:
                continue

            table_student_ids = {
                enrollment.student_id
                for enrollment in table_enrollments
            }
            subject_grades = [
                grade
                for grade in grades_map.get((group.pk, subject.pk), [])
                if grade.student_id in table_student_ids
            ]
            dates = sorted({grade.date for grade in subject_grades})
            row_map = {
                enrollment.student_id: {
                    lesson_date: ''
                    for lesson_date in dates
                }
                for enrollment in table_enrollments
            }

            for grade in subject_grades:
                if grade.student_id in row_map:
                    row_map[grade.student_id][grade.date] = str(grade.value)

            rows = []
            for enrollment in table_enrollments:
                student = enrollment.student
                grades_by_date = {}
                grade_values = []
                for lesson_date in dates:
                    value = row_map[student.pk][lesson_date]
                    grades_by_date[lesson_date] = value
                    if value:
                        grade_values.append(value)

                subject_result = result_map.get(
                    (student.pk, subject.pk, selected_academic_year.pk),
                )

                rows.append(
                    {
                        'student': student,
                        'student_name': enrollment.full_name,
                        'enrollment': enrollment,
                        'grades_by_date': grades_by_date,
                        'average_grade': _calculate_average(grade_values),
                        'exam_grade': '' if subject_result is None or subject_result.exam_grade is None else subject_result.exam_grade,
                        'final_grade': '' if subject_result is None or subject_result.final_grade is None else subject_result.final_grade,
                    }
                )

            subject_name, final_grade_type = assignment_metadata.get(
                (group.pk, subject.pk),
                (subject.name, subject.final_grade_type),
            )
            journal_tables.append(
                {
                    'group': group,
                    'subject': subject,
                    'subject_name': subject_name or subject.name,
                    'dates': dates,
                    'rows': rows,
                    'final_grade_options': _final_grade_options(
                        final_grade_type or subject.final_grade_type,
                    ),
                    'academic_year': selected_academic_year,
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
    if not _can_edit_academic_year(selected_academic_year):
        messages.error(
            request,
            'Архивный учебный год доступен только для просмотра. Изменения можно вносить только в активном учебном году.',
        )
        return False

    changed = 0
    student_map = {
        student.pk: student
        for student in students
    }
    subject_map = {
        subject.pk: subject
        for subject in subjects
    }
    student_ids = set(student_map)
    subject_ids = set(subject_map)
    enrollment_group_by_student = dict(
        StudentEnrollment.objects.filter(
            academic_year=selected_academic_year,
            student_id__in=student_ids,
        ).values_list('student_id', 'group_id')
    )
    teacher_group_subject_pairs: set[tuple[int, int]] = set()
    teacher_individual_subject_pairs: set[tuple[int, int]] = set()

    if role_mode == 'teacher' and teacher is not None and student_ids and subject_ids:
        teacher_group_subject_pairs = set(
            GroupSubject.objects
            .filter(
                group_id__in={
                    group_id
                    for group_id in enrollment_group_by_student.values()
                    if group_id is not None
                },
                subject_id__in=subject_ids,
                teacher=teacher,
                group__academic_year=selected_academic_year,
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
                academic_year=selected_academic_year,
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
                        (
                            enrollment_group_by_student.get(student_id),
                            subject_id,
                        ) not in teacher_group_subject_pairs
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

                academic_year = selected_academic_year
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
async def journal_view(request):
    return await _run_db_sync(_journal_view_sync, request)


def _journal_view_sync(request):
    if (
        not request.user.is_superuser
        and user_has_temporary_credential(request.user)
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
                'can_edit_journal': False,
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
        .all()
        .select_related('academic_year')
        .order_by('academic_year__name', 'name')
    )
    groups = _filter_groups_by_academic_year(groups, selected_academic_year)
    if selected_academic_year and selected_academic_year.is_active:
        groups = groups.filter(is_active=True)
    subjects = get_grade_subjects(academic_year=selected_academic_year)
    can_edit_journal = _can_edit_academic_year(selected_academic_year)

    selected_group = _get_selected_object(groups, selected_group_id)
    selected_subject = _get_selected_object(subjects, selected_subject_id)

    groups_to_show = [selected_group] if selected_group else []

    if selected_subject:
        subjects_to_show = [selected_subject]
    elif selected_group:
        subjects_to_show = list(_subjects_for_groups(
            groups_to_show,
            academic_year=selected_academic_year,
        ))
    else:
        subjects_to_show = []

    enrollments_qs = (
        StudentEnrollment.objects
        .filter(
            academic_year=selected_academic_year,
            group__in=groups_to_show,
        )
        .select_related('student', 'student__instrument', 'student__user', 'group', 'academic_year')
        .order_by('full_name')
    )
    if can_edit_journal:
        enrollments_qs = enrollments_qs.filter(is_active=True, student__is_active=True)
    enrollments = list(enrollments_qs)
    student_ids = [enrollment.student_id for enrollment in enrollments]
    students_qs = Student.objects.filter(pk__in=student_ids).order_by('full_name')
    students = list(students_qs)

    grade_qs = (
        Grade.objects
        .filter(
            enrollment_id__in=[enrollment.pk for enrollment in enrollments],
            subject__in=subjects_to_show,
            academic_year=selected_academic_year,
        )
        .select_related('student', 'enrollment', 'enrollment__group', 'subject', 'teacher', 'academic_year')
    )

    result_year_ids = _result_year_ids(groups_to_show, selected_academic_year)
    results_qs = (
        SubjectResult.objects
        .filter(
            enrollment_id__in=[enrollment.pk for enrollment in enrollments],
            subject__in=subjects_to_show,
            academic_year_id__in=result_year_ids,
        )
        .select_related('student', 'enrollment', 'enrollment__group', 'subject', 'academic_year')
    )

    journal_tables = _build_journal_tables(
        groups=groups_to_show,
        subjects=subjects_to_show,
        enrollments=enrollments,
        grade_qs=grade_qs,
        results_qs=results_qs,
        selected_academic_year=selected_academic_year,
    )

    archived_post_response = _reject_archived_academic_year_post(
        request,
        selected_academic_year,
        selected_group=selected_group,
        selected_subject=selected_subject,
    )
    if archived_post_response is not None:
        return archived_post_response

    grade_form = None
    if can_edit_journal:
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
            can_edit_journal=can_edit_journal,
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

    groups = get_grade_groups(teacher=teacher, academic_year=selected_academic_year).select_related('academic_year')
    selected_group = _get_selected_object(groups, selected_group_id)
    groups_to_show = [selected_group] if selected_group else []
    can_edit_journal = _can_edit_academic_year(selected_academic_year)

    subjects = get_teacher_subjects(
        teacher,
        selected_group,
        selected_academic_year,
    )
    selected_subject = _get_selected_object(subjects, selected_subject_id)
    subjects_to_show = (
        [selected_subject]
        if selected_subject
        else (list(subjects) if selected_group else [])
    )

    eligible_students = get_grade_students(
        group=selected_group,
        teacher=teacher,
        academic_year=selected_academic_year,
    )
    enrollments_qs = (
        StudentEnrollment.objects
        .filter(
            academic_year=selected_academic_year,
            group__in=groups_to_show,
            student_id__in=eligible_students.values_list('pk', flat=True),
        )
        .select_related('student', 'student__instrument', 'student__user', 'group', 'academic_year')
        .order_by('full_name')
    )
    if can_edit_journal:
        enrollments_qs = enrollments_qs.filter(is_active=True, student__is_active=True)
    enrollments = list(enrollments_qs)
    student_ids = [enrollment.student_id for enrollment in enrollments]
    students_qs = Student.objects.filter(pk__in=student_ids).order_by('full_name')
    students = list(students_qs)

    grade_qs = (
        Grade.objects
        .filter(
            teacher=teacher,
            enrollment_id__in=[enrollment.pk for enrollment in enrollments],
            subject__in=subjects_to_show,
            academic_year=selected_academic_year,
        )
        .select_related('student', 'enrollment', 'enrollment__group', 'subject', 'teacher', 'academic_year')
    )

    result_year_ids = _result_year_ids(groups_to_show, selected_academic_year)
    results_qs = (
        SubjectResult.objects
        .filter(
            enrollment_id__in=[enrollment.pk for enrollment in enrollments],
            subject__in=subjects_to_show,
            academic_year_id__in=result_year_ids,
        )
        .select_related('student', 'enrollment', 'enrollment__group', 'subject', 'academic_year')
    )

    journal_tables = _build_journal_tables(
        groups=groups_to_show,
        subjects=subjects_to_show,
        enrollments=enrollments,
        grade_qs=grade_qs,
        results_qs=results_qs,
        selected_academic_year=selected_academic_year,
        teacher=teacher,
    )

    archived_post_response = _reject_archived_academic_year_post(
        request,
        selected_academic_year,
        selected_group=selected_group,
        selected_subject=selected_subject,
    )
    if archived_post_response is not None:
        return archived_post_response

    grade_form = None
    if can_edit_journal:
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
            can_edit_journal=can_edit_journal,
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
    enrollment = student.enrollment_for_year(selected_academic_year)
    selected_group = enrollment.group if enrollment is not None else None
    groups = [selected_group] if selected_group is not None else []
    students = [student]
    enrollments = [enrollment] if enrollment is not None else []

    subjects = get_student_allowed_subjects(student, selected_academic_year)
    selected_subject = _get_selected_object(subjects, selected_subject_id)
    subjects_to_show = [selected_subject] if selected_subject else list(subjects)

    if request.method == 'POST':
        messages.error(request, 'Ученику недоступно редактирование оценок.')
        return _redirect_journal(subject=selected_subject, academic_year=selected_academic_year)

    grade_qs = (
        Grade.objects
        .filter(
            enrollment=enrollment,
            subject__in=subjects_to_show,
            academic_year=selected_academic_year,
        )
        .select_related('student', 'enrollment', 'enrollment__group', 'subject', 'teacher', 'academic_year')
    )

    result_year_ids = _result_year_ids(groups, selected_academic_year)
    results_qs = (
        SubjectResult.objects
        .filter(
            enrollment=enrollment,
            subject__in=subjects_to_show,
            academic_year_id__in=result_year_ids,
        )
        .select_related('student', 'enrollment', 'enrollment__group', 'subject', 'academic_year')
    )

    journal_tables = _build_journal_tables(
        groups=groups,
        subjects=subjects_to_show,
        enrollments=enrollments,
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
            can_edit_journal=False,
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
                academic_year=selected_academic_year,
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
            academic_year=selected_academic_year,
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
    can_edit_journal=False,
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
        'can_edit_journal': can_edit_journal,
    }


# -----------------------------------------------------------------------------
# Регистрация на курсы
# -----------------------------------------------------------------------------

COURSE_REGISTRATION_API_THROTTLE_LIMIT = 10
COURSE_REGISTRATION_API_THROTTLE_WINDOW = 60


def _load_registration_payload(request):
    if request.content_type == 'application/json':
        try:
            payload = json.loads(request.body.decode('utf-8') or '{}')
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None
    return request.POST


def _get_client_ip(request) -> str:
    remote_addr = request.META.get('REMOTE_ADDR', '') or 'unknown'
    if not getattr(settings, 'TRUST_X_FORWARDED_FOR', False):
        return remote_addr

    forwarded_ips = [
        item.strip()
        for item in request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')
        if item.strip()
    ]
    trusted_proxy_count = getattr(settings, 'TRUSTED_PROXY_COUNT', 1)
    if len(forwarded_ips) < trusted_proxy_count:
        return remote_addr

    # Read from the trusted (right-hand) side of the chain. With one reverse
    # proxy this returns the address appended by that proxy instead of a
    # spoofable client-supplied first value.
    return forwarded_ips[-trusted_proxy_count]


def _registration_is_throttled(request) -> bool:
    cache_key = f'course_registration:{_get_client_ip(request)}'
    now = timezone.now()
    window_start_limit = now - timedelta(seconds=COURSE_REGISTRATION_API_THROTTLE_WINDOW)

    with transaction.atomic():
        CourseRegistrationRateLimit.objects.filter(window_started_at__lt=window_start_limit).delete()
        rate_limit, _created = (
            CourseRegistrationRateLimit.objects
            .select_for_update()
            .get_or_create(
                cache_key=cache_key,
                defaults={
                    'attempts': 0,
                    'window_started_at': now,
                },
            )
        )

        if rate_limit.window_started_at < window_start_limit:
            rate_limit.attempts = 1
            rate_limit.window_started_at = now
            rate_limit.save(update_fields=['attempts', 'window_started_at', 'updated_at'])
            return False

        rate_limit.attempts += 1
        rate_limit.save(update_fields=['attempts', 'updated_at'])
        return rate_limit.attempts > COURSE_REGISTRATION_API_THROTTLE_LIMIT


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


async def course_registration_view(request):
    return await _run_db_sync(_course_registration_view_sync, request)


def _course_registration_view_sync(request):
    if request.method not in {'GET', 'POST'}:
        return HttpResponseNotAllowed(['GET', 'POST'])

    registration_settings = _get_registration_settings()
    form = CourseApplicationPublicForm(
        request.POST or None,
        registration_settings=registration_settings,
    )
    redirect_url = _get_telegram_redirect_url(registration_settings)

    if request.method == 'POST' and _registration_is_throttled(request):
        form.add_error(
            None,
            'Слишком много попыток регистрации. Подождите минуту и попробуйте снова.',
        )
        return render(
            request,
            'journal/course_registration.html',
            {
                'form': form,
                'submitted': False,
                'redirect_url': redirect_url,
            },
            status=429,
        )

    if request.method == 'POST' and form.is_valid():
        try:
            application = form.save()
        except IntegrityError:
            form.add_error(
                'student_phone',
                'Заявка с этим номером телефона уже зарегистрирована на текущий учебный год.',
            )
            return render(
                request,
                'journal/course_registration.html',
                {
                    'form': form,
                    'submitted': False,
                    'redirect_url': redirect_url,
                },
                status=409,
            )
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


async def course_registration_api(request):
    return await _run_db_sync(_course_registration_api_sync, request)


def _course_registration_api_sync(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    if _registration_is_throttled(request):
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
        try:
            application = form.save()
        except IntegrityError:
            return JsonResponse(
                {
                    'success': False,
                    'message': (
                        'Заявка с этим номером телефона уже зарегистрирована '
                        'на текущий учебный год.'
                    ),
                    'errors': {
                        'student_phone': [
                            'Этот номер телефона уже используется в заявке.',
                        ],
                    },
                },
                status=409,
            )
        credential = _get_application_credential(application)
        return JsonResponse(
            {
                'success': True,
                'message': 'Заявка успешно отправлена.',
                'redirect_url': redirect_url,
                'application_id': application.pk,
                'status': application.status,
                'status_display': application.get_status_display(),
                'credentials_created': credential is not None,
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
@require_GET
async def export_student_credentials_xlsx(request):
    return await _run_db_sync(_export_student_credentials_xlsx_sync, request)


def _export_student_credentials_xlsx_sync(request):
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
@require_GET
async def export_all_data_excel(request):
    return await _run_db_sync(_export_all_data_excel_sync, request)


def _export_all_data_excel_sync(request):
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
