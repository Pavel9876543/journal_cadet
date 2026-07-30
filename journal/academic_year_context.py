from __future__ import annotations

from collections.abc import Iterable

from django.core.exceptions import ObjectDoesNotExist

ADMIN_ACADEMIC_YEAR_PARAM = 'academic_year'
ADMIN_ACADEMIC_YEAR_SESSION_KEY = 'journal_admin_academic_year_id'
ADMIN_ACADEMIC_YEAR_REQUEST_CACHE = '_journal_admin_academic_year'


def get_selected_admin_academic_year(request):
    """Return the academic year selected for admin browsing.

    The selection is stored in the session so that it survives navigation from
    a changelist to a change form. The active year is the default.
    """
    from .models import AcademicYear

    cached = getattr(request, ADMIN_ACADEMIC_YEAR_REQUEST_CACHE, None)
    if cached is not None:
        return cached

    available_years = AcademicYear.objects.all()
    user = getattr(request, 'user', None)
    if getattr(user, 'is_authenticated', False):
        available_years = available_years.filter(pk__in=academic_year_ids_for_user(user))

    query_params = getattr(request, 'GET', {})
    session = getattr(request, 'session', None)
    requested_value = query_params.get(ADMIN_ACADEMIC_YEAR_PARAM)
    if requested_value:
        selected = None
        if requested_value == 'active':
            selected = available_years.filter(is_active=True).first()
        else:
            try:
                requested_id = int(requested_value)
            except (TypeError, ValueError):
                requested_id = None
            if requested_id is not None:
                selected = available_years.filter(pk=requested_id).first()
        if selected is not None:
            if session is not None:
                session[ADMIN_ACADEMIC_YEAR_SESSION_KEY] = selected.pk
            setattr(request, ADMIN_ACADEMIC_YEAR_REQUEST_CACHE, selected)
            return selected

    selected_id = session.get(ADMIN_ACADEMIC_YEAR_SESSION_KEY) if session is not None else None
    if selected_id:
        selected = available_years.filter(pk=selected_id).first()
        if selected is not None:
            setattr(request, ADMIN_ACADEMIC_YEAR_REQUEST_CACHE, selected)
            return selected
        session.pop(ADMIN_ACADEMIC_YEAR_SESSION_KEY, None)

    selected = available_years.filter(is_active=True).first() or available_years.order_by(
        '-starts_on', '-ends_on', '-pk'
    ).first()
    if selected is not None and session is not None:
        session[ADMIN_ACADEMIC_YEAR_SESSION_KEY] = selected.pk
    if selected is not None:
        setattr(request, ADMIN_ACADEMIC_YEAR_REQUEST_CACHE, selected)
    return selected


def get_admin_academic_year_context(request) -> dict:
    from .models import AcademicYear

    selected = get_selected_admin_academic_year(request)
    available_years = AcademicYear.objects.filter(
        pk__in=academic_year_ids_for_user(getattr(request, 'user', None)),
    ).order_by('-starts_on', '-ends_on', '-pk')
    return {
        'admin_academic_years': available_years,
        'admin_selected_academic_year': selected,
        'admin_selected_year_is_archived': bool(selected and not selected.is_active),
        'admin_academic_year_param': ADMIN_ACADEMIC_YEAR_PARAM,
    }


def academic_year_ids_for_user(user) -> Iterable[int]:
    """Return only academic years the user participated in."""
    from .models import AcademicYear

    if not getattr(user, 'is_authenticated', False):
        return ()
    year_ids: set[int] = set(
        user.journal_year_memberships.values_list('academic_year_id', flat=True),
    )
    try:
        student = user.student_profile
    except ObjectDoesNotExist:
        student = None
    if student is not None:
        year_ids.update(student.enrollments.values_list('academic_year_id', flat=True))

    try:
        teacher = user.teacher_profile
    except ObjectDoesNotExist:
        teacher = None
    if teacher is not None:
        # An inactive membership still proves that the teacher participated in
        # the year and therefore may inspect its archived journal.
        year_ids.update(
            teacher.academic_year_memberships.values_list('academic_year_id', flat=True),
        )

    # Existing installations may have a bootstrap administrator without an
    # explicit membership yet. Keep all years available only until the first
    # manual assignment is created for that account.
    if (user.is_superuser or user.is_staff) and not year_ids:
        return AcademicYear.objects.values_list('pk', flat=True)

    return year_ids


def filter_temporary_credentials_for_year(queryset, academic_year):
    """Limit a temporary credential to the year in which the account appeared."""
    from django.db.models import OuterRef, Q, Subquery

    from .models import AcademicYear

    if academic_year is None:
        return queryset.none()

    first_profile_year = (
        AcademicYear.objects
        .filter(
            Q(student_enrollments__student__user_id=OuterRef('user_id'))
            | Q(teacher_enrollments__teacher__user_id=OuterRef('user_id')),
        )
        .order_by('starts_on', 'ends_on', 'pk')
        .values('pk')[:1]
    )
    queryset = queryset.annotate(
        _first_profile_year_id=Subquery(first_profile_year),
    )
    year_filter = (
        Q(course_application__academic_year=academic_year)
        | Q(
            course_application__isnull=True,
            user__journal_year_memberships__academic_year=academic_year,
        )
        | Q(
            course_application__isnull=True,
            _first_profile_year_id=academic_year.pk,
        )
    )
    # Keep compatibility for a bootstrap staff account only until explicit
    # academic-year memberships have been configured for it.
    if AcademicYear.objects.filter(pk=academic_year.pk, is_active=True).exists():
        year_filter |= Q(
            user__is_staff=True,
            user__journal_year_memberships__isnull=True,
            course_application__isnull=True,
        )
    return queryset.filter(year_filter).distinct()
