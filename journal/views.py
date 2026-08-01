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
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import Max, Prefetch, Q, QuerySet
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .services.excel_export import build_full_export_workbook
from .access_scope import JournalAccessScope

from .assessment_services import (
    assessment_rows_for_student,
    assessment_sections_for_teacher,
    assessment_subject_sections_for_student,
    assessment_summary_for_teacher,
    clear_assessment_result,
    set_assessment_result,
)
from .assessment_filtering import (
    assessment_filter_querysets,
    resolve_assessment_filter_selection,
    serialize_assessment_filter_options,
)

from .academic_year_context import (
    academic_year_ids_for_user,
    filter_temporary_credentials_for_year,
    get_selected_admin_academic_year,
)
from .account_utils import user_has_temporary_credential
from .error_logging import log_handled_error
from .assignment_options import (
    active_group_queryset,
    active_student_queryset,
    assignment_teacher_queryset,
    group_subject_queryset,
    student_subject_queryset,
)
from .forms import (
    CourseApplicationPublicForm,
    GradeCreateForm,
    get_student_allowed_subjects,
)
from .grade_options import (
    get_grade_form_options,
    get_grade_groups,
    get_grade_students,
    get_grade_subjects,
    get_grade_teachers,
)
from .models import (
    AcademicYear,
    AssessmentElement,
    AssessmentGroup,
    AssessmentItem,
    AssessmentResult,
    CourseApplication,
    CourseRegistrationSettings,
    Grade,
    GroupSubject,
    OrchestraPart,
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
from .teacher_access import teacher_has_active_assignment


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


@require_GET
def orchestra_part_options_api(request):
    instrument_id = request.GET.get('instrument')
    try:
        instrument_id = int(instrument_id)
    except (TypeError, ValueError):
        return JsonResponse({'parts': []})

    parts = list(
        OrchestraPart.objects.filter(
            instrument_id=instrument_id,
            is_active=True,
        ).order_by('name').values('id', 'name')
    )
    return JsonResponse({'parts': parts})


def csrf_failure_view(request, reason=''):
    from .error_views import render_error_response

    return render_error_response(
        request,
        403,
        code='csrf_failed',
        title='Срок действия формы истёк',
        message=(
            'Не удалось подтвердить отправку формы. Это происходит, если страница '
            'была открыта слишком долго или форма уже отправлялась ранее.'
        ),
        suggestions=(
            'Обновите страницу, чтобы получить новый защитный токен.',
            'Повторно заполните форму и отправьте её только один раз.',
            'Войдите в систему заново, если сеанс завершился.',
        ),
        retry_url=request.get_full_path(),
    )


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

    academic_year = _get_selected_object(
        AcademicYear.objects.all(),
        request.GET.get('academic_year'),
    ) or AcademicYear.get_active()
    active_year = academic_year is None or academic_year.is_active
    group_queryset = StudyGroup.objects.filter(is_active=True) if active_year else StudyGroup.objects.all()
    student_queryset = Student.objects.filter(is_active=True) if active_year else Student.objects.all()
    subject_queryset = Subject.objects.filter(is_active=True) if active_year else Subject.objects.all()
    teacher_queryset = Teacher.objects.filter(is_active=True) if active_year else Teacher.objects.all()

    group = _get_selected_object(group_queryset, request.GET.get('group'))
    student = _get_selected_object(
        student_queryset.select_related('group'),
        request.GET.get('student'),
    )
    subject = _get_selected_object(subject_queryset, request.GET.get('subject'))

    if can_manage_all_grades:
        teacher = _get_selected_object(
            teacher_queryset,
            request.GET.get('teacher'),
        )
    else:
        teacher = teacher_profile

    mode = request.GET.get('mode')
    if mode == 'grade':
        fixed_teacher = teacher_profile if not can_manage_all_grades else None
        individual_only = group is None
        options = get_grade_form_options(
            academic_year=academic_year,
            group=group,
            fixed_teacher=fixed_teacher,
            teacher=teacher,
            student=student,
            subject=subject,
            individual_only=individual_only,
        )
        if group is not None and not options['groups'].filter(pk=group.pk).exists():
            group = None
        if student is not None and not options['students'].filter(pk=student.pk).exists():
            student = None
        if subject is not None and not options['subjects'].filter(pk=subject.pk).exists():
            subject = None
        options = get_grade_form_options(
            academic_year=academic_year,
            group=group,
            fixed_teacher=fixed_teacher,
            teacher=teacher,
            student=student,
            subject=subject,
            individual_only=individual_only,
        )
        groups = options['groups'].select_related('academic_year')
        students = options['students']
        subjects = options['subjects']
        teachers = options['teachers']
        try:
            grade_date = date.fromisoformat(request.GET.get('date', ''))
        except (TypeError, ValueError):
            grade_date = None
        existing_grade = None
        if student is not None and subject is not None and grade_date is not None:
            existing_grade = (
                Grade.objects
                .filter(student=student, subject=subject, date=grade_date)
                .select_related('teacher', 'academic_year')
                .first()
            )
        existing_change = None
        if existing_grade is not None:
            current_teacher = teacher
            existing_change = {
                'fields': {
                    'value': {
                        'label': 'Оценка',
                        'field_name': 'value',
                        'old_raw': existing_grade.value,
                        'old_label': existing_grade.value,
                    },
                    'comment': {
                        'label': 'Комментарий',
                        'field_name': 'comment',
                        'old_raw': existing_grade.comment,
                        'old_label': existing_grade.comment,
                    },
                    'teacher': {
                        'label': 'Преподаватель',
                        'field_name': 'teacher',
                        'old_raw': str(existing_grade.teacher_id),
                        'old_label': existing_grade.teacher.full_name,
                        'current_raw': str(getattr(current_teacher, 'pk', '') or ''),
                        'current_label': getattr(current_teacher, 'full_name', '') or '',
                    },
                    'academic_year': {
                        'label': 'Учебный год',
                        'field_name': 'academic_year',
                        'old_raw': str(existing_grade.academic_year_id or ''),
                        'old_label': str(existing_grade.academic_year or 'Не указан'),
                    },
                },
            }
        return JsonResponse({
            'groups': [
                {
                    'id': item.pk,
                    'label': item.name,
                    'academic_year_id': item.academic_year_id,
                }
                for item in groups
            ],
            'students': [
                {'id': item.pk, 'label': item.full_name}
                for item in students
            ],
            'subjects': [
                {
                    'id': item.pk,
                    'label': item.name,
                    'is_individual': item.is_specialty,
                }
                for item in subjects
            ],
            'teachers': [
                {'id': item.pk, 'label': item.full_name}
                for item in teachers
            ],
            'defaults': {},
            'existing_change': existing_change,
        })

    if mode == 'subject_result':
        students = get_grade_students(
            academic_year=academic_year,
        )
        if student is not None and not students.filter(pk=student.pk).exists():
            student = None
        subjects = (
            get_grade_subjects(
                student=student,
                academic_year=academic_year,
            )
            if student is not None
            else Subject.objects.none()
        )
        if subject is not None and not subjects.filter(pk=subject.pk).exists():
            subject = None
        return JsonResponse({
            'groups': [],
            'students': [
                {'id': item.pk, 'label': item.full_name}
                for item in students
            ],
            'subjects': [
                {
                    'id': item.pk,
                    'label': item.name,
                    'is_individual': item.is_specialty,
                }
                for item in subjects
            ],
            'teachers': [],
            'defaults': {},
        })

    if mode == 'journal_filter':
        groups = get_grade_groups(
            teacher=teacher,
            academic_year=academic_year,
        ).select_related('academic_year')
        if group is not None and not groups.filter(pk=group.pk).exists():
            group = None
        students = get_grade_students(
            group=group,
            teacher=teacher,
            academic_year=academic_year,
        )
        subjects = get_grade_subjects(
            group=group,
            teacher=teacher,
            academic_year=academic_year,
        )
        teachers = get_grade_teachers(
            group=group,
            academic_year=academic_year,
        )
        if not can_manage_all_grades:
            teachers = teachers.filter(pk=teacher_profile.pk)
        return JsonResponse({
            'groups': [
                {
                    'id': item.pk,
                    'label': item.name,
                    'academic_year_id': item.academic_year_id,
                }
                for item in groups
            ],
            'students': [
                {'id': item.pk, 'label': item.full_name}
                for item in students
            ],
            'subjects': [
                {
                    'id': item.pk,
                    'label': item.name,
                    'is_individual': item.is_specialty,
                }
                for item in subjects
            ],
            'teachers': [
                {'id': item.pk, 'label': item.full_name}
                for item in teachers
            ],
            'defaults': {},
        })

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
async def assessment_options_api(request):
    return await _run_db_sync(_assessment_options_api_sync, request)


@login_required
@require_GET
async def assessment_filter_options_api(request):
    return await _run_db_sync(_assessment_filter_options_api_sync, request)


def _assessment_filter_options_api_sync(request):
    fixed_teacher = getattr(request.user, 'teacher_profile', None)
    if request.user.is_superuser:
        academic_years = AcademicYear.objects.filter(
            pk__in=academic_year_ids_for_user(request.user),
        )
    elif fixed_teacher is not None:
        academic_years = AcademicYear.objects.filter(
            pk__in=academic_year_ids_for_user(request.user),
        )
    else:
        return JsonResponse(
            {'error': 'Фильтры сдачи произведений недоступны для этой учетной записи.'},
            status=403,
        )

    academic_year = _get_selected_object(
        academic_years,
        request.GET.get('academic_year'),
    )
    if academic_year is None:
        active_year = AcademicYear.get_active()
        academic_year = (
            academic_years.filter(pk=getattr(active_year, 'pk', None)).first()
            or academic_years.order_by('-starts_on', '-pk').first()
        )
    if academic_year is None:
        return JsonResponse({
            'academic_years': [],
            'teachers': [],
            'subjects': [],
            'assessment_groups': [],
            'items': [],
            'students': [],
        })

    selection = resolve_assessment_filter_selection(
        request.GET,
        academic_year=academic_year,
        fixed_teacher=fixed_teacher,
    )
    options = assessment_filter_querysets(
        selection,
        allowed_academic_years=academic_years,
        fixed_teacher=fixed_teacher,
    )
    editable_only = request.GET.get('editable') == '1'
    payload = serialize_assessment_filter_options(
        options,
        editable_only=editable_only,
    )
    if editable_only and selection.assessment_group is None:
        # The quick result form uses the group as its main selector. Required
        # dependent selects stay enabled, but contain no values until a group
        # is chosen.
        payload['items'] = []
        payload['students'] = []
    payload['existing_change'] = None
    if (
        editable_only
        and selection.item is not None
        and selection.student is not None
        and any(row['id'] == selection.item.pk for row in payload['items'])
        and any(row['id'] == selection.student.pk for row in payload['students'])
    ):
        enrollment = selection.student.enrollment_for_year(academic_year)
        existing_result = None
        if enrollment is not None:
            existing_result = (
                AssessmentResult.objects
                .filter(enrollment=enrollment, item=selection.item)
                .select_related('assessed_by')
                .first()
            )
        if existing_result is not None:
            current_teacher = fixed_teacher or selection.item.responsible_teacher
            payload['existing_change'] = {
                'fields': {
                    'status': {
                        'label': 'Зачёт / результат',
                        'field_name': 'status',
                        'old_raw': existing_result.status,
                        'old_label': existing_result.get_status_display(),
                    },
                    'comment': {
                        'label': 'Комментарий',
                        'field_name': 'comment',
                        'old_raw': existing_result.comment,
                        'old_label': existing_result.comment,
                    },
                    'assessed_by': {
                        'label': 'Преподаватель, выставивший результат',
                        'old_raw': str(existing_result.assessed_by_id),
                        'old_label': existing_result.assessed_by.full_name,
                        'current_raw': str(getattr(current_teacher, 'pk', '') or ''),
                        'current_label': getattr(current_teacher, 'full_name', '') or '',
                    },
                },
            }
    return JsonResponse(payload)


def _assessment_options_api_sync(request):
    """Return dependent options from the same canonical assignment scope.

    The parent assessment group owns the subject/year, an AssessmentItem owns
    its responsible teacher, and StudentAssessmentGroup owns student access.
    No result row or helper membership is allowed to manufacture an option.
    """
    assessment_type = request.GET.get('type')
    if assessment_type not in {'item', 'student_group', 'rule', 'result'}:
        return JsonResponse({'error': 'Не удалось определить тип связанных полей.'}, status=400)

    changed_field = request.GET.get('changed') or ''
    strict_options = request.GET.get('strict') == '1'
    allow_teacher_reassignment = bool(
        assessment_type == 'item'
        and request.GET.get('allow_teacher_reassignment') == '1'
        and request.user.is_staff
    )
    parent_responsible_teacher = _get_selected_object(
        Teacher.objects.all(),
        request.GET.get('parent_responsible_teacher'),
    )
    selected_year = get_selected_admin_academic_year(request) or AcademicYear.get_active()

    group = _get_selected_object(
        AssessmentGroup.objects.select_related('subject', 'academic_year'),
        request.GET.get('group') or request.GET.get('assessment_group'),
    )
    item = _get_selected_object(
        AssessmentItem.objects.select_related(
            'element', 'group', 'group__subject', 'group__academic_year',
            'responsible_teacher',
        ),
        request.GET.get('item'),
    )
    current_item = _get_selected_object(
        AssessmentItem.objects.select_related('element', 'group'),
        request.GET.get('current_item'),
    )
    student = _get_selected_object(Student.objects.all(), request.GET.get('student'))
    element = _get_selected_object(
        AssessmentElement.objects.select_related('subject'),
        request.GET.get('element'),
    )
    selected_teacher = _get_selected_object(
        Teacher.objects.all(),
        request.GET.get('responsible_teacher') or request.GET.get('assessed_by'),
    )

    # A strict request comes from a real change event. Ignore stale values in
    # downstream fields so clearing or replacing a main selector immediately
    # clears its dependants instead of letting them constrain the response.
    if strict_options:
        if assessment_type == 'item' and changed_field == 'group':
            element = None
            selected_teacher = None
        elif assessment_type == 'student_group' and changed_field == 'student':
            group = None
        elif assessment_type == 'rule' and changed_field in {'subject', 'academic_year'}:
            group = None
        elif assessment_type == 'result':
            if changed_field == 'student':
                item = None
                selected_teacher = None
            elif changed_field == 'item':
                selected_teacher = None

    academic_year = _get_selected_object(
        AcademicYear.objects.all(), request.GET.get('academic_year')
    ) or selected_year
    subject = _get_selected_object(
        Subject.objects.filter(assessment_mode=Subject.ASSESSMENT_MODE_ELEMENTS),
        request.GET.get('subject'),
    )
    if item is not None:
        group = item.group
    if group is not None:
        academic_year = group.academic_year
        subject = group.subject
    elif element is not None and assessment_type == 'item':
        subject = element.subject

    years = AcademicYear.objects.filter(is_active=True).order_by('-starts_on', '-pk')
    if academic_year is not None:
        years = _include_selected_option(years, AcademicYear, academic_year)

    subjects = Subject.objects.filter(
        is_active=True,
        assessment_mode=Subject.ASSESSMENT_MODE_ELEMENTS,
    ).order_by('name', 'pk')
    groups = AssessmentGroup.objects.filter(is_active=True).select_related(
        'subject', 'academic_year'
    )
    if academic_year is not None:
        groups = groups.filter(academic_year=academic_year)
    if subject is not None:
        groups = groups.filter(subject=subject)

    elements = AssessmentElement.objects.filter(is_active=True).select_related('subject')
    if subject is not None:
        elements = elements.filter(subject=subject)

    teachers = assignment_teacher_queryset(
        subject=subject,
        academic_year=academic_year,
    )
    students = Student.objects.none()
    items = AssessmentItem.objects.none()
    enrollments = StudentEnrollment.objects.none()

    if assessment_type == 'item':
        # Group is the first and authoritative selector. Subject/year are
        # derived, while any active teacher can become the responsible one.
        if group is None:
            elements = AssessmentElement.objects.none()
            teachers = Teacher.objects.none()
        items = AssessmentItem.objects.filter(
            group__in=groups,
            is_active=True,
        ).select_related('group', 'group__subject', 'group__academic_year')
        if group is not None:
            items = items.filter(group=group)
        if selected_teacher is not None:
            items = items.filter(responsible_teacher=selected_teacher)
        if element is not None and not allow_teacher_reassignment:
            occupied_group_ids = AssessmentItem.objects.filter(element=element)
            if current_item is not None:
                occupied_group_ids = occupied_group_ids.exclude(pk=current_item.pk)
            groups = groups.exclude(pk__in=occupied_group_ids.values_list('group_id', flat=True))

    elif assessment_type == 'student_group':
        if group is not None:
            academic_year = group.academic_year
        if academic_year is not None:
            enrollment_qs = StudentEnrollment.objects.filter(academic_year=academic_year)
            if academic_year.is_active:
                enrollment_qs = enrollment_qs.filter(is_active=True)
            students = Student.objects.filter(
                pk__in=enrollment_qs.values_list('student_id', flat=True)
            )
        if group is None and student is None:
            groups = AssessmentGroup.objects.none()

    elif assessment_type == 'rule':
        # A group is meaningful only after both authoritative fields are
        # known. Keep the select interactive and empty before that point.
        if subject is None or academic_year is None:
            groups = AssessmentGroup.objects.none()

    elif assessment_type == 'result':
        if academic_year is not None:
            scope = JournalAccessScope(
                academic_year,
                include_inactive=bool(getattr(item, 'pk', None)),
            )
            configured_items = scope.assessment_items()
            students = scope.assessment_students()
            if student is not None:
                items = scope.assessment_items_for_student(student)
            else:
                items = AssessmentItem.objects.none()
            if item is not None:
                items = configured_items.filter(pk=item.pk)
                students = scope.assessment_students(group_ids=[item.group_id])
                enrollments = scope.assessment_enrollments(group_ids=[item.group_id])
                if item.responsible_teacher_id:
                    teachers = Teacher.objects.filter(pk=item.responsible_teacher_id)
                else:
                    teachers = Teacher.objects.none()
            else:
                teachers = Teacher.objects.none()
                enrollments = scope.assessment_enrollments()
            groups = AssessmentGroup.objects.filter(
                pk__in=configured_items.values_list('group_id', flat=True)
            ).select_related('subject', 'academic_year')
            subjects = Subject.objects.filter(
                pk__in=configured_items.values_list('group__subject_id', flat=True)
            )

    # Preserve an edited value only for non-strict initial loads. A change
    # event must remove an incompatible old value instead of silently keeping
    # an impossible combination.
    teacher_field = 'responsible_teacher' if assessment_type == 'item' else 'assessed_by'
    if not strict_options or changed_field in {'group', 'assessment_group'}:
        groups = _include_selected_option(groups, AssessmentGroup, group)
    if not strict_options or changed_field == 'subject':
        subjects = _include_selected_option(subjects, Subject, subject)
    if not strict_options or changed_field == 'student':
        students = _include_selected_option(students, Student, student)
    if not strict_options or changed_field == 'element':
        elements = _include_selected_option(elements, AssessmentElement, element)
    if not strict_options or changed_field == 'item':
        items = _include_selected_option(items, AssessmentItem, item)
    if not strict_options or changed_field == teacher_field:
        teachers = _include_selected_option(teachers, Teacher, selected_teacher)

    if group is not None and not allow_teacher_reassignment:
        occupied = AssessmentItem.objects.filter(group=group, element__isnull=False)
        if current_item is not None:
            occupied = occupied.exclude(pk=current_item.pk)
        elements = elements.exclude(pk__in=occupied.values_list('element_id', flat=True))
        if current_item is not None and current_item.group_id == group.pk and current_item.element_id:
            elements = _include_selected_option(elements, AssessmentElement, current_item.element)

    groups = groups.distinct().order_by('subject__name', 'sort_order', 'name', 'pk')
    subjects = subjects.distinct().order_by('name', 'pk')
    elements = elements.distinct().order_by('subject__name', 'title', 'pk')
    students = students.distinct().order_by('full_name', 'pk')
    teachers = teachers.distinct().order_by('full_name', 'pk')
    items = items.distinct().order_by(
        'group__subject__name', 'group__sort_order', 'group__name',
        'sort_order', 'title', 'pk',
    )
    enrollments = enrollments.distinct().order_by('full_name', 'pk')

    defaults = {}
    if academic_year is not None:
        defaults['academic_year_id'] = academic_year.pk
    if subject is not None:
        defaults['subject_id'] = subject.pk
    if group is not None:
        defaults.update({
            'academic_year_id': group.academic_year_id,
            'subject_id': group.subject_id,
        })
    if item is not None and item.responsible_teacher_id:
        defaults['assessed_by_id'] = item.responsible_teacher_id
        defaults['responsible_teacher_id'] = item.responsible_teacher_id

    existing_assignment_by_element = {}
    if assessment_type == 'item' and group is not None and allow_teacher_reassignment:
        existing_assignment_by_element = {
            row.element_id: row
            for row in AssessmentItem.objects.filter(
                group=group,
                element__isnull=False,
            ).select_related('responsible_teacher')
        }

    element_options = []
    for row in elements:
        option = {
            'id': row.pk,
            'label': row.title,
            'subject_id': row.subject_id,
        }
        existing_assignment = existing_assignment_by_element.get(row.pk)
        if existing_assignment is not None:
            option['existing_assignment'] = {
                'id': existing_assignment.pk,
                'responsible_teacher_id': existing_assignment.responsible_teacher_id,
                'responsible_teacher_label': (
                    existing_assignment.responsible_teacher.full_name
                    if existing_assignment.responsible_teacher_id
                    else 'Не назначен'
                ),
                'target_teacher_id': getattr(parent_responsible_teacher, 'pk', None),
                'target_teacher_label': (
                    parent_responsible_teacher.full_name
                    if parent_responsible_teacher is not None
                    else ''
                ),
                'sort_order': existing_assignment.sort_order,
                'is_required': existing_assignment.is_required,
                'is_active': existing_assignment.is_active,
            }
        element_options.append(option)

    return JsonResponse({
        'academic_years': [
            {'id': row.pk, 'label': row.name}
            for row in years
        ],
        'subjects': [
            {'id': row.pk, 'label': row.name}
            for row in subjects
        ],
        'elements': element_options,
        'groups': [
            {
                'id': row.pk,
                'label': str(row),
                'subject_id': row.subject_id,
                'academic_year_id': row.academic_year_id,
            }
            for row in groups
        ],
        'teachers': [
            {'id': row.pk, 'label': row.full_name}
            for row in teachers
        ],
        'students': [
            {'id': row.pk, 'label': row.full_name}
            for row in students
        ],
        'items': [
            {
                'id': row.pk,
                'label': f'{row.title} — {row.group.name}',
                'subject_id': row.group.subject_id,
                'academic_year_id': row.group.academic_year_id,
            }
            for row in items.select_related('group')
        ],
        'enrollments': [
            {
                'id': row.pk,
                'label': row.full_name + (f' — {row.group.name}' if row.group_id else ''),
            }
            for row in enrollments
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
    changed_field = request.GET.get('changed') or ''
    strict_options = request.GET.get('strict') == '1'
    if strict_options and changed_field == 'subject':
        # ``teacher`` is downstream from ``subject``. Do not reintroduce a
        # stale teacher submitted by the browser after the subject changed.
        teacher = None

    if assignment_type == 'group_subject':
        subjects = group_subject_queryset()
        defaults = _group_subject_defaults(group)
    else:
        subjects = student_subject_queryset()
        defaults = _student_subject_defaults(student, subject)

    groups = active_group_queryset()
    students = active_student_queryset()
    teachers = (
        assignment_teacher_queryset(subject)
        if subject is not None
        else Teacher.objects.none()
    )

    groups = _include_selected_option(groups, StudyGroup, group)
    students = _include_selected_option(students, Student, student)
    subjects = _include_selected_option(subjects, Subject, subject)
    if subject is not None:
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
        defaults['subject_is_individual'] = subject.is_specialty
        defaults['final_grade_type'] = subject.final_grade_type
    return defaults


def _calculate_average(grade_values: Iterable[str]) -> str:
    numeric_values: list[float] = []
    for value in grade_values:
        text = str(value).strip().upper()
        if text in {'Н', 'N'}:
            continue
        if len(text) in {1, 2} and text[0] in {'1', '2', '3', '4', '5'}:
            modifier = {'+': 0.5, '-': -0.5, '': 0}.get(text[1:], None)
            if modifier is not None:
                numeric_values.append(int(text[0]) + modifier)
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
    normalized = Grade(value=value)
    normalized.normalize_value()
    return normalized.value or ''


def _normalize_final_grade_value(subject: Subject, value: str):
    if subject.uses_element_assessment:
        raise ValidationError('Итог специального режима рассчитывается автоматически.')
    return subject.validate_final_grade(value)


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
    return _redirect_current_journal(request)


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


def _redirect_current_journal(request):
    query = request.GET.urlencode()
    url = reverse('journal')
    return redirect(f'{url}?{query}' if query else url)


def _assessment_workspace_context(
    request,
    *,
    academic_year: AcademicYear | None,
    academic_years,
    fixed_teacher: Teacher | None = None,
):
    if academic_year is None:
        return {
            'assessment_filter_enabled': False,
            'assessment_sections': [],
            'assessment_summary': assessment_summary_for_teacher([]),
        }

    selection = resolve_assessment_filter_selection(
        request.GET,
        academic_year=academic_year,
        fixed_teacher=fixed_teacher,
    )
    options = assessment_filter_querysets(
        selection,
        allowed_academic_years=academic_years,
        fixed_teacher=fixed_teacher,
    )
    assessment_filter_enabled = options['items'].exists()
    sections = (
        assessment_sections_for_teacher(
            selection.teacher,
            academic_year,
            subject=selection.subject,
            assessment_group=selection.assessment_group,
            item=selection.item,
            student=selection.student,
        )
        if assessment_filter_enabled
        else []
    )
    return {
        'assessment_filter_enabled': assessment_filter_enabled,
        'assessment_filter_options': options,
        'assessment_selection': selection,
        'assessment_sections': sections,
        'assessment_summary': assessment_summary_for_teacher(sections),
        'assessment_fixed_teacher': fixed_teacher,
        'assessment_has_editable_items': any(
            section.get('can_edit', False)
            for section in sections
        ),
    }


def _handle_assessment_result_post(
    request,
    *,
    can_edit: bool,
    fixed_teacher: Teacher | None = None,
):
    if request.method != 'POST' or request.POST.get('action') != 'assessment_result':
        return None
    if not can_edit:
        messages.error(request, 'Результаты выбранного учебного года доступны только для просмотра.')
        return _redirect_current_journal(request)

    item = _get_selected_object(
        AssessmentItem.objects.select_related(
            'group',
            'subject',
            'academic_year',
            'responsible_teacher',
        ),
        request.POST.get('item_id') or request.POST.get('assessment_item'),
    )
    student = _get_selected_object(
        Student.objects.all(),
        request.POST.get('student_id') or request.POST.get('assessment_student'),
    )
    assessment_group = _get_selected_object(
        AssessmentGroup.objects.all(),
        request.POST.get('assessment_group'),
    )
    status = (request.POST.get('status') or '').strip()
    comment = (request.POST.get('comment') or '').strip()
    try:
        if item is None or student is None:
            raise ValidationError('Не удалось определить произведение или ученика.')
        if 'assessment_group' in request.POST:
            if assessment_group is None:
                raise ValidationError('Выберите оркестр (группу произведений).')
            if item.group_id != assessment_group.pk:
                raise ValidationError('Выбранное произведение не относится к указанному оркестру.')
        acting_teacher = fixed_teacher or item.responsible_teacher
        if acting_teacher is None:
            raise ValidationError('У произведения не назначен ответственный преподаватель.')
        enrollment = student.enrollment_for_year(item.group.academic_year)
        existing_result = None
        if enrollment is not None:
            existing_result = (
                AssessmentResult.objects
                .filter(enrollment=enrollment, item=item)
                .select_related('assessed_by')
                .first()
            )

        confirmation_changes = []
        has_actual_changes = False
        if existing_result is not None:
            if status == 'clear':
                has_actual_changes = True
                confirmation_changes.append(
                    'Зачёт / результат: '
                    f'«{existing_result.get_status_display()}» → «Пусто»'
                )
                if existing_result.comment:
                    confirmation_changes.append(
                        f'Комментарий: «{existing_result.comment}» → «Пусто»'
                    )
            else:
                comparisons = (
                    (
                        'Зачёт / результат',
                        existing_result.status,
                        status,
                        existing_result.get_status_display(),
                        dict(AssessmentResult.STATUS_CHOICES).get(status, status),
                    ),
                    (
                        'Комментарий',
                        existing_result.comment,
                        comment,
                        existing_result.comment,
                        comment,
                    ),
                    (
                        'Преподаватель, выставивший результат',
                        existing_result.assessed_by_id,
                        acting_teacher.pk,
                        existing_result.assessed_by.full_name,
                        acting_teacher.full_name,
                    ),
                )
                for label, old_raw, new_raw, old_label, new_label in comparisons:
                    if old_raw == new_raw:
                        continue
                    has_actual_changes = True
                    if old_raw in (None, ''):
                        continue
                    confirmation_changes.append(
                        f'{label}: «{old_label or "Пусто"}» → «{new_label or "Пусто"}»'
                    )

        if (
            confirmation_changes
            and request.POST.get('_confirm_existing_changes') != '1'
        ):
            messages.error(
                request,
                'Подтвердите изменение существующего зачёта: '
                + '; '.join(confirmation_changes),
            )
            return _redirect_current_journal(request)

        if existing_result is not None and not has_actual_changes:
            messages.info(request, 'Значения зачёта не изменились.')
            return _redirect_current_journal(request)

        if status == 'clear':
            clear_assessment_result(
                item=item,
                student=student,
                acting_teacher=acting_teacher,
            )
            messages.success(request, 'Результат очищен. Итоговая оценка пересчитана.')
        else:
            _result, created = set_assessment_result(
                item=item,
                student=student,
                acting_teacher=acting_teacher,
                status=status,
                comment=comment,
                return_created=True,
            )
            if created:
                messages.success(request, 'Зачёт добавлен. Итоговая оценка пересчитана.')
            else:
                messages.success(request, 'Зачёт изменён. Итоговая оценка пересчитана.')
    except (PermissionDenied, ValidationError) as exc:
        log_handled_error(
            request,
            exc,
            status_code=403 if isinstance(exc, PermissionDenied) else 400,
            logger_name='journal.assessment',
        )
        error_messages = getattr(exc, 'messages', None) or [str(exc)]
        messages.error(request, '; '.join(error_messages))
    return _redirect_current_journal(request)


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
    """Build table rows from the same assignments as the access scope.

    This function used to re-query GroupSubject/StudentSubject with its own
    unconditional ``is_active=True`` rules.  As a result the filter controls
    could show a subject while the table itself stayed empty, especially in an
    archived year.  The scope is now the single source of truth for both.
    """
    group_ids = {group.pk for group in groups if group is not None}
    subject_ids = {subject.pk for subject in subjects if subject is not None}
    if not group_ids or not subject_ids:
        return set(), defaultdict(set), {}

    scope = JournalAccessScope(academic_year, teacher=teacher)
    group_assignments = scope.group_subjects().filter(
        group_id__in=group_ids,
        subject_id__in=subject_ids,
    )
    individual_assignments = scope.student_subjects().filter(
        student_id__in=enrollment_group_by_student,
        subject_id__in=subject_ids,
    )

    group_assignment_rows = list(group_assignments.values_list(
        'group_id',
        'subject_id',
        'subject_name_snapshot',
        'final_grade_type_snapshot',
    ))
    group_subject_pairs = {
        (group_id, subject_id)
        for group_id, subject_id, _subject_name, _final_grade_type
        in group_assignment_rows
    }
    individual_students_by_pair: dict[tuple[int, int], set[int]] = defaultdict(set)
    assignment_metadata: dict[tuple[int, int], tuple[str, str]] = {
        (group_id, subject_id): (subject_name, final_grade_type)
        for group_id, subject_id, subject_name, final_grade_type
        in group_assignment_rows
    }
    for student_id, subject_id, subject_name, final_grade_type in (
        individual_assignments.values_list(
            'student_id',
            'subject_id',
            'subject_name_snapshot',
            'final_grade_type_snapshot',
        )
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
        return ['Зачет', 'Незачет', 'Не аттестован']
    return [
        value
        for number in range(1, 6)
        for value in (str(number), f'{number}+', f'{number}-')
    ] + ['Н']


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

    added_result_ids: set[int] = set()
    added_result_keys: set[tuple[int, int, int]] = set()
    changed_result_ids: set[int] = set()
    changed_grade_ids: set[int] = set()
    deleted_grade_ids: set[int] = set()
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

    if request.POST.get('_confirm_existing_changes') != '1':
        confirmation_changes = []
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
                try:
                    subject_id = int(parts[1])
                    student_id = int(parts[2])
                except (TypeError, ValueError):
                    continue
                student = student_map.get(student_id)
                subject = subject_map.get(subject_id)
                if student is None or subject is None or selected_academic_year is None:
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
                except ValidationError:
                    continue
                result = SubjectResult.objects.filter(
                    student=student,
                    subject=subject,
                    academic_year=selected_academic_year,
                ).first()
                if result is None:
                    continue
                old_value = (
                    result.exam_grade if field_mode == 'exam'
                    else result.final_grade
                )
                if old_value in (None, '') or old_value == normalized_value:
                    continue
                field_label = 'Экзамен' if field_mode == 'exam' else 'Итоговая оценка'
                confirmation_changes.append(
                    f'{field_label} — {student.full_name} — {subject.name}: '
                    f'«{old_value}» → «{normalized_value or "Пусто"}»'
                )
                continue

            parts = field_name.split('__')
            if len(parts) != 4:
                continue
            try:
                subject_id = int(parts[1])
                student_id = int(parts[2])
                grade_date = date.fromisoformat(parts[3])
            except (TypeError, ValueError):
                continue
            student = student_map.get(student_id)
            subject = subject_map.get(subject_id)
            if student is None or subject is None:
                continue
            if role_mode == 'teacher':
                if teacher is None:
                    continue
                if (
                    (
                        enrollment_group_by_student.get(student_id),
                        subject_id,
                    ) not in teacher_group_subject_pairs
                    and (student_id, subject_id) not in teacher_individual_subject_pairs
                ):
                    continue
            grade = Grade.objects.filter(
                student_id=student_id,
                subject_id=subject_id,
                date=grade_date,
                academic_year=selected_academic_year,
            ).first()
            if grade is None:
                continue
            normalized_value = _normalize_grade_value(value)
            if grade.value == normalized_value:
                continue
            confirmation_changes.append(
                f'Оценка — {student.full_name} — {subject.name} — '
                f'{grade_date:%d.%m.%Y}: «{grade.value}» → '
                f'«{normalized_value or "Пусто"}»'
            )

        if confirmation_changes:
            messages.error(
                request,
                'Подтвердите изменение существующих значений: '
                + '; '.join(confirmation_changes),
            )
            return False

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
                    log_handled_error(
                        request,
                        exc,
                        logger_name='journal.inline_grades',
                    )
                    messages.error(request, '; '.join(exc.messages))
                    transaction.set_rollback(True)
                    return False

                academic_year = selected_academic_year
                if academic_year is None:
                    messages.error(request, 'Не удалось определить учебный год для итоговой оценки.')
                    transaction.set_rollback(True)
                    return False

                result_key = (student.pk, subject.pk, academic_year.pk)
                result = SubjectResult.objects.filter(
                    student=student,
                    subject=subject,
                    academic_year=academic_year,
                ).first()
                if result is None and normalized_value in {None, ''}:
                    # Do not manufacture empty result rows just because every
                    # select in the table is submitted with the form.
                    continue
                created_result = result is None
                if result is None:
                    result = SubjectResult(
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
                    log_handled_error(
                        request,
                        exc,
                        logger_name='journal.inline_grades',
                    )
                    messages.error(request, '; '.join(exc.messages))
                    transaction.set_rollback(True)
                    return False
                if created_result:
                    added_result_ids.add(result.pk)
                    added_result_keys.add(result_key)
                elif result_key not in added_result_keys:
                    changed_result_ids.add(result.pk)
                continue

            normalized_grade_value = _normalize_grade_value(value)
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
                    academic_year=selected_academic_year,
                    student_id__in=student_ids,
                    subject_id__in=subject_ids,
                )
                .select_related('teacher', 'student', 'subject')
                .first()
            )
            if grade is None:
                continue

            if role_mode == 'teacher':
                if teacher is None:
                    continue
                group_subject_key = (
                    enrollment_group_by_student.get(student_id),
                    subject_id,
                )
                if (
                    group_subject_key not in teacher_group_subject_pairs
                    and (student_id, subject_id) not in teacher_individual_subject_pairs
                ):
                    continue

            if normalized_grade_value == '':
                grade_pk = grade.pk
                grade.delete()
                deleted_grade_ids.add(grade_pk)
                continue

            if grade.value == normalized_grade_value:
                continue

            grade.value = normalized_grade_value
            if role_mode == 'teacher' and teacher is not None:
                # Legacy/admin imports may carry an outdated author. Once the
                # currently assigned teacher edits the cell, align ownership
                # with the active assignment so model validation remains true.
                grade.teacher = teacher
            try:
                grade.save()
            except ValidationError as exc:
                log_handled_error(
                    request,
                    exc,
                    logger_name='journal.inline_grades',
                )
                messages.error(request, '; '.join(exc.messages))
                transaction.set_rollback(True)
                return False
            changed_grade_ids.add(grade.pk)

    changed_count = len(changed_result_ids) + len(changed_grade_ids)
    if added_result_ids or changed_count or deleted_grade_ids:
        summaries = []
        if added_result_ids:
            summaries.append(f'Добавлено итогов: {len(added_result_ids)}.')
        if changed_count:
            summaries.append(f'Изменено оценок/итогов: {changed_count}.')
        if deleted_grade_ids:
            summaries.append(f'Удалено оценок: {len(deleted_grade_ids)}.')
        messages.success(request, ' '.join(summaries))
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
        return redirect('password_change')

    selected_group_id = request.GET.get('group')
    selected_subject_id = request.GET.get('subject')
    selected_year_id = request.GET.get('academic_year') or request.GET.get('year')

    all_academic_years = AcademicYear.objects.all().order_by('-starts_on', '-ends_on', '-pk')
    academic_years = all_academic_years.filter(
        pk__in=academic_year_ids_for_user(request.user),
    )

    selected_academic_year = (
        _get_selected_object(academic_years, selected_year_id)
        if selected_year_id
        else None
    )
    if selected_academic_year is None:
        active_year = AcademicYear.get_active()
        selected_academic_year = (
            academic_years.filter(pk=getattr(active_year, 'pk', None)).first()
            or academic_years.first()
        )

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
    scope = JournalAccessScope(selected_academic_year)

    groups = scope.standard_groups()
    selected_group = _get_selected_object(groups, selected_group_id)
    groups_to_show = [selected_group] if selected_group else list(groups)

    subjects = scope.standard_subjects(group=selected_group)
    selected_subject = _get_selected_object(subjects, selected_subject_id)
    subjects_to_show = [selected_subject] if selected_subject else list(subjects)
    can_edit_journal = _can_edit_academic_year(selected_academic_year)

    enrollments = list(scope.standard_enrollments(group=selected_group))
    students_qs = Student.objects.filter(
        pk__in=[row.student_id for row in enrollments]
    ).order_by('full_name', 'pk')
    students = list(students_qs)

    grade_qs = scope.standard_grades(group=selected_group).filter(
        subject__in=subjects_to_show,
    )
    results_qs = scope.standard_subject_results(group=selected_group).filter(
        subject__in=subjects_to_show,
    )

    journal_tables = _build_journal_tables(
        groups=groups_to_show,
        subjects=subjects_to_show,
        enrollments=enrollments,
        grade_qs=grade_qs,
        results_qs=results_qs,
        selected_academic_year=selected_academic_year,
    )

    assessment_context = _assessment_workspace_context(
        request,
        academic_year=selected_academic_year,
        academic_years=academic_years,
    )
    assessment_post_response = _handle_assessment_result_post(
        request,
        can_edit=can_edit_journal,
    )
    if assessment_post_response is not None:
        return assessment_post_response

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
            return _redirect_current_journal(request)

    if isinstance(grade_form, HttpResponse):
        return grade_form

    context = _journal_context(
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
    )
    context.update(assessment_context)
    return render(
        request,
        'journal.html',
        context,
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
    scope = JournalAccessScope(selected_academic_year, teacher=teacher)

    groups = scope.standard_groups()
    selected_group = _get_selected_object(groups, selected_group_id)
    groups_to_show = [selected_group] if selected_group else list(groups)
    has_active_assignment = teacher_has_active_assignment(
        teacher,
        selected_academic_year,
    )
    can_edit_journal = bool(
        _can_edit_academic_year(selected_academic_year)
        and has_active_assignment
    )

    subjects = scope.standard_subjects(group=selected_group)
    selected_subject = _get_selected_object(subjects, selected_subject_id)
    subjects_to_show = [selected_subject] if selected_subject else list(subjects)

    enrollments = list(scope.standard_enrollments(group=selected_group))
    students_qs = Student.objects.filter(
        pk__in=[row.student_id for row in enrollments]
    ).order_by('full_name', 'pk')
    students = list(students_qs)

    grade_qs = scope.standard_grades(group=selected_group).filter(
        subject__in=subjects_to_show,
    )
    results_qs = scope.standard_subject_results(group=selected_group).filter(
        subject__in=subjects_to_show,
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

    assessment_context = _assessment_workspace_context(
        request,
        academic_year=selected_academic_year,
        academic_years=academic_years,
        fixed_teacher=teacher,
    )
    assessment_post_response = _handle_assessment_result_post(
        request,
        can_edit=can_edit_journal,
        fixed_teacher=teacher,
    )
    if assessment_post_response is not None:
        return assessment_post_response

    archived_post_response = _reject_archived_academic_year_post(
        request,
        selected_academic_year,
        selected_group=selected_group,
        selected_subject=selected_subject,
    )
    if archived_post_response is not None:
        return archived_post_response

    if request.method == 'POST' and not can_edit_journal:
        messages.error(
            request,
            'Ваше участие в выбранном учебном году доступно только для просмотра.',
        )
        return _redirect_current_journal(request)

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
            return _redirect_current_journal(request)

    if isinstance(grade_form, HttpResponse):
        return grade_form

    context = _journal_context(
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
    )
    context.update(assessment_context)
    return render(request, 'journal.html', context)


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
        return _redirect_current_journal(request)

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

    assessment_rows = (
        assessment_rows_for_student(student, selected_academic_year)
        if selected_academic_year is not None
        else []
    )
    assessment_subject_ids = {row['item'].subject_id for row in assessment_rows}
    assessment_final_results = {
        result.subject_id: result
        for result in SubjectResult.objects.filter(
            student=student,
            academic_year=selected_academic_year,
            subject_id__in=assessment_subject_ids,
        ).select_related('subject')
    } if selected_academic_year is not None else {}

    context = _journal_context(
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
    )
    context['assessment_rows'] = assessment_rows
    context['assessment_final_results'] = assessment_final_results
    context['assessment_subject_sections'] = assessment_subject_sections_for_student(
        assessment_rows,
        assessment_final_results,
    )
    return render(request, 'journal.html', context)


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
        raw_group_id = request.POST.get('group')
        if 'group' in request.POST:
            posted_group = _get_selected_object(groups, raw_group_id) if raw_group_id else None
        else:
            posted_group = selected_group
        posted_subject = _get_selected_object(subjects, request.POST.get('subject'))
        posted_student = _get_selected_object(
            get_grade_students(
                group=posted_group,
                teacher=teacher,
                academic_year=selected_academic_year,
            ),
            request.POST.get('student'),
        )
        try:
            posted_date = date.fromisoformat(request.POST.get('date', ''))
        except (TypeError, ValueError):
            posted_date = None

        existing_grade = None
        existing_snapshot = None
        if posted_student is not None and posted_subject is not None and posted_date is not None:
            existing_grade = Grade.objects.filter(
                student=posted_student,
                subject=posted_subject,
                date=posted_date,
            ).select_related('teacher', 'academic_year').first()
            if existing_grade is not None:
                existing_snapshot = {
                    'value': existing_grade.value,
                    'comment': existing_grade.comment,
                    'teacher_id': existing_grade.teacher_id,
                    'teacher_label': existing_grade.teacher.full_name,
                    'academic_year_id': existing_grade.academic_year_id,
                    'academic_year_label': str(existing_grade.academic_year or 'Не указан'),
                }

        form_data = request.POST.copy()
        if posted_group is not None and 'group' not in form_data:
            form_data['group'] = str(posted_group.pk)

        grade_form = GradeCreateForm(
            form_data,
            instance=existing_grade,
            teacher=teacher,
            initial_group=posted_group,
            academic_year=selected_academic_year,
        )
        if grade_form.is_valid():
            grade = grade_form.save(commit=False)
            changed_values = []
            has_actual_changes = False
            if existing_snapshot is not None:
                comparisons = (
                    (
                        'Оценка',
                        existing_snapshot['value'],
                        grade.value,
                        existing_snapshot['value'],
                        grade.value,
                    ),
                    (
                        'Комментарий',
                        existing_snapshot['comment'],
                        grade.comment,
                        existing_snapshot['comment'],
                        grade.comment,
                    ),
                    (
                        'Преподаватель',
                        existing_snapshot['teacher_id'],
                        grade.teacher_id,
                        existing_snapshot['teacher_label'],
                        grade.teacher.full_name if grade.teacher_id else 'Не назначен',
                    ),
                    (
                        'Учебный год',
                        existing_snapshot['academic_year_id'],
                        grade.academic_year_id,
                        existing_snapshot['academic_year_label'],
                        str(grade.academic_year or 'Не указан'),
                    ),
                )
                for label, old_raw, new_raw, old_label, new_label in comparisons:
                    if old_raw == new_raw:
                        continue
                    has_actual_changes = True
                    if old_raw in (None, ''):
                        continue
                    changed_values.append(
                        f'{label}: «{old_label or "Пусто"}» → «{new_label or "Пусто"}»'
                    )

            if (
                existing_grade is not None
                and changed_values
                and request.POST.get('_confirm_existing_changes') != '1'
            ):
                grade_form.add_error(
                    None,
                    'Подтвердите изменение существующей оценки: '
                    + '; '.join(changed_values),
                )
                return grade_form

            if existing_grade is not None and not has_actual_changes:
                messages.info(request, 'Значения оценки не изменились.')
                return _redirect_current_journal(request)

            grade.save()
            if existing_grade is None:
                messages.success(request, 'Оценка успешно добавлена.')
            else:
                messages.success(request, 'Оценка успешно изменена.')
            return _redirect_current_journal(request)

        log_handled_error(
            request,
            ValidationError('Форма добавления оценки содержит ошибки.'),
            logger_name='journal.grade_form',
            metadata={'errors': grade_form.errors.get_json_data()},
        )

        return grade_form

    return GradeCreateForm(
        None,
        teacher=teacher,
        initial_group=selected_group,
        initial_subject=selected_subject,
        academic_year=selected_academic_year,
    )


def _has_items(value) -> bool:
    if value is None:
        return False
    exists = getattr(value, 'exists', None)
    if callable(exists):
        return exists()
    return bool(value)


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
        'has_subjects': _has_items(subjects),
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


COURSE_REGISTRATION_CLOSED_MESSAGE = (
    'Регистрация завершена. Новые заявки сейчас не принимаются.'
)


def _course_registration_context(
    registration_settings,
    *,
    form=None,
    submitted=False,
    application=None,
    credential=None,
):
    registration_open = registration_settings.registration_is_open()
    return {
        'form': form,
        'submitted': submitted,
        'application': application,
        'credential': credential,
        'redirect_url': _get_telegram_redirect_url(registration_settings),
        'registration_open': registration_open,
        'registration_closed_message': COURSE_REGISTRATION_CLOSED_MESSAGE,
    }


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


COURSE_APPLICATION_PHONE_CONSTRAINT = 'unique_course_app_phone_per_year'
COURSE_APPLICATION_DUPLICATE_PHONE_MESSAGE = (
    'Заявка с этим номером телефона уже зарегистрирована на текущий учебный год.'
)


def _is_duplicate_course_application_phone_error(exc: Exception) -> bool:
    if isinstance(exc, ValidationError):
        errors = getattr(exc, 'error_dict', {}).get('student_phone', [])
        return any(
            getattr(error, 'code', None) == 'duplicate_phone_for_year'
            for error in errors
        )

    if isinstance(exc, IntegrityError):
        cause = exc.__cause__
        diagnostic = getattr(cause, 'diag', None)
        constraint_name = getattr(diagnostic, 'constraint_name', None)
        if constraint_name:
            return constraint_name == COURSE_APPLICATION_PHONE_CONSTRAINT

        # SQLite and some database adapters don't expose constraint_name.
        message = str(exc).lower()
        return (
            COURSE_APPLICATION_PHONE_CONSTRAINT in message
            or (
                'courseapplication' in message
                and 'academic_year' in message
                and 'student_phone' in message
                and ('unique' in message or 'duplicate' in message)
            )
        )

    return False


def _add_duplicate_phone_form_error(form) -> None:
    form.add_error('student_phone', COURSE_APPLICATION_DUPLICATE_PHONE_MESSAGE)


async def course_registration_view(request):
    return await _run_db_sync(_course_registration_view_sync, request)


def _course_registration_view_sync(request):
    if request.method not in {'GET', 'POST'}:
        return HttpResponseNotAllowed(['GET', 'POST'])

    registration_settings = _get_registration_settings()
    registration_open = registration_settings.registration_is_open()
    if not registration_open:
        return render(
            request,
            'journal/course_registration.html',
            _course_registration_context(registration_settings),
            status=409 if request.method == 'POST' else 200,
        )

    form = CourseApplicationPublicForm(
        request.POST or None,
        registration_settings=registration_settings,
    )

    if request.method == 'POST' and _registration_is_throttled(request):
        form.add_error(
            None,
            'Слишком много попыток регистрации. Подождите минуту и попробуйте снова.',
        )
        return render(
            request,
            'journal/course_registration.html',
            _course_registration_context(registration_settings, form=form),
            status=429,
        )

    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                locked_settings = (
                    CourseRegistrationSettings.objects
                    .select_for_update()
                    .get(pk=registration_settings.pk)
                )
                if not locked_settings.registration_is_open():
                    return render(
                        request,
                        'journal/course_registration.html',
                        _course_registration_context(locked_settings),
                        status=409,
                    )
                application = form.save()
        except (ValidationError, IntegrityError) as exc:
            if not _is_duplicate_course_application_phone_error(exc):
                raise
            _add_duplicate_phone_form_error(form)
            return render(
                request,
                'journal/course_registration.html',
                _course_registration_context(registration_settings, form=form),
                status=409,
            )
        credential = _get_application_credential(application)
        return render(
            request,
            'journal/course_registration.html',
            _course_registration_context(
                registration_settings,
                submitted=True,
                application=application,
                credential=credential,
            ),
        )

    return render(
        request,
        'journal/course_registration.html',
        _course_registration_context(registration_settings, form=form),
    )


async def course_registration_api(request):
    return await _run_db_sync(_course_registration_api_sync, request)


def _course_registration_api_sync(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    registration_settings = _get_registration_settings()
    if not registration_settings.registration_is_open():
        return JsonResponse(
            {
                'success': False,
                'message': COURSE_REGISTRATION_CLOSED_MESSAGE,
            },
            status=409,
        )

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

    form = CourseApplicationPublicForm(payload, registration_settings=registration_settings)
    redirect_url = _get_telegram_redirect_url(registration_settings)

    if form.is_valid():
        try:
            with transaction.atomic():
                locked_settings = (
                    CourseRegistrationSettings.objects
                    .select_for_update()
                    .get(pk=registration_settings.pk)
                )
                if not locked_settings.registration_is_open():
                    return JsonResponse(
                        {
                            'success': False,
                            'message': COURSE_REGISTRATION_CLOSED_MESSAGE,
                        },
                        status=409,
                    )
                application = form.save()
        except (ValidationError, IntegrityError) as exc:
            if not _is_duplicate_course_application_phone_error(exc):
                raise
            return JsonResponse(
                {
                    'success': False,
                    'message': COURSE_APPLICATION_DUPLICATE_PHONE_MESSAGE,
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
    rows = TemporaryCredential.objects.all()
    selected_academic_year = get_selected_admin_academic_year(request)
    if selected_academic_year is not None:
        rows = filter_temporary_credentials_for_year(rows, selected_academic_year)
    rows = rows.select_related('course_application').order_by('id')

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
    workbook = build_full_export_workbook(get_selected_admin_academic_year(request))

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
