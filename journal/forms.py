from __future__ import annotations

import json
from datetime import date
from typing import Optional

from django import forms
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.urls import reverse

from .grade_options import (
    get_grade_form_options,
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
    Instrument,
    OrchestraPart,
    Student,
    StudyGroup,
    Subject,
    SubjectResult,
    Teacher,
)
from .registration_utils import (
    latest_birth_date_for_age_in_year,
    reaches_age_in_calendar_year,
    normalize_parent_contacts,
    normalize_phone_number,
)
from .account_utils import clear_temporary_credentials_for_user


HTML_DATE_INPUT_FORMAT = '%Y-%m-%d'


def html_date_input(attrs=None):
    widget_attrs = {'type': 'date'}
    if attrs:
        widget_attrs.update(attrs)
    return forms.DateInput(format=HTML_DATE_INPUT_FORMAT, attrs=widget_attrs)


def configure_orchestra_part_field(form, instrument_field_name: str) -> None:
    """Configure a dynamically filtered orchestra-part field.

    The browser receives a complete, instrument-scoped option map. This keeps
    the dependency reliable in Django Admin/Jazzmin even when an AJAX request
    is blocked by a proxy, stale browser cache or a Select2 lifecycle race.
    Server-side validation still verifies that the selected part belongs to
    the selected instrument.
    """
    if 'orchestra_part' not in form.fields or instrument_field_name not in form.fields:
        return

    instrument_id = None
    custom_instrument = ''
    selected_part_id = None
    if form.is_bound:
        instrument_id = form.data.get(form.add_prefix(instrument_field_name))
        custom_instrument = (
            form.data.get(form.add_prefix('custom_instrument'), '') or ''
        ).strip()
        selected_part_id = form.data.get(form.add_prefix('orchestra_part'))
    elif form.instance and form.instance.pk:
        instrument_id = getattr(form.instance, f'{instrument_field_name}_id', None)
        custom_instrument = (form.instance.custom_instrument or '').strip()
        selected_part_id = form.instance.orchestra_part_id

    available_parts = OrchestraPart.objects.filter(is_active=True)
    if selected_part_id:
        try:
            available_parts = OrchestraPart.objects.filter(
                Q(is_active=True) | Q(pk=selected_part_id),
            )
        except (TypeError, ValueError):
            available_parts = OrchestraPart.objects.filter(is_active=True)
    available_parts = available_parts.select_related('instrument').order_by(
        'instrument__name',
        'name',
        'pk',
    )
    part_rows = list(
        available_parts.values('id', 'name', 'instrument_id')
    )
    parts_by_instrument: dict[str, list[dict[str, object]]] = {}
    for part in part_rows:
        parts_by_instrument.setdefault(str(part['instrument_id']), []).append({
            'id': part['id'],
            'name': part['name'],
        })

    field = form.fields['orchestra_part']
    field.required = False
    # Keep all active choices available to ModelChoiceField. The visible list
    # is narrowed by JavaScript and clean() rejects a mismatched instrument.
    field.queryset = available_parts
    field.empty_label = 'Не выбрана'
    has_available_parts = bool(
        instrument_id
        and parts_by_instrument.get(str(instrument_id))
    )
    # Do not set forms.Field.disabled: Django would then ignore the submitted
    # value even after JavaScript unlocks the HTML select.
    field.disabled = False
    field.help_text = (
        'Поле становится доступным только для инструмента, у которого в справочнике '
        'созданы активные партии оркестра.'
    )
    field.widget.attrs.update({
        'data-orchestra-part': '1',
        'data-orchestra-parts-url': reverse('orchestra_part_options_api'),
        'data-orchestra-parts-map': json.dumps(
            parts_by_instrument,
            ensure_ascii=False,
            separators=(',', ':'),
        ),
        'data-instrument-field': instrument_field_name,
        'data-selected-orchestra-part': str(selected_part_id or ''),
        'aria-disabled': 'false' if has_available_parts and not custom_instrument else 'true',
    })
    if custom_instrument or not has_available_parts:
        field.widget.attrs['disabled'] = True
    else:
        field.widget.attrs.pop('disabled', None)



