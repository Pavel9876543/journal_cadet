from urllib.parse import urlencode

from django import forms
from django.forms.models import BaseInlineFormSet
from django.contrib import admin
from django.contrib import messages
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin, UserAdmin as BaseUserAdmin
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.contrib.auth.models import Group as AuthGroup, User as AuthUser
from django.core.exceptions import FieldDoesNotExist, PermissionDenied, ValidationError
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from .academic_year_context import (
    filter_temporary_credentials_for_year,
    get_admin_academic_year_context,
    get_selected_admin_academic_year,
)
from .account_utils import (
    build_username_from_full_name,
    clear_temporary_credentials_for_user,
    display_name_for_user,
    ensure_temporary_credential_for_user,
    generate_temporary_password,
    split_user_name,
    username_with_spaces_validator,
    user_has_temporary_credential,
)
from .admin_relations import RelatedRecordsAdminMixin
from .assignment_options import (
    active_group_queryset,
    active_student_queryset,
    assignment_teacher_queryset,
    group_subject_queryset,
    student_subject_queryset,
)
from .assessment_services import enrollments_for_assessment_item
from .error_logging import log_handled_error
from .forms import (
    CourseApplicationAdminForm,
    CourseRegistrationSettingsForm,
    configure_instrument_selection_fields,
    clean_instrument_selection,
    html_date_input,
)
from .grade_options import (
    get_grade_form_options,
    get_grade_groups,
    get_grade_students,
    get_grade_subjects,
    get_grade_teachers,
)
from .models import (
    AccountProfile,
    AcademicYear,
    AssessmentElement,
    AssessmentGroup,
    AssessmentItem,
    AssessmentResult,
    CourseApplication,
    CourseRegistrationSettings,
    ErrorLog,
    Grade,
    GroupSubject,
    FinalGradeRule,
    Instrument,
    OrchestraPart,
    PasswordRecoveryContact,
    Student,
    StudentAssessmentGroup,
    StudentEnrollment,
    StudentSubject,
    StudyGroup,
    Subject,
    SubjectResult,
    Teacher,
    TeacherEnrollment,
    TeacherSubject,
    TemporaryCredential,
    UserAcademicYearMembership,
    object_is_in_archived_academic_year,
)
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


class JournalAdminDescriptionMixin(RelatedRecordsAdminMixin):
    change_list_template = 'admin/journal/change_list_with_description.html'
    change_form_template = 'admin/journal/change_form_with_year.html'
    changelist_description = ''

    def _year_extra_context(self, request, extra_context=None):
        context = dict(extra_context or {})
        context.update(get_admin_academic_year_context(request))
        return context

    def changelist_view(self, request, extra_context=None):
        extra_context = self._year_extra_context(request, extra_context)
        extra_context['changelist_description'] = self.changelist_description
        return super().changelist_view(request, extra_context=extra_context)

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        response = super().changeform_view(
            request,
            object_id=object_id,
            form_url=form_url,
            extra_context=self._year_extra_context(request, extra_context),
        )
        if request.method == 'POST' and getattr(response, 'status_code', 200) == 200:
            context = getattr(response, 'context_data', None) or {}
            admin_form = context.get('adminform')
            form_errors = (
                admin_form.form.errors.get_json_data()
                if admin_form is not None and admin_form.form.errors
                else {}
            )
            inline_errors = []
            for inline_admin_formset in context.get('inline_admin_formsets', ()):
                formset = inline_admin_formset.formset
                if formset.non_form_errors() or any(form.errors for form in formset.forms):
                    inline_errors.append({
                        'prefix': formset.prefix,
                        'non_form_errors': list(formset.non_form_errors()),
                        'forms': [
                            form.errors.get_json_data()
                            for form in formset.forms
                            if form.errors
                        ],
                    })
            if form_errors or inline_errors:
                log_handled_error(
                    request,
                    ValidationError('Форма администратора содержит ошибки.'),
                    logger_name='journal.admin.form',
                    metadata={
                        'model': self.model._meta.label,
                        'object_id': object_id or '',
                        'form_errors': form_errors,
                        'inline_errors': inline_errors,
                    },
                )
        return response

    @staticmethod
    def _keep_change_form_open(request):
        return not any(
            key in request.POST
            for key in ('_continue', '_addanother', '_saveasnew', '_popup')
        )

    def response_add(self, request, obj, post_url_continue=None):
        if self._keep_change_form_open(request):
            request.POST = request.POST.copy()
            request.POST['_continue'] = '1'
        return super().response_add(request, obj, post_url_continue=post_url_continue)

    def response_change(self, request, obj):
        if self._keep_change_form_open(request):
            request.POST = request.POST.copy()
            request.POST['_continue'] = '1'
        return super().response_change(request, obj)

    def model_has_active_state(self):
        try:
            field = self.model._meta.get_field('is_active')
        except FieldDoesNotExist:
            return False
        return field.get_internal_type() == 'BooleanField' and self.model is not AcademicYear

    def get_actions(self, request):
        actions = super().get_actions(request)
        if self.model_has_active_state():
            actions['activate_selected_records'] = (
                self.__class__.activate_selected_records,
                'activate_selected_records',
                'Активировать выбранные записи',
            )
            actions['deactivate_selected_records'] = (
                self.__class__.deactivate_selected_records,
                'deactivate_selected_records',
                'Деактивировать выбранные записи',
            )
        return actions

    def _set_selected_active_state(self, request, queryset, *, is_active):
        changed = 0
        failed = 0
        for obj in queryset:
            if obj.is_active == is_active:
                continue
            obj.is_active = is_active
            try:
                obj.save(update_fields=['is_active'])
            except (ValidationError, PermissionDenied):
                failed += 1
            else:
                changed += 1

        state = 'активировано' if is_active else 'деактивировано'
        self.message_user(request, f'Записей {state}: {changed}.', level=messages.SUCCESS)
        if failed:
            self.message_user(
                request,
                f'Не удалось изменить статус записей: {failed}.',
                level=messages.ERROR,
            )

    @admin.action(permissions=['change'], description='Активировать выбранные записи')
    def activate_selected_records(self, request, queryset):
        self._set_selected_active_state(request, queryset, is_active=True)

    @admin.action(permissions=['change'], description='Деактивировать выбранные записи')
    def deactivate_selected_records(self, request, queryset):
        self._set_selected_active_state(request, queryset, is_active=False)


class SelectedAcademicYearMixin:
    academic_year_lookup = None

    def selected_academic_year(self, request):
        return get_selected_admin_academic_year(request)

    def selected_year_is_archived(self, request) -> bool:
        academic_year = self.selected_academic_year(request)
        return bool(academic_year and not academic_year.is_active)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        academic_year = self.selected_academic_year(request)
        if academic_year is not None and self.academic_year_lookup:
            queryset = queryset.filter(**{self.academic_year_lookup: academic_year})
        return queryset


class ArchivedAcademicYearAdminMixin(SelectedAcademicYearMixin):
    """Make year-scoped and global reference data read-only in archive mode."""

    def has_add_permission(self, request):
        if self.selected_year_is_archived(request):
            return False
        return super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        if self.selected_year_is_archived(request):
            return False
        if obj is not None and object_is_in_archived_academic_year(obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if self.selected_year_is_archived(request):
            return {
                name: action
                for name, action in actions.items()
                if name == 'delete_selected'
            }
        return actions

    def save_model(self, request, obj, form, change):
        if self.selected_year_is_archived(request):
            raise PermissionDenied('Архивный учебный год доступен только для просмотра.')
        return super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        if self.selected_year_is_archived(request):
            raise PermissionDenied('Архивный учебный год доступен только для просмотра.')
        return super().save_formset(request, form, formset, change)

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)


class SharedProfileAcademicYearAdminMixin(SelectedAcademicYearMixin):
    """Allow profile fields to be updated, but never mutate year-specific archive data."""

    def has_add_permission(self, request):
        if self.selected_year_is_archived(request):
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if self.selected_year_is_archived(request):
            return {
                name: action
                for name, action in actions.items()
                if name == 'delete_selected'
            }
        return actions

    def save_formset(self, request, form, formset, change):
        if self.selected_year_is_archived(request):
            # Profile fields remain editable in an archived year, while every
            # year-scoped inline is deliberately ignored. Inline permissions
            # render those rows read-only, and skipping save here is an extra
            # server-side guard against a forged POST.
            return None
        return super().save_formset(request, form, formset, change)


