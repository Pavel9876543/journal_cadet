from __future__ import annotations

from datetime import date
from typing import Optional

from django import forms
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm
from django.db.models import Q

from .models import (
    AcademicYear,
    CourseApplication,
    CourseRegistrationSettings,
    Grade,
    GroupSubject,
    Student,
    StudentSubject,
    StudyGroup,
    Subject,
    SubjectResult,
    Teacher,
)
from .registration_utils import (
    calculate_age,
    minimum_birth_date_for_age,
    normalize_parent_contacts,
    normalize_phone_number,
)


HTML_DATE_INPUT_FORMAT = '%Y-%m-%d'


def html_date_input(attrs=None):
    widget_attrs = {'type': 'date'}
    if attrs:
        widget_attrs.update(attrs)
    return forms.DateInput(format=HTML_DATE_INPUT_FORMAT, attrs=widget_attrs)


# -----------------------------------------------------------------------------
# Общие queryset/helper-функции для форм журнала
# -----------------------------------------------------------------------------


def get_student_allowed_subjects(student: Optional[Student]):
    """
    Возвращает предметы, доступные ученику:
    1) предметы его группы через GroupSubject;
    2) индивидуальные предметы через StudentSubject.
    """
    if student is None or not getattr(student, 'pk', None):
        return Subject.objects.none()

    group_subject_ids = GroupSubject.objects.filter(
        group_id=student.group_id,
        is_active=True,
    ).values_list('subject_id', flat=True)

    individual_subject_ids = StudentSubject.objects.filter(
        student_id=student.pk,
        is_active=True,
    ).values_list('subject_id', flat=True)

    return (
        Subject.objects
        .filter(Q(pk__in=group_subject_ids) | Q(pk__in=individual_subject_ids))
        .distinct()
        .order_by('name')
    )


def get_student_subject_teachers(student: Optional[Student], subject: Optional[Subject]):
    """
    Возвращает преподавателей, которые действительно могут вести выбранный
    предмет у выбранного ученика.
    """
    if student is None or subject is None or not getattr(student, 'pk', None) or not getattr(subject, 'pk', None):
        return Teacher.objects.none()

    group_teacher_ids = GroupSubject.objects.filter(
        group_id=student.group_id,
        subject_id=subject.pk,
        is_active=True,
    ).values_list('teacher_id', flat=True)

    individual_teacher_ids = StudentSubject.objects.filter(
        student_id=student.pk,
        subject_id=subject.pk,
        is_active=True,
    ).values_list('teacher_id', flat=True)

    return (
        Teacher.objects
        .filter(Q(pk__in=group_teacher_ids) | Q(pk__in=individual_teacher_ids))
        .distinct()
        .order_by('full_name')
    )


def get_teacher_subjects(teacher: Optional[Teacher], group: Optional[StudyGroup] = None):
    """
    Предметы, которые преподаватель реально ведет:
    - в группах через GroupSubject;
    - индивидуально у учеников через StudentSubject.
    Если передана группа, ограничиваем выбор этой группой.
    """
    if teacher is None or not getattr(teacher, 'pk', None):
        return Subject.objects.none()

    group_assignments = GroupSubject.objects.filter(teacher=teacher, is_active=True)
    individual_assignments = StudentSubject.objects.filter(teacher=teacher, is_active=True)

    if group is not None:
        group_assignments = group_assignments.filter(group=group)
        individual_assignments = individual_assignments.filter(student__group=group)

    group_subject_ids = group_assignments.values_list('subject_id', flat=True)
    individual_subject_ids = individual_assignments.values_list('subject_id', flat=True)

    return (
        Subject.objects
        .filter(Q(pk__in=group_subject_ids) | Q(pk__in=individual_subject_ids))
        .distinct()
        .order_by('name')
    )


def get_teacher_groups(teacher: Optional[Teacher]):
    """
    Группы, с которыми связан преподаватель:
    - ведет групповой предмет;
    - ведет индивидуальный предмет ученика из группы.
    """
    if teacher is None or not getattr(teacher, 'pk', None):
        return StudyGroup.objects.none()

    group_ids = GroupSubject.objects.filter(
        teacher=teacher,
        is_active=True,
    ).values_list('group_id', flat=True)

    individual_group_ids = StudentSubject.objects.filter(
        teacher=teacher,
        is_active=True,
    ).values_list('student__group_id', flat=True)

    return (
        StudyGroup.objects
        .filter(Q(pk__in=group_ids) | Q(pk__in=individual_group_ids))
        .distinct()
        .order_by('academic_year__name', 'name')
    )


