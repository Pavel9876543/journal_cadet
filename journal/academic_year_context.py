from __future__ import annotations

from collections.abc import Iterable

from django.core.exceptions import ObjectDoesNotExist

ADMIN_ACADEMIC_YEAR_PARAM = 'academic_year'
ADMIN_ACADEMIC_YEAR_SESSION_KEY = 'journal_admin_academic_year_id'


def get_selected_admin_academic_year(request):
    """Return the academic year selected for admin browsing.

    The selection is stored in the session so that it survives navigation from
    a changelist to a change form. The active year is the default.
    """
    from .models import AcademicYear

    query_params = getattr(request, 'GET', {})
    session = getattr(request, 'session', None)
    requested_value = query_params.get(ADMIN_ACADEMIC_YEAR_PARAM)
    if requested_value:
        selected = None
        if requested_value == 'active':
            selected = AcademicYear.get_active()
        else:
            try:
                requested_id = int(requested_value)
            except (TypeError, ValueError):
                requested_id = None
            if requested_id is not None:
                selected = AcademicYear.objects.filter(pk=requested_id).first()
        if selected is not None:
            if session is not None:
                session[ADMIN_ACADEMIC_YEAR_SESSION_KEY] = selected.pk
            return selected

    selected_id = session.get(ADMIN_ACADEMIC_YEAR_SESSION_KEY) if session is not None else None
    if selected_id:
        selected = AcademicYear.objects.filter(pk=selected_id).first()
        if selected is not None:
            return selected
        session.pop(ADMIN_ACADEMIC_YEAR_SESSION_KEY, None)

    selected = AcademicYear.get_active() or AcademicYear.latest()
    if selected is not None and session is not None:
        session[ADMIN_ACADEMIC_YEAR_SESSION_KEY] = selected.pk
    return selected


def get_admin_academic_year_context(request) -> dict:
    from .models import AcademicYear

    selected = get_selected_admin_academic_year(request)
    return {
        'admin_academic_years': AcademicYear.objects.order_by('-starts_on', '-ends_on', '-pk'),
        'admin_selected_academic_year': selected,
        'admin_selected_year_is_archived': bool(selected and not selected.is_active),
        'admin_academic_year_param': ADMIN_ACADEMIC_YEAR_PARAM,
    }


def academic_year_ids_for_user(user) -> Iterable[int]:
    """Return only academic years the user is allowed to open in the journal."""
    from .models import AcademicYear

    if not getattr(user, 'is_authenticated', False):
        return AcademicYear.objects.none().values_list('pk', flat=True)
    if user.is_superuser:
        return AcademicYear.objects.values_list('pk', flat=True)

    try:
        student = user.student_profile
    except ObjectDoesNotExist:
        student = None
    if student is not None:
        return student.enrollments.values_list('academic_year_id', flat=True)

    try:
        teacher = user.teacher_profile
    except ObjectDoesNotExist:
        teacher = None
    if teacher is not None:
        return teacher.academic_year_memberships.filter(is_active=True).values_list(
            'academic_year_id',
            flat=True,
        )

    return AcademicYear.objects.none().values_list('pk', flat=True)