class ArchivedAcademicYearInlineMixin:
    def selected_academic_year(self, request):
        return get_selected_admin_academic_year(request)

    def parent_is_archived(self, request, obj=None) -> bool:
        selected = self.selected_academic_year(request)
        return bool(
            (selected and not selected.is_active)
            or (obj is not None and object_is_in_archived_academic_year(obj))
        )

    def has_add_permission(self, request, obj=None):
        if self.parent_is_archived(request, obj):
            return False
        return super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if self.parent_is_archived(request, obj):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if self.parent_is_archived(request, obj):
            return False
        return super().has_delete_permission(request, obj)

    def get_extra(self, request, obj=None, **kwargs):
        if self.parent_is_archived(request, obj):
            return 0
        return super().get_extra(request, obj, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        if not self.parent_is_archived(request, obj):
            return super().get_readonly_fields(request, obj)
        configured_fields = self.fields or ()
        flattened = []
        for field in configured_fields:
            if isinstance(field, (tuple, list)):
                flattened.extend(field)
            else:
                flattened.append(field)
        return tuple(flattened)


class SelectedAcademicYearGroupSubjectInlineMixin:
    def get_queryset(self, request):
        selected = get_selected_admin_academic_year(request)
        queryset = super().get_queryset(request)
        return queryset.filter(group__academic_year=selected) if selected else queryset.none()


class SelectedAcademicYearStudentSubjectInlineMixin:
    def get_queryset(self, request):
        selected = get_selected_admin_academic_year(request)
        queryset = super().get_queryset(request).select_related(
            'student',
            'subject',
            'teacher',
            'academic_year',
        )
        return queryset.filter(academic_year=selected) if selected else queryset.none()


class SelectedAcademicYearSubjectResultInlineMixin:
    def get_queryset(self, request):
        selected = get_selected_admin_academic_year(request)
        queryset = super().get_queryset(request).select_related(
            'student',
            'subject',
            'academic_year',
            'enrollment',
            'enrollment__group',
        )
        return queryset.filter(academic_year=selected) if selected else queryset.none()


class SelectedAcademicYearGradeInlineMixin:
    def get_queryset(self, request):
        selected = get_selected_admin_academic_year(request)
        queryset = super().get_queryset(request).select_related(
            'student',
            'subject',
            'teacher',
            'academic_year',
            'enrollment',
            'enrollment__group',
        )
        return queryset.filter(academic_year=selected) if selected else queryset.none()


class SelectedYearStudentGroupFilter(admin.SimpleListFilter):
    title = 'группа выбранного года'
    parameter_name = 'selected_year_group'

    def lookups(self, request, model_admin):
        academic_year = get_selected_admin_academic_year(request)
        if academic_year is None:
            return ()
        return StudyGroup.objects.filter(academic_year=academic_year).values_list('pk', 'name')

    def queryset(self, request, queryset):
        if not self.value():
            return queryset
        return queryset.filter(
            enrollments__academic_year=get_selected_admin_academic_year(request),
            enrollments__group_id=self.value(),
        )


class SelectedYearStudentActiveFilter(admin.SimpleListFilter):
    title = 'активность в выбранном году'
    parameter_name = 'selected_year_student_active'

    def lookups(self, request, model_admin):
        return (('1', 'Да'), ('0', 'Нет'))

    def queryset(self, request, queryset):
        if self.value() not in {'0', '1'}:
            return queryset
        return queryset.filter(
            enrollments__academic_year=get_selected_admin_academic_year(request),
            enrollments__is_active=self.value() == '1',
        )


class SelectedYearTeacherActiveFilter(admin.SimpleListFilter):
    title = 'активность в выбранном году'
    parameter_name = 'selected_year_teacher_active'

    def lookups(self, request, model_admin):
        return (('1', 'Да'), ('0', 'Нет'))

    def queryset(self, request, queryset):
        if self.value() not in {'0', '1'}:
            return queryset
        return queryset.filter(
            academic_year_memberships__academic_year=get_selected_admin_academic_year(request),
            academic_year_memberships__is_active=self.value() == '1',
        )


class SpaceFriendlyUsernameFormMixin:
    username = forms.CharField(
        label='Логин',
        max_length=150,
        help_text=USERNAME_WITH_SPACES_HELP_TEXT,
        validators=[username_with_spaces_validator],
    )


class SpaceFriendlyUserCreationForm(SpaceFriendlyUsernameFormMixin, UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = AuthUser
        fields = ('username',)


class SpaceFriendlyUserChangeForm(SpaceFriendlyUsernameFormMixin, UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = AuthUser
        fields = '__all__'


class AccountProfileInline(admin.StackedInline):
    model = AccountProfile
    extra = 0
    max_num = 1
    fields = ('birth_date',)
    verbose_name_plural = 'Дата рождения администратора'


class UserAcademicYearMembershipForUserInline(admin.TabularInline):
    model = UserAcademicYearMembership
    fk_name = 'user'
    extra = 0
    fields = ('academic_year', 'is_active')
    autocomplete_fields = ('academic_year',)
    verbose_name = 'Участие в учебном году'
    verbose_name_plural = 'Учебные годы пользователя'


@admin.register(AuthUser)
class UserAdmin(SharedProfileAcademicYearAdminMixin, JournalAdminDescriptionMixin, BaseUserAdmin):
    changelist_description = (
        'Учетные записи для входа в систему. Здесь видны администраторы, преподаватели '
        'и ученики, а учебные профили открываются по ссылкам в строках таблицы.'
    )
    form = SpaceFriendlyUserChangeForm
    add_form = SpaceFriendlyUserCreationForm
    inlines = (
        *BaseUserAdmin.inlines,
        AccountProfileInline,
        UserAcademicYearMembershipForUserInline,
    )
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
        queryset = super().get_queryset(request).select_related(
            'student_profile',
            'teacher_profile',
        )
        academic_year = self.selected_academic_year(request)
        if academic_year is None:
            return queryset.none()
        return queryset.filter(
            Q(
                is_staff=True,
                journal_year_memberships__isnull=True,
            )
            | Q(journal_year_memberships__academic_year=academic_year)
            | Q(student_profile__enrollments__academic_year=academic_year)
            | Q(teacher_profile__academic_year_memberships__academic_year=academic_year),
        ).distinct()

    def user_change_password(self, request, id, form_url=''):
        user = self.get_object(request, id)
        previous_password = getattr(user, 'password', None)
        response = super().user_change_password(request, id, form_url=form_url)

        if request.method == 'POST' and user is not None:
            user.refresh_from_db(fields=['password'])
            if user.password != previous_password:
                deleted_count, _details = clear_temporary_credentials_for_user(user)
                if deleted_count:
                    self.message_user(
                        request,
                        'Временные учетные данные пользователя удалены после сброса пароля.',
                        level=messages.SUCCESS,
                    )
        return response

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            temporary_password = form.cleaned_data.get('password1') if form else None
            if temporary_password:
                ensure_temporary_credential_for_user(
                    obj,
                    password=temporary_password,
                    user_was_created=True,
                )
        elif user_has_temporary_credential(obj):
            # Keep login/profile metadata in sync without ever rotating the
            # password or creating a new temporary credential on edit.
            ensure_temporary_credential_for_user(obj)

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
class AuthGroupAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, BaseGroupAdmin):
    changelist_description = (
        'Роли пользователей и наборы прав. Обычно используются роли Администратор, '
        'Преподаватель и Ученик.'
    )
    search_fields = ('name',)
    list_per_page = 40


@admin.register(UserAcademicYearMembership)
class UserAcademicYearMembershipAdmin(
    ArchivedAcademicYearAdminMixin,
    JournalAdminDescriptionMixin,
    admin.ModelAdmin,
):
    academic_year_lookup = 'academic_year'
    changelist_description = (
        'Ручное назначение учебных годов администраторам, преподавателям '
        'и другим пользователям. Один аккаунт можно назначить в несколько лет.'
    )
    list_display = ('user', 'academic_year', 'is_active', 'updated_at')
    list_filter = ('academic_year', 'is_active')
    search_fields = (
        'user__username', 'user__first_name', 'user__last_name', 'academic_year__name',
    )
    autocomplete_fields = ('user', 'academic_year')
    list_select_related = ('user', 'academic_year')
    ordering = ('-academic_year__starts_on', 'user__username')
    readonly_fields = ('created_at', 'updated_at')
    fields = ('user', 'academic_year', 'is_active', 'created_at', 'updated_at')


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
        empty_label='Все группы',
    )

    class Meta:
        model = Grade
        fields = '__all__'
        widgets = {
            'date': html_date_input(),
            'comment': forms.TextInput(attrs={'size': 80}),
        }

    def __init__(self, *args, fixed_academic_year=None, **kwargs):
        super().__init__(*args, **kwargs)

        instance = self.instance if self.instance and self.instance.pk else None
        student = getattr(instance, 'student', None)
        subject = getattr(instance, 'subject', None)
        teacher = getattr(instance, 'teacher', None)
        academic_year = fixed_academic_year or getattr(instance, 'academic_year', None)

        group_id = self.data.get('group')
        student_id = self.data.get('student') or getattr(instance, 'student_id', None)
        subject_id = self.data.get('subject') or getattr(instance, 'subject_id', None)
        teacher_id = self.data.get('teacher') or getattr(instance, 'teacher_id', None)
        academic_year_id = (
            getattr(fixed_academic_year, 'pk', None)
            or self.data.get('academic_year')
            or getattr(instance, 'academic_year_id', None)
        )

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

        group_was_submitted = self.is_bound and 'group' in self.data
        enrollment = (
            student.enrollment_for_year(academic_year)
            if student is not None and academic_year is not None
            else None
        )
        group = None if group_was_submitted else (
            enrollment.group if enrollment is not None else None
        )
        if group_id:
            try:
                group = StudyGroup.objects.get(pk=group_id)
            except (StudyGroup.DoesNotExist, ValueError, TypeError):
                group = None

        dependency_options = get_grade_form_options(
            academic_year=academic_year,
            group=group,
            teacher=teacher,
            student=student,
            subject=subject,
            individual_only=group is None,
        )

        if 'group' in self.fields:
            self.fields['group'].queryset = self._include_submitted_choice(
                dependency_options['groups'],
                StudyGroup,
                group_id,
            )
            self.fields['group'].initial = group
        if 'student' in self.fields:
            self.fields['student'].queryset = self._include_submitted_choice(
                dependency_options['students'],
                Student,
                student_id,
            )
        if 'subject' in self.fields:
            self.fields['subject'].queryset = self._include_submitted_choice(
                dependency_options['subjects'],
                Subject,
                subject_id,
            )
        if 'teacher' in self.fields:
            self.fields['teacher'].queryset = self._include_submitted_choice(
                dependency_options['teachers'],
                Teacher,
                teacher_id,
            )
        if 'academic_year' in self.fields:
            self.fields['academic_year'].queryset = self._include_submitted_choice(
                AcademicYear.objects.filter(is_active=True).order_by('-starts_on'),
                AcademicYear,
                academic_year_id,
            )
            if fixed_academic_year is not None:
                self.fields['academic_year'].initial = fixed_academic_year
                self.fields['academic_year'].disabled = True
        dependency_url = reverse('grade_options_api')
        for field_name in ('group', 'student', 'subject', 'teacher'):
            if field_name in self.fields:
                self.fields[field_name].widget.attrs.update({
                    'data-grade-options-url': dependency_url,
                    'data-grade-dependency-mode': 'grade',
                })

        self.fixed_academic_year = fixed_academic_year

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
        academic_year = self.fixed_academic_year or cleaned_data.get('academic_year')
        if self.fixed_academic_year is not None:
            cleaned_data['academic_year'] = self.fixed_academic_year

        if group is None and 'group' not in self.fields and student is not None:
            enrollment = student.enrollment_for_year(academic_year)
            group = enrollment.group if enrollment is not None else None
            cleaned_data['group'] = group

        if academic_year is None and group is not None:
            academic_year = group.academic_year
            cleaned_data['academic_year'] = academic_year

        if group and student:
            enrollment = student.enrollment_for_year(academic_year)
            if enrollment is None or enrollment.group_id != group.pk:
                self._add_available_error(
                    'student',
                    'Ученик не состоит в выбранной группе в этом учебном году.',
                )

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

        if student and subject and teacher:
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
        if student and academic_year and student.enrollment_for_year(academic_year) is None:
            self.add_error('student', 'Ученик не зачислен в выбранный учебный год.')

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
            'city_church': forms.TextInput(attrs={
                'class': 'city-church-field',
                'size': '80',
            }),
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

        configure_instrument_selection_fields(
            self,
            'instrument',
            custom_placeholder='Введите название собственного инструмента',
        )
        if 'orchestra_part' in self.fields:
            # JavaScript controls only the HTML availability. The Django field
            # must remain enabled so a newly selected part is accepted on POST.
            self.fields['orchestra_part'].disabled = False

    class Media:
        js = (
            'journal/admin_responsive.js',
            'journal/orchestra_part_dependencies_v5.js',
        )

    def clean(self):
        cleaned_data = super().clean()
        return clean_instrument_selection(self, cleaned_data, 'instrument')


class AcademicYearHistoryInlineMixin:
    """Read-only proof that one profile is reused across academic years."""

    extra = 0
    can_delete = False
    show_change_link = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class AcademicYearHistoryInlineForm(forms.ModelForm):
    """Do not revalidate immutable archived rows on a parent change POST.

    Django still constructs ModelForms for read-only inline history rows. A
    normal ``ModelForm._post_clean`` calls ``instance.full_clean()`` and may
    receive a validation error for ``academic_year`` even though that field is
    intentionally absent from the editable form. Django then raises ValueError
    while trying to attach the error to a non-existent field. Existing history
    rows are immutable, so unchanged rows must bypass model revalidation.
    """

    def _post_clean(self):
        if self.instance and self.instance.pk:
            return
        super()._post_clean()


class TeacherEnrollmentHistoryInline(AcademicYearHistoryInlineMixin, admin.TabularInline):
    model = TeacherEnrollment
    form = AcademicYearHistoryInlineForm
    fk_name = 'teacher'
    fields = ('academic_year', 'is_active', 'created_at', 'updated_at')
    readonly_fields = fields
    verbose_name = 'Участие преподавателя в учебном году'
    verbose_name_plural = 'История участия преподавателя по учебным годам'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('academic_year').order_by(
            '-academic_year__starts_on',
            '-academic_year_id',
        )


class StudentEnrollmentHistoryInline(AcademicYearHistoryInlineMixin, admin.TabularInline):
    model = StudentEnrollment
    form = AcademicYearHistoryInlineForm
    fk_name = 'student'
    fields = (
        'academic_year',
        'group',
        'is_active',
        'instrument_name',
        'orchestra_part',
        'created_at',
        'updated_at',
    )
    readonly_fields = fields
    verbose_name = 'Зачисление ученика в учебный год'
    verbose_name_plural = 'История зачислений ученика по учебным годам'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'academic_year',
            'group',
        ).order_by('-academic_year__starts_on', '-academic_year_id')


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


class GroupSubjectForSubjectAdminForm(GroupSubjectAdminForm):
    """Inline form with complete controls for the related study group."""

    parent_subject = None

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        if self.parent_subject is not None and instance is not None and not instance.subject_id:
            instance.subject = self.parent_subject
        super().__init__(*args, **kwargs)
        group_field = self.fields.get('group')
        if group_field is None:
            return

        relation = GroupSubject._meta.get_field('group').remote_field
        if not isinstance(group_field.widget, RelatedFieldWidgetWrapper):
            group_field.widget = RelatedFieldWidgetWrapper(
                group_field.widget,
                relation,
                admin.site,
                can_add_related=True,
                can_change_related=True,
                can_delete_related=True,
                can_view_related=True,
            )
        group_field.help_text = (
            'Связь предмета с группой удаляется флажком «Удалить» в строке. '
            'Кнопки рядом со списком позволяют отдельно создать, открыть, '
            'изменить или удалить саму группу.'
        )


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
        academic_year = (
            getattr(self, 'parent_academic_year', None)
            or getattr(self.instance, 'academic_year', None)
        )

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
                assignment_teacher_queryset(subject, academic_year),
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

    def clean(self):
        cleaned_data = super().clean()
        student = cleaned_data.get('student')
        subject = cleaned_data.get('subject')
        teacher = cleaned_data.get('teacher')

        if subject and not subject.is_specialty:
            self.add_error('subject', 'Групповой предмет нельзя назначить индивидуальному ученику.')

        active_year = (
            getattr(self, 'parent_academic_year', None)
            or getattr(self.instance, 'academic_year', None)
            or AcademicYear.get_active()
        )
        if student and student.enrollment_for_year(active_year) is None:
            self.add_error('student', 'Ученик не зачислен в активный учебный год.')

        if teacher and not teacher.is_active:
            self.add_error('teacher', 'Выберите активного преподавателя.')

        if student and subject:
            duplicate_qs = StudentSubject.objects.filter(
                student=student,
                subject=subject,
                academic_year=active_year,
            )
            if self.instance.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.instance.pk)
            if duplicate_qs.exists():
                self.add_error('subject', 'У ученика уже есть такой индивидуальный предмет.')


        return cleaned_data


