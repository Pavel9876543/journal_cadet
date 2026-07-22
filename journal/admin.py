from datetime import date
from urllib.parse import urlencode

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin, UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.contrib.auth.models import Group as AuthGroup, User as AuthUser
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .account_utils import (
    build_username_from_full_name,
    display_name_for_user,
    ensure_temporary_credential_for_user,
    generate_temporary_password,
    split_user_name,
    user_has_temporary_credential,
)
from .assignment_options import (
    active_group_queryset,
    active_student_queryset,
    assignment_teacher_queryset,
    group_subject_queryset,
    is_default_specialty_assignment,
    student_subject_queryset,
)
from .forms import CourseApplicationAdminForm, CourseRegistrationSettingsForm, html_date_input
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
    Instrument,
    PasswordRecoveryContact,
    Student,
    StudentSubject,
    StudyGroup,
    Subject,
    SubjectResult,
    Teacher,
    TeacherSubject,
    TemporaryCredential,
    object_is_in_archived_academic_year,
)
from .registration_utils import calculate_age


admin.site.site_header = 'Электронный журнал музыкальной школы'
admin.site.site_title = 'Электронный журнал'
admin.site.index_title = 'Панель администратора'
admin.site.empty_value_display = '—'


try:
    admin.site.unregister(AuthUser)
except admin.sites.NotRegistered:
    pass

try:
    admin.site.unregister(AuthGroup)
except admin.sites.NotRegistered:
    pass


USERNAME_WITH_SPACES_HELP_TEXT = (
    'Обязательное поле. Не больше 150 символов. Можно использовать буквы, цифры и пробелы.'
)


class JournalAdminDescriptionMixin:
    change_list_template = 'admin/journal/change_list_with_description.html'
    changelist_description = ''

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['changelist_description'] = self.changelist_description
        return super().changelist_view(request, extra_context=extra_context)


