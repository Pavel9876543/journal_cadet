from __future__ import annotations

from calendar import isleap
from datetime import date, timedelta

from django.utils import timezone

from .models import AccountProfile, Student, Teacher


def _birthday_in_year(birth_date: date, year: int) -> date:
    try:
        return birth_date.replace(year=year)
    except ValueError:
        return date(year, 2, 28)


def _age_word(age: int) -> str:
    if age % 10 == 1 and age % 100 != 11:
        return 'год'
    if age % 10 in {2, 3, 4} and age % 100 not in {12, 13, 14}:
        return 'года'
    return 'лет'


def _candidate_birth_dates(target_dates: tuple[date, ...]) -> set[date]:
    dates = set()
    for target_date in target_dates:
        for birth_year in range(1900, target_date.year + 1):
            try:
                dates.add(date(birth_year, target_date.month, target_date.day))
            except ValueError:
                continue
            if target_date.month == 2 and target_date.day == 28 and not isleap(target_date.year):
                if isleap(birth_year):
                    dates.add(date(birth_year, 2, 29))
    return dates


def birthday_notifications_for_user(user, *, today: date | None = None) -> list[dict]:
    if not getattr(user, 'is_authenticated', False):
        return []

    today = today or timezone.localdate()
    tomorrow = today + timedelta(days=1)
    targets = {today: ('Сегодня', 'исполнилось'), tomorrow: ('Завтра', 'исполнится')}
    birth_dates = _candidate_birth_dates((today, tomorrow))
    people: list[tuple[str, str, date]] = []

    students = Student.objects.filter(
        is_active=True,
        birth_date__in=birth_dates,
    ).order_by('full_name')
    for student in students:
        people.append(('Ученик', student.full_name, student.birth_date))

    teachers = Teacher.objects.filter(
        is_active=True,
        birth_date__in=birth_dates,
    ).order_by('full_name')
    for teacher in teachers:
        people.append(('Преподаватель', teacher.full_name, teacher.birth_date))

    admin_profiles = (
        AccountProfile.objects
        .filter(
            user__is_active=True,
            user__is_staff=True,
            birth_date__in=birth_dates,
            user__student_profile__isnull=True,
            user__teacher_profile__isnull=True,
        )
        .select_related('user')
        .order_by('user__last_name', 'user__first_name', 'user__username')
    )
    for profile in admin_profiles:
        admin_user = profile.user
        name = ' '.join(
            part for part in (admin_user.last_name, admin_user.first_name) if part
        ) or admin_user.username
        people.append(('Администратор', name, profile.birth_date))

    notifications = []
    for role, name, birth_date in people:
        for target_date, (day_label, verb) in targets.items():
            if _birthday_in_year(birth_date, target_date.year) != target_date:
                continue
            age = target_date.year - birth_date.year
            notifications.append({
                'is_today': target_date == today,
                'message': (
                    f'{day_label} день рождения: {name} ({role.lower()}) — '
                    f'{verb} {age} {_age_word(age)}.'
                ),
            })
            break

    return sorted(
        notifications,
        key=lambda item: (not item['is_today'], item['message']),
    )


def birthday_notifications(request):
    return {
        'birthday_notifications': birthday_notifications_for_user(request.user),
    }
