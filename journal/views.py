from collections import defaultdict
from datetime import date
import json
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render

from .forms import (
    CourseApplicationPublicForm,
    GradeCreateForm,
)
from .models import CourseApplication, CourseRegistrationSettings, Grade, Group, Student, Subject, SubjectResult, Teacher


def _calculate_average(grade_values):
    numeric_values = []
    for value in grade_values:
        text = str(value).strip().upper()
        if text in {'1', '2', '3', '4', '5'}:
            numeric_values.append(int(text))
    if not numeric_values:
        return ''
    return f'{(sum(numeric_values) / len(numeric_values)):.2f}'


def _students_for_group_subject(group, subject, *, base_students=None):
    students = Student.objects.filter(group=group).filter(
        Q(group__subjects=subject) | Q(individual_subjects=subject)
    ).distinct()
    if base_students is not None:
        students = students.filter(pk__in=base_students.values('pk'))
    return students.order_by('full_name')


def _build_journal_tables(groups, subjects, students, grade_qs, results_qs):
    journal_tables = []
    result_map = {(result.student_id, result.subject_id): result for result in results_qs}
    grades_map = defaultdict(list)
    students_by_group = defaultdict(list)
    group_subject_ids = {group.id: set(group.subjects.values_list('id', flat=True)) for group in groups}
    subject_student_ids = {subject.id: set(subject.students.values_list('id', flat=True)) for subject in subjects}

    for grade in grade_qs:
        grades_map[(grade.student.group_id, grade.subject_id)].append(grade)

    for student in students:
        students_by_group[student.group_id].append(student)

    for group in groups:
        group_students = students_by_group.get(group.id, [])
        allowed_subject_ids = group_subject_ids.get(group.id, set())

        for subject in subjects:
            if subject.id in allowed_subject_ids:
                table_students = group_students
            else:
                allowed_student_ids = subject_student_ids.get(subject.id, set())
                table_students = [student for student in group_students if student.id in allowed_student_ids]

            subject_grades = grades_map.get((group.id, subject.id), [])
            dates = sorted({grade.date for grade in subject_grades})
            row_map = {student.id: {lesson_date: '' for lesson_date in dates} for student in table_students}
            for grade in subject_grades:
                row_map[grade.student_id][grade.date] = str(grade.value)

            rows = []
            for student in table_students:
                grades_by_date = {}
                grade_values = []
                for lesson_date in dates:
                    grades_by_date[lesson_date] = row_map[student.id][lesson_date]
                    if row_map[student.id][lesson_date]:
                        grade_values.append(row_map[student.id][lesson_date])

                subject_result = result_map.get((student.id, subject.id))
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
                }
            )

    return journal_tables