class ArchivedAcademicYearAdminMixin:
    def has_change_permission(self, request, obj=None):
        if obj is not None and object_is_in_archived_academic_year(obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and object_is_in_archived_academic_year(obj):
            return False
        return super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        active_ids = []
        archived_count = 0
        for obj in queryset:
            if object_is_in_archived_academic_year(obj):
                archived_count += 1
            else:
                active_ids.append(obj.pk)

        if archived_count:
            self.message_user(
                request,
                f'Архивные записи пропущены и не удалены: {archived_count}.',
                level='ERROR',
            )
        if active_ids:
            super().delete_queryset(request, queryset.filter(pk__in=active_ids))


class ArchivedAcademicYearInlineMixin:
    def parent_is_archived(self, obj) -> bool:
        return obj is not None and object_is_in_archived_academic_year(obj)

    def has_add_permission(self, request, obj=None):
        if self.parent_is_archived(obj):
            return False
        return super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if self.parent_is_archived(obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if self.parent_is_archived(obj):
            return False
        return super().has_delete_permission(request, obj)

    def get_extra(self, request, obj=None, **kwargs):
        if self.parent_is_archived(obj):
            return 0
        return super().get_extra(request, obj, **kwargs)


class ActiveAcademicYearGroupSubjectInlineMixin:
    def get_queryset(self, request):
        return super().get_queryset(request).filter(group__academic_year__is_active=True)


class ActiveAcademicYearStudentSubjectInlineMixin:
    def get_queryset(self, request):
        return super().get_queryset(request).filter(student__group__academic_year__is_active=True)


class SpaceFriendlyUsernameFormMixin:
    username = forms.CharField(
        label='Логин',
        max_length=150,
        help_text=USERNAME_WITH_SPACES_HELP_TEXT,
    )


class SpaceFriendlyUserCreationForm(SpaceFriendlyUsernameFormMixin, UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = AuthUser
        fields = ('username',)


class SpaceFriendlyUserChangeForm(SpaceFriendlyUsernameFormMixin, UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = AuthUser
        fields = '__all__'


@admin.register(AuthUser)
class UserAdmin(JournalAdminDescriptionMixin, BaseUserAdmin):
    changelist_description = (
        'Учетные записи для входа в систему. Здесь видны администраторы, преподаватели '
        'и ученики, а учебные профили открываются по ссылкам в строках таблицы.'
    )
    form = SpaceFriendlyUserChangeForm
    add_form = SpaceFriendlyUserCreationForm
    list_display = (
        'username',
        'last_name',
        'first_name',
        'email',
        'journal_profile_display',
        'is_staff',
        'is_active',
    )
    list_filter = ('is_staff', 'is_superuser', 'is_active', 'groups')
    search_fields = (
        'username',
        'first_name',
        'last_name',
        'email',
        'student_profile__full_name',
        'student_profile__student_phone',
        'teacher_profile__full_name',
        'teacher_profile__phone',
    )
    readonly_fields = (
        *BaseUserAdmin.readonly_fields,
        'student_profile_link',
        'teacher_profile_link',
    )
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Профиль журнала', {
            'fields': ('student_profile_link', 'teacher_profile_link'),
            'classes': ('collapse',),
        }),
    )
    list_per_page = 40

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('student_profile', 'teacher_profile')

    @admin.display(description='Профиль журнала')
    def journal_profile_display(self, obj):
        if hasattr(obj, 'student_profile') and obj.student_profile:
            return format_html('Ученик: {}', admin_change_link(obj.student_profile))
        if hasattr(obj, 'teacher_profile') and obj.teacher_profile:
            return format_html('Преподаватель: {}', admin_change_link(obj.teacher_profile))
        return '—'

    @admin.display(description='Карточка ученика')
    def student_profile_link(self, obj):
        if not obj:
            return '—'
        try:
            student = obj.student_profile
        except Student.DoesNotExist:
            student = None
        return admin_change_link(student)

    @admin.display(description='Карточка преподавателя')
    def teacher_profile_link(self, obj):
        if not obj:
            return '—'
        try:
            teacher = obj.teacher_profile
        except Teacher.DoesNotExist:
            teacher = None
        return admin_change_link(teacher)


@admin.register(AuthGroup)
class AuthGroupAdmin(JournalAdminDescriptionMixin, BaseGroupAdmin):
    changelist_description = (
        'Роли пользователей и наборы прав. Обычно используются роли Администратор, '
        'Преподаватель и Ученик.'
    )
    search_fields = ('name',)
    list_per_page = 40


# -----------------------------------------------------------------------------
# Вспомогательные функции
# -----------------------------------------------------------------------------


def admin_change_link(obj, label=None):
    if not obj:
        return '—'
    url = reverse(f'admin:{obj._meta.app_label}_{obj._meta.model_name}_change', args=[obj.pk])
    return format_html('<a href="{}">{}</a>', url, label or str(obj))


def truncate_text(value, length=80):
    if not value:
        return '—'
    value = str(value)
    if len(value) <= length:
        return value
    return f'{value[:length]}…'


def admin_changelist_url(model_name, params=None):
    url = reverse(f'admin:journal_{model_name}_changelist')
    if params:
        return f'{url}?{urlencode(params)}'
    return url


def journal_url(params=None):
    url = reverse('journal')
    if params:
        return f'{url}?{urlencode(params)}'
    return url


# -----------------------------------------------------------------------------
# Forms для админки
# -----------------------------------------------------------------------------


class GradeAdminForm(forms.ModelForm):
    group = forms.ModelChoiceField(
        label='Группа',
        queryset=StudyGroup.objects.none(),
        required=False,
        empty_label='Выберите группу',
    )

    class Meta:
        model = Grade
        fields = '__all__'
        widgets = {
            'date': html_date_input(),
            'comment': forms.TextInput(attrs={'size': 80}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        instance = self.instance if self.instance and self.instance.pk else None
        student = getattr(instance, 'student', None)
        subject = getattr(instance, 'subject', None)
        teacher = getattr(instance, 'teacher', None)
        academic_year = getattr(instance, 'academic_year', None)

        group_id = self.data.get('group')
        student_id = self.data.get('student') or getattr(instance, 'student_id', None)
        subject_id = self.data.get('subject') or getattr(instance, 'subject_id', None)
        teacher_id = self.data.get('teacher') or getattr(instance, 'teacher_id', None)
        academic_year_id = self.data.get('academic_year') or getattr(instance, 'academic_year_id', None)

        if student_id:
            try:
                student = Student.objects.select_related('group').get(pk=student_id)
            except (Student.DoesNotExist, ValueError, TypeError):
                student = None

        if subject_id:
            try:
                subject = Subject.objects.get(pk=subject_id)
            except (Subject.DoesNotExist, ValueError, TypeError):
                subject = None

        if teacher_id:
            try:
                teacher = Teacher.objects.get(pk=teacher_id)
            except (Teacher.DoesNotExist, ValueError, TypeError):
                teacher = None

        if academic_year_id:
            try:
                academic_year = AcademicYear.objects.get(pk=academic_year_id)
            except (AcademicYear.DoesNotExist, ValueError, TypeError):
                academic_year = None

        group = student.group if student is not None else None
        if group_id:
            try:
                group = StudyGroup.objects.get(pk=group_id)
            except (StudyGroup.DoesNotExist, ValueError, TypeError):
                group = None

        if 'group' in self.fields:
            self.fields['group'].queryset = self._include_submitted_choice(
                get_grade_groups(
                    student=student,
                    subject=subject,
                    teacher=teacher,
                    academic_year=academic_year,
                ),
                StudyGroup,
                group_id,
            )
            self.fields['group'].initial = group
            self.fields['group'].widget.attrs['required'] = True
        if 'student' in self.fields:
            self.fields['student'].queryset = self._include_submitted_choice(
                get_grade_students(
                    group=group,
                    subject=subject,
                    teacher=teacher,
                    academic_year=academic_year,
                ),
                Student,
                student_id,
            )
        if 'subject' in self.fields:
            self.fields['subject'].queryset = self._include_submitted_choice(
                get_grade_subjects(
                    group=group,
                    student=student,
                    teacher=teacher,
                    academic_year=academic_year,
                ),
                Subject,
                subject_id,
            )
        if 'teacher' in self.fields:
            self.fields['teacher'].queryset = self._include_submitted_choice(
                get_grade_teachers(
                    group=group,
                    student=student,
                    subject=subject,
                    academic_year=academic_year,
                ),
                Teacher,
                teacher_id,
            )
        if 'academic_year' in self.fields:
            self.fields['academic_year'].queryset = self._include_submitted_choice(
                AcademicYear.objects.filter(is_active=True).order_by('-starts_on'),
                AcademicYear,
                academic_year_id,
            )
        dependency_url = reverse('grade_options_api')
        for field_name in ('group', 'student', 'subject', 'teacher'):
            if field_name in self.fields:
                self.fields[field_name].widget.attrs['data-grade-options-url'] = dependency_url

    def _include_submitted_choice(self, queryset, model, raw_value):
        if not raw_value:
            return queryset
        try:
            return model.objects.filter(
                Q(pk__in=queryset.values('pk')) | Q(pk=raw_value),
            ).distinct()
        except (TypeError, ValueError):
            return queryset

    def _add_available_error(self, field_name, message):
        self.add_error(field_name if field_name in self.fields else None, message)

    def clean(self):
        cleaned_data = super().clean()
        group = cleaned_data.get('group')
        student = cleaned_data.get('student')
        subject = cleaned_data.get('subject')
        teacher = cleaned_data.get('teacher')
        academic_year = cleaned_data.get('academic_year')

        if group is None and student is not None:
            group = student.group
            cleaned_data['group'] = group

        if academic_year is None and group is not None:
            academic_year = group.academic_year
            cleaned_data['academic_year'] = academic_year

        if group is None:
            self._add_available_error('group', 'Выберите группу или ученика с указанной группой.')

        if group and student and student.group_id != group.pk:
            self._add_available_error('student', 'Ученик не состоит в выбранной группе.')

        if group and academic_year and group.academic_year_id != academic_year.pk:
            self._add_available_error('academic_year', 'Группа относится к другому учебному году.')

        if academic_year and not academic_year.is_active:
            self._add_available_error('academic_year', 'Архивный учебный год доступен только для просмотра.')

        grade_date = cleaned_data.get('date')
        if grade_date and academic_year and not (academic_year.starts_on <= grade_date <= academic_year.ends_on):
            self._add_available_error(
                'date',
                (
                    'Дата оценки должна попадать в период выбранного учебного года: '
                    f'{academic_year.starts_on:%d.%m.%Y} - {academic_year.ends_on:%d.%m.%Y}.'
                ),
            )

        if student and subject and cleaned_data.get('date'):
            duplicate_qs = Grade.objects.filter(
                student=student,
                subject=subject,
                date=cleaned_data['date'],
            )
            if self.instance.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)
            if duplicate_qs.exists():
                self._add_available_error(
                    'date',
                    'У этого ученика уже есть оценка по выбранному предмету за эту дату.',
                )

        if group and student and subject and teacher:
            teacher_is_allowed = get_grade_teachers(
                group=group,
                student=student,
                subject=subject,
                academic_year=academic_year,
            ).filter(pk=teacher.pk).exists()
            if not teacher_is_allowed:
                self._add_available_error(
                    'teacher',
                    'Преподаватель не ведёт выбранный предмет у этого ученика.',
                )

        return cleaned_data


class SubjectResultAdminForm(forms.ModelForm):
    class Meta:
        model = SubjectResult
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        instance = self.instance if self.instance and self.instance.pk else None
        student_id = self._raw_value('student') or getattr(instance, 'student_id', None)
        subject_id = self._raw_value('subject') or getattr(instance, 'subject_id', None)
        academic_year_id = self._raw_value('academic_year') or getattr(instance, 'academic_year_id', None)

        student = self._selected_object(Student.objects.select_related('group'), student_id)
        subject = self._selected_object(Subject.objects.all(), subject_id)
        academic_year = self._selected_object(AcademicYear.objects.all(), academic_year_id)

        if 'student' in self.fields:
            self.fields['student'].queryset = self._include_selected_choice(
                get_grade_students(
                    subject=subject,
                    academic_year=academic_year,
                ),
                Student,
                student_id,
            )
            self.fields['student'].widget.attrs['data-grade-options-url'] = reverse('grade_options_api')

        if 'subject' in self.fields:
            self.fields['subject'].queryset = self._include_selected_choice(
                get_grade_subjects(
                    student=student,
                    academic_year=academic_year,
                ),
                Subject,
                subject_id,
            )
            if 'student' not in self.fields:
                self.fields['subject'].widget.attrs['data-grade-options-url'] = reverse('grade_options_api')

        if 'academic_year' in self.fields:
            self.fields['academic_year'].queryset = self._include_selected_choice(
                AcademicYear.objects.filter(is_active=True).order_by('-starts_on'),
                AcademicYear,
                academic_year_id,
            )

    def _raw_value(self, field_name):
        if not self.is_bound:
            return None
        return self.data.get(self.add_prefix(field_name)) or self.data.get(field_name)

    def _selected_object(self, queryset, raw_value):
        if not raw_value:
            return None
        try:
            return queryset.filter(pk=raw_value).first()
        except (TypeError, ValueError):
            return None

    def _include_selected_choice(self, queryset, model, raw_value):
        if not raw_value:
            return queryset
        try:
            return model.objects.filter(
                Q(pk__in=queryset.values('pk')) | Q(pk=raw_value),
            ).distinct()
        except (TypeError, ValueError):
            return queryset

    def clean(self):
        cleaned_data = super().clean()
        if self._is_unchanged_existing_inline():
            return cleaned_data

        student = cleaned_data.get('student')
        subject = cleaned_data.get('subject')
        academic_year = cleaned_data.get('academic_year')

        if academic_year and not academic_year.is_active:
            self.add_error('academic_year', 'Архивный учебный год доступен только для просмотра.')
        if student and student.group_id and academic_year and student.group.academic_year_id != academic_year.pk:
            self.add_error('academic_year', 'Учебный год итога должен совпадать с учебным годом группы ученика.')

        if student and subject:
            subject_is_allowed = get_grade_subjects(
                student=student,
                academic_year=academic_year,
            ).filter(pk=subject.pk).exists()
            if not subject_is_allowed:
                self.add_error('subject', 'Этот предмет не назначен выбранному ученику.')

        if student and subject and academic_year:
            duplicate_qs = SubjectResult.objects.filter(
                student=student,
                subject=subject,
                academic_year=academic_year,
            )
            if self.instance.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)
            if duplicate_qs.exists():
                self.add_error('subject', 'У ученика уже есть итог по этому предмету за выбранный учебный год.')

        return cleaned_data

    def _is_unchanged_existing_inline(self):
        return (
            self.is_bound
            and self.prefix
            and self.prefix.startswith('subject_results-')
            and self.instance
            and self.instance.pk
            and not self.has_changed()
        )

    def _post_clean(self):
        if self._is_unchanged_existing_inline():
            return
        super()._post_clean()


class TeacherAdminForm(forms.ModelForm):
    class Meta:
        model = Teacher
        fields = '__all__'
        widgets = {
            'birth_date': html_date_input(),
            'comments': forms.Textarea(attrs={'rows': 4}),
        }


class StudentAdminForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = '__all__'
        widgets = {
            'birth_date': html_date_input(),
            'parent_contacts': forms.Textarea(attrs={'rows': 4}),
            'comments': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'group' not in self.fields:
            return

        raw_group_id = None
        if self.is_bound:
            raw_group_id = self.data.get(self.add_prefix('group')) or self.data.get('group')
        elif self.instance and self.instance.pk:
            raw_group_id = self.instance.group_id

        group_queryset = active_group_queryset()
        if raw_group_id:
            try:
                group_queryset = StudyGroup.objects.filter(
                    Q(pk__in=group_queryset.values('pk')) | Q(pk=raw_group_id),
                ).select_related('academic_year').distinct().order_by('academic_year__name', 'name')
            except (TypeError, ValueError):
                pass

        self.fields['group'].queryset = group_queryset


class GroupSubjectAdminForm(forms.ModelForm):
    class Meta:
        model = GroupSubject
        fields = '__all__'

    class Media:
        js = ('journal/admin_assignment_dependencies.js',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        subject_id = self._raw_value('subject')
        subject = self._selected_object(Subject.objects.all(), subject_id)

        if 'group' in self.fields:
            self.fields['group'].queryset = self._include_selected_choice(
                active_group_queryset(),
                StudyGroup,
                'group',
            )
        if 'subject' in self.fields:
            self.fields['subject'].queryset = self._include_selected_choice(
                group_subject_queryset(),
                Subject,
                'subject',
            )
        if 'teacher' in self.fields:
            self.fields['teacher'].queryset = self._include_selected_choice(
                assignment_teacher_queryset(subject),
                Teacher,
                'teacher',
            )
        self._attach_dependency_attrs('group_subject')

    def _raw_value(self, field_name):
        if self.is_bound:
            return self.data.get(self.add_prefix(field_name)) or self.data.get(field_name)
        return getattr(self.instance, f'{field_name}_id', None)

    def _selected_object(self, queryset, raw_value):
        if not raw_value:
            return None
        try:
            return queryset.filter(pk=raw_value).first()
        except (TypeError, ValueError):
            return None

    def _include_selected_choice(self, queryset, model, field_name):
        raw_value = self._raw_value(field_name)
        if not raw_value:
            return queryset
        try:
            return model.objects.filter(Q(pk__in=queryset.values('pk')) | Q(pk=raw_value)).distinct()
        except (TypeError, ValueError):
            return queryset

    def _attach_dependency_attrs(self, assignment_type):
        url = reverse('assignment_options_api')
        for field_name in ('group', 'subject', 'teacher'):
            if field_name in self.fields:
                self.fields[field_name].widget.attrs.update({
                    'data-assignment-options-url': url,
                    'data-assignment-type': assignment_type,
                })

    def clean(self):
        cleaned_data = super().clean()
        group = cleaned_data.get('group')
        subject = cleaned_data.get('subject')
        teacher = cleaned_data.get('teacher')

        if subject and subject.is_specialty:
            self.add_error('subject', 'Индивидуальный предмет нельзя назначить группе.')

        if group and not group.academic_year.is_active:
            self.add_error('group', 'Архивный учебный год доступен только для просмотра.')

        if teacher and not teacher.is_active:
            self.add_error('teacher', 'Выберите активного преподавателя.')

        if group and subject:
            duplicate_qs = GroupSubject.objects.filter(group=group, subject=subject)
            if self.instance.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)
            if duplicate_qs.exists():
                self.add_error('subject', 'В этой группе уже есть такой предмет.')

        return cleaned_data


class StudentSubjectAdminForm(forms.ModelForm):
    class Meta:
        model = StudentSubject
        fields = '__all__'

    class Media:
        js = ('journal/admin_assignment_dependencies.js',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        subject_id = self._raw_value('subject')
        subject = self._selected_object(Subject.objects.all(), subject_id)

        if 'student' in self.fields:
            self.fields['student'].queryset = self._include_selected_choice(
                active_student_queryset(),
                Student,
                'student',
            )
        if 'subject' in self.fields:
            self.fields['subject'].queryset = self._include_selected_choice(
                student_subject_queryset(),
                Subject,
                'subject',
            )
        if 'teacher' in self.fields:
            self.fields['teacher'].queryset = self._include_selected_choice(
                assignment_teacher_queryset(subject),
                Teacher,
                'teacher',
            )
        self._attach_dependency_attrs('student_subject')

    def _raw_value(self, field_name):
        if self.is_bound:
            return self.data.get(self.add_prefix(field_name)) or self.data.get(field_name)
        return getattr(self.instance, f'{field_name}_id', None)

    def _selected_object(self, queryset, raw_value):
        if not raw_value:
            return None
        try:
            return queryset.filter(pk=raw_value).first()
        except (TypeError, ValueError):
            return None

    def _include_selected_choice(self, queryset, model, field_name):
        raw_value = self._raw_value(field_name)
        if not raw_value:
            return queryset
        try:
            return model.objects.filter(Q(pk__in=queryset.values('pk')) | Q(pk=raw_value)).distinct()
        except (TypeError, ValueError):
            return queryset

    def _attach_dependency_attrs(self, assignment_type):
        url = reverse('assignment_options_api')
        for field_name in ('student', 'subject', 'teacher'):
            if field_name in self.fields:
                self.fields[field_name].widget.attrs.update({
                    'data-assignment-options-url': url,
                    'data-assignment-type': assignment_type,
                })
        if 'is_specialty' in self.fields:
            self.fields['is_specialty'].widget.attrs['data-assignment-specialty-target'] = '1'

    def clean(self):
        cleaned_data = super().clean()
        student = cleaned_data.get('student')
        subject = cleaned_data.get('subject')
        teacher = cleaned_data.get('teacher')
        is_active = cleaned_data.get('is_active')

        if subject:
            cleaned_data['is_specialty'] = is_default_specialty_assignment(subject)
            if not subject.is_specialty:
                self.add_error('subject', 'Групповой предмет нельзя назначить индивидуальному ученику.')

        if student and student.group_id and not student.group.academic_year.is_active:
            self.add_error('student', 'Архивный учебный год доступен только для просмотра.')

        if teacher and not teacher.is_active:
            self.add_error('teacher', 'Выберите активного преподавателя.')

        if student and subject:
            duplicate_qs = StudentSubject.objects.filter(student=student, subject=subject)
            if self.instance.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)
            if duplicate_qs.exists():
                self.add_error('subject', 'У ученика уже есть такой индивидуальный предмет.')

        if student and cleaned_data.get('is_specialty') and is_active:
            specialty_qs = StudentSubject.objects.filter(
                student=student,
                is_specialty=True,
                is_active=True,
            )
            if self.instance.pk:
                specialty_qs = specialty_qs.exclude(pk=self.instance.pk)
            if specialty_qs.exists():
                self.add_error('is_specialty', 'У ученика уже есть активная специальность.')

        return cleaned_data


class StudentChoiceWithCityWidget(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        student = getattr(value, 'instance', None)
        if student is not None:
            option['attrs']['data-city-church'] = student.city_church or ''
        return option


class GroupStudentInlineForm(forms.ModelForm):
    student = forms.ModelChoiceField(
        label='ФИО ученика',
        queryset=Student.objects.none(),
        required=False,
        empty_label='Выберите ученика',
        widget=StudentChoiceWithCityWidget(attrs={'data-student-city-source': '1'}),
    )

    class Meta:
        model = Student
        fields = ('city_church',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        selected_student = self.instance if self.instance and self.instance.pk else None
        student_queryset = active_student_queryset()
        if selected_student is not None:
            student_queryset = Student.objects.select_related('group', 'group__academic_year').filter(
                Q(pk__in=student_queryset.values('pk')) | Q(pk=selected_student.pk),
            )

        self.fields['student'].queryset = student_queryset.order_by('full_name', 'pk')
        self.fields['student'].initial = selected_student
        self.fields['city_church'].disabled = True
        self.fields['city_church'].required = False
        self.fields['city_church'].initial = selected_student.city_church if selected_student is not None else ''
        self.fields['city_church'].widget.attrs['data-student-city-target'] = '1'


class UniqueInlineFormSetMixin:
    unique_checks = ()
    unique_error_message = 'Такая запись уже существует.'
    unique_formset_error_message = 'Исправьте ошибки в строках таблицы.'

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        has_errors = False
        for check in self.unique_checks:
            if self._validate_unique_check(check):
                has_errors = True

        if has_errors:
            raise forms.ValidationError(self.unique_formset_error_message)

    def _validate_unique_check(self, check):
        fields = check['fields']
        condition = check.get('condition')
        extra_filters = check.get('filters', {})
        message = check.get('message', self.unique_error_message)
        seen = {}
        has_errors = False

        for form in self.forms:
            if not hasattr(form, 'cleaned_data') or not form.cleaned_data:
                continue
            if form.cleaned_data.get('DELETE'):
                continue
            if condition is not None and not condition(form.cleaned_data):
                continue

            key, filters = self._unique_key_and_filters(form, fields)
            if key is None:
                continue

            duplicate_form = seen.get(key)
            if duplicate_form is not None:
                duplicate_form.add_error(None, message)
                form.add_error(None, message)
                has_errors = True
            else:
                seen[key] = form

            queryset = self.model.objects.filter(**filters, **extra_filters)
            if form.instance.pk:
                queryset = queryset.exclude(pk=form.instance.pk)
            if queryset.exists():
                form.add_error(None, message)
                has_errors = True

        return has_errors

    def _unique_key_and_filters(self, form, fields):
        key = []
        filters = {}
        for field_name in fields:
            value = self._unique_field_value(form, field_name)
            if value in (None, ''):
                return None, None

            model_field = self.model._meta.get_field(field_name)
            if getattr(model_field, 'remote_field', None):
                value_id = value.pk if hasattr(value, 'pk') else value
                key.append((field_name, value_id))
                filters[f'{field_name}_id'] = value_id
            else:
                key.append((field_name, value))
                filters[field_name] = value

        return tuple(key), filters

    def _unique_field_value(self, form, field_name):
        if field_name in form.cleaned_data:
            return form.cleaned_data[field_name]
        if getattr(self, 'fk', None) is not None and field_name == self.fk.name:
            return self.instance
        return getattr(form.instance, field_name, None)


def fallback_group_for_detached_student(current_group_id=None):
    groups = (
        StudyGroup.objects
        .filter(name=CourseApplication.STUDENT_COURSE_GROUP_NAME)
        .select_related('academic_year')
    )
    if current_group_id:
        groups = groups.exclude(pk=current_group_id)

    return (
        groups.filter(is_active=True, academic_year__is_active=True)
        .order_by('-academic_year__starts_on', 'name')
        .first()
    )


class StudentInlineFormSet(forms.models.BaseInlineFormSet):
    def clean(self):
        super().clean()

        selected_student_ids = set()
        has_errors = False
        for form in self.forms:
            if not hasattr(form, 'cleaned_data') or not form.cleaned_data:
                continue
            if form.cleaned_data.get('DELETE'):
                continue

            selected_student = form.cleaned_data.get('student')
            if selected_student is None:
                if form.has_changed():
                    form.add_error('student', 'Выберите ученика из списка.')
                    has_errors = True
                continue

            if selected_student.pk in selected_student_ids:
                form.add_error('student', 'Этот ученик уже выбран в таблице.')
                has_errors = True
            selected_student_ids.add(selected_student.pk)

        if has_errors:
            raise forms.ValidationError('Исправьте ошибки в строках таблицы.')

    def delete_existing(self, obj, commit=True):
        target_group = fallback_group_for_detached_student(
            current_group_id=getattr(self.instance, 'pk', None),
        )
        obj.group = target_group
        if commit:
            obj.save(update_fields=['group'])

    def save_existing(self, form, obj, commit=True):
        selected_student = form.cleaned_data.get('student') or obj

        if selected_student.pk != obj.pk:
            self.delete_existing(obj, commit=commit)
            selected_student.group = self.instance
            if commit:
                selected_student.save(update_fields=['group'])
            return selected_student

        obj.group = self.instance
        if commit:
            obj.save(update_fields=['group'])
        return obj

    def save_new(self, form, commit=True):
        selected_student = form.cleaned_data.get('student')
        if selected_student is None:
            return super().save_new(form, commit=commit)

        selected_student.group = self.instance
        if commit:
            selected_student.save(update_fields=['group'])
        return selected_student


class GroupSubjectInlineFormSet(UniqueInlineFormSetMixin, forms.models.BaseInlineFormSet):
    unique_checks = (
        {
            'fields': ('group', 'subject'),
            'message': 'В этой группе уже есть такой предмет.',
        },
    )


class StudentSubjectInlineFormSet(UniqueInlineFormSetMixin, forms.models.BaseInlineFormSet):
    unique_checks = (
        {
            'fields': ('student', 'subject'),
            'message': 'У ученика уже есть такой индивидуальный предмет.',
        },
        {
            'fields': ('student',),
            'condition': lambda cleaned_data: (
                cleaned_data.get('is_specialty') and cleaned_data.get('is_active')
            ),
            'filters': {'is_specialty': True, 'is_active': True},
            'message': 'У ученика уже есть активная специальность.',
        },
    )


class SubjectResultInlineFormSet(UniqueInlineFormSetMixin, forms.models.BaseInlineFormSet):
    unique_checks = (
        {
            'fields': ('student', 'subject', 'academic_year'),
            'message': 'Итог по этому предмету и учебному году уже есть у ученика.',
        },
    )


# -----------------------------------------------------------------------------
# Inline-классы
# -----------------------------------------------------------------------------


class GroupSubjectInline(ArchivedAcademicYearInlineMixin, admin.TabularInline):
    model = GroupSubject
    form = GroupSubjectAdminForm
    formset = GroupSubjectInlineFormSet
    extra = 0
    fields = ('subject', 'teacher', 'sort_order', 'is_active')
    show_change_link = True
    verbose_name = 'Предмет группы'
    verbose_name_plural = 'Предметы группы'


class GroupSubjectForTeacherInline(ActiveAcademicYearGroupSubjectInlineMixin, admin.TabularInline):
    model = GroupSubject
    form = GroupSubjectAdminForm
    formset = GroupSubjectInlineFormSet
    extra = 1
    fields = ('group', 'subject', 'sort_order', 'is_active')
    show_change_link = True
    verbose_name = 'Групповой предмет'
    verbose_name_plural = 'Групповые предметы преподавателя'


class GroupSubjectForSubjectInline(ActiveAcademicYearGroupSubjectInlineMixin, admin.TabularInline):
    model = GroupSubject
    form = GroupSubjectAdminForm
    formset = GroupSubjectInlineFormSet
    extra = 0
    fields = ('group', 'teacher', 'sort_order', 'is_active')
    show_change_link = True
    classes = ('collapse',)
    verbose_name = 'Групповой предмет'
    verbose_name_plural = 'Группы, где есть этот предмет'


class StudentSubjectInline(ArchivedAcademicYearInlineMixin, admin.TabularInline):
    model = StudentSubject
    form = StudentSubjectAdminForm
    formset = StudentSubjectInlineFormSet
    extra = 0
    fields = ('subject', 'teacher', 'is_specialty', 'is_active')
    show_change_link = True
    verbose_name = 'Индивидуальный предмет'
    verbose_name_plural = 'Индивидуальные предметы ученика'


class StudentSubjectForTeacherInline(ActiveAcademicYearStudentSubjectInlineMixin, admin.TabularInline):
    model = StudentSubject
    form = StudentSubjectAdminForm
    formset = StudentSubjectInlineFormSet
    extra = 1
    fields = ('student', 'subject', 'is_specialty', 'is_active')
    show_change_link = True
    verbose_name = 'Индивидуальный ученик'
    verbose_name_plural = 'Индивидуальные ученики преподавателя'


class StudentSubjectForSubjectInline(ActiveAcademicYearStudentSubjectInlineMixin, admin.TabularInline):
    model = StudentSubject
    form = StudentSubjectAdminForm
    formset = StudentSubjectInlineFormSet
    extra = 0
    fields = ('student', 'teacher', 'is_specialty', 'is_active')
    show_change_link = True
    classes = ('collapse',)
    verbose_name = 'Индивидуальный предмет ученика'
    verbose_name_plural = 'Индивидуальные ученики по этому предмету'


class StudentInline(ArchivedAcademicYearInlineMixin, admin.TabularInline):
    model = Student
    form = GroupStudentInlineForm
    formset = StudentInlineFormSet
    extra = 1
    fields = ('student', 'city_church')
    show_change_link = True
    verbose_name = 'Ученик'
    verbose_name_plural = 'Ученики группы'

    class Media:
        js = ('journal/group_student_inline.js',)


class GradeInline(ArchivedAcademicYearInlineMixin, admin.TabularInline):
    model = Grade
    form = GradeAdminForm
    extra = 0
    autocomplete_fields = ('academic_year',)
    fields = ('date', 'subject', 'teacher', 'value', 'academic_year', 'comment')
    ordering = ('-date',)
    show_change_link = True
    classes = ('collapse',)

    class Media:
        js = ('journal/grade_dependencies.js',)


class SubjectResultInline(ArchivedAcademicYearInlineMixin, admin.TabularInline):
    model = SubjectResult
    form = SubjectResultAdminForm
    formset = SubjectResultInlineFormSet
    extra = 0
    fields = ('academic_year', 'subject', 'exam_grade', 'final_grade')
    show_change_link = True
    verbose_name = 'Итог'
    verbose_name_plural = 'Итоги по предметам'

    class Media:
        js = ('journal/grade_dependencies.js',)

    def get_formset(self, request, obj=None, **kwargs):
        parent_student = obj
        base_form = self.form

        class InlineSubjectResultAdminForm(base_form):
            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, **form_kwargs)
                if parent_student is None or 'subject' not in self.fields:
                    return

                subject_id = self._raw_value('subject') or getattr(self.instance, 'subject_id', None)
                academic_year_id = self._raw_value('academic_year') or getattr(
                    self.instance,
                    'academic_year_id',
                    None,
                )
                academic_year = self._selected_object(AcademicYear.objects.all(), academic_year_id)
                self.fields['subject'].queryset = self._include_selected_choice(
                    get_grade_subjects(
                        student=parent_student,
                        academic_year=academic_year,
                    ),
                    Subject,
                    subject_id,
                )
                self.fields['subject'].widget.attrs.update({
                    'data-fixed-student': str(parent_student.pk),
                    'data-grade-options-url': reverse('grade_options_api'),
                })

        kwargs['form'] = InlineSubjectResultAdminForm
        return super().get_formset(request, obj, **kwargs)


# -----------------------------------------------------------------------------
# Справочники
# -----------------------------------------------------------------------------


@admin.register(AcademicYear)
class AcademicYearAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Учебные годы задают периоды обучения. Активный учебный год используется по умолчанию '
        'для групп, заявок и дат оценок.'
    )
    list_display = ('name', 'starts_on', 'ends_on', 'is_active', 'groups_count')
    list_filter = ('is_active',)
    search_fields = ('name',)
    ordering = ('-starts_on',)
    list_per_page = 30
    fieldsets = (
        ('Учебный год', {
            'fields': ('name', 'starts_on', 'ends_on', 'is_active'),
            'description': 'Активным может быть только один учебный год.',
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(_groups_count=Count('study_groups', distinct=True))

    @admin.display(description='Групп')
    def groups_count(self, obj):
        return obj._groups_count


@admin.register(Instrument)
class InstrumentAdmin(JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Справочник инструментов и партий. Значение выбирается в карточке ученика '
        'и используется для поиска и отчетов.'
    )
    list_display = ('name', 'students_count')
    search_fields = ('name',)
    ordering = ('name',)
    list_per_page = 50

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(_students_count=Count('students', distinct=True))

    @admin.display(description='Учеников')
    def students_count(self, obj):
        return obj._students_count


@admin.register(Subject)
class SubjectAdmin(JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Справочник предметов. Поле Индивидуальный предмет определяет, куда можно назначать предмет: '
        'в группу или конкретному ученику.'
    )
    list_display = (
        'name',
        'final_grade_type',
        'is_specialty',
        'is_active',
        'groups_count',
        'teachers_count',
        'individual_students_count',
    )
    list_filter = ('final_grade_type', 'is_specialty', 'is_active')
    search_fields = (
        'name',
        'group_subjects__group__name',
        'group_subjects__teacher__full_name',
        'individual_students__student__full_name',
        'individual_students__teacher__full_name',
    )
    inlines = (
        GroupSubjectForSubjectInline,
        StudentSubjectForSubjectInline,
    )
    ordering = ('name',)
    list_per_page = 50
    fieldsets = (
        ('Предмет', {
            'fields': ('name', 'final_grade_type', 'is_specialty', 'is_active'),
            'description': (
                'Групповые предметы назначаются группе. Индивидуальные предметы '
                'назначаются конкретному ученику.'
            ),
        }),
    )

    def get_inlines(self, request, obj=None):
        if obj is None:
            return ()
        if obj.is_specialty:
            return (StudentSubjectForSubjectInline,)
        return (GroupSubjectForSubjectInline,)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _groups_count=Count(
                'group_subjects__group',
                filter=Q(group_subjects__is_active=True),
                distinct=True,
            ),
            _teachers_count=Count(
                'group_subjects__teacher',
                filter=Q(group_subjects__is_active=True),
                distinct=True,
            ),
            _individual_students_count=Count(
                'individual_students__student',
                filter=Q(individual_students__is_active=True),
                distinct=True,
            ),
        )

    @admin.display(description='Групп')
    def groups_count(self, obj):
        return obj._groups_count

    @admin.display(description='Преподавателей')
    def teachers_count(self, obj):
        return obj._teachers_count

    @admin.display(description='Индивидуальных учеников')
    def individual_students_count(self, obj):
        return obj._individual_students_count

    def get_search_results(self, request, queryset, search_term):
        queryset, use_distinct = super().get_search_results(request, queryset, search_term)
        if request.GET.get('field_name') == 'subject':
            related_model_name = request.GET.get('model_name')
            if related_model_name == 'groupsubject':
                queryset = queryset.filter(is_specialty=False)
            elif related_model_name == 'studentsubject':
                queryset = queryset.filter(is_specialty=True)
        return queryset, use_distinct


# -----------------------------------------------------------------------------
# Основные учебные сущности
# -----------------------------------------------------------------------------


@admin.register(StudyGroup)
class StudyGroupAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Группы объединяют учеников одного учебного года. В карточке группы можно назначить '
        'групповые предметы и перевести учеников из других групп.'
    )
    list_display = (
        'name',
        'academic_year',
        'is_active',
        'students_count_display',
        'subjects_display_short',
        'teachers_display_short',
        'journal_link',
    )
    list_filter = ('academic_year', 'is_active')
    search_fields = (
        'name',
        'academic_year__name',
        'students__full_name',
        'group_subjects__subject__name',
        'group_subjects__teacher__full_name',
    )
    autocomplete_fields = ('academic_year',)
    inlines = (GroupSubjectInline, StudentInline)
    ordering = ('academic_year__name', 'name')
    list_select_related = ('academic_year',)
    list_per_page = 30
    fieldsets = (
        ('Группа', {
            'fields': ('name', 'academic_year', 'is_active'),
            'description': (
                'Здесь настраивается состав предметов группы. Учеников удобнее '
                'добавлять и искать в отдельном разделе «Ученики».'
            ),
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _students_count=Count('students', filter=Q(students__is_active=True), distinct=True),
        ).prefetch_related(
            'group_subjects__subject',
            'group_subjects__teacher',
        )

    @admin.display(description='Учеников')
    def students_count_display(self, obj):
        return format_html(
            '<a href="{}">{}</a>',
            admin_changelist_url('student', {'group__id__exact': obj.pk}),
            obj._students_count,
        )

    @admin.display(description='Предметы')
    def subjects_display_short(self, obj):
        subjects = [
            item.subject.name
            for item in obj.group_subjects.all()
            if item.is_active and item.subject_id
        ]
        return truncate_text(', '.join(subjects))

    @admin.display(description='Преподаватели')
    def teachers_display_short(self, obj):
        pairs = [
            f'{item.subject.name}: {item.teacher.full_name}'
            for item in obj.group_subjects.all()
            if item.is_active and item.subject_id and item.teacher_id
        ]
        return truncate_text(', '.join(pairs), length=120)

    @admin.display(description='Журнал')
    def journal_link(self, obj):
        return format_html('<a href="{}">Открыть</a>', journal_url({'group': obj.pk}))


@admin.register(Teacher)
class TeacherAdmin(JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Карточки преподавателей и их учетные записи. Назначения на групповые и индивидуальные '
        'предметы редактируются во вкладках карточки.'
    )
    form = TeacherAdminForm
    list_display = (
        'full_name',
        'phone',
        'email',
        'age_display',
        'user_link',
        'is_active',
        'group_subjects_count',
        'individual_students_count_display',
        'group_subjects_short',
    )
    list_filter = (
        'is_active',
        'group_subjects__subject',
        'group_subjects__group',
        'individual_subjects__subject',
    )
    search_fields = (
        'full_name',
        'phone',
        'email',
        'comments',
        'user__username',
        'user__first_name',
        'user__last_name',
        'user__email',
        'group_subjects__group__name',
        'group_subjects__subject__name',
        'individual_subjects__student__full_name',
    )
    autocomplete_fields = ('user',)
    inlines = (GroupSubjectForTeacherInline, StudentSubjectForTeacherInline)
    ordering = ('full_name',)
    list_select_related = ('user',)
    list_per_page = 30
    readonly_fields = ('age_display',)
    fieldsets = (
        ('Преподаватель', {
            'fields': ('full_name', 'birth_date', 'age_display', 'is_active'),
            'description': (
                'Групповые предметы назначаются в карточке группы. '
                'Индивидуальные предметы назначаются в карточке ученика.'
            ),
        }),
        ('Контакты', {
            'fields': ('phone', 'email', 'comments'),
        }),
        ('Аккаунт', {
            'fields': ('user',),
            'classes': ('collapse',),
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _group_subjects_count=Count(
                'group_subjects',
                filter=Q(group_subjects__is_active=True),
                distinct=True,
            ),
            _individual_students_count=Count(
                'individual_subjects__student',
                filter=Q(individual_subjects__is_active=True),
                distinct=True,
            ),
        ).prefetch_related('group_subjects__group', 'group_subjects__subject')

    def save_model(self, request, obj, form, change):
        temporary_password = None
        if not change and obj.user_id is None:
            username = build_username_from_full_name(
                obj.full_name,
                existing_usernames=set(AuthUser.objects.values_list('username', flat=True)),
            )
            temporary_password = generate_temporary_password()
            first_name, last_name = split_user_name(obj.full_name)
            obj.user = AuthUser.objects.create_user(
                username=username,
                password=temporary_password,
                first_name=first_name,
                last_name=last_name,
                email=obj.email,
            )
        elif not change and obj.user_id and not user_has_temporary_credential(obj.user):
            temporary_password = generate_temporary_password()
            obj.user.set_password(temporary_password)
            obj.user.save(update_fields=['password'])

        if obj.user_id:
            teacher_group, _created = AuthGroup.objects.get_or_create(name='Преподаватель')
            obj.user.groups.add(teacher_group)

        super().save_model(request, obj, form, change)

        if obj.user_id:
            ensure_temporary_credential_for_user(
                obj.user,
                password=temporary_password,
            )

    @admin.display(description='Пользователь')
    def user_link(self, obj):
        return admin_change_link(obj.user)

    @admin.display(description='Возраст')
    def age_display(self, obj):
        return obj.age if obj and obj.age is not None else '—'

    @admin.display(description='Групповых предметов')
    def group_subjects_count(self, obj):
        return obj._group_subjects_count

    @admin.display(description='Индивидуальных учеников')
    def individual_students_count_display(self, obj):
        return obj._individual_students_count

    @admin.display(description='Группы и предметы')
    def group_subjects_short(self, obj):
        items = [
            f'{item.group.name}: {item.subject.name}'
            for item in obj.group_subjects.all()
            if item.is_active and item.group_id and item.subject_id
        ]
        return truncate_text(', '.join(items), length=120)


@admin.register(Student)
class StudentAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Карточки учеников: группа, инструмент, контакты, индивидуальные предметы и итоги. '
        'Обычные оценки удобнее вносить через журнал.'
    )
    form = StudentAdminForm
    list_display = (
        'full_name',
        'group',
        'instrument',
        'age_display',
        'student_phone',
        'city_church',
        'specialty_teacher_display',
        'specialty_subject_display',
        'user_link',
        'is_active',
    )
    list_filter = (
        'is_active',
        'group',
        'group__academic_year',
        'instrument',
        'individual_subjects__teacher',
        'individual_subjects__subject',
    )
    search_fields = (
        'full_name',
        'student_phone',
        'parent_contacts',
        'city_church',
        'comments',
        'user__username',
        'user__first_name',
        'user__last_name',
        'user__email',
        'group__name',
        'instrument__name',
        'individual_subjects__teacher__full_name',
        'individual_subjects__subject__name',
    )
    autocomplete_fields = ('user', 'group', 'instrument')
    inlines = (StudentSubjectInline, SubjectResultInline)
    ordering = ('full_name',)
    list_select_related = ('user', 'group', 'group__academic_year', 'instrument')
    list_per_page = 40
    readonly_fields = ('age_display', 'course_application_link')
    fieldsets = (
        ('Ученик', {
            'fields': (
                'full_name',
                'gender',
                'birth_date',
                'age_display',
                'group',
                'instrument',
                'is_active',
            ),
            'description': (
                'В этой карточке хранится состав обучения ученика. '
                'Оценки редактируются в журнале или в отдельном разделе «Оценки».'
            ),
        }),
        ('Контакты и анкета', {
            'fields': (
                'student_phone',
                'parent_contacts',
                'city_church',
                'music_education',
                'comments',
            ),
        }),
        ('Аккаунт', {
            'fields': ('user', 'course_application_link'),
            'classes': ('collapse',),
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.prefetch_related(
            Prefetch(
                'individual_subjects',
                queryset=(
                    StudentSubject.objects
                    .filter(is_specialty=True, is_active=True)
                    .select_related('subject', 'teacher')
                    .order_by('subject__name')
                ),
                to_attr='active_specialty_assignments',
            ),
        )

    @admin.display(description='Пользователь')
    def user_link(self, obj):
        return admin_change_link(obj.user)

    @admin.display(description='Возраст')
    def age_display(self, obj):
        return obj.age if obj and obj.age is not None else '—'

    @admin.display(description='Заявка на курсы')
    def course_application_link(self, obj):
        if not obj:
            return '—'
        try:
            application = obj.course_application
        except CourseApplication.DoesNotExist:
            application = None
        return admin_change_link(application)

    @admin.display(description='Преподаватель по специальности')
    def specialty_teacher_display(self, obj):
        teacher = obj.specialty_teacher
        return teacher or '—'

    @admin.display(description='Предмет специальности')
    def specialty_subject_display(self, obj):
        subject = obj.specialty_subject
        return subject or '—'


@admin.register(TeacherSubject)
class TeacherSubjectAdmin(JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Квалификации показывают, какие предметы преподаватель может вести. '
        'При назначении предмета преподавателю запись создается автоматически.'
    )
    list_display = ('teacher', 'subject')
    list_filter = ('subject', 'teacher')
    search_fields = ('teacher__full_name', 'subject__name')
    autocomplete_fields = ('teacher', 'subject')
    list_select_related = ('teacher', 'subject')
    ordering = ('teacher__full_name', 'subject__name')
    list_per_page = 50
    fieldsets = (
        ('Квалификация преподавателя', {
            'fields': ('teacher', 'subject'),
            'description': (
                'Эта связь показывает, какие предметы может вести преподаватель. '
                'При назначении преподавателя группе или ученику она создается автоматически.'
            ),
        }),
    )


@admin.register(GroupSubject)
class GroupSubjectAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Групповые предметы связывают группу, предмет и преподавателя. '
        'Сюда нельзя назначать индивидуальные предметы.'
    )
    form = GroupSubjectAdminForm
    list_display = ('group', 'subject', 'teacher', 'sort_order', 'is_active')
    list_filter = ('is_active', 'group__academic_year', 'group', 'subject', 'teacher')
    search_fields = ('group__name', 'subject__name', 'teacher__full_name')
    list_select_related = ('group', 'group__academic_year', 'subject', 'teacher')
    ordering = ('group__academic_year__name', 'group__name', 'sort_order', 'subject__name')
    list_per_page = 50
    fieldsets = (
        ('Групповой предмет', {
            'fields': ('group', 'subject', 'teacher', 'sort_order', 'is_active'),
            'description': (
                'Связь можно редактировать здесь, в карточке группы, преподавателя или предмета. '
                'При смене преподавателя связанные оценки по этому назначению обновляются автоматически.'
            ),
        }),
    )


@admin.register(StudentSubject)
class StudentSubjectAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Индивидуальные предметы связывают конкретного ученика, предмет и преподавателя. '
        'Сюда нельзя назначать групповые предметы.'
    )
    form = StudentSubjectAdminForm
    list_display = ('student', 'student_group_display', 'subject', 'teacher', 'is_specialty', 'is_active')
    list_filter = ('is_active', 'is_specialty', 'subject', 'teacher', 'student__group')
    search_fields = ('student__full_name', 'student__group__name', 'subject__name', 'teacher__full_name')
    list_select_related = ('student', 'student__group', 'subject', 'teacher')
    ordering = ('student__full_name', 'subject__name')
    list_per_page = 50
    fieldsets = (
        ('Индивидуальный предмет ученика', {
            'fields': ('student', 'subject', 'teacher', 'is_specialty', 'is_active'),
            'description': (
                'Связь можно редактировать здесь, в карточке ученика, преподавателя или предмета. '
                'При смене преподавателя связанные оценки по этому назначению обновляются автоматически.'
            ),
        }),
    )

    @admin.display(description='Группа')
    def student_group_display(self, obj):
        if obj and obj.student_id:
            return obj.student.group or '—'
        return '—'


@admin.register(Grade)
class GradeAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Оценки за занятия. В форме доступны только ученики, предметы и преподаватели, '
        'которые состыкованы через групповые или индивидуальные назначения.'
    )
    form = GradeAdminForm
    list_display = (
        'date',
        'student',
        'student_group_display',
        'subject',
        'teacher',
        'value',
        'academic_year',
        'source_type_display',
    )
    list_filter = (
        'academic_year',
        'date',
        'subject',
        'teacher',
        'student__group',
        'student__group__academic_year',
    )
    search_fields = (
        'student__full_name',
        'student__group__name',
        'subject__name',
        'teacher__full_name',
        'comment',
    )
    autocomplete_fields = ('academic_year',)
    readonly_fields = ('source_type_display',)
    date_hierarchy = 'date'
    list_select_related = ('student', 'student__group', 'subject', 'teacher', 'academic_year')
    ordering = ('-date', 'student__full_name')
    list_per_page = 50

    fieldsets = (
        ('Оценка', {
            'fields': (
                'date',
                'group',
                'student',
                'subject',
                'teacher',
                'value',
                'academic_year',
                'comment',
            ),
            'description': (
                'Для массовой работы с оценками удобнее использовать страницу журнала. '
                'Эта форма нужна для точечной правки.'
            ),
        }),
        ('Проверка назначения', {
            'fields': ('source_type_display',),
            'classes': ('collapse',),
        }),
    )

    class Media:
        js = ('journal/grade_dependencies.js',)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _is_group_subject=Exists(
                GroupSubject.objects.filter(
                    group_id=OuterRef('student__group_id'),
                    subject_id=OuterRef('subject_id'),
                    teacher_id=OuterRef('teacher_id'),
                    is_active=True,
                ),
            ),
            _is_individual_subject=Exists(
                StudentSubject.objects.filter(
                    student_id=OuterRef('student_id'),
                    subject_id=OuterRef('subject_id'),
                    teacher_id=OuterRef('teacher_id'),
                    is_active=True,
                ),
            ),
        )

    @admin.display(description='Группа')
    def student_group_display(self, obj):
        if obj and obj.student_id:
            return obj.student.group or '—'
        return '—'

    @admin.display(description='Тип назначения')
    def source_type_display(self, obj):
        if not obj or not obj.pk:
            return 'Будет проверено при сохранении'
        is_group_subject = getattr(obj, '_is_group_subject', None)
        if is_group_subject is None:
            is_group_subject = obj.is_group_subject
        if is_group_subject:
            return 'Групповой предмет'

        is_individual_subject = getattr(obj, '_is_individual_subject', None)
        if is_individual_subject is None:
            is_individual_subject = obj.is_individual_subject
        if is_individual_subject:
            return 'Индивидуальный предмет'
        return 'Нет активного назначения'


@admin.register(SubjectResult)
class SubjectResultAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Итоги по предметам за учебный год: экзамен и итоговая оценка. '
        'Допустимые значения зависят от типа итоговой оценки предмета.'
    )
    form = SubjectResultAdminForm
    list_display = (
        'student',
        'student_group_display',
        'subject',
        'academic_year',
        'exam_grade',
        'final_grade',
    )
    list_filter = ('academic_year', 'subject', 'student__group', 'student__group__academic_year')
    search_fields = ('student__full_name', 'student__group__name', 'subject__name')
    list_select_related = ('student', 'student__group', 'subject', 'academic_year')
    ordering = ('academic_year__name', 'student__full_name', 'subject__name')
    list_per_page = 50
    fieldsets = (
        ('Итоговая аттестация', {
            'fields': (
                'student',
                'student_group_display',
                'subject',
                'academic_year',
                'exam_grade',
                'final_grade',
            ),
            'description': (
                'Для предметов с типом «Зачет/незачет» допустимы только значения '
                '«Зачет» и «Незачет».'
            ),
        }),
    )
    readonly_fields = ('student_group_display',)

    class Media:
        js = ('journal/grade_dependencies.js',)

    @admin.display(description='Группа')
    def student_group_display(self, obj):
        if obj and obj.student_id:
            return obj.student.group or '—'
        return '—'


# -----------------------------------------------------------------------------
# Заявки на курсы и служебные настройки
# -----------------------------------------------------------------------------


class HasJournalStudentFilter(admin.SimpleListFilter):
    title = 'Ученик в журнале'
    parameter_name = 'has_journal_student'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Создан'),
            ('no', 'Не создан'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(student__isnull=False, user__isnull=False)
        if self.value() == 'no':
            return queryset.filter(Q(student__isnull=True) | Q(user__isnull=True))
        return queryset


@admin.register(CourseApplication)
class CourseApplicationAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Заявки с публичной регистрации. Подтвержденная заявка создает ученика, пользователя '
        'и временный пароль; отклоненная заявка удаляет связанные учебные записи.'
    )
    form = CourseApplicationAdminForm
    list_display = (
        'registration_date',
        'full_name_display',
        'academic_year',
        'status',
        'has_journal_student_display',
        'generated_login',
        'age_display',
        'student_phone',
        'city_church',
        'instrument',
    )
    list_filter = (
        'status',
        'academic_year',
        HasJournalStudentFilter,
        'gender',
        'music_education',
        'registration_date',
    )
    search_fields = (
        'last_name',
        'first_name',
        'middle_name',
        'student_phone',
        'parent_contacts',
        'city_church',
        'instrument',
        'generated_login',
        'user__username',
        'student__full_name',
    )
    readonly_fields = (
        'registration_date',
        'academic_year',
        'age_display',
        'has_journal_student_display',
        'student_link',
        'user_link',
        'temporary_credential_link',
        'generated_login',
        'journal_created_at',
        'journal_removed_at',
    )
    date_hierarchy = 'registration_date'
    list_select_related = ('student', 'user', 'academic_year')
    actions = ('confirm_applications', 'reject_applications')
    list_per_page = 40

    fieldsets = (
        ('Статус заявки', {
            'fields': (
                'registration_date',
                'academic_year',
                'status',
                'has_journal_student_display',
                'generated_login',
            ),
            'description': (
                'Подтвержденная заявка автоматически создает ученика, пользователя '
                'и временный пароль. При отклонении связанные записи удаляются из журнала.'
            ),
        }),
        ('Связанные записи', {
            'fields': (
                'student_link',
                'user_link',
                'temporary_credential_link',
                'journal_created_at',
                'journal_removed_at',
            ),
            'classes': ('collapse',),
        }),
        ('Основные данные ученика', {
            'fields': (
                'last_name',
                'first_name',
                'middle_name',
                'gender',
                'birth_date',
                'age_display',
            )
        }),
        ('Контакты и обучение', {
            'fields': (
                'city_church',
                'instrument',
                'music_education',
                'student_phone',
                'parent_contacts',
                'comments',
            )
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        self._course_age_reference_date = (
            CourseRegistrationSettings.objects
            .filter(pk=1)
            .values_list('course_starts_on', flat=True)
            .first()
            or date.today()
        )
        return qs

    @admin.display(description='ФИО', ordering='last_name')
    def full_name_display(self, obj):
        return obj.full_name

    @admin.display(description='Возраст на начало курсов')
    def age_display(self, obj):
        if not obj.birth_date:
            return '—'
        reference_date = getattr(self, '_course_age_reference_date', None)
        if reference_date is None:
            return obj.age
        return calculate_age(obj.birth_date, today=reference_date)

    @admin.display(description='Ученик создан', boolean=True)
    def has_journal_student_display(self, obj):
        return obj.has_journal_student

    @admin.display(description='Ученик в журнале')
    def student_link(self, obj):
        return admin_change_link(obj.student)

    @admin.display(description='Пользователь')
    def user_link(self, obj):
        return admin_change_link(obj.user)

    @admin.display(description='Временные учетные данные')
    def temporary_credential_link(self, obj):
        try:
            credential = obj.temporary_credential
        except TemporaryCredential.DoesNotExist:
            credential = None
        return admin_change_link(credential, label=credential.login if credential else None)

    @admin.action(description='Подтвердить выбранные заявки и создать учеников')
    def confirm_applications(self, request, queryset):
        processed = 0
        skipped = 0
        for application in queryset:
            if object_is_in_archived_academic_year(application):
                skipped += 1
                continue
            application.status = CourseApplication.STATUS_CONFIRMED
            application.save()
            processed += 1
        self.message_user(request, f'Подтверждено заявок: {processed}.')
        if skipped:
            self.message_user(request, f'Архивные заявки пропущены: {skipped}.', level='ERROR')

    @admin.action(description='Отклонить выбранные заявки и удалить учеников из журнала')
    def reject_applications(self, request, queryset):
        processed = 0
        skipped = 0
        for application in queryset:
            if object_is_in_archived_academic_year(application):
                skipped += 1
                continue
            application.status = CourseApplication.STATUS_REJECTED
            application.save()
            processed += 1
        self.message_user(request, f'Отклонено заявок: {processed}. Ученики удалены из журнала.')
        if skipped:
            self.message_user(request, f'Архивные заявки пропущены: {skipped}.', level='ERROR')


@admin.register(TemporaryCredential)
class TemporaryCredentialAdmin(JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Временные логины и пароли для выдачи пользователям: ученикам, преподавателям '
        'и администраторам. После смены пароля запись больше не нужна.'
    )
    list_display = (
        'login',
        'user_link',
        'role_display',
        'contact_phone_display',
        'course_application_link',
        'created_at',
    )
    list_filter = ('created_at', 'user__groups', 'user__is_staff', 'user__is_superuser')
    search_fields = (
        'login',
        'student_phone',
        'user__username',
        'user__first_name',
        'user__last_name',
        'user__email',
        'user__student_profile__full_name',
        'user__teacher_profile__full_name',
        'course_application__last_name',
        'course_application__first_name',
        'course_application__middle_name',
    )
    readonly_fields = ('created_at',)
    autocomplete_fields = ('user', 'course_application')
    date_hierarchy = 'created_at'
    list_per_page = 50
    fieldsets = (
        ('Временный доступ', {
            'fields': (
                'user',
                'course_application',
                'login',
                'temporary_password',
                'student_phone',
                'created_at',
            ),
            'description': 'Эти данные нужны только для выдачи первичного доступа пользователю.',
        }),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                'user',
                'user__student_profile',
                'user__teacher_profile',
                'course_application',
            )
            .prefetch_related('user__groups')
        )

    @admin.display(description='Заявка')
    def course_application_link(self, obj):
        return admin_change_link(obj.course_application)

    @admin.display(description='Пользователь')
    def user_link(self, obj):
        user = obj.user
        if user is None and obj.login:
            user = AuthUser.objects.filter(username=obj.login).first()
        return admin_change_link(user, label=display_name_for_user(user) or getattr(user, 'username', None))

    @admin.display(description='Роль')
    def role_display(self, obj):
        user = obj.user
        if user is None and obj.login:
            user = AuthUser.objects.filter(username=obj.login).prefetch_related('groups').first()
        if user is None:
            return 'Ученик' if obj.course_application_id or obj.student_phone else '—'

        group_names = set(user.groups.values_list('name', flat=True))
        if user.is_superuser or user.is_staff or 'Администратор' in group_names:
            return 'Администратор'
        if 'Преподаватель' in group_names or hasattr(user, 'teacher_profile'):
            return 'Преподаватель'
        if 'Ученик' in group_names or hasattr(user, 'student_profile'):
            return 'Ученик'
        return 'Пользователь'

    @admin.display(description='Телефон')
    def contact_phone_display(self, obj):
        if obj.student_phone:
            return obj.student_phone
        user = obj.user
        if user is None and obj.login:
            user = AuthUser.objects.filter(username=obj.login).select_related('student_profile').first()
        if user is not None and hasattr(user, 'student_profile'):
            return user.student_profile.student_phone or '—'
        return '—'


@admin.register(CourseRegistrationSettings)
class CourseRegistrationSettingsAdmin(JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Единая таблица настроек публичной регистрации: возраст, даты начала и окончания курсов, '
        'ссылка на Telegram-группу.'
    )
    form = CourseRegistrationSettingsForm
    list_display = (
        'telegram_group_url',
        'minimum_registration_age',
        'course_starts_on',
        'course_ends_on',
        'updated_at',
    )
    readonly_fields = ('updated_at',)
    fieldsets = (
        ('Регистрация на курсы', {
            'fields': (
                'telegram_group_url',
                'minimum_registration_age',
                'course_starts_on',
                'course_ends_on',
                'updated_at',
            ),
            'description': (
                'Здесь хранятся все настройки публичной регистрации: ссылка после заявки, '
                'минимальный возраст и даты курсов.'
            ),
        }),
    )

    def has_add_permission(self, request):
        return not CourseRegistrationSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PasswordRecoveryContact)
class PasswordRecoveryContactAdmin(JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Контакты администраторов, которые показываются пользователям на странице восстановления доступа.'
    )
    list_display = (
        'name',
        'phone',
        'messengers',
        'is_active',
        'display_order',
        'updated_at',
    )
    list_editable = ('is_active', 'display_order')
    list_filter = ('is_active', 'messengers')
    search_fields = ('name', 'phone', 'messengers')
    readonly_fields = ('updated_at',)
    ordering = ('display_order', 'name')
    fieldsets = (
        ('Контакт для восстановления доступа', {
            'fields': (
                'name',
                'phone',
                'messengers',
                'is_active',
                'display_order',
                'updated_at',
            ),
            'description': (
                'Активные контакты показываются на публичной странице восстановления пароля.'
            ),
        }),
    )