def configure_instrument_selection_fields(
    form,
    instrument_field_name: str,
    *,
    instrument_help_text: str | None = None,
    custom_help_text: str | None = None,
    custom_placeholder: str = 'Введите название собственного инструмента',
) -> None:
    """Apply one shared instrument/part workflow to registration and admin forms.

    Both forms intentionally expose the same three-step interaction:
    1. choose an instrument from the directory or select ``Другой инструмент``;
    2. enter a custom instrument only for that fallback choice;
    3. choose an orchestra part only from active parts of the selected directory
       instrument.

    Keeping this setup in one helper prevents the admin form and registration
    form from drifting apart again.
    """
    instrument_field = form.fields.get(instrument_field_name)
    custom_field = form.fields.get('custom_instrument')
    if instrument_field is None or custom_field is None:
        return

    selected_instrument_id = None
    if form.is_bound:
        selected_instrument_id = (
            form.data.get(form.add_prefix(instrument_field_name))
            or form.data.get(instrument_field_name)
        )
    elif form.instance:
        selected_instrument_id = getattr(
            form.instance,
            f'{instrument_field_name}_id',
            None,
        )

    instrument_field.queryset = Instrument.objects.order_by('name')
    instrument_field.label = 'Инструмент'
    instrument_field.empty_label = 'Другой инструмент'
    instrument_field.help_text = instrument_help_text or (
        'Выберите инструмент из справочника. Если подходящего значения нет, '
        'оставьте «Другой инструмент» и заполните поле ниже.'
    )
    instrument_field.widget.attrs.update({
        'data-instrument-reference': '1',
        'data-instrument-dependency': '1',
        'data-placeholder': 'Другой инструмент',
    })

    custom_field.required = False
    custom_field.label = 'Собственный инструмент'
    custom_field.help_text = custom_help_text or (
        'Поле доступно только при выборе варианта «Другой инструмент».'
    )
    custom_field.widget.attrs.update({
        'data-custom-instrument': '1',
        'data-instrument-dependency': '1',
        'placeholder': custom_placeholder,
        'aria-disabled': 'true' if selected_instrument_id else 'false',
    })
    if selected_instrument_id:
        custom_field.widget.attrs['disabled'] = True
    else:
        custom_field.widget.attrs.pop('disabled', None)

    configure_orchestra_part_field(form, instrument_field_name)
    orchestra_part_field = form.fields.get('orchestra_part')
    if orchestra_part_field is not None:
        orchestra_part_field.label = 'Партия в оркестре'
        orchestra_part_field.widget.attrs.update({
            'data-instrument-dependency': '1',
            'data-native-dependent-select': '1',
        })


# -----------------------------------------------------------------------------
# Общие queryset/helper-функции для форм журнала
# -----------------------------------------------------------------------------


def get_student_allowed_subjects(
    student: Optional[Student],
    academic_year: Optional[AcademicYear] = None,
):
    """
    Возвращает предметы, доступные ученику:
    1) предметы его группы через GroupSubject;
    2) индивидуальные предметы через StudentSubject.
    """
    if student is None or not getattr(student, 'pk', None):
        return Subject.objects.none()
    return get_grade_subjects(student=student, academic_year=academic_year)


def get_student_subject_teachers(
    student: Optional[Student],
    subject: Optional[Subject],
    academic_year: Optional[AcademicYear] = None,
):
    """
    Возвращает преподавателей, которые действительно могут вести выбранный
    предмет у выбранного ученика.
    """
    if student is None or subject is None or not getattr(student, 'pk', None) or not getattr(subject, 'pk', None):
        return Teacher.objects.none()

    return get_grade_teachers(
        student=student,
        subject=subject,
        academic_year=academic_year,
    )


def get_teacher_subjects(
    teacher: Optional[Teacher],
    group: Optional[StudyGroup] = None,
    academic_year: Optional[AcademicYear] = None,
):
    """
    Предметы, которые преподаватель реально ведет:
    - в группах через GroupSubject;
    - индивидуально у учеников через StudentSubject.
    Если передана группа, ограничиваем выбор этой группой.
    """
    if teacher is None or not getattr(teacher, 'pk', None):
        return Subject.objects.none()

    return get_grade_subjects(
        group=group,
        teacher=teacher,
        academic_year=academic_year,
    )