def _save_inline_grades(request, *, role_mode, students, subjects, teacher=None):
    changed = 0

    for field_name, raw_value in request.POST.items():
        if not field_name.startswith('grade__') and not field_name.startswith('exam__') and not field_name.startswith('final__'):
            continue

        if field_name.startswith('grade__'):
            field_mode = 'grade'
        elif field_name.startswith('exam__'):
            field_mode = 'exam'
        else:
            field_mode = 'final'

        value = raw_value.strip()

        if field_mode in {'exam', 'final'}:
            parts = field_name.split('__')
            if len(parts) != 3:
                continue

            _, subject_id_raw, student_id_raw = parts
            try:
                subject_id = int(subject_id_raw)
                student_id = int(student_id_raw)
            except (TypeError, ValueError):
                continue

            student = students.filter(pk=student_id).first()
            subject = subjects.filter(pk=subject_id).first()
            if student is None or subject is None:
                continue

            if role_mode == 'teacher' and (teacher is None or not teacher.subjects.filter(pk=subject.id).exists()):
                continue

            normalized_value = value
            if normalized_value.lower() == 'зачет':
                normalized_value = 'Зачет'
            elif normalized_value.lower() == 'незачет':
                normalized_value = 'Незачет'

            allowed_values = subject.get_final_grade_allowed_values()
            if normalized_value and normalized_value not in allowed_values:
                messages.error(request, 'Недопустимое значение для итоговой оценки по выбранному предмету.')
                return False

            result, _ = SubjectResult.objects.get_or_create(student=student, subject=subject)
            new_value = normalized_value if normalized_value else None

            if field_mode == 'exam':
                if result.exam_grade == new_value:
                    continue
                result.exam_grade = new_value
            else:
                if result.final_grade == new_value:
                    continue
                result.final_grade = new_value

            result.save()
            changed += 1
            continue

        normalized_grade_value = value.upper()
        if normalized_grade_value and normalized_grade_value not in {'1', '2', '3', '4', '5', 'Н'}:
            messages.error(request, 'Оценка должна быть 1-5 или Н.')
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

        grade = Grade.objects.filter(
            student_id=student_id,
            subject_id=subject_id,
            date=grade_date,
            student__in=students,
            subject__in=subjects,
        ).select_related('teacher').first()
        if not grade:
            continue

        if role_mode == 'teacher' and (teacher is None or grade.teacher_id != teacher.id):
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
        selected_group = groups.filter(pk=selected_group_id).first() if selected_group_id else None
        selected_subject = subjects.filter(pk=selected_subject_id).first() if selected_subject_id else None

        groups_to_show = [selected_group] if selected_group else list(groups)
        subjects_to_show = [selected_subject] if selected_subject else list(subjects)
        students_qs = Student.objects.filter(
            Q(group__in=groups_to_show) | Q(individual_subjects__in=subjects_to_show)
        ).distinct()

        students = list(students_qs.order_by('full_name'))
        grade_qs = Grade.objects.filter(student__in=students_qs, subject__in=subjects_to_show).select_related(
            'student',
            'subject',
            'teacher',
        )
        results_qs = SubjectResult.objects.filter(student__in=students_qs, subject__in=subjects_to_show).select_related(
            'student',
            'subject',
        )
        journal_tables = _build_journal_tables(groups_to_show, subjects_to_show, students, grade_qs, results_qs)

        grade_form = None
        if request.method == 'POST' and request.POST.get('action') == 'add_grade':
            posted_group_id = request.POST.get('group') or selected_group_id
            posted_subject_id = request.POST.get('subject') or selected_subject_id
            posted_group = groups.filter(pk=posted_group_id).first() if posted_group_id else None
            posted_subject = subjects.filter(pk=posted_subject_id).first() if posted_subject_id else None
            if posted_group and posted_subject:
                grade_form = GradeCreateForm(
                    request.POST,
                    group=posted_group,
                    students_queryset=_students_for_group_subject(posted_group, posted_subject),
                )
                if grade_form.is_valid():
                    grade_form.save()
                    messages.success(request, 'Оценка успешно добавлена.')
                    query = {'subject': posted_subject.id, 'group': posted_group.id}
                    return redirect(f"/?{urlencode(query)}")
                messages.error(request, 'Не удалось сохранить оценку. Проверьте данные формы.')
            else:
                messages.error(request, 'Не удалось сохранить оценку. Выберите группу и предмет.')

        if selected_group and selected_subject and grade_form is None:
            grade_form = GradeCreateForm(
                request.POST or None,
                group=selected_group,
                students_queryset=_students_for_group_subject(selected_group, selected_subject),
            )

        if request.method == 'POST' and request.POST.get('action') == 'inline_edit':
            if _save_inline_grades(
                request,
                role_mode=role_mode,
                students=students_qs,
                subjects=subjects,
            ):
                query = {}
                if selected_group:
                    query['group'] = selected_group.id
                if selected_subject:
                    query['subject'] = selected_subject.id
                return redirect(f"/?{urlencode(query)}")

        context = {
            'role_mode': role_mode,
            'groups': groups,
            'subjects': subjects,
            'students': students,
            'journal_tables': journal_tables,
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
                'selected_group_id': '',
                'selected_subject_id': '',
                'grade_form': None,
                'role_mode': '',
            },
        )

    if teacher is not None:
        role_mode = 'teacher'

        groups = Group.objects.filter(
            Q(subjects__in=teacher.subjects.all()) | Q(students__individual_subjects__in=teacher.subjects.all())
        ).distinct().order_by('name')
        subjects = teacher.subjects.all().order_by('name')
        selected_group = groups.filter(pk=selected_group_id).first() if selected_group_id else None
        selected_subject = subjects.filter(pk=selected_subject_id).first() if selected_subject_id else None

        groups_to_show = [selected_group] if selected_group else list(groups)
        subjects_to_show = [selected_subject] if selected_subject else list(subjects)
        students_qs = Student.objects.filter(
            Q(group__in=groups_to_show) | Q(individual_subjects__in=subjects_to_show)
        ).distinct()

        students = list(students_qs.order_by('full_name'))
        grade_qs = Grade.objects.filter(
            teacher=teacher,
            student__in=students_qs,
            subject__in=subjects_to_show,
        ).select_related('student', 'subject', 'teacher')
        results_qs = SubjectResult.objects.filter(
            student__in=students_qs,
            subject__in=subjects_to_show,
        ).select_related('student', 'subject')

        journal_tables = _build_journal_tables(groups_to_show, subjects_to_show, students, grade_qs, results_qs)

        grade_form = None
        if selected_group and selected_subject:
            grade_form = GradeCreateForm(
                request.POST or None,
                teacher=teacher,
                group=selected_group,
                students_queryset=_students_for_group_subject(selected_group, selected_subject),
            )
            if request.method == 'POST' and request.POST.get('action') == 'add_grade':
                if grade_form.is_valid():
                    grade_form.save()
                    messages.success(request, 'Оценка успешно добавлена.')
                    query = {'subject': selected_subject.id}
                    if selected_group:
                        query['group'] = selected_group.id
                    return redirect(f"/?{urlencode(query)}")
                messages.error(request, 'Не удалось сохранить оценку. Проверьте данные формы.')

        if request.method == 'POST' and request.POST.get('action') == 'inline_edit':
            if _save_inline_grades(
                request,
                role_mode=role_mode,
                students=students_qs,
                subjects=subjects,
                teacher=teacher,
            ):
                query = {}
                if selected_group:
                    query['group'] = selected_group.id
                if selected_subject:
                    query['subject'] = selected_subject.id
                return redirect(f"/?{urlencode(query)}")

        context = {
            'role_mode': role_mode,
            'groups': groups,
            'subjects': subjects,
            'students': students,
            'journal_tables': journal_tables,
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

    subjects = Subject.objects.filter(
        Q(groups=selected_group) | Q(students=student_profile)
    ).distinct().order_by('name')
    selected_subject = subjects.filter(pk=selected_subject_id).first() if selected_subject_id else None

    if selected_subject:
        table_subjects = [selected_subject]
    else:
        table_subjects = list(subjects)

    grade_qs = Grade.objects.filter(student=student_profile, subject__in=table_subjects).select_related(
        'student',
        'subject',
        'teacher',
    )
    results_qs = SubjectResult.objects.filter(
        student=student_profile,
        subject__in=table_subjects,
    ).select_related('student', 'subject')

    journal_tables = _build_journal_tables(groups, table_subjects, students, grade_qs, results_qs)

    if request.method == 'POST':
        messages.error(request, 'Ученику недоступно редактирование оценок.')
        return redirect(f"/?{urlencode({'subject': selected_subject_id} if selected_subject_id else {})}")

    context = {
        'role_mode': role_mode,
        'groups': groups,
        'subjects': subjects,
        'students': students,
        'journal_tables': journal_tables,
        'selected_group': selected_group,
        'selected_group_id': str(selected_group.id),
        'selected_subject_id': str(selected_subject_id or ''),
        'grade_form': None,
    }
    return render(request, 'journal.html', context)


def _load_registration_payload(request):
    if 'application/json' in request.headers.get('Content-Type', ''):
        try:
            return json.loads(request.body.decode('utf-8') or '{}')
        except json.JSONDecodeError:
            return None
    return request.POST


def _get_telegram_redirect_url() -> str:
    settings_obj = CourseRegistrationSettings.objects.first()
    if settings_obj is None:
        settings_obj = CourseRegistrationSettings.objects.create(pk=1, telegram_group_url='')
    return settings_obj.telegram_group_url.strip()


def course_registration_view(request):
    if request.method not in {'GET', 'POST'}:
        return HttpResponseNotAllowed(['GET', 'POST'])

    form = CourseApplicationPublicForm(request.POST or None)
    redirect_url = _get_telegram_redirect_url()

    if request.method == 'POST' and form.is_valid():
        application = form.save()
        return render(
            request,
            'journal/course_registration.html',
            {
                'submitted': True,
                'application': application,
                'redirect_url': redirect_url,
                'redirect_delay_seconds': 2,
            },
        )

    return render(
        request,
        'journal/course_registration.html',
        {
            'form': form,
            'submitted': False,
            'redirect_url': redirect_url,
            'redirect_delay_seconds': 2,
        },
    )


def course_registration_api(request):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    payload = _load_registration_payload(request)
    if payload is None:
        return JsonResponse({'success': False, 'message': 'Неверный формат запроса.'}, status=400)

    form = CourseApplicationPublicForm(payload)
    redirect_url = _get_telegram_redirect_url()

    if form.is_valid():
        application = form.save()
        return JsonResponse(
            {
                'success': True,
                'message': 'Заявка успешно отправлена.',
                'redirect_url': redirect_url,
                'application_id': application.id,
                'status': application.status,
            },
            status=201,
        )

    return JsonResponse(
        {
            'success': False,
            'message': 'Проверьте данные формы.',
            'errors': form.errors,
        },
        status=400,
    )