class AssessmentDependencyFormMixin:
    assessment_type = ''
    dependency_fields = ()

    class Media:
        js = ('journal/admin_assessment_dependencies.js',)

    def _raw_value(self, field_name):
        if self.is_bound:
            return self.data.get(self.add_prefix(field_name)) or self.data.get(field_name)
        return getattr(self.instance, f'{field_name}_id', None)

    def _set_queryset(self, field_name, queryset):
        """Assign a queryset only when the field is present in this form.

        Django removes the parent foreign-key field from inline forms.  The
        same ModelForm is intentionally reused both as a standalone admin form
        and as an inline, therefore direct ``self.fields['parent']`` access is
        unsafe and caused the Student change page to fail with ``KeyError``.
        """
        field = self.fields.get(field_name)
        if field is not None:
            field.queryset = queryset
        return field

    def _set_help_text(self, field_name, value):
        field = self.fields.get(field_name)
        if field is not None:
            field.help_text = value
        return field

    def _selected_object(self, queryset, field_name):
        raw_value = self._raw_value(field_name)
        if not raw_value:
            return None
        try:
            return queryset.filter(pk=raw_value).first()
        except (TypeError, ValueError):
            return None

    def _include_selected(self, queryset, model, field_name):
        selected = self._selected_object(model.objects.all(), field_name)
        if selected is None:
            return queryset
        return model.objects.filter(Q(pk__in=queryset.values('pk')) | Q(pk=selected.pk)).distinct()

    def attach_dependencies(self):
        endpoint = reverse('assessment_options_api')
        parent_attrs = {
            'parent_student': 'data-parent-student-id',
            'parent_subject': 'data-parent-subject-id',
            'parent_assessment_group': 'data-parent-assessment-group-id',
            'parent_assessment_item': 'data-parent-assessment-item-id',
            'parent_academic_year': 'data-parent-academic-year-id',
        }
        for field_name in self.dependency_fields:
            if field_name in self.fields:
                self.fields[field_name].widget.attrs.update({
                    'data-assessment-options-url': endpoint,
                    'data-assessment-type': self.assessment_type,
                })
                if self.assessment_type == 'item' and getattr(self.instance, 'pk', None):
                    self.fields[field_name].widget.attrs[
                        'data-current-assessment-item-id'
                    ] = str(self.instance.pk)
                for attribute_name, data_attribute in parent_attrs.items():
                    parent = getattr(self, attribute_name, None)
                    parent_id = getattr(parent, 'pk', parent)
                    if parent_id:
                        self.fields[field_name].widget.attrs[data_attribute] = str(parent_id)