def get_teacher_groups(
    teacher: Optional[Teacher],
    academic_year: Optional[AcademicYear] = None,
):
    """
    Группы, с которыми связан преподаватель:
    - ведет групповой предмет;
    - ведет индивидуальный предмет ученика из группы.
    """
    if teacher is None or not getattr(teacher, 'pk', None):
        return StudyGroup.objects.none()

    return get_grade_groups(teacher=teacher, academic_year=academic_year)


def get_students_for_group_subject(
    *,
    group: Optional[StudyGroup],
    subject: Optional[Subject],
    teacher: Optional[Teacher] = None,
    base_queryset=None,
    academic_year: Optional[AcademicYear] = None,
):
    """
    Ученики, которым можно поставить оценку по выбранному предмету.
    Учитывает групповые и индивидуальные назначения.
    """
    if group is None or subject is None:
        return Student.objects.none()
    return get_grade_students(
        group=group,
        subject=subject,
        teacher=teacher,
        base_queryset=base_queryset,
        academic_year=academic_year,
    )


def _safe_model_choice_value(model, raw_value):
    if model is None or raw_value in (None, ''):
        return None
    try:
        return model.objects.get(pk=raw_value)
    except (model.DoesNotExist, TypeError, ValueError):
        return None


# -----------------------------------------------------------------------------
# Авторизация и смена пароля
# -----------------------------------------------------------------------------


class DetailedPasswordChangeForm(SetPasswordForm):
    """
    Смена пароля без ввода текущего пароля.

    Используется в PasswordChangeView как form_class. В форме остаются только:
    - new_password1;
    - new_password2.
    """

    error_messages = {
        **SetPasswordForm.error_messages,
        'password_mismatch': 'Новый пароль и подтверждение не совпадают.',
        'password_unchanged': 'Новый пароль не должен совпадать со старым.',
    }

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        self.fields['new_password1'].label = 'Новый пароль'
        self.fields['new_password2'].label = 'Повторите новый пароль'
        self.fields['new_password1'].widget.attrs.update({
            'autocomplete': 'new-password',
            'autocapitalize': 'none',
            'spellcheck': 'false',
            'placeholder': 'Введите новый пароль',
        })
        self.fields['new_password2'].widget.attrs.update({
            'autocomplete': 'new-password',
            'autocapitalize': 'none',
            'spellcheck': 'false',
            'placeholder': 'Повторите новый пароль',
        })

    def clean(self):
        cleaned_data = super().clean()
        password2 = cleaned_data.get('new_password2')
        if password2 and self.user.check_password(password2):
            self.add_error(
                'new_password2',
                forms.ValidationError(
                    self.error_messages['password_unchanged'],
                    code='password_unchanged',
                ),
            )
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            clear_temporary_credentials_for_user(user)
        return user


class SiteAuthenticationForm(AuthenticationForm):
    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request, *args, **kwargs)
        self.fields['username'].label = 'Логин'
        self.fields['password'].label = 'Пароль'
        self.fields['username'].widget.attrs.update({
            'autocomplete': 'username',
            'inputmode': 'text',
            'placeholder': 'Введите логин',
        })
        self.fields['password'].widget.attrs.update({
            'autocomplete': 'current-password',
            'placeholder': 'Введите пароль',
        })


# -----------------------------------------------------------------------------
# Формы оценок и итогов
# -----------------------------------------------------------------------------