def get_students_for_group_subject(
    *,
    group: Optional[StudyGroup],
    subject: Optional[Subject],
    teacher: Optional[Teacher] = None,
    base_queryset=None,
):
    """
    Ученики, которым можно поставить оценку по выбранному предмету.
    Учитывает групповые и индивидуальные назначения.
    """
    if group is None or subject is None:
        return Student.objects.none()

    qs = base_queryset if base_queryset is not None else Student.objects.filter(group=group, is_active=True)
    qs = qs.filter(group=group)

    group_assignment_filter = Q(group__group_subjects__subject=subject, group__group_subjects__is_active=True)
    individual_assignment_filter = Q(individual_subjects__subject=subject, individual_subjects__is_active=True)

    if teacher is not None:
        group_assignment_filter &= Q(group__group_subjects__teacher=teacher)
        individual_assignment_filter &= Q(individual_subjects__teacher=teacher)

    return qs.filter(group_assignment_filter | individual_assignment_filter).distinct().order_by('full_name')


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
            'placeholder': 'Введите новый пароль',
        })
        self.fields['new_password2'].widget.attrs.update({
            'autocomplete': 'new-password',
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
    class Meta:
        model = Grade
        fields = ['student', 'subject', 'teacher', 'academic_year', 'date', 'value', 'comment']
        widgets = {
            'date': html_date_input(),
            'comment': forms.TextInput(attrs={'placeholder': 'Комментарий, если нужен'}),
            'value': forms.Select(choices=Grade.GRADE_CHOICES),
        }

    def __init__(
        self,
        *args,
        teacher: Optional[Teacher] = None,
        group: Optional[StudyGroup] = None,
        subject: Optional[Subject] = None,
        students_queryset=None,
        academic_year: Optional[AcademicYear] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.teacher = teacher
        self.group = group
        self.fixed_subject = subject
        self.fixed_academic_year = academic_year

        self.fields['date'].initial = self.fields['date'].initial or date.today()
        self.fields['academic_year'].queryset = AcademicYear.objects.order_by('-starts_on')
        self.fields['student'].queryset = Student.objects.filter(is_active=True).select_related('group', 'instrument').order_by('full_name')
        self.fields['subject'].queryset = Subject.objects.filter(is_active=True).order_by('name')
        self.fields['teacher'].queryset = Teacher.objects.filter(is_active=True).order_by('full_name')

        if academic_year is not None:
            self.fields['academic_year'].initial = academic_year

        if group is not None:
            self.fields['student'].queryset = Student.objects.filter(group=group, is_active=True).order_by('full_name')
            self.fields['subject'].queryset = get_teacher_subjects(teacher, group) if teacher else Subject.objects.filter(
                group_subjects__group=group,
                group_subjects__is_active=True,
                is_active=True,
            ).distinct().order_by('name')

        if students_queryset is not None:
            self.fields['student'].queryset = students_queryset.order_by('full_name')

        if subject is not None:
            self.fields['subject'].initial = subject
            self.fields['subject'].queryset = Subject.objects.filter(pk=subject.pk)

        selected_student = self._selected_student()
        selected_subject = self._selected_subject() or subject

        if selected_student is not None:
            self.fields['subject'].queryset = get_student_allowed_subjects(selected_student)
            if subject is not None:
                self.fields['subject'].queryset = self.fields['subject'].queryset.filter(pk=subject.pk)

        if selected_student is not None and selected_subject is not None:
            self.fields['teacher'].queryset = get_student_subject_teachers(
                selected_student,
                selected_subject,
            )
            if teacher is not None:
                self.fields['teacher'].queryset = self.fields['teacher'].queryset.filter(
                    pk=teacher.pk,
                )

        if self.is_bound and 'teacher' in self.fields:
            teacher_id = self.data.get(self.add_prefix('teacher')) or self.data.get('teacher')
            if teacher_id:
                self.fields['teacher'].queryset = Teacher.objects.filter(pk=teacher_id)

        if teacher is not None:
            self.fields['teacher'].initial = teacher
            self.fields.pop('teacher', None)

        if subject is not None:
            self.fields.pop('subject', None)

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

    def clean_value(self):
        value = str(self.cleaned_data['value']).strip().upper()
        if value not in Grade.ALLOWED_VALUES:
            raise forms.ValidationError('Оценка должна быть 1-5 или Н.')
        return value

    def clean(self):
        cleaned_data = super().clean()

        student = cleaned_data.get('student')
        subject = self.fixed_subject or cleaned_data.get('subject')
        teacher = self.teacher or cleaned_data.get('teacher')

        if self.fixed_subject is not None:
            cleaned_data['subject'] = self.fixed_subject
        if self.teacher is not None:
            cleaned_data['teacher'] = self.teacher
        if self.fixed_academic_year is not None and not cleaned_data.get('academic_year'):
            cleaned_data['academic_year'] = self.fixed_academic_year

        if student and subject:
            if not get_student_allowed_subjects(student).filter(pk=subject.pk).exists():
                raise forms.ValidationError(
                    'Ученик не может получить оценку по предмету, который не назначен его группе '
                    'и не назначен ему индивидуально.'
                )

        if student and subject and teacher:
            group_assignment_exists = GroupSubject.objects.filter(
                group=student.group,
                subject=subject,
                teacher=teacher,
                is_active=True,
            ).exists()

            individual_assignment_exists = StudentSubject.objects.filter(
                student=student,
                subject=subject,
                teacher=teacher,
                is_active=True,
            ).exists()

            if not group_assignment_exists and not individual_assignment_exists:
                self.add_error(
                    'teacher',
                    'Этот преподаватель не назначен выбранному ученику по выбранному предмету.'
                )

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
            'exam_grade': forms.TextInput(attrs={'placeholder': 'Например: 5 или Зачет'}),
            'final_grade': forms.TextInput(attrs={'placeholder': 'Например: 5 или Зачет'}),
        }

    def __init__(self, *args, student: Optional[Student] = None, subject: Optional[Subject] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fixed_student = student
        self.fixed_subject = subject

        self.fields['student'].queryset = Student.objects.filter(is_active=True).order_by('full_name')
        self.fields['subject'].queryset = Subject.objects.filter(is_active=True).order_by('name')
        self.fields['academic_year'].queryset = AcademicYear.objects.order_by('-starts_on')

        if student is not None:
            self.fields['student'].initial = student
            self.fields['student'].queryset = Student.objects.filter(pk=student.pk)
            self.fields['subject'].queryset = get_student_allowed_subjects(student)

        if subject is not None:
            self.fields['subject'].initial = subject
            self.fields['subject'].queryset = self.fields['subject'].queryset.filter(pk=subject.pk)

    def clean(self):
        cleaned_data = super().clean()
        student = self.fixed_student or cleaned_data.get('student')
        subject = self.fixed_subject or cleaned_data.get('subject')

        if self.fixed_student is not None:
            cleaned_data['student'] = self.fixed_student
        if self.fixed_subject is not None:
            cleaned_data['subject'] = self.fixed_subject

        if student and subject:
            if not get_student_allowed_subjects(student).filter(pk=subject.pk).exists():
                raise forms.ValidationError(
                    'Нельзя выставить итог по предмету, который не назначен ученику.'
                )

            allowed_values = subject.get_final_grade_allowed_values()
            for field_name in ('exam_grade', 'final_grade'):
                value = Subject.normalize_final_grade(cleaned_data.get(field_name))
                if value is None:
                    cleaned_data[field_name] = None
                    continue
                if value not in allowed_values:
                    raise forms.ValidationError({
                        field_name: 'Недопустимое значение для типа итоговой аттестации выбранного предмета.'
                    })
                cleaned_data[field_name] = value

        return cleaned_data


# -----------------------------------------------------------------------------
# Заявки на курсы
# -----------------------------------------------------------------------------


class BaseCourseApplicationForm(forms.ModelForm):
    def __init__(self, *args, age_limit: bool = False, include_status: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.age_limit = age_limit

        if 'status' in self.fields and not include_status:
            self.fields.pop('status')

        self.fields['last_name'].widget.attrs.update({
            'autocomplete': 'family-name',
            'placeholder': 'Фамилия',
        })
        self.fields['first_name'].widget.attrs.update({
            'autocomplete': 'given-name',
            'placeholder': 'Имя',
        })
        self.fields['middle_name'].widget.attrs.update({
            'autocomplete': 'additional-name',
            'placeholder': 'Отчество, если есть',
        })
        self.fields['gender'].widget = forms.RadioSelect(choices=CourseApplication.GENDER_CHOICES)
        self.fields['birth_date'].widget = html_date_input()
        self.fields['city_church'].widget.attrs.update({
            'placeholder': 'Например: Тамбов или Воронеж, Отрожка',
        })
        self.fields['instrument'].help_text = (
            'Укажите музыкальный инструмент или партию в оркестре. '
            'Если ранее не играли, укажите инструмент, на котором планируете обучаться.'
        )
        self.fields['instrument'].widget.attrs.update({
            'placeholder': 'Например: Баян, Домра малая II, Фортепиано',
        })
        self.fields['music_education'].widget = forms.Select(choices=CourseApplication.MUSIC_EDUCATION_CHOICES)
        self.fields['student_phone'].widget = forms.TextInput(attrs={
            'type': 'tel',
            'inputmode': 'tel',
            'placeholder': '+7 (999) 123-45-67',
            'autocomplete': 'tel',
        })
        self.fields['parent_contacts'].widget = forms.Textarea(attrs={
            'rows': 4,
            'placeholder': 'Иванов Иван Иванович — +7 (999) 123-45-67\nИванова Мария Петровна — +7 (999) 987-65-43',
        })
        self.fields['comments'].widget = forms.Textarea(attrs={
            'rows': 4,
            'placeholder': 'Дополнительные вопросы или комментарии',
        })
        self.fields['comments'].required = False
        self.fields['parent_contacts'].required = False

        if self.age_limit:
            self.fields['birth_date'].widget.attrs['max'] = minimum_birth_date_for_age(14).isoformat()
            self.fields['birth_date'].widget.attrs['data-age-limit'] = '14'
            self.fields['birth_date'].help_text = 'Регистрация на курсы доступна с 14 лет.'

    class Meta:
        model = CourseApplication
        fields = [
            'last_name',
            'first_name',
            'middle_name',
            'gender',
            'birth_date',
            'city_church',
            'instrument',
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

    def clean_instrument(self):
        return self.cleaned_data['instrument'].strip()

    def clean_student_phone(self):
        return normalize_phone_number(self.cleaned_data['student_phone'])

    def clean_parent_contacts(self):
        return normalize_parent_contacts(self.cleaned_data.get('parent_contacts', ''))

    def clean_birth_date(self):
        birth_date = self.cleaned_data['birth_date']
        if birth_date > date.today():
            raise forms.ValidationError('Дата рождения не может быть в будущем.')
        if self.age_limit and calculate_age(birth_date) < 14:
            raise forms.ValidationError('Регистрация на курсы доступна только с 14 лет.')
        return birth_date

    def clean_comments(self):
        return self.cleaned_data.get('comments', '').strip()


class CourseApplicationPublicForm(BaseCourseApplicationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, age_limit=True, include_status=False, **kwargs)


class CourseApplicationAdminForm(BaseCourseApplicationForm):
    """
    Форма для ручного редактирования заявки вне ModelAdmin.
    В публичной форме status скрыт, а здесь доступен.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, age_limit=False, include_status=True, **kwargs)


# -----------------------------------------------------------------------------
# Настройки регистрации
# -----------------------------------------------------------------------------


class CourseRegistrationSettingsForm(forms.ModelForm):
    class Meta:
        model = CourseRegistrationSettings
        fields = ['telegram_group_url']
        widgets = {
            'telegram_group_url': forms.URLInput(attrs={
                'placeholder': 'https://t.me/your_group_or_invite_link',
            }),
        }

    def clean_telegram_group_url(self):
        value = self.cleaned_data.get('telegram_group_url', '').strip()
        if not value:
            raise forms.ValidationError('Укажите ссылку на Telegram-группу.')
        return value