class ExistingValuesTextInput(forms.TextInput):
    """Text input with a native dropdown of values already stored in a table."""

    def __init__(self, *args, values=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.suggestion_values = tuple(dict.fromkeys(value for value in values if value))

    def render(self, name, value, attrs=None, renderer=None):
        attrs = dict(attrs or {})
        input_id = attrs.get('id') or f'id_{name}'
        datalist_id = f'{input_id}_stored_values'
        attrs['list'] = datalist_id
        input_html = super().render(name, value, attrs=attrs, renderer=renderer)
        options_html = format_html_join(
            '',
            '<option value="{}"></option>',
            ((item,) for item in self.suggestion_values),
        )
        return format_html(
            '{}<datalist id="{}">{}</datalist>',
            input_html,
            datalist_id,
            options_html,
        )


class AssessmentGroupAdminForm(forms.ModelForm):
    class Meta:
        model = AssessmentGroup
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        subject_id = (
            self.data.get(self.add_prefix('subject')) if self.is_bound else None
        ) or getattr(self.instance, 'subject_id', None) or getattr(
            getattr(self, 'parent_subject', None), 'pk', None
        )
        academic_year_id = (
            self.data.get(self.add_prefix('academic_year')) if self.is_bound else None
        ) or getattr(self.instance, 'academic_year_id', None) or getattr(
            getattr(self, 'parent_academic_year', None), 'pk', None
        )
        group_names = AssessmentGroup.objects.all()
        if subject_id:
            group_names = group_names.filter(subject_id=subject_id)
        if academic_year_id:
            group_names = group_names.filter(academic_year_id=academic_year_id)
        group_names = group_names.order_by('name').values_list('name', flat=True).distinct()
        if 'name' in self.fields:
            self.fields['name'].widget = ExistingValuesTextInput(values=group_names)
        subject_field = self.fields.get('subject')
        if subject_field is not None:
            subject_field.queryset = Subject.objects.filter(
                is_active=True,
                assessment_mode=Subject.ASSESSMENT_MODE_ELEMENTS,
            ).order_by('name')
        year_field = self.fields.get('academic_year')
        if year_field is not None:
            year_field.queryset = AcademicYear.objects.filter(
                is_active=True,
            ).order_by('-starts_on')


class AssessmentGroupForSubjectAdminForm(AssessmentGroupAdminForm):
    """Use only names that already exist in the assessment-group table."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'name' not in self.fields:
            return

        names = list(
            AssessmentGroup.objects
            .order_by('name')
            .values_list('name', flat=True)
            .distinct()
        )
        current_name = (getattr(self.instance, 'name', '') or '').strip()
        bound_name = (
            self.data.get(self.add_prefix('name'))
            if self.is_bound
            else ''
        )
        for value in (current_name, bound_name):
            if value and value not in names:
                names.append(value)
        names.sort(key=str.casefold)

        self.fields['name'] = forms.ChoiceField(
            label='Название группы произведений',
            choices=[('', 'Выберите группу произведений')] + [
                (name, name) for name in names
            ],
            required=True,
            help_text=(
                'Список формируется только из таблицы «Группы произведений». '
                'Новое название сначала создайте в этом справочнике.'
            ),
        )
        self.fields['name'].initial = current_name


class AssessmentItemAdminForm(AssessmentDependencyFormMixin, forms.ModelForm):
    assessment_type = 'item'
    dependency_fields = ('subject', 'academic_year', 'group', 'element', 'responsible_teacher')

    class Meta:
        model = AssessmentItem
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        subject = self._selected_object(Subject.objects.all(), 'subject')
        year = self._selected_object(AcademicYear.objects.all(), 'academic_year')
        group = (
            self._selected_object(AssessmentGroup.objects.all(), 'group')
            or getattr(self, 'parent_assessment_group', None)
        )
        if group is not None:
            subject = group.subject
            year = group.academic_year
        elements = AssessmentElement.objects.filter(is_active=True)
        if subject is not None:
            elements = elements.filter(subject=subject)
        if group is not None:
            occupied = AssessmentItem.objects.filter(
                group=group,
                element__isnull=False,
            )
            if self.instance and self.instance.pk:
                occupied = occupied.exclude(pk=self.instance.pk)
            elements = elements.exclude(
                pk__in=occupied.values('element_id'),
            )
        # Keep only the value owned by the edited row.  A submitted value that
        # is already occupied by another row must not be silently reintroduced
        # into the dropdown, otherwise the database constraint is reached.
        if self.instance and self.instance.pk and self.instance.element_id:
            elements = AssessmentElement.objects.filter(
                Q(pk__in=elements.values('pk')) | Q(pk=self.instance.element_id),
            )
        self._set_queryset(
            'element',
            elements.select_related('subject').distinct().order_by(
                'subject__name', 'title'
            ),
        )
        if 'element' in self.fields:
            self.fields['element'].required = True
            self.fields['element'].help_text = (
                'Выберите произведение только из справочника. Новое значение '
                'добавляется через кнопку «+» рядом с полем.'
            )
        self._set_queryset('subject', self._include_selected(
            Subject.objects.filter(
                is_active=True,
                assessment_mode=Subject.ASSESSMENT_MODE_ELEMENTS,
            ).order_by('name'),
            Subject,
            'subject',
        ))
        self._set_queryset('academic_year', self._include_selected(
            AcademicYear.objects.filter(is_active=True).order_by('-starts_on'),
            AcademicYear,
            'academic_year',
        ))
        groups = AssessmentGroup.objects.filter(is_active=True)
        if year is not None:
            groups = groups.filter(academic_year=year)
        selected_element = (
            self._selected_object(AssessmentElement.objects.all(), 'element')
            or (
                self.instance.element
                if self.instance and self.instance.pk and self.instance.element_id
                else None
            )
        )
        if selected_element is not None:
            occupied_groups = AssessmentItem.objects.filter(
                element=selected_element,
            )
            if self.instance and self.instance.pk:
                occupied_groups = occupied_groups.exclude(pk=self.instance.pk)
            groups = groups.exclude(pk__in=occupied_groups.values('group_id'))
        if self.instance and self.instance.pk and self.instance.group_id:
            groups = AssessmentGroup.objects.filter(
                Q(pk__in=groups.values('pk')) | Q(pk=self.instance.group_id),
            )
        self._set_queryset(
            'group',
            groups.select_related('subject', 'academic_year').distinct().order_by(
                'sort_order', 'name'
            ),
        )
        teachers = Teacher.objects.filter(is_active=True)
        if year is not None:
            teachers = teachers.filter(
                academic_year_memberships__academic_year=year,
                academic_year_memberships__is_active=True,
            )
        self._set_queryset('responsible_teacher', self._include_selected(
            teachers.distinct().order_by('full_name'),
            Teacher,
            'responsible_teacher',
        ))
        self._set_help_text('responsible_teacher', (
            'Можно выбрать любого активного преподавателя этого учебного года. '
            'Без ответственного преподавателя произведение недоступно для выставления результатов.'
        ))
        self._set_help_text('group', (
            'Главное поле произведения. При смене группы предмет и учебный год '
            'обновятся автоматически.'
        ))
        self.attach_dependencies()

    def clean(self):
        cleaned_data = super().clean()
        group = cleaned_data.get('group') or getattr(self, 'parent_assessment_group', None)
        if group is not None:
            cleaned_data['subject'] = group.subject
            cleaned_data['academic_year'] = group.academic_year
            self.instance.subject = group.subject
            self.instance.academic_year = group.academic_year

        element = cleaned_data.get('element')
        if group is not None and element is not None:
            duplicate = AssessmentItem.objects.filter(
                group=group,
                element=element,
            )
            if self.instance and self.instance.pk:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                self.add_error(
                    'element',
                    'Это произведение уже добавлено в выбранную группу. '
                    'Выберите другое значение.',
                )
        return cleaned_data


class AssessmentItemInlineFormSet(BaseInlineFormSet):
    """Reject duplicate group/element pairs before database constraints."""

    def clean(self):
        super().clean()
        seen = {}
        for form in self.forms:
            cleaned_data = getattr(form, 'cleaned_data', None) or {}
            if not cleaned_data or cleaned_data.get('DELETE'):
                continue
            group = (
                cleaned_data.get('group')
                or getattr(form, 'parent_assessment_group', None)
                or getattr(form.instance, 'group', None)
            )
            element = cleaned_data.get('element')
            if group is None or element is None:
                continue
            key = (group.pk, element.pk)
            previous_form = seen.get(key)
            if previous_form is None:
                seen[key] = form
                continue
            message = (
                'Это произведение уже выбрано в другой строке для той же группы.'
            )
            form.add_error('element', message)
            if 'element' not in previous_form.errors:
                previous_form.add_error('element', message)


class StudentAssessmentGroupAdminForm(AssessmentDependencyFormMixin, forms.ModelForm):
    assessment_type = 'student_group'
    dependency_fields = ('student', 'academic_year', 'assessment_group')

    class Meta:
        model = StudentAssessmentGroup
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        student = (
            self._selected_object(Student.objects.all(), 'student')
            or getattr(self, 'parent_student', None)
        )
        year = (
            self._selected_object(AcademicYear.objects.all(), 'academic_year')
            or getattr(self, 'parent_academic_year', None)
        )
        group = (
            self._selected_object(AssessmentGroup.objects.all(), 'assessment_group')
            or getattr(self, 'parent_assessment_group', None)
        )
        if group is not None:
            year = group.academic_year
        student_queryset = active_student_queryset()
        if group is not None:
            student_queryset = Student.objects.filter(
                enrollments__academic_year=group.academic_year,
                enrollments__is_active=True,
            )
            if not (self.instance and self.instance.pk):
                student_queryset = student_queryset.filter(is_active=True)
            student_queryset = student_queryset.distinct().order_by('full_name', 'pk')
        self._set_queryset('student', self._include_selected(
            student_queryset, Student, 'student'
        ))
        self._set_queryset('academic_year', self._include_selected(
            AcademicYear.objects.filter(is_active=True).order_by('-starts_on'),
            AcademicYear,
            'academic_year',
        ))
        groups = AssessmentGroup.objects.filter(is_active=True)
        if year is not None:
            groups = groups.filter(academic_year=year)
        self._set_queryset('assessment_group', self._include_selected(
            groups.select_related('subject', 'academic_year').order_by('subject__name', 'sort_order', 'name'),
            AssessmentGroup,
            'assessment_group',
        ))
        self.attach_dependencies()


class AssessmentResultAdminForm(AssessmentDependencyFormMixin, forms.ModelForm):
    assessment_type = 'result'
    dependency_fields = ('item', 'enrollment', 'assessed_by')

    class Meta:
        model = AssessmentResult
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        item = (
            self._selected_object(AssessmentItem.objects.all(), 'item')
            or getattr(self, 'parent_assessment_item', None)
        )
        selected_year = getattr(self, 'parent_academic_year', None)
        items = AssessmentItem.objects.select_related(
            'group', 'subject', 'academic_year', 'responsible_teacher'
        )
        if selected_year is not None:
            items = items.filter(academic_year=selected_year)
        if not (self.instance and self.instance.pk):
            items = items.filter(is_active=True, group__is_active=True)
        self._set_queryset('item', self._include_selected(
            items.order_by('subject__name', 'group__sort_order', 'sort_order', 'title'),
            AssessmentItem,
            'item',
        ))

        enrollments = StudentEnrollment.objects.none()
        teachers = Teacher.objects.none()
        if item is not None:
            enrollments = enrollments_for_assessment_item(
                item,
                include_inactive=bool(self.instance and self.instance.pk),
            )
            if item.responsible_teacher_id:
                teachers = Teacher.objects.filter(pk=item.responsible_teacher_id)
                assessed_by_id = getattr(self.instance, 'assessed_by_id', None)
                if assessed_by_id and assessed_by_id != item.responsible_teacher_id:
                    teachers = Teacher.objects.filter(
                        Q(pk=item.responsible_teacher_id) | Q(pk=assessed_by_id)
                    )
                assessed_by_field = self.fields.get('assessed_by')
                if assessed_by_field is not None and not assessed_by_field.initial:
                    assessed_by_field.initial = item.responsible_teacher_id

        self._set_queryset('enrollment', self._include_selected(
            enrollments,
            StudentEnrollment,
            'enrollment',
        ))
        self._set_queryset('assessed_by', self._include_selected(
            teachers.order_by('full_name'),
            Teacher,
            'assessed_by',
        ))
        self.attach_dependencies()


class FinalGradeRuleAdminForm(AssessmentDependencyFormMixin, forms.ModelForm):
    assessment_type = 'rule'
    dependency_fields = ('subject', 'academic_year', 'assessment_group')

    class Meta:
        model = FinalGradeRule
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        subject = (
            self._selected_object(Subject.objects.all(), 'subject')
            or getattr(self, 'parent_subject', None)
        )
        year = (
            self._selected_object(AcademicYear.objects.all(), 'academic_year')
            or getattr(self, 'parent_academic_year', None)
        )
        self._set_queryset('subject', self._include_selected(
            Subject.objects.filter(
                is_active=True,
                assessment_mode=Subject.ASSESSMENT_MODE_ELEMENTS,
            ).order_by('name'),
            Subject,
            'subject',
        ))
        self._set_queryset('academic_year', self._include_selected(
            AcademicYear.objects.filter(is_active=True).order_by('-starts_on'),
            AcademicYear,
            'academic_year',
        ))
        groups = AssessmentGroup.objects.all()
        if subject is not None:
            groups = groups.filter(subject=subject)
        if year is not None:
            groups = groups.filter(academic_year=year)
        self._set_queryset('assessment_group', self._include_selected(
            groups.select_related('subject', 'academic_year').order_by('sort_order', 'name'),
            AssessmentGroup,
            'assessment_group',
        ))
        self.attach_dependencies()


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
        model = StudentEnrollment
        fields = ('student', 'city_church')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'student' not in self.fields or 'city_church' not in self.fields:
            return

        selected_student = (
            self.instance.student
            if self.instance and self.instance.pk
            else None
        )
        # The current year may enroll a person who already studied in an older
        # year. Profiles are shared, while StudentEnrollment remains year-scoped.
        # Archived groups are read-only, so exposing all profiles here cannot
        # backfill a student into a past year.
        student_queryset = Student.objects.select_related(
            'group',
            'group__academic_year',
        ).order_by('full_name', 'pk')

        self.fields['student'].queryset = student_queryset
        self.fields['student'].initial = selected_student
        self.fields['student'].widget = RelatedFieldWidgetWrapper(
            self.fields['student'].widget,
            StudentEnrollment._meta.get_field('student').remote_field,
            admin.site,
            can_add_related=True,
            can_change_related=True,
            can_delete_related=True,
            can_view_related=True,
        )
        self.fields['city_church'].disabled = True
        self.fields['city_church'].required = False
        self.fields['city_church'].initial = (
            self.instance.city_church
            if self.instance and self.instance.pk
            else (selected_student.city_church if selected_student is not None else '')
        )
        self.fields['city_church'].widget.attrs.update({
            'data-student-city-target': '1',
            'class': 'city-church-field',
            'size': '80',
        })


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
            Student.objects.filter(pk=obj.student_id).update(group=target_group)

    def save_existing(self, form, obj, commit=True):
        selected_student = form.cleaned_data.get('student') or obj.student

        if selected_student.pk != obj.student_id:
            self.delete_existing(obj, commit=commit)
            return self._move_student_enrollment(selected_student, commit=commit)

        obj.group = self.instance
        if commit:
            obj.save(update_fields=['group'])
            Student.objects.filter(pk=obj.student_id).update(group=self.instance)
        return obj

    def save_new(self, form, commit=True):
        selected_student = form.cleaned_data.get('student')
        if selected_student is None:
            return super().save_new(form, commit=commit)

        return self._move_student_enrollment(selected_student, commit=commit)

    def _move_student_enrollment(self, student, *, commit):
        academic_year = self.instance.academic_year
        enrollment = StudentEnrollment.objects.filter(
            student=student,
            academic_year=academic_year,
        ).first()
        if enrollment is None:
            enrollment = StudentEnrollment(
                student=student,
                academic_year=academic_year,
                group=self.instance,
                **StudentEnrollment.snapshot_values_for_student(student),
            )
        else:
            enrollment.group = self.instance
            enrollment.copy_from_student(student)
        enrollment.is_active = True

        if commit:
            enrollment.save()
            Student.objects.filter(pk=student.pk).update(
                group=self.instance,
                is_active=True,
            )
        return enrollment


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
            'fields': ('student', 'subject', 'academic_year'),
            'message': 'У ученика уже есть такой индивидуальный предмет.',
        },
    )

    def _unique_field_value(self, form, field_name):
        if field_name == 'academic_year':
            if getattr(form.instance, 'academic_year_id', None):
                return form.instance.academic_year
            return AcademicYear.get_active()
        return super()._unique_field_value(form, field_name)


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

    def get_fields(self, request, obj=None):
        if self.parent_is_archived(request, obj):
            return ('archived_subject_name', 'archived_teacher_name', 'sort_order', 'is_active')
        return super().get_fields(request, obj)

    def get_readonly_fields(self, request, obj=None):
        if self.parent_is_archived(request, obj):
            return ('archived_subject_name', 'archived_teacher_name', 'sort_order', 'is_active')
        return super().get_readonly_fields(request, obj)

    @admin.display(description='Предмет')
    def archived_subject_name(self, obj):
        return obj.subject_name_snapshot or obj.subject.name

    @admin.display(description='Преподаватель')
    def archived_teacher_name(self, obj):
        return obj.teacher_name_snapshot or obj.teacher.full_name


class GroupSubjectForTeacherInline(SelectedAcademicYearGroupSubjectInlineMixin, ArchivedAcademicYearInlineMixin, admin.TabularInline):
    model = GroupSubject
    form = GroupSubjectAdminForm
    formset = GroupSubjectInlineFormSet
    extra = 0
    fields = ('group', 'subject', 'sort_order', 'is_active')
    show_change_link = True
    verbose_name = 'Групповой предмет'
    verbose_name_plural = 'Групповые предметы преподавателя'


class GroupSubjectForSubjectInline(SelectedAcademicYearGroupSubjectInlineMixin, ArchivedAcademicYearInlineMixin, admin.TabularInline):
    model = GroupSubject
    fk_name = 'subject'
    form = GroupSubjectForSubjectAdminForm
    formset = GroupSubjectInlineFormSet
    extra = 1
    fields = ('group', 'teacher', 'sort_order', 'is_active')
    show_change_link = True
    # Jazzmin already renders inlines as tab panels. The Bootstrap ``collapse``
    # class hides the panel body after switching to this tab, leaving only an
    # empty area even though the formset and its rows are present in the HTML.
    # Keep this reverse relation expanded so existing groups and the add-row
    # control are always visible from the subject card.
    ordering = ('group__name', 'sort_order', 'pk')
    verbose_name = 'Групповой предмет'
    verbose_name_plural = 'Группы, где есть этот предмет'
    can_delete = True

    def get_formset(self, request, obj=None, **kwargs):
        base_form = self.form
        kwargs['form'] = type(
            'InlineGroupSubjectForSubjectAdminForm',
            (base_form,),
            {'parent_subject': obj},
        )
        return super().get_formset(request, obj, **kwargs)


class StudentSubjectInline(
    SelectedAcademicYearStudentSubjectInlineMixin,
    ArchivedAcademicYearInlineMixin,
    admin.TabularInline,
):
    model = StudentSubject
    form = StudentSubjectAdminForm
    formset = StudentSubjectInlineFormSet
    extra = 0
    autocomplete_fields = ('subject', 'teacher')
    fields = ('subject', 'teacher', 'is_active')
    show_change_link = True
    verbose_name = 'Индивидуальный предмет'
    verbose_name_plural = 'Индивидуальные предметы ученика'

    def get_formset(self, request, obj=None, **kwargs):
        base_form = self.form
        selected_year = get_selected_admin_academic_year(request)
        kwargs['form'] = type(
            'InlineStudentSubjectAdminForm',
            (base_form,),
            {
                'parent_student': obj,
                'parent_academic_year': selected_year,
            },
        )
        return super().get_formset(request, obj, **kwargs)


class StudentSubjectForTeacherInline(SelectedAcademicYearStudentSubjectInlineMixin, ArchivedAcademicYearInlineMixin, admin.TabularInline):
    model = StudentSubject
    form = StudentSubjectAdminForm
    formset = StudentSubjectInlineFormSet
    extra = 0
    fields = ('student', 'subject', 'is_active')
    show_change_link = True
    verbose_name = 'Индивидуальный ученик'
    verbose_name_plural = 'Индивидуальные ученики преподавателя'


class StudentSubjectForSubjectInline(SelectedAcademicYearStudentSubjectInlineMixin, ArchivedAcademicYearInlineMixin, admin.TabularInline):
    model = StudentSubject
    form = StudentSubjectAdminForm
    formset = StudentSubjectInlineFormSet
    extra = 0
    fields = ('student', 'teacher', 'is_active')
    show_change_link = True
    classes = ('collapse',)
    verbose_name = 'Индивидуальный предмет ученика'
    verbose_name_plural = 'Индивидуальные ученики по этому предмету'


class StudentInline(ArchivedAcademicYearInlineMixin, admin.TabularInline):
    model = StudentEnrollment
    fk_name = 'group'
    form = GroupStudentInlineForm
    formset = StudentInlineFormSet
    extra = 0
    fields = ('student_card_link', 'student', 'city_church')
    readonly_fields = ('student_card_link',)
    show_change_link = False
    verbose_name = 'Ученик'
    verbose_name_plural = 'Ученики группы'

    class Media:
        js = ('journal/group_student_inline.js',)

    def get_fields(self, request, obj=None):
        if self.parent_is_archived(request, obj):
            return ('archived_student_name', 'archived_city_church')
        return super().get_fields(request, obj)

    def get_readonly_fields(self, request, obj=None):
        if self.parent_is_archived(request, obj):
            return ('archived_student_name', 'archived_city_church')
        return super().get_readonly_fields(request, obj)

    @admin.display(description='ФИО ученика')
    def archived_student_name(self, obj):
        return admin_change_link(obj.student, label=obj.full_name)

    @admin.display(description='Карточка ученика')
    def student_card_link(self, obj):
        if not obj or not obj.pk:
            return 'После выбора ученика появится ссылка'
        return admin_change_link(obj.student, label=obj.full_name or obj.student.full_name)

    @admin.display(description='Город / Церковь')
    def archived_city_church(self, obj):
        return obj.city_church or '—'


class GradeInline(SelectedAcademicYearGradeInlineMixin, ArchivedAcademicYearInlineMixin, admin.TabularInline):
    model = Grade
    form = GradeAdminForm
    extra = 0
    fields = ('date', 'subject', 'teacher', 'value', 'academic_year', 'comment')
    ordering = ('-date',)
    show_change_link = True
    classes = ('collapse',)

    class Media:
        js = ('journal/grade_dependencies.js',)

    def get_formset(self, request, obj=None, **kwargs):
        selected_year = get_selected_admin_academic_year(request)
        base_form = kwargs.get('form', self.form)

        class SelectedYearInlineGradeForm(base_form):
            def __init__(self, *args, **form_kwargs):
                form_kwargs['fixed_academic_year'] = selected_year
                super().__init__(*args, **form_kwargs)

        kwargs['form'] = SelectedYearInlineGradeForm
        return super().get_formset(request, obj, **kwargs)

class SubjectResultInline(
    SelectedAcademicYearSubjectResultInlineMixin,
    ArchivedAcademicYearInlineMixin,
    admin.TabularInline,
):
    model = SubjectResult
    form = SubjectResultAdminForm
    formset = SubjectResultInlineFormSet
    extra = 0
    autocomplete_fields = ('academic_year', 'subject')
    fields = ('academic_year', 'subject', 'exam_grade', 'final_grade')
    show_change_link = True
    verbose_name = 'Итог'
    verbose_name_plural = 'Итоги по предметам'

    class Media:
        js = ('journal/grade_dependencies.js',)

    def get_formset(self, request, obj=None, **kwargs):
        parent_student = obj
        selected_year = get_selected_admin_academic_year(request)
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
                academic_year = academic_year or selected_year
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


class SelectedAssessmentYearInlineMixin:
    academic_year_lookup = 'academic_year'

    def get_queryset(self, request):
        selected = get_selected_admin_academic_year(request) or AcademicYear.get_active()
        queryset = super().get_queryset(request)
        if selected is None:
            return queryset.none()
        return queryset.filter(**{self.academic_year_lookup: selected})


class ParentContextInlineMixin:
    """Expose a parent object to reusable ModelForms without duplicating forms."""

    def get_form_context(self, request, obj=None):
        return {}

    def get_formset(self, request, obj=None, **kwargs):
        base_form = kwargs.get('form', self.form)
        attributes = {
            '__module__': base_form.__module__,
            **self.get_form_context(request, obj),
        }
        kwargs['form'] = type(
            f'{self.__class__.__name__}Form',
            (base_form,),
            attributes,
        )
        return super().get_formset(request, obj, **kwargs)


class AssessmentItemForGroupInline(
    ParentContextInlineMixin,
    ArchivedAcademicYearInlineMixin,
    admin.TabularInline,
):
    model = AssessmentItem
    form = AssessmentItemAdminForm
    formset = AssessmentItemInlineFormSet
    fk_name = 'group'
    extra = 0
    fields = ('element', 'responsible_teacher', 'sort_order', 'is_required', 'is_active')
    show_change_link = True
    verbose_name = 'Произведение / элемент'
    verbose_name_plural = 'Произведения и ответственные дирижёры'

    def get_form_context(self, request, obj=None):
        return {
            'parent_assessment_group': obj,
            'parent_academic_year': obj.academic_year if obj else None,
        }

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'element', 'element__subject', 'subject', 'academic_year', 'responsible_teacher'
        )


class StudentAssessmentGroupForGroupInline(
    ParentContextInlineMixin,
    ArchivedAcademicYearInlineMixin,
    admin.TabularInline,
):
    model = StudentAssessmentGroup
    form = StudentAssessmentGroupAdminForm
    fk_name = 'assessment_group'
    extra = 0
    fields = ('student', 'is_active')
    show_change_link = True
    verbose_name = 'Назначение ученику'
    verbose_name_plural = 'Ученики группы произведений'

    def get_form_context(self, request, obj=None):
        return {
            'parent_assessment_group': obj,
            'parent_academic_year': obj.academic_year if obj else None,
        }

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'student', 'academic_year', 'enrollment', 'enrollment__group'
        )


class FinalGradeRuleForGroupInline(
    ParentContextInlineMixin,
    ArchivedAcademicYearInlineMixin,
    admin.TabularInline,
):
    model = FinalGradeRule
    form = FinalGradeRuleAdminForm
    fk_name = 'assessment_group'
    extra = 0
    fields = ('rule_type', 'passed_count', 'condition_value', 'grade', 'priority', 'is_active')
    show_change_link = True
    verbose_name = 'Правило итоговой оценки'
    verbose_name_plural = 'Правила итоговой оценки этой группы'

    def get_form_context(self, request, obj=None):
        return {
            'parent_subject': obj.subject if obj else None,
            'parent_academic_year': obj.academic_year if obj else None,
        }


class AssessmentResultForItemInline(
    ParentContextInlineMixin,
    ArchivedAcademicYearInlineMixin,
    admin.TabularInline,
):
    model = AssessmentResult
    form = AssessmentResultAdminForm
    fk_name = 'item'
    extra = 0
    fields = ('enrollment', 'status', 'assessed_by', 'assessed_at', 'comment')
    show_change_link = True
    verbose_name = 'Результат ученика'
    verbose_name_plural = 'Результаты учеников по произведению'

    def get_form_context(self, request, obj=None):
        return {
            'parent_assessment_item': obj,
            'parent_academic_year': obj.academic_year if obj else None,
        }

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'enrollment', 'enrollment__student', 'enrollment__group', 'assessed_by'
        )


class AssessmentGroupForSubjectInline(
    ParentContextInlineMixin,
    SelectedAssessmentYearInlineMixin,
    ArchivedAcademicYearInlineMixin,
    admin.TabularInline,
):
    model = AssessmentGroup
    form = AssessmentGroupForSubjectAdminForm
    fk_name = 'subject'
    extra = 0
    fields = ('name', 'academic_year', 'sort_order', 'is_active')
    show_change_link = True
    verbose_name = 'Группа произведений'
    verbose_name_plural = 'Группы произведений выбранного учебного года'

    def get_form_context(self, request, obj=None):
        return {
            'parent_subject': obj,
            'parent_academic_year': get_selected_admin_academic_year(request) or AcademicYear.get_active(),
        }


class FinalGradeRuleForSubjectInline(
    ParentContextInlineMixin,
    SelectedAssessmentYearInlineMixin,
    ArchivedAcademicYearInlineMixin,
    admin.TabularInline,
):
    model = FinalGradeRule
    form = FinalGradeRuleAdminForm
    fk_name = 'subject'
    extra = 0
    fields = (
        'academic_year', 'assessment_group', 'rule_type', 'passed_count',
        'condition_value', 'grade', 'priority', 'is_active',
    )
    show_change_link = True
    verbose_name = 'Правило итоговой оценки'
    verbose_name_plural = 'Правила автоматической итоговой оценки'

    def get_form_context(self, request, obj=None):
        return {
            'parent_subject': obj,
            'parent_academic_year': get_selected_admin_academic_year(request) or AcademicYear.get_active(),
        }


class AssessmentItemForTeacherInline(
    ParentContextInlineMixin,
    SelectedAssessmentYearInlineMixin,
    ArchivedAcademicYearInlineMixin,
    admin.TabularInline,
):
    model = AssessmentItem
    form = AssessmentItemAdminForm
    formset = AssessmentItemInlineFormSet
    fk_name = 'responsible_teacher'
    academic_year_lookup = 'academic_year'
    extra = 0
    fields = ('element', 'group', 'subject', 'academic_year', 'sort_order', 'is_required', 'is_active')
    show_change_link = True
    verbose_name = 'Произведение под руководством преподавателя'
    verbose_name_plural = 'Произведения, где преподаватель назначен дирижёром'

    def get_form_context(self, request, obj=None):
        return {
            'parent_academic_year': get_selected_admin_academic_year(request) or AcademicYear.get_active(),
        }

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('element', 'group', 'subject', 'academic_year')


class AcademicYearParentInlineMixin:
    """Scope archive permissions to the year being edited, not the UI selection."""

    def selected_academic_year(self, request):
        return None


class StudyGroupForAcademicYearInline(
    AcademicYearParentInlineMixin,
    ArchivedAcademicYearInlineMixin,
    admin.TabularInline,
):
    model = StudyGroup
    fk_name = 'academic_year'
    extra = 0
    fields = ('name', 'is_active')
    show_change_link = True
    verbose_name = 'Учебная группа'
    verbose_name_plural = 'Учебные группы'


class TeacherEnrollmentForAcademicYearInline(
    AcademicYearParentInlineMixin,
    ArchivedAcademicYearInlineMixin,
    admin.TabularInline,
):
    model = TeacherEnrollment
    fk_name = 'academic_year'
    extra = 0
    fields = ('teacher', 'is_active')
    autocomplete_fields = ('teacher',)
    show_change_link = False
    verbose_name = 'Участие преподавателя'
    verbose_name_plural = 'Преподаватели учебного года'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('teacher')


class UserAcademicYearMembershipForAcademicYearInline(
    AcademicYearParentInlineMixin,
    ArchivedAcademicYearInlineMixin,
    admin.TabularInline,
):
    model = UserAcademicYearMembership
    fk_name = 'academic_year'
    extra = 0
    fields = ('user', 'is_active')
    autocomplete_fields = ('user',)
    verbose_name = 'Участие пользователя'
    verbose_name_plural = 'Все пользователи учебного года'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user')


class AssessmentGroupForAcademicYearInline(
    AcademicYearParentInlineMixin,
    ArchivedAcademicYearInlineMixin,
    admin.TabularInline,
):
    model = AssessmentGroup
    form = AssessmentGroupAdminForm
    fk_name = 'academic_year'
    extra = 0
    fields = ('name', 'subject', 'sort_order', 'is_active')
    show_change_link = True
    verbose_name = 'Группа произведений'
    verbose_name_plural = 'Группы произведений'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('subject')


# -----------------------------------------------------------------------------
# Справочники
# -----------------------------------------------------------------------------


@admin.register(AcademicYear)
class AcademicYearAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Учебные годы задают периоды обучения. Активный учебный год используется по умолчанию '
        'для групп, заявок и дат оценок.'
    )
    inlines = (
        StudyGroupForAcademicYearInline,
        TeacherEnrollmentForAcademicYearInline,
        UserAcademicYearMembershipForAcademicYearInline,
        AssessmentGroupForAcademicYearInline,
    )
    list_display = (
        'name', 'starts_on', 'ends_on', 'is_active', 'groups_count',
        'students_count', 'teachers_count', 'assessment_groups_count',
    )
    list_filter = ('is_active',)
    search_fields = ('name',)
    ordering = ('-starts_on',)
    list_per_page = 30
    readonly_fields = ('is_active',)
    fieldsets = (
        ('Учебный год', {
            'fields': ('name', 'starts_on', 'ends_on', 'is_active'),
            'description': 'Активным может быть только один учебный год.',
        }),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _groups_count=Count('study_groups', distinct=True),
            _students_count=Count(
                'student_enrollments',
                filter=Q(student_enrollments__is_active=True),
                distinct=True,
            ),
            _teachers_count=Count(
                'teacher_enrollments',
                filter=Q(teacher_enrollments__is_active=True),
                distinct=True,
            ),
            _assessment_groups_count=Count(
                'assessment_groups',
                filter=Q(assessment_groups__is_active=True),
                distinct=True,
            ),
        )

    def delete_queryset(self, request, queryset):
        deleted = 0
        for academic_year in queryset.order_by('-starts_on', '-ends_on', '-pk'):
            academic_year.delete()
            deleted += 1
        if deleted:
            self.message_user(request, f'Удалено учебных лет: {deleted}.')

    @admin.display(description='Групп', ordering='_groups_count')
    def groups_count(self, obj):
        return format_html(
            '<a href="{}">{}</a>',
            admin_changelist_url('studygroup', {'academic_year__id__exact': obj.pk}),
            obj._groups_count,
        )

    @admin.display(description='Учеников', ordering='_students_count')
    def students_count(self, obj):
        return obj._students_count

    @admin.display(description='Преподавателей', ordering='_teachers_count')
    def teachers_count(self, obj):
        return obj._teachers_count

    @admin.display(description='Групп произведений', ordering='_assessment_groups_count')
    def assessment_groups_count(self, obj):
        return format_html(
            '<a href="{}">{}</a>',
            admin_changelist_url('assessmentgroup', {'academic_year__id__exact': obj.pk}),
            obj._assessment_groups_count,
        )


class OrchestraPartInline(ArchivedAcademicYearInlineMixin, admin.TabularInline):
    model = OrchestraPart
    fields = ('name', 'is_active')
    extra = 0
    ordering = ('name',)


@admin.register(Instrument)
class InstrumentAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Справочник инструментов и партий. Значение выбирается в карточке ученика '
        'и используется для поиска и отчетов.'
    )
    list_display = ('name', 'orchestra_parts_count', 'students_count')
    search_fields = ('name',)
    inlines = (OrchestraPartInline,)
    ordering = ('name',)
    list_per_page = 50

    def get_queryset(self, request):
        academic_year = self.selected_academic_year(request)
        qs = super().get_queryset(request)
        return qs.annotate(
            _orchestra_parts_count=Count(
                'orchestra_parts',
                filter=Q(orchestra_parts__is_active=True),
                distinct=True,
            ),
            _students_count=Count(
                'students',
                filter=Q(students__enrollments__academic_year=academic_year),
                distinct=True,
            ),
        )

    @admin.display(description='Партий')
    def orchestra_parts_count(self, obj):
        return obj._orchestra_parts_count

    @admin.display(description='Учеников')
    def students_count(self, obj):
        return format_html(
            '<a href="{}">{}</a>',
            admin_changelist_url('student', {'instrument__id__exact': obj.pk}),
            obj._students_count,
        )


@admin.register(OrchestraPart)
class OrchestraPartAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Партии оркестра привязаны к инструментам. В карточке ученика отображаются '
        'только активные партии выбранного инструмента. Используемую партию '
        'деактивируйте, чтобы сохранить историю.'
    )
    list_display = ('name', 'instrument', 'is_active', 'students_count')
    list_filter = ('instrument', 'is_active')
    search_fields = ('name', 'instrument__name')
    autocomplete_fields = ('instrument',)
    ordering = ('instrument__name', 'name')
    list_select_related = ('instrument',)
    list_per_page = 50

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _students_count=Count('students', distinct=True),
        )

    @admin.display(description='Учеников')
    def students_count(self, obj):
        return obj._students_count


@admin.register(Subject)
class SubjectAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Справочник предметов. Поле Индивидуальный предмет определяет, куда можно назначать предмет: '
        'в группу или конкретному ученику.'
    )
    list_display = (
        'name',
        'assessment_mode',
        'final_grade_type',
        'is_specialty',
        'is_active',
        'groups_count',
        'teachers_count',
        'individual_students_count',
    )
    list_filter = ('assessment_mode', 'final_grade_type', 'is_specialty', 'is_active')
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
        AssessmentGroupForSubjectInline,
        FinalGradeRuleForSubjectInline,
    )
    ordering = ('name',)
    list_per_page = 50
    fieldsets = (
        ('Предмет', {
            'fields': ('name', 'assessment_mode', 'final_grade_type', 'is_specialty', 'is_active'),
            'description': (
                'Групповые предметы назначаются группе. Индивидуальные предметы '
                'назначаются конкретному ученику. Специальный режим включается явно и '
                'не зависит от названия предмета.'
            ),
        }),
    )

    def get_inlines(self, request, obj=None):
        if obj is None:
            return ()
        inlines = []
        # The current classification controls where new assignments belong,
        # but it must never hide related rows that already exist in the
        # database (including historical/legacy data).
        if not obj.is_specialty or obj.group_subjects.exists():
            inlines.append(GroupSubjectForSubjectInline)
        if obj.is_specialty or obj.individual_students.exists():
            inlines.append(StudentSubjectForSubjectInline)
        has_assessment_data = (
            obj.assessment_groups.exists()
            or obj.final_grade_rules.exists()
        )
        if obj.uses_element_assessment or has_assessment_data:
            inlines.extend((
                AssessmentGroupForSubjectInline,
                FinalGradeRuleForSubjectInline,
            ))
        return tuple(inlines)

    def get_queryset(self, request):
        academic_year = self.selected_academic_year(request)
        qs = super().get_queryset(request)
        return qs.annotate(
            _groups_count=Count(
                'group_subjects__group',
                filter=Q(
                    group_subjects__is_active=True,
                    group_subjects__group__academic_year=academic_year,
                ),
                distinct=True,
            ),
            _teachers_count=Count(
                'group_subjects__teacher',
                filter=Q(
                    group_subjects__is_active=True,
                    group_subjects__group__academic_year=academic_year,
                ),
                distinct=True,
            ),
            _individual_students_count=Count(
                'individual_students__student',
                filter=Q(
                    individual_students__is_active=True,
                    individual_students__academic_year=academic_year,
                ),
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
    academic_year_lookup = 'academic_year'
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
        'student_enrollments__full_name',
        'group_subjects__subject__name',
        'group_subjects__subject_name_snapshot',
        'group_subjects__teacher__full_name',
        'group_subjects__teacher_name_snapshot',
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
            _students_count=Count(
                'student_enrollments',
                filter=Q(student_enrollments__is_active=True),
                distinct=True,
            ),
        ).prefetch_related(
            'group_subjects__subject',
            'group_subjects__teacher',
        )

    @admin.display(description='Учеников')
    def students_count_display(self, obj):
        if object_is_in_archived_academic_year(obj):
            return obj._students_count
        return format_html(
            '<a href="{}">{}</a>',
            admin_changelist_url('student', {'selected_year_group': obj.pk}),
            obj._students_count,
        )

    @admin.display(description='Предметы')
    def subjects_display_short(self, obj):
        archived = not obj.academic_year.is_active
        subjects = [
            (
                item.subject_name_snapshot or item.subject.name
                if archived
                else item.subject.name
            )
            for item in obj.group_subjects.all()
            if item.is_active and item.subject_id
        ]
        return truncate_text(', '.join(subjects))

    @admin.display(description='Преподаватели')
    def teachers_display_short(self, obj):
        archived = not obj.academic_year.is_active
        pairs = [
            (
                f'{item.subject_name_snapshot or item.subject.name}: '
                f'{item.teacher_name_snapshot or item.teacher.full_name}'
                if archived
                else f'{item.subject.name}: {item.teacher.full_name}'
            )
            for item in obj.group_subjects.all()
            if item.is_active and item.subject_id and item.teacher_id
        ]
        return truncate_text(', '.join(pairs), length=120)

    @admin.display(description='Журнал')
    def journal_link(self, obj):
        return format_html(
            '<a href="{}">Открыть</a>',
            journal_url({'academic_year': obj.academic_year_id, 'group': obj.pk}),
        )


@admin.register(Teacher)
class TeacherAdmin(SharedProfileAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
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
        'selected_year_active_display',
        'group_subjects_count',
        'individual_students_count_display',
        'group_subjects_short',
    )
    list_filter = (SelectedYearTeacherActiveFilter,)
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
        'group_subjects__subject_name_snapshot',
        'group_subjects__teacher_name_snapshot',
        'individual_subjects__student__full_name',
    )
    autocomplete_fields = ('user',)
    inlines = (
        TeacherEnrollmentHistoryInline,
        GroupSubjectForTeacherInline,
        StudentSubjectForTeacherInline,
        AssessmentItemForTeacherInline,
    )
    ordering = ('full_name',)
    list_select_related = ('user',)
    list_per_page = 30
    show_full_result_count = False
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
        academic_year = self.selected_academic_year(request)
        qs = super().get_queryset(request)
        if academic_year is None:
            return qs.none()
        return (
            qs.filter(academic_year_memberships__academic_year=academic_year)
            .annotate(
                _group_subjects_count=Count(
                    'group_subjects',
                    filter=Q(
                        group_subjects__is_active=True,
                        group_subjects__group__academic_year=academic_year,
                    ),
                    distinct=True,
                ),
                _individual_students_count=Count(
                    'individual_subjects__student',
                    filter=Q(
                        individual_subjects__is_active=True,
                        individual_subjects__academic_year=academic_year,
                    ),
                    distinct=True,
                ),
            )
            .prefetch_related(
                Prefetch(
                    'academic_year_memberships',
                    queryset=TeacherEnrollment.objects.filter(academic_year=academic_year),
                    to_attr='journal_year_memberships',
                ),
                Prefetch(
                    'group_subjects',
                    queryset=GroupSubject.objects.filter(
                        group__academic_year=academic_year,
                    ).select_related('group', 'group__academic_year', 'subject'),
                    to_attr='selected_year_group_subjects',
                ),
            )
            .distinct()
        )

    def get_fieldsets(self, request, obj=None):
        if not self.selected_year_is_archived(request):
            return super().get_fieldsets(request, obj)
        return (
            ('Преподаватель', {
                'fields': (
                    'full_name',
                    'birth_date',
                    'age_display',
                    'selected_year_active_display',
                ),
                'description': (
                    'Профиль можно уточнять. Участие и назначения архивного года '
                    'доступны только для просмотра.'
                ),
            }),
            ('Контакты', {'fields': ('phone', 'email', 'comments')}),
            ('Аккаунт', {'fields': ('user',), 'classes': ('collapse',)}),
        )

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if self.selected_year_is_archived(request):
            fields.append('selected_year_active_display')
        return tuple(dict.fromkeys(fields))

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
        if obj.user_id:
            teacher_group, _created = AuthGroup.objects.get_or_create(name='Преподаватель')
            obj.user.groups.add(teacher_group)

        super().save_model(request, obj, form, change)

        if obj.user_id and (
            temporary_password is not None
            or user_has_temporary_credential(obj.user)
        ):
            ensure_temporary_credential_for_user(
                obj.user,
                password=temporary_password,
                user_was_created=temporary_password is not None,
            )

    @admin.display(description='Активен в выбранном году', boolean=True)
    def selected_year_active_display(self, obj):
        memberships = getattr(obj, 'journal_year_memberships', ())
        return bool(memberships and memberships[0].is_active)

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
            (
                f'{item.group.name}: {item.subject_name_snapshot or item.subject.name}'
                if not item.group.academic_year.is_active
                else f'{item.group.name}: {item.subject.name}'
            )
            for item in getattr(obj, 'selected_year_group_subjects', ())
            if item.is_active and item.group_id and item.subject_id
        ]
        return truncate_text(', '.join(items), length=120)


class StudentAssessmentGroupInline(admin.TabularInline):
    model = StudentAssessmentGroup
    form = StudentAssessmentGroupAdminForm
    fk_name = 'student'
    extra = 0
    autocomplete_fields = ('assessment_group',)
    fields = ('assessment_group', 'is_active')
    show_change_link = True
    verbose_name = 'Группа произведений'
    verbose_name_plural = 'Группы произведений ученика'

    def get_formset(self, request, obj=None, **kwargs):
        base_form = self.form
        selected_year = get_selected_admin_academic_year(request) or AcademicYear.get_active()
        kwargs['form'] = type(
            'InlineStudentAssessmentGroupAdminForm',
            (base_form,),
            {
                'parent_student': obj,
                'parent_academic_year': selected_year,
            },
        )
        return super().get_formset(request, obj, **kwargs)

    def get_queryset(self, request):
        academic_year = get_selected_admin_academic_year(request) or AcademicYear.get_active()
        queryset = super().get_queryset(request).select_related(
            'assessment_group', 'assessment_group__subject', 'academic_year'
        )
        return queryset.filter(academic_year=academic_year) if academic_year else queryset.none()


@admin.register(Student)
class StudentAdmin(SharedProfileAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Карточки учеников: группа, инструмент, контакты, индивидуальные предметы и итоги. '
        'Обычные оценки удобнее вносить через журнал.'
    )
    form = StudentAdminForm
    list_display = (
        'full_name',
        'selected_year_group_display',
        'instrument_display',
        'age_display',
        'student_phone',
        'city_church',
        'specialty_teacher_display',
        'specialty_subject_display',
        'user_link',
        'selected_year_active_display',
    )
    list_filter = (
        SelectedYearStudentActiveFilter,
        SelectedYearStudentGroupFilter,
        'instrument',
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
        'custom_instrument',
        'orchestra_part__name',
        'individual_subjects__teacher__full_name',
        'individual_subjects__teacher_name_snapshot',
        'individual_subjects__subject__name',
        'individual_subjects__subject_name_snapshot',
    )
    autocomplete_fields = ('user', 'group')
    inlines = (
        StudentEnrollmentHistoryInline,
        StudentSubjectInline,
        StudentAssessmentGroupInline,
        SubjectResultInline,
    )
    ordering = ('full_name',)
    list_select_related = (
        'user',
        'group',
        'group__academic_year',
        'instrument',
        'orchestra_part',
    )
    list_per_page = 40
    show_full_result_count = False
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
                'custom_instrument',
                'orchestra_part',
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

    class Media:
        js = ('journal/orchestra_part_dependencies_v5.js',)

    def get_queryset(self, request):
        academic_year = self.selected_academic_year(request)
        qs = super().get_queryset(request)
        if academic_year is None:
            return qs.none()
        return (
            qs.filter(enrollments__academic_year=academic_year)
            .prefetch_related(
                Prefetch(
                    'enrollments',
                    queryset=StudentEnrollment.objects.filter(
                        academic_year=academic_year,
                    ).select_related('group', 'group__academic_year', 'academic_year'),
                    to_attr='journal_enrollments',
                ),
                Prefetch(
                    'individual_subjects',
                    queryset=(
                        StudentSubject.objects
                        .filter(
                            subject__is_specialty=True,
                            is_active=True,
                            academic_year=academic_year,
                        )
                        .select_related('subject', 'teacher', 'academic_year')
                        .order_by('subject__name')
                    ),
                    to_attr='selected_year_specialty_assignments',
                ),
                Prefetch(
                    'course_applications',
                    queryset=CourseApplication.objects.filter(
                        academic_year=academic_year,
                    ).select_related('academic_year').order_by('-registration_date', '-pk'),
                    to_attr='selected_year_course_applications',
                ),
            )
            .distinct()
        )

    def get_fieldsets(self, request, obj=None):
        if not self.selected_year_is_archived(request):
            return super().get_fieldsets(request, obj)
        return (
            ('Ученик', {
                'fields': (
                    'full_name',
                    'gender',
                    'birth_date',
                    'age_display',
                    'selected_year_group_display',
                    'instrument_display',
                    'selected_year_active_display',
                ),
                'description': (
                    'Профиль можно уточнять. Группа, активность, предметы и результаты '
                    'архивного года доступны только для просмотра.'
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

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if self.selected_year_is_archived(request):
            fields.extend(('selected_year_group_display', 'selected_year_active_display'))
        return tuple(dict.fromkeys(fields))

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
            )
        if obj.user_id:
            student_group, _created = AuthGroup.objects.get_or_create(name='Ученик')
            obj.user.groups.add(student_group)

        super().save_model(request, obj, form, change)

        if obj.user_id and (
            temporary_password is not None
            or user_has_temporary_credential(obj.user)
        ):
            ensure_temporary_credential_for_user(
                obj.user,
                password=temporary_password,
                user_was_created=temporary_password is not None,
            )

    @admin.display(description='Группа выбранного года')
    def selected_year_group_display(self, obj):
        enrollments = getattr(obj, 'journal_enrollments', ())
        enrollment = enrollments[0] if enrollments else None
        return admin_change_link(enrollment.group) if enrollment and enrollment.group_id else '—'

    @admin.display(description='Активен в выбранном году', boolean=True)
    def selected_year_active_display(self, obj):
        enrollments = getattr(obj, 'journal_enrollments', ())
        enrollment = enrollments[0] if enrollments else None
        return bool(enrollment and enrollment.is_active)

    @admin.display(description='Пользователь')
    def user_link(self, obj):
        return admin_change_link(obj.user)

    @admin.display(description='Инструмент', ordering='instrument__name')
    def instrument_display(self, obj):
        return obj.instrument_display

    @admin.display(description='Возраст')
    def age_display(self, obj):
        return obj.age if obj and obj.age is not None else '—'

    @admin.display(description='Заявка на курсы')
    def course_application_link(self, obj):
        if not obj:
            return '—'
        applications = getattr(obj, 'selected_year_course_applications', None)
        application = applications[0] if applications else None
        if applications is None:
            application = (
                obj.course_applications
                .select_related('academic_year')
                .order_by('-registration_date', '-pk')
                .first()
            )
        label = (
            f'{application.academic_year}: {application.full_name}'
            if application is not None and application.academic_year_id
            else None
        )
        return admin_change_link(application, label=label)

    @admin.display(description='Преподаватель по специальности')
    def specialty_teacher_display(self, obj):
        assignments = getattr(obj, 'selected_year_specialty_assignments', ())
        if not assignments:
            return '—'
        assignment = assignments[0]
        if assignment.academic_year.is_active:
            return assignment.teacher
        return assignment.teacher_name_snapshot or assignment.teacher.full_name

    @admin.display(description='Предмет специальности')
    def specialty_subject_display(self, obj):
        assignments = getattr(obj, 'selected_year_specialty_assignments', ())
        if not assignments:
            return '—'
        assignment = assignments[0]
        if assignment.academic_year.is_active:
            return assignment.subject
        return assignment.subject_name_snapshot or assignment.subject.name


@admin.register(TeacherSubject)
class TeacherSubjectAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
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

    def get_queryset(self, request):
        academic_year = self.selected_academic_year(request)
        queryset = super().get_queryset(request)
        if academic_year is None:
            return queryset.none()
        return queryset.filter(
            teacher__academic_year_memberships__academic_year=academic_year,
        ).distinct()


@admin.register(GroupSubject)
class GroupSubjectAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    academic_year_lookup = 'group__academic_year'
    changelist_description = (
        'Групповые предметы связывают группу, предмет и преподавателя. '
        'Сюда нельзя назначать индивидуальные предметы.'
    )
    form = GroupSubjectAdminForm
    list_display = (
        'group',
        'subject_name_display',
        'teacher_name_display',
        'sort_order',
        'is_active',
    )
    list_filter = ('is_active', 'group__academic_year', 'group', 'subject', 'teacher')
    search_fields = ('group__name', 'subject__name', 'teacher__full_name')
    list_select_related = ('group', 'group__academic_year', 'subject', 'teacher')
    ordering = ('group__academic_year__name', 'group__name', 'sort_order', 'subject__name')
    list_per_page = 50
    show_full_result_count = False
    fieldsets = (
        ('Групповой предмет', {
            'fields': ('group', 'subject', 'teacher', 'sort_order', 'is_active'),
            'description': (
                'Связь можно редактировать здесь, в карточке группы, преподавателя или предмета. '
                'При смене преподавателя связанные оценки по этому назначению обновляются автоматически.'
            ),
        }),
    )

    @admin.display(description='Предмет', ordering='subject__name')
    def subject_name_display(self, obj):
        if obj.group.academic_year.is_active:
            return obj.subject.name
        return obj.subject_name_snapshot or obj.subject.name

    @admin.display(description='Преподаватель', ordering='teacher__full_name')
    def teacher_name_display(self, obj):
        if obj.group.academic_year.is_active:
            return obj.teacher.full_name
        return obj.teacher_name_snapshot or obj.teacher.full_name


@admin.register(StudentSubject)
class StudentSubjectAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    academic_year_lookup = 'academic_year'
    changelist_description = (
        'Индивидуальные предметы связывают конкретного ученика, предмет и преподавателя. '
        'Сюда нельзя назначать групповые предметы.'
    )
    form = StudentSubjectAdminForm
    list_display = (
        'student_name_display',
        'student_group_display',
        'subject_name_display',
        'teacher_name_display',
        'academic_year',
        'is_active',
    )
    list_filter = ('academic_year', 'is_active', 'subject', 'teacher')
    search_fields = ('student__full_name', 'subject__name', 'teacher__full_name', 'subject_name_snapshot')
    list_select_related = ('student', 'subject', 'teacher', 'academic_year')
    ordering = ('student__full_name', 'subject__name')
    list_per_page = 50
    show_full_result_count = False
    fieldsets = (
        ('Индивидуальный предмет ученика', {
            'fields': ('student', 'subject', 'teacher', 'is_active'),
            'description': (
                'Связь можно редактировать здесь, в карточке ученика, преподавателя или предмета. '
                'При смене преподавателя связанные оценки по этому назначению обновляются автоматически.'
            ),
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related(
            Prefetch(
                'student__enrollments',
                queryset=StudentEnrollment.objects.select_related(
                    'academic_year',
                    'group',
                    'group__academic_year',
                ),
                to_attr='journal_enrollments',
            ),
        )

    @admin.display(description='Ученик', ordering='student__full_name')
    def student_name_display(self, obj):
        if obj.academic_year.is_active:
            return obj.student.full_name
        enrollment = obj.student.enrollment_for_year(obj.academic_year)
        return enrollment.full_name if enrollment is not None else obj.student.full_name

    @admin.display(description='Группа')
    def student_group_display(self, obj):
        if obj and obj.student_id:
            enrollment = obj.student.enrollment_for_year(obj.academic_year)
            return enrollment.group if enrollment is not None and enrollment.group_id else '—'
        return '—'

    @admin.display(description='Предмет', ordering='subject__name')
    def subject_name_display(self, obj):
        if obj.academic_year.is_active:
            return obj.subject.name
        return obj.subject_name_snapshot or obj.subject.name

    @admin.display(description='Преподаватель', ordering='teacher__full_name')
    def teacher_name_display(self, obj):
        if obj.academic_year.is_active:
            return obj.teacher.full_name
        return obj.teacher_name_snapshot or obj.teacher.full_name


@admin.register(Grade)
class GradeAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    academic_year_lookup = 'academic_year'
    changelist_description = (
        'Оценки за занятия. В форме доступны только ученики, предметы и преподаватели, '
        'которые состыкованы через групповые или индивидуальные назначения.'
    )
    form = GradeAdminForm
    list_display = (
        'date',
        'student_name_display',
        'student_group_display',
        'subject_name_display',
        'teacher_name_display',
        'value',
        'academic_year',
        'source_type_display',
    )
    list_filter = (
        'academic_year',
        'date',
        'subject',
        'teacher',
        'enrollment__group',
    )
    search_fields = (
        'student__full_name',
        'student_name_snapshot',
        'group_name_snapshot',
        'subject__name',
        'subject_name_snapshot',
        'teacher__full_name',
        'teacher_name_snapshot',
        'comment',
    )
    readonly_fields = ('source_type_display',)
    date_hierarchy = 'date'
    list_select_related = ('student', 'enrollment', 'enrollment__group', 'subject', 'teacher', 'academic_year')
    ordering = ('-date', 'student__full_name')
    list_per_page = 50
    show_full_result_count = False

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
        js = (
            'journal/grade_dependencies.js',
        )

    def get_form(self, request, obj=None, change=False, **kwargs):
        base_form = super().get_form(request, obj=obj, change=change, **kwargs)
        selected_year = get_selected_admin_academic_year(request)

        class SelectedYearGradeAdminForm(base_form):
            def __init__(self, *args, **form_kwargs):
                form_kwargs['fixed_academic_year'] = selected_year
                super().__init__(*args, **form_kwargs)

        return SelectedYearGradeAdminForm

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _is_group_subject=Exists(
                GroupSubject.objects.filter(
                    group_id=OuterRef('enrollment__group_id'),
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
                    academic_year_id=OuterRef('academic_year_id'),
                    is_active=True,
                ),
            ),
        )

    @admin.display(description='Группа')
    def student_group_display(self, obj):
        if obj and obj.enrollment_id:
            return obj.enrollment.group or obj.group_name_snapshot or '—'
        return '—'

    @admin.display(description='Ученик', ordering='student__full_name')
    def student_name_display(self, obj):
        if obj.academic_year.is_active:
            return obj.enrollment.full_name if obj.enrollment_id else obj.student.full_name
        return obj.student_name_snapshot or obj.student.full_name

    @admin.display(description='Предмет', ordering='subject__name')
    def subject_name_display(self, obj):
        if obj.academic_year.is_active:
            return obj.subject.name
        return obj.subject_name_snapshot or obj.subject.name

    @admin.display(description='Преподаватель', ordering='teacher__full_name')
    def teacher_name_display(self, obj):
        if obj.academic_year.is_active:
            return obj.teacher.full_name
        return obj.teacher_name_snapshot or obj.teacher.full_name

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
    academic_year_lookup = 'academic_year'
    changelist_description = (
        'Итоги по предметам за учебный год: экзамен и итоговая оценка. '
        'Допустимые значения зависят от типа итоговой оценки предмета.'
    )
    form = SubjectResultAdminForm
    list_display = (
        'student_name_display',
        'student_group_display',
        'subject_name_display',
        'academic_year',
        'exam_grade',
        'final_grade',
    )
    list_filter = ('academic_year', 'subject', 'enrollment__group')
    search_fields = (
        'student__full_name',
        'student_name_snapshot',
        'group_name_snapshot',
        'subject__name',
        'subject_name_snapshot',
    )
    list_select_related = ('student', 'enrollment', 'enrollment__group', 'subject', 'academic_year')
    ordering = ('academic_year__name', 'student__full_name', 'subject__name')
    list_per_page = 50
    show_full_result_count = False
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
        if obj and obj.enrollment_id:
            return obj.enrollment.group or obj.group_name_snapshot or '—'
        return '—'

    @admin.display(description='Ученик', ordering='student__full_name')
    def student_name_display(self, obj):
        if obj.academic_year.is_active:
            return obj.enrollment.full_name if obj.enrollment_id else obj.student.full_name
        return obj.student_name_snapshot or obj.student.full_name

    @admin.display(description='Предмет', ordering='subject__name')
    def subject_name_display(self, obj):
        if obj.academic_year.is_active:
            return obj.subject.name
        return obj.subject_name_snapshot or obj.subject.name

    def get_related_record_label(self, obj):
        exam_grade = obj.exam_grade or '—'
        final_grade = obj.final_grade or '—'
        return (
            f'{obj} | Экзамен: {exam_grade} | '
            f'Итоговая оценка: {final_grade}'
        )


@admin.register(ErrorLog)
class ErrorLogAdmin(admin.ModelAdmin):
    list_display = (
        'created_at',
        'level',
        'status_code',
        'request_id',
        'method',
        'path',
        'user_label',
    )
    list_filter = ('level', 'status_code', 'created_at')
    search_fields = (
        'request_id',
        'message',
        'exception',
        'path',
        'user_label',
        'logger_name',
    )
    readonly_fields = (
        'created_at',
        'level',
        'logger_name',
        'message',
        'exception',
        'request_id',
        'status_code',
        'method',
        'path',
        'user_label',
        'metadata',
    )
    ordering = ('-created_at', '-pk')
    list_per_page = 100
    show_full_result_count = False

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return bool(request.user.is_superuser)

    def has_delete_permission(self, request, obj=None):
        return bool(request.user.is_superuser)


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
    academic_year_lookup = 'academic_year'
    changelist_description = (
        'Заявки с публичной регистрации. Подтвержденная заявка создает ученика, пользователя '
        'и временный пароль; отклоненная заявка удаляет только неиспользуемые связанные записи.'
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
        'instrument_display',
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
        'instrument_reference__name',
        'custom_instrument',
        'orchestra_part__name',
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
    list_select_related = (
        'student',
        'user',
        'academic_year',
        'instrument_reference',
        'orchestra_part',
    )
    actions = ('confirm_applications', 'reject_applications')
    list_per_page = 40
    show_full_result_count = False

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
                'и временный пароль. При отклонении удаляются только записи, '
                'не используемые другими подтвержденными заявками.'
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
                'instrument_reference',
                'custom_instrument',
                'orchestra_part',
                'music_education',
                'student_phone',
                'parent_contacts',
                'comments',
            )
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request)

    def delete_queryset(self, request, queryset):
        deleted = 0
        for application in queryset.select_related('academic_year'):
            application.delete()
            deleted += 1
        if deleted:
            self.message_user(request, f'Удалено заявок: {deleted}.')

    @admin.display(description='ФИО', ordering='last_name')
    def full_name_display(self, obj):
        return obj.full_name

    @admin.display(description='Инструмент', ordering='instrument')
    def instrument_display(self, obj):
        return obj.instrument

    @admin.display(description='Возраст на начало курсов')
    def age_display(self, obj):
        if not obj.birth_date:
            return '—'
        return obj.age

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
        self.message_user(
            request,
            (
                f'Отклонено заявок: {processed}. Неиспользуемые связанные записи '
                'удалены; общие аккаунты и зачисления сохранены.'
            ),
        )
        if skipped:
            self.message_user(request, f'Архивные заявки пропущены: {skipped}.', level='ERROR')


@admin.register(TemporaryCredential)
class TemporaryCredentialAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
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
    show_full_result_count = False
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
        academic_year = self.selected_academic_year(request)
        queryset = (
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
        return filter_temporary_credentials_for_year(queryset, academic_year)

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

        group_names = {group.name for group in user.groups.all()}
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
class CourseRegistrationSettingsAdmin(
    ArchivedAcademicYearAdminMixin,
    JournalAdminDescriptionMixin,
    admin.ModelAdmin,
):
    academic_year_lookup = 'academic_year'
    changelist_description = (
        'Настройки публичной регистрации для каждого учебного года: ручное управление, '
        'автоматический лимит, минимальный возраст и ссылка на Telegram-группу.'
    )
    form = CourseRegistrationSettingsForm
    list_display = (
        'academic_year',
        'telegram_group_url',
        'minimum_registration_age',
        'registration_mode',
        'application_limit',
        'registration_status_display',
        'registered_applications_display',
        'active_academic_year_display',
        'updated_at',
    )
    readonly_fields = (
        'academic_year',
        'registration_status_display',
        'registered_applications_display',
        'active_academic_year_display',
        'updated_at',
    )
    fieldsets = (
        ('Регистрация на курсы', {
            'fields': (
                'academic_year',
                'telegram_group_url',
                'minimum_registration_age',
                'registration_mode',
                'application_limit',
                'registration_status_display',
                'registered_applications_display',
                'active_academic_year_display',
                'updated_at',
            ),
            'description': (
                'Ручной режим позволяет открыть или завершить регистрацию независимо от лимита. '
                'Все значения относятся только к указанному учебному году. '
                'Даты начала и окончания курсов задаются только в таблице «Учебные годы».'
            ),
        }),
    )

    @admin.display(description='Активный учебный год')
    def active_academic_year_display(self, obj=None):
        academic_year = getattr(obj, 'academic_year', None) or AcademicYear.get_active()
        if academic_year is None:
            return 'Не создан'
        return (
            f'{academic_year.name}: '
            f'{academic_year.starts_on:%d.%m.%Y} — {academic_year.ends_on:%d.%m.%Y}'
        )

    @admin.display(description='Текущее состояние')
    def registration_status_display(self, obj=None):
        settings_obj = obj or CourseRegistrationSettings.load()
        return 'Регистрация открыта' if settings_obj.registration_is_open() else 'Регистрация завершена'

    @admin.display(description='Зарегистрировано (без отклонённых)')
    def registered_applications_display(self, obj=None):
        settings_obj = obj or CourseRegistrationSettings.load()
        count = settings_obj.registered_applications_count()
        if settings_obj.application_limit is None:
            return str(count)
        return f'{count} из {settings_obj.application_limit}'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj)


@admin.register(PasswordRecoveryContact)
class PasswordRecoveryContactAdmin(JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Контакты администраторов, которые показываются пользователям на странице восстановления доступа.'
    )
    list_display = (
        'name',
        'phone',
        'messengers',
        'messenger_username',
        'is_active',
        'display_order',
        'updated_at',
    )
    list_editable = ('is_active', 'display_order')
    list_filter = ('is_active', 'messengers')
    search_fields = ('name', 'phone', 'messengers', 'messenger_username')
    readonly_fields = ('updated_at',)
    ordering = ('display_order', 'name')
    fieldsets = (
        ('Контакт для восстановления доступа', {
            'fields': (
                'name',
                'phone',
                'messengers',
                'messenger_username',
                'is_active',
                'display_order',
                'updated_at',
            ),
            'description': (
                'Активные контакты показываются на публичной странице восстановления пароля.'
            ),
        }),
    )


class SelectedAssessmentYearAdminMixin(ArchivedAcademicYearAdminMixin):
    academic_year_lookup = 'academic_year'

    def selected_year(self, request):
        return self.selected_academic_year(request) or AcademicYear.get_active()

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        academic_year = self.selected_year(request)
        if academic_year is None:
            return queryset.none()
        return queryset.filter(**{self.academic_year_lookup: academic_year})

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        academic_year = self.selected_year(request)
        if academic_year is not None:
            initial.setdefault('academic_year', academic_year.pk)
        return initial


@admin.register(AssessmentGroup)
class AssessmentGroupAdmin(SelectedAssessmentYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    form = AssessmentGroupAdminForm
    inlines = (
        AssessmentItemForGroupInline,
        StudentAssessmentGroupForGroupInline,
        FinalGradeRuleForGroupInline,
    )
    changelist_description = (
        'Отдельные группы произведений. Они не являются учебными группами учеников и '
        'используются только для распределения произведений в специальном режиме предмета.'
    )
    list_display = (
        'name', 'subject', 'academic_year', 'items_count_display',
        'students_count_display', 'is_active',
    )
    list_filter = ('academic_year', 'subject', 'is_active')
    search_fields = ('name', 'description', 'subject__name')
    list_select_related = ('subject', 'academic_year')
    ordering = ('subject__name', 'sort_order', 'name')
    fields = ('name', 'description', 'subject', 'academic_year', 'sort_order', 'is_active')

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _items_count=Count('items', distinct=True),
            _students_count=Count(
                'student_assignments',
                filter=Q(student_assignments__is_active=True),
                distinct=True,
            ),
        )

    @admin.display(description='Произведений', ordering='_items_count')
    def items_count_display(self, obj):
        url = reverse('admin:journal_assessmentitem_changelist')
        return format_html('<a href="{}?group__id__exact={}">{}</a>', url, obj.pk, obj._items_count)

    @admin.display(description='Учеников', ordering='_students_count')
    def students_count_display(self, obj):
        url = reverse('admin:journal_studentassessmentgroup_changelist')
        return format_html(
            '<a href="{}?assessment_group__id__exact={}">{}</a>',
            url,
            obj.pk,
            obj._students_count,
        )

@admin.register(AssessmentElement)
class AssessmentElementAdmin(ArchivedAcademicYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    changelist_description = (
        'Единый справочник произведений и элементов. В группу произведений можно '
        'добавить только значение из этого списка.'
    )
    list_display = ('title', 'subject', 'is_active', 'placements_count_display')
    list_filter = ('subject', 'is_active')
    search_fields = ('title', 'description', 'subject__name')
    list_select_related = ('subject',)
    ordering = ('subject__name', 'title')
    fields = ('title', 'description', 'subject', 'is_active')
    autocomplete_fields = ('subject',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _placements_count=Count('group_placements', distinct=True),
        )

    @admin.display(description='Использований', ordering='_placements_count')
    def placements_count_display(self, obj):
        url = reverse('admin:journal_assessmentitem_changelist')
        return format_html(
            '<a href="{}?element__id__exact={}">{}</a>',
            url,
            obj.pk,
            obj._placements_count,
        )


@admin.register(AssessmentItem)
class AssessmentItemAdmin(SelectedAssessmentYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    form = AssessmentItemAdminForm
    inlines = (AssessmentResultForItemInline,)
    changelist_description = (
        'Каждое произведение относится ровно к одной группе и имеет не более одного '
        'текущего ответственного преподавателя-дирижёра.'
    )
    list_display = (
        'element', 'group', 'subject', 'responsible_teacher', 'configuration_status',
        'academic_year', 'sort_order', 'is_required', 'is_active', 'results_count_display',
    )
    list_filter = ('academic_year', 'subject', 'group', 'responsible_teacher', 'is_required', 'is_active')
    search_fields = ('element__title', 'title', 'group__name', 'subject__name', 'responsible_teacher__full_name')
    list_select_related = ('element', 'group', 'subject', 'responsible_teacher', 'academic_year')
    ordering = ('group__sort_order', 'sort_order', 'title')
    fields = (
        'element', 'group', 'responsible_teacher',
        'sort_order', 'is_required', 'is_active',
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_results_count=Count('results', distinct=True))

    @admin.display(description='Результатов', ordering='_results_count')
    def results_count_display(self, obj):
        url = reverse('admin:journal_assessmentresult_changelist')
        return format_html('<a href="{}?item__id__exact={}">{}</a>', url, obj.pk, obj._results_count)


    @admin.display(description='Настройка')
    def configuration_status(self, obj):
        if obj.responsible_teacher_id:
            return 'Готово'
        return format_html('<strong style="color:#b45309">Не назначен дирижёр</strong>')

@admin.register(StudentAssessmentGroup)
class StudentAssessmentGroupAdmin(SelectedAssessmentYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    form = StudentAssessmentGroupAdminForm
    changelist_description = (
        'Назначение ученику одной или нескольких групп произведений. Доступные группы '
        'ограничиваются предметами ученика и выбранным учебным годом.'
    )
    list_display = (
        'student', 'assessment_group', 'subject_display', 'academic_year',
        'items_count_display', 'results_workspace_link', 'is_active',
    )
    list_filter = ('academic_year', 'assessment_group__subject', 'assessment_group', 'is_active')
    search_fields = ('student__full_name', 'assessment_group__name', 'assessment_group__subject__name')
    list_select_related = ('student', 'assessment_group', 'assessment_group__subject', 'academic_year', 'enrollment')
    fields = ('student', 'academic_year', 'assessment_group', 'is_active')

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _items_count=Count(
                'assessment_group__items',
                filter=Q(assessment_group__items__is_active=True),
                distinct=True,
            ),
        )

    @admin.display(description='Произведений', ordering='_items_count')
    def items_count_display(self, obj):
        return format_html(
            '<a href="{}">{}</a>',
            admin_changelist_url(
                'assessmentitem',
                {'group__id__exact': obj.assessment_group_id},
            ),
            obj._items_count,
        )

    @admin.display(description='Результаты')
    def results_workspace_link(self, obj):
        return format_html(
            '<a href="{}">Открыть</a>',
            admin_changelist_url(
                'assessmentresult',
                {
                    'item__group__id__exact': obj.assessment_group_id,
                    'enrollment__student__id__exact': obj.student_id,
                },
            ),
        )

    @admin.display(description='Предмет', ordering='assessment_group__subject__name')
    def subject_display(self, obj):
        return obj.assessment_group.subject


@admin.register(AssessmentResult)
class AssessmentResultAdmin(SelectedAssessmentYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    academic_year_lookup = 'item__academic_year'
    form = AssessmentResultAdminForm
    changelist_description = (
        'История результатов по каждому произведению. Изменения преподавателя в кабинете '
        'дополнительно проверяются сервером по текущему назначению дирижёра.'
    )
    list_display = (
        'student_display', 'item', 'subject_display', 'status', 'assessed_by',
        'assessed_at', 'academic_year_display',
    )
    list_filter = (
        'item__academic_year', 'item__subject', 'item__group', 'item',
        'enrollment__student', 'status', 'assessed_by',
    )
    search_fields = ('enrollment__full_name', 'item__title', 'assessed_by__full_name', 'comment')
    list_select_related = (
        'enrollment', 'enrollment__student', 'item', 'item__subject',
        'item__academic_year', 'assessed_by',
    )
    readonly_fields = ('created_at', 'updated_at')
    fields = ('enrollment', 'item', 'status', 'assessed_by', 'assessed_at', 'comment', 'created_at', 'updated_at')

    def get_form(self, request, obj=None, change=False, **kwargs):
        base_form = super().get_form(request, obj, change=change, **kwargs)
        return type(
            'YearScopedAssessmentResultAdminForm',
            (base_form,),
            {
                '__module__': base_form.__module__,
                'parent_academic_year': self.selected_year(request),
            },
        )

    @admin.display(description='Ученик', ordering='enrollment__full_name')
    def student_display(self, obj):
        return obj.enrollment.full_name

    @admin.display(description='Предмет', ordering='item__subject__name')
    def subject_display(self, obj):
        return obj.item.subject

    @admin.display(description='Учебный год', ordering='item__academic_year__starts_on')
    def academic_year_display(self, obj):
        return obj.item.academic_year


@admin.register(FinalGradeRule)
class FinalGradeRuleAdmin(SelectedAssessmentYearAdminMixin, JournalAdminDescriptionMixin, admin.ModelAdmin):
    form = FinalGradeRuleAdminForm
    changelist_description = (
        'Настраиваемое соответствие количества зачётов или выполнения всех обязательных '
        'произведений строковой итоговой оценке.'
    )
    list_display = (
        'subject', 'assessment_group', 'academic_year', 'rule_type',
        'condition_display', 'grade', 'priority', 'is_active',
    )
    list_filter = ('academic_year', 'subject', 'assessment_group', 'rule_type', 'is_active')
    search_fields = ('subject__name', 'assessment_group__name', 'grade')
    list_select_related = ('subject', 'assessment_group', 'academic_year')
    fields = (
        'subject', 'academic_year', 'assessment_group', 'rule_type',
        'passed_count', 'condition_value', 'grade', 'priority', 'is_active',
    )

    @admin.display(description='Условие')
    def condition_display(self, obj):
        if obj.rule_type == FinalGradeRule.RULE_COUNT:
            return f'{obj.passed_count} зачётов'
        if obj.rule_type == FinalGradeRule.RULE_ALL_REQUIRED:
            return 'Все сданы' if obj.condition_value else 'Не все сданы'
        return 'По умолчанию'