class GradeCreateForm(forms.ModelForm):
    group = forms.ModelChoiceField(
        label='Группа',
        queryset=StudyGroup.objects.none(),
        required=False,
        empty_label='Все группы',
    )

    class Meta:
        model = Grade
        fields = [
            'group',
            'student',
            'subject',
            'teacher',
            'academic_year',
            'date',
            'value',
            'comment',
        ]
        widgets = {
            'date': html_date_input(),
            'comment': forms.TextInput(attrs={'placeholder': 'Комментарий, если нужен'}),
            'value': forms.TextInput(attrs={'maxlength': 2, 'placeholder': 'Например: 4+, 5- или Н'}),
        }

    def __init__(
        self,
        *args,
        teacher: Optional[Teacher] = None,
        group: Optional[StudyGroup] = None,
        initial_group: Optional[StudyGroup] = None,
        initial_subject: Optional[Subject] = None,
        subject: Optional[Subject] = None,
        students_queryset=None,
        academic_year: Optional[AcademicYear] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.teacher = teacher
        self.context_group = group
        self.initial_group = initial_group
        self.initial_subject = initial_subject
        self.fixed_subject = subject
        self.fixed_academic_year = academic_year
        self.dependency_teacher_id = teacher.pk if teacher is not None else ''
        self.dependency_subject_id = subject.pk if subject is not None else ''
        self.dependency_academic_year_id = academic_year.pk if academic_year is not None else ''
        self.fields['student'].error_messages['invalid_choice'] = (
            'Выбранный ученик недоступен для этой группы, предмета или учебного года.'
        )

        default_grade_date = date.today()
        if academic_year is not None and not (academic_year.starts_on <= default_grade_date <= academic_year.ends_on):
            default_grade_date = academic_year.starts_on
        self.fields['date'].initial = self.fields['date'].initial or default_grade_date
        self.fields['academic_year'].queryset = AcademicYear.objects.filter(is_active=True).order_by('-starts_on')

        if academic_year is not None:
            self.fields['academic_year'].initial = academic_year
            self.fields['academic_year'].disabled = True

        selected_student = self._selected_student()
        selected_academic_year = academic_year or self._selected_academic_year()
        group_was_submitted = self.is_bound and (
            self.add_prefix('group') in self.data or 'group' in self.data
        )
        selected_group = group or self._selected_group(
            selected_student,
            selected_academic_year,
        )
        if selected_group is None and not group_was_submitted:
            selected_group = initial_group
        selected_subject = subject or self._selected_subject() or initial_subject
        selected_teacher = teacher or self._selected_teacher()
        dependency_options = get_grade_form_options(
            academic_year=selected_academic_year,
            group=selected_group,
            fixed_teacher=teacher,
            teacher=selected_teacher,
            student=selected_student,
            subject=selected_subject,
            students_queryset=students_queryset,
            individual_only=selected_group is None,
        )
        group_queryset = dependency_options['groups']
        if group is not None:
            group_queryset = group_queryset.filter(pk=group.pk)
        student_queryset = dependency_options['students']
        subject_queryset = dependency_options['subjects']
        teacher_queryset = dependency_options['teachers']

        if subject is not None:
            subject_queryset = subject_queryset.filter(pk=subject.pk)
            self.fields['subject'].initial = subject
        elif initial_subject is not None:
            self.fields['subject'].initial = initial_subject
        if teacher is not None:
            teacher_queryset = teacher_queryset.filter(pk=teacher.pk)
            self.fields['teacher'].initial = teacher

        self.fields['group'].queryset = self._include_submitted_choice(
            group_queryset,
            StudyGroup,
            'group',
            allowed_submitted_queryset=StudyGroup.objects.all(),
        )
        self.fields['student'].queryset = self._include_submitted_choice(
            student_queryset,
            Student,
            'student',
            allowed_submitted_queryset=get_grade_students(
                group=selected_group,
                academic_year=selected_academic_year,
                base_queryset=students_queryset,
            ),
        )
        self.fields['subject'].queryset = self._include_submitted_choice(
            subject_queryset,
            Subject,
            'subject',
            allowed_submitted_queryset=get_grade_subjects(
                group=selected_group,
                student=selected_student,
                academic_year=selected_academic_year,
            ),
        )
        self.fields['teacher'].queryset = self._include_submitted_choice(
            teacher_queryset,
            Teacher,
            'teacher',
            allowed_submitted_queryset=Teacher.objects.filter(is_active=True),
        )

        if selected_group is not None:
            self.fields['group'].initial = selected_group

        if teacher is not None:
            self.fields.pop('teacher', None)

        if subject is not None:
            self.fields.pop('subject', None)

    def _include_submitted_choice(
        self,
        queryset,
        model,
        field_name,
        *,
        allowed_submitted_queryset=None,
    ):
        if not self.is_bound:
            return queryset
        raw_value = self.data.get(self.add_prefix(field_name)) or self.data.get(field_name)
        selected = _safe_model_choice_value(model, raw_value)
        if selected is None:
            return queryset
        instance_value = getattr(self.instance, f'{field_name}_id', None)
        is_existing_value = self.instance.pk and selected.pk == instance_value
        include_selected = Q(pk=selected.pk) if is_existing_value else Q(pk__in=[])
        if allowed_submitted_queryset is not None:
            include_selected |= (
                Q(pk=selected.pk)
                & Q(pk__in=allowed_submitted_queryset.values('pk'))
            )
        if not is_existing_value and allowed_submitted_queryset is None:
            return queryset
        return model.objects.filter(
            Q(pk__in=queryset.values('pk')) | include_selected,
        ).distinct()

    def _selected_student(self):
        student = getattr(self.instance, 'student', None) if self.instance and self.instance.pk else None
        raw_student_id = self.data.get(self.add_prefix('student')) or self.data.get('student') or getattr(self.instance, 'student_id', None)
        if raw_student_id:
            student = _safe_model_choice_value(Student, raw_student_id) or student
        return student

    def _selected_subject(self):
        subject = getattr(self.instance, 'subject', None) if self.instance and self.instance.pk else None
        raw_subject_id = self.data.get(self.add_prefix('subject')) or self.data.get('subject') or getattr(self.instance, 'subject_id', None)
        if raw_subject_id:
            subject = _safe_model_choice_value(Subject, raw_subject_id) or subject
        return subject

    def _selected_teacher(self):
        teacher = getattr(self.instance, 'teacher', None) if self.instance and self.instance.pk else None
        raw_teacher_id = self.data.get(self.add_prefix('teacher')) or self.data.get('teacher') or getattr(self.instance, 'teacher_id', None)
        if raw_teacher_id:
            teacher = _safe_model_choice_value(Teacher, raw_teacher_id) or teacher
        return teacher

    def _selected_group(self, selected_student=None, academic_year=None):
        group = None
        raw_group_id = self.data.get(self.add_prefix('group')) or self.data.get('group')
        if raw_group_id:
            group = _safe_model_choice_value(StudyGroup, raw_group_id) or group
        return group

    def _selected_academic_year(self):
        academic_year = getattr(self.instance, 'academic_year', None) if self.instance and self.instance.pk else None
        raw_year_id = self.data.get(self.add_prefix('academic_year')) or self.data.get('academic_year') or getattr(self.instance, 'academic_year_id', None)
        if raw_year_id:
            academic_year = _safe_model_choice_value(AcademicYear, raw_year_id) or academic_year
        return academic_year

    def clean_value(self):
        value = Grade(value=self.cleaned_data['value'])
        value.normalize_value()
        value = value.value
        if value not in Grade.ALLOWED_VALUES:
            raise forms.ValidationError(
                'Допустимы оценки от 1 до 5 со знаком +/− либо Н.',
            )
        return value

    def clean(self):
        cleaned_data = super().clean()

        group = self.context_group or cleaned_data.get('group')
        student = cleaned_data.get('student')
        subject = self.fixed_subject or cleaned_data.get('subject')
        teacher = self.teacher or cleaned_data.get('teacher')
        academic_year = self.fixed_academic_year or cleaned_data.get('academic_year')

        if self.context_group is not None:
            cleaned_data['group'] = self.context_group

        if self.fixed_subject is not None:
            cleaned_data['subject'] = self.fixed_subject
        if self.teacher is not None:
            cleaned_data['teacher'] = self.teacher
        if self.fixed_academic_year is not None:
            cleaned_data['academic_year'] = self.fixed_academic_year

        if group and student:
            enrollment = student.enrollment_for_year(academic_year)
            if enrollment is None or enrollment.group_id != group.pk:
                self.add_error(
                    'student',
                    'Ученик не состоит в выбранной группе.',
                )

        if group and academic_year and group.academic_year_id != academic_year.pk:
            self.add_error('academic_year', 'Группа относится к другому учебному году.')

        if academic_year and not academic_year.is_active:
            self.add_error('academic_year', 'Архивный учебный год доступен только для просмотра.')

        grade_date = cleaned_data.get('date')
        if grade_date and academic_year and not (academic_year.starts_on <= grade_date <= academic_year.ends_on):
            self.add_error(
                'date',
                (
                    'Дата оценки должна попадать в период выбранного учебного года: '
                    f'{academic_year.starts_on:%d.%m.%Y} - {academic_year.ends_on:%d.%m.%Y}.'
                ),
            )

        if student and subject:
            if not get_grade_subjects(
                group=group,
                student=student,
                academic_year=academic_year,
            ).filter(pk=subject.pk).exists():
                raise forms.ValidationError(
                    'Ученик не может получить оценку по предмету, который не назначен его группе '
                    'и не назначен ему индивидуально.'
                )

        if student and subject and teacher:
            teacher_is_allowed = get_grade_teachers(
                group=group,
                student=student,
                subject=subject,
                academic_year=academic_year,
            ).filter(pk=teacher.pk).exists()
            if not teacher_is_allowed:
                message = 'Этот преподаватель не назначен выбранному ученику по выбранному предмету.'
                if 'teacher' in self.fields:
                    self.add_error('teacher', message)
                else:
                    raise forms.ValidationError(message)

        return cleaned_data

    def save(self, commit=True):
        grade = super().save(commit=False)
        if self.teacher is not None:
            grade.teacher = self.teacher
        if self.fixed_subject is not None:
            grade.subject = self.fixed_subject
        if self.fixed_academic_year is not None and grade.academic_year_id is None:
            grade.academic_year = self.fixed_academic_year
        if commit:
            grade.save()
        return grade


class SubjectResultForm(forms.ModelForm):
    class Meta:
        model = SubjectResult
        fields = ['student', 'subject', 'academic_year', 'exam_grade', 'final_grade']
        widgets = {
            'exam_grade': forms.TextInput(attrs={'placeholder': 'Например: 4+, 5- или Н'}),
            'final_grade': forms.TextInput(attrs={'placeholder': 'Например: 4+, 5- или Н'}),
        }

    def __init__(self, *args, student: Optional[Student] = None, subject: Optional[Subject] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fixed_student = student
        self.fixed_subject = subject

        self.fields['student'].queryset = Student.objects.filter(is_active=True).order_by('full_name')
        self.fields['subject'].queryset = Subject.objects.filter(is_active=True).order_by('name')
        self.fields['academic_year'].queryset = AcademicYear.objects.filter(is_active=True).order_by('-starts_on')

        if student is not None:
            self.fields['student'].initial = student
            self.fields['student'].queryset = Student.objects.filter(pk=student.pk)
            self.fields['subject'].queryset = get_student_allowed_subjects(
                student,
                AcademicYear.get_active(),
            )

        if subject is not None:
            self.fields['subject'].initial = subject
            self.fields['subject'].queryset = self.fields['subject'].queryset.filter(pk=subject.pk)

    def clean(self):
        cleaned_data = super().clean()
        student = self.fixed_student or cleaned_data.get('student')
        subject = self.fixed_subject or cleaned_data.get('subject')
        academic_year = cleaned_data.get('academic_year')

        if self.fixed_student is not None:
            cleaned_data['student'] = self.fixed_student
        if self.fixed_subject is not None:
            cleaned_data['subject'] = self.fixed_subject

        if student and subject:
            if academic_year and not academic_year.is_active:
                self.add_error('academic_year', 'Архивный учебный год доступен только для просмотра.')
            if academic_year and student.enrollment_for_year(academic_year) is None:
                self.add_error('student', 'Ученик не зачислен в выбранный учебный год.')

            if not get_student_allowed_subjects(
                student,
                academic_year,
            ).filter(pk=subject.pk).exists():
                raise forms.ValidationError(
                    'Нельзя выставить итог по предмету, который не назначен ученику.'
                )

            if subject.uses_element_assessment:
                self.add_error(
                    'final_grade',
                    'Итог специального режима рассчитывается автоматически и вручную не изменяется.',
                )
            for field_name in ('exam_grade', 'final_grade'):
                try:
                    cleaned_data[field_name] = subject.validate_final_grade(
                        cleaned_data.get(field_name),
                    )
                except ValidationError as exc:
                    self.add_error(field_name, exc)

        return cleaned_data


# -----------------------------------------------------------------------------
# Заявки на курсы
# -----------------------------------------------------------------------------


class BaseCourseApplicationForm(forms.ModelForm):
    def __init__(
        self,
        *args,
        age_limit: bool = False,
        include_status: bool = False,
        registration_settings: CourseRegistrationSettings | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.age_limit = age_limit
        self.registration_settings = registration_settings
        self.minimum_registration_age = None
        self.age_reference_date = None

        if self.age_limit:
            self.registration_settings = self.registration_settings or CourseRegistrationSettings.load()
            self.minimum_registration_age = self.registration_settings.minimum_registration_age
            active_year = AcademicYear.get_active()
            self.age_reference_date = (
                active_year.starts_on
                if active_year is not None
                else date.today()
            )

        if 'status' in self.fields and not include_status:
            self.fields.pop('status')

        field_attrs = {
            'last_name': {
                'autocomplete': 'family-name',
                'placeholder': 'Фамилия',
            },
            'first_name': {
                'autocomplete': 'given-name',
                'placeholder': 'Имя',
            },
            'middle_name': {
                'autocomplete': 'additional-name',
                'placeholder': 'Отчество',
            },
            'city_church': {
                'placeholder': 'Например: Тамбов или Воронеж, Отрожка',
                'class': 'city-church-field',
                'size': '80',
            },
        }
        for field_name, attrs in field_attrs.items():
            if field_name in self.fields:
                self.fields[field_name].widget.attrs.update(attrs)

        if 'gender' in self.fields:
            self.fields['gender'].widget = forms.RadioSelect(choices=CourseApplication.GENDER_CHOICES)
        if 'birth_date' in self.fields:
            self.fields['birth_date'].widget = html_date_input()
        configure_instrument_selection_fields(
            self,
            'instrument_reference',
            custom_placeholder='Например, домра малая',
        )
        if 'music_education' in self.fields:
            self.fields['music_education'].widget = forms.Select(
                choices=CourseApplication.MUSIC_EDUCATION_CHOICES,
            )
        if 'student_phone' in self.fields:
            self.fields['student_phone'].widget = forms.TextInput(attrs={
                'type': 'tel',
                'inputmode': 'tel',
                'placeholder': '+7 (999) 123-45-67',
                'autocomplete': 'tel',
            })
        if 'parent_contacts' in self.fields:
            self.fields['parent_contacts'].widget = forms.Textarea(attrs={
                'rows': 4,
                'placeholder': (
                    'Иванов Иван Иванович — +7 (999) 123-45-67\n'
                    'Петрова Анна Сергеевна — +7 (999) 987-65-43'
                ),
            })
            self.fields['parent_contacts'].required = False
        if 'comments' in self.fields:
            self.fields['comments'].widget = forms.Textarea(attrs={
                'rows': 4,
                'placeholder': 'Дополнительные вопросы или комментарии',
            })
            self.fields['comments'].required = False

        if self.age_limit and 'birth_date' in self.fields:
            course_start_year = self.age_reference_date.year
            latest_birth_date = latest_birth_date_for_age_in_year(
                self.minimum_registration_age,
                year=course_start_year,
            )
            age_error_message = (
                f'Регистрация доступна ученикам, которым в {course_start_year} году '
                f'исполнится не менее {self.minimum_registration_age} лет.'
            )
            self.fields['birth_date'].widget.attrs.update({
                'max': latest_birth_date.isoformat(),
                'data-age-limit': str(self.minimum_registration_age),
                'data-age-reference-year': str(course_start_year),
                'data-age-error-message': age_error_message,
            })
            self.fields['birth_date'].help_text = (
                f'Можно зарегистрироваться, если в {course_start_year} году исполнится '
                f'{self.minimum_registration_age} лет или больше.'
            )


    class Meta:
        model = CourseApplication
        fields = [
            'last_name',
            'first_name',
            'middle_name',
            'gender',
            'birth_date',
            'city_church',
            'instrument_reference',
            'custom_instrument',
            'orchestra_part',
            'music_education',
            'student_phone',
            'parent_contacts',
            'comments',
            'status',
        ]
        widgets = {
            'comments': forms.Textarea(attrs={'rows': 4}),
        }

    def clean_last_name(self):
        return self.cleaned_data['last_name'].strip()

    def clean_first_name(self):
        return self.cleaned_data['first_name'].strip()

    def clean_middle_name(self):
        return self.cleaned_data.get('middle_name', '').strip()

    def clean_city_church(self):
        return self.cleaned_data['city_church'].strip()

    def clean(self):
        cleaned_data = super().clean()
        reference = cleaned_data.get('instrument_reference')
        custom = (cleaned_data.get('custom_instrument') or '').strip()
        orchestra_part = cleaned_data.get('orchestra_part')
        cleaned_data['custom_instrument'] = custom
        if reference and custom:
            self.add_error(
                'custom_instrument',
                'Нельзя одновременно выбрать инструмент из справочника и указать собственный.',
            )
        elif not reference and not custom:
            self.add_error(
                'custom_instrument',
                'Выберите инструмент или укажите собственное название.',
            )
        if reference:
            cleaned_data['custom_instrument'] = ''
        if custom:
            cleaned_data['orchestra_part'] = None
        elif orchestra_part and reference and orchestra_part.instrument_id != reference.pk:
            self.add_error(
                'orchestra_part',
                'Выбранная партия не относится к выбранному инструменту.',
            )
        return cleaned_data

    def clean_student_phone(self):
        return normalize_phone_number(self.cleaned_data['student_phone'])

    def clean_parent_contacts(self):
        return normalize_parent_contacts(self.cleaned_data.get('parent_contacts', ''))

    def clean_birth_date(self):
        birth_date = self.cleaned_data['birth_date']
        if birth_date > date.today():
            raise forms.ValidationError('Дата рождения не может быть в будущем.')
        if (
            self.age_limit
            and not reaches_age_in_calendar_year(
                birth_date,
                self.minimum_registration_age,
                year=self.age_reference_date.year,
            )
        ):
            raise forms.ValidationError(
                f'Регистрация доступна ученикам, которым в {self.age_reference_date.year} году '
                f'исполнится не менее {self.minimum_registration_age} лет.'
            )
        return birth_date

    def clean_comments(self):
        return self.cleaned_data.get('comments', '').strip()


class CourseApplicationPublicForm(BaseCourseApplicationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, age_limit=True, include_status=False, **kwargs)

    class Media:
        js = ('journal/orchestra_part_dependencies_v5.js',)


class CourseApplicationAdminForm(BaseCourseApplicationForm):
    """
    Форма для ручного редактирования заявки вне ModelAdmin.
    В публичной форме status скрыт, а здесь доступен.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, age_limit=False, include_status=True, **kwargs)

    class Media:
        js = ('journal/orchestra_part_dependencies_v5.js',)


# -----------------------------------------------------------------------------
# Настройки регистрации
# -----------------------------------------------------------------------------


class CourseRegistrationSettingsForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        if kwargs.get('instance') is None:
            try:
                kwargs['instance'] = CourseRegistrationSettings.load()
            except CourseRegistrationSettings.DoesNotExist:
                pass
        super().__init__(*args, **kwargs)

    class Meta:
        model = CourseRegistrationSettings
        fields = [
            'telegram_group_url',
            'minimum_registration_age',
            'registration_mode',
            'application_limit',
        ]
        widgets = {
            'telegram_group_url': forms.URLInput(attrs={
                'placeholder': 'https://t.me/your_group_or_invite_link',
            }),
            'minimum_registration_age': forms.NumberInput(attrs={
                'min': 0,
                'max': 120,
            }),
            'application_limit': forms.NumberInput(attrs={
                'min': 1,
                'step': 1,
                'placeholder': 'Например: 100',
            }),
        }

    def clean_telegram_group_url(self):
        value = self.cleaned_data.get('telegram_group_url', '').strip()
        if not value:
            raise forms.ValidationError('Укажите ссылку на Telegram-группу.')
        return value
