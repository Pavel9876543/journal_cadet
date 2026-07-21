from urllib.parse import urlencode

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin, UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group as AuthGroup, User as AuthUser
from django.db.models import Count, Q
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .forms import CourseApplicationAdminForm, CourseRegistrationSettingsForm, html_date_input
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


@admin.register(AuthUser)
class UserAdmin(BaseUserAdmin):
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
class AuthGroupAdmin(BaseGroupAdmin):
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


class HiddenFromAdminIndexMixin:
    """Оставляет прямые admin URL, но убирает техническую модель из списков меню."""

    def get_model_perms(self, request):
        return {}


# -----------------------------------------------------------------------------
# Forms для админки
# -----------------------------------------------------------------------------


class GradeAdminForm(forms.ModelForm):
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

        student_id = self.data.get('student') or getattr(instance, 'student_id', None)
        subject_id = self.data.get('subject') or getattr(instance, 'subject_id', None)

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

        if student:
            group_subject_ids = GroupSubject.objects.filter(
                group_id=student.group_id,
                is_active=True,
            ).values_list('subject_id', flat=True)
            individual_subject_ids = StudentSubject.objects.filter(
                student_id=student.pk,
                is_active=True,
            ).values_list('subject_id', flat=True)
            self.fields['subject'].queryset = Subject.objects.filter(
                Q(pk__in=group_subject_ids) | Q(pk__in=individual_subject_ids)
            ).distinct().order_by('name')

        if student and subject:
            group_teacher_ids = GroupSubject.objects.filter(
                group_id=student.group_id,
                subject=subject,
                is_active=True,
            ).values_list('teacher_id', flat=True)
            individual_teacher_ids = StudentSubject.objects.filter(
                student=student,
                subject=subject,
                is_active=True,
            ).values_list('teacher_id', flat=True)
            self.fields['teacher'].queryset = Teacher.objects.filter(
                Q(pk__in=group_teacher_ids) | Q(pk__in=individual_teacher_ids)
            ).distinct().order_by('full_name')


class SubjectResultAdminForm(forms.ModelForm):
    class Meta:
        model = SubjectResult
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        instance = self.instance if self.instance and self.instance.pk else None
        student_id = self.data.get('student') or getattr(instance, 'student_id', None)

        if student_id:
            try:
                student = Student.objects.select_related('group').get(pk=student_id)
            except (Student.DoesNotExist, ValueError, TypeError):
                student = None

            if student:
                group_subject_ids = GroupSubject.objects.filter(
                    group_id=student.group_id,
                    is_active=True,
                ).values_list('subject_id', flat=True)
                individual_subject_ids = StudentSubject.objects.filter(
                    student_id=student.pk,
                    is_active=True,
                ).values_list('subject_id', flat=True)
                self.fields['subject'].queryset = Subject.objects.filter(
                    Q(pk__in=group_subject_ids) | Q(pk__in=individual_subject_ids)
                ).distinct().order_by('name')


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


# -----------------------------------------------------------------------------
# Inline-классы
# -----------------------------------------------------------------------------


class TeacherSubjectInline(admin.TabularInline):
    model = TeacherSubject
    extra = 0
    autocomplete_fields = ('subject',)
    fields = ('subject',)
    show_change_link = True
    verbose_name = 'Предмет преподавателя'
    verbose_name_plural = 'Предметы, которые может вести преподаватель'


class TeacherSubjectForSubjectInline(admin.TabularInline):
    model = TeacherSubject
    extra = 0
    autocomplete_fields = ('teacher',)
    fields = ('teacher',)
    show_change_link = True
    verbose_name = 'Преподаватель'
    verbose_name_plural = 'Преподаватели, которые могут вести предмет'


class GroupSubjectInline(admin.TabularInline):
    model = GroupSubject
    extra = 0
    autocomplete_fields = ('subject', 'teacher')
    fields = ('subject', 'teacher', 'sort_order', 'is_active')
    show_change_link = True
    verbose_name = 'Предмет группы'
    verbose_name_plural = 'Предметы группы'


class GroupSubjectForTeacherInline(admin.TabularInline):
    model = GroupSubject
    extra = 0
    autocomplete_fields = ('group', 'subject')
    fields = ('group', 'subject', 'sort_order', 'is_active')
    show_change_link = True
    classes = ('collapse',)
    verbose_name = 'Групповой предмет'
    verbose_name_plural = 'Групповые предметы преподавателя'


class GroupSubjectForSubjectInline(admin.TabularInline):
    model = GroupSubject
    extra = 0
    autocomplete_fields = ('group', 'teacher')
    fields = ('group', 'teacher', 'sort_order', 'is_active')
    show_change_link = True
    classes = ('collapse',)


class StudentSubjectInline(admin.TabularInline):
    model = StudentSubject
    extra = 0
    autocomplete_fields = ('subject', 'teacher')
    fields = ('subject', 'teacher', 'is_specialty', 'is_active')
    show_change_link = True
    verbose_name = 'Индивидуальный предмет'
    verbose_name_plural = 'Индивидуальные предметы ученика'


class StudentSubjectForTeacherInline(admin.TabularInline):
    model = StudentSubject
    extra = 0
    autocomplete_fields = ('student', 'subject')
    fields = ('student', 'subject', 'is_specialty', 'is_active')
    show_change_link = True
    classes = ('collapse',)
    verbose_name = 'Индивидуальный ученик'
    verbose_name_plural = 'Индивидуальные ученики преподавателя'


class StudentSubjectForSubjectInline(admin.TabularInline):
    model = StudentSubject
    extra = 0
    autocomplete_fields = ('student', 'teacher')
    fields = ('student', 'teacher', 'is_specialty', 'is_active')
    show_change_link = True
    classes = ('collapse',)


class StudentInline(admin.TabularInline):
    model = Student
    extra = 0
    autocomplete_fields = ('user', 'instrument')
    fields = (
        'full_name',
        'instrument',
        'specialty_teacher_inline',
        'user',
        'is_active',
    )
    readonly_fields = ('specialty_teacher_inline',)
    show_change_link = True
    classes = ('collapse',)

    @admin.display(description='Преподаватель по специальности')
    def specialty_teacher_inline(self, obj):
        if not obj or not obj.pk:
            return '—'
        teacher = obj.specialty_teacher
        return teacher or '—'


class GradeInline(admin.TabularInline):
    model = Grade
    form = GradeAdminForm
    extra = 0
    autocomplete_fields = ('subject', 'teacher', 'academic_year')
    fields = ('date', 'subject', 'teacher', 'value', 'academic_year', 'comment')
    ordering = ('-date',)
    show_change_link = True
    classes = ('collapse',)


class SubjectResultInline(admin.TabularInline):
    model = SubjectResult
    form = SubjectResultAdminForm
    extra = 0
    autocomplete_fields = ('subject', 'academic_year')
    fields = ('academic_year', 'subject', 'exam_grade', 'final_grade')
    show_change_link = True
    verbose_name = 'Итог'
    verbose_name_plural = 'Итоги по предметам'


# -----------------------------------------------------------------------------
# Справочники
# -----------------------------------------------------------------------------


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
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
class InstrumentAdmin(admin.ModelAdmin):
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
class SubjectAdmin(admin.ModelAdmin):
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
    inlines = (TeacherSubjectForSubjectInline,)
    ordering = ('name',)
    list_per_page = 50
    fieldsets = (
        ('Предмет', {
            'fields': ('name', 'final_grade_type', 'is_specialty', 'is_active'),
            'description': (
                'Обычные предметы назначаются группе. Предметы специальности '
                'назначаются конкретному ученику.'
            ),
        }),
    )

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


# -----------------------------------------------------------------------------
# Основные учебные сущности
# -----------------------------------------------------------------------------


@admin.register(StudyGroup)
class StudyGroupAdmin(admin.ModelAdmin):
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
    inlines = (GroupSubjectInline,)
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
class TeacherAdmin(admin.ModelAdmin):
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
    inlines = (TeacherSubjectInline, GroupSubjectForTeacherInline, StudentSubjectForTeacherInline)
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
class StudentAdmin(admin.ModelAdmin):
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


@admin.register(GroupSubject)
class GroupSubjectAdmin(HiddenFromAdminIndexMixin, admin.ModelAdmin):
    list_display = ('group', 'subject', 'teacher', 'sort_order', 'is_active')
    list_filter = ('is_active', 'group__academic_year', 'group', 'subject', 'teacher')
    search_fields = ('group__name', 'subject__name', 'teacher__full_name')
    autocomplete_fields = ('group', 'subject', 'teacher')
    list_select_related = ('group', 'group__academic_year', 'subject', 'teacher')
    ordering = ('group__academic_year__name', 'group__name', 'sort_order', 'subject__name')
    list_per_page = 50


@admin.register(StudentSubject)
class StudentSubjectAdmin(HiddenFromAdminIndexMixin, admin.ModelAdmin):
    list_display = ('student', 'student_group_display', 'subject', 'teacher', 'is_specialty', 'is_active')
    list_filter = ('is_active', 'is_specialty', 'subject', 'teacher', 'student__group')
    search_fields = ('student__full_name', 'student__group__name', 'subject__name', 'teacher__full_name')
    autocomplete_fields = ('student', 'subject', 'teacher')
    list_select_related = ('student', 'student__group', 'subject', 'teacher')
    ordering = ('student__full_name', 'subject__name')
    list_per_page = 50

    @admin.display(description='Группа')
    def student_group_display(self, obj):
        return obj.student.group if obj.student_id else '—'


@admin.register(Grade)
class GradeAdmin(admin.ModelAdmin):
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
    autocomplete_fields = ('student', 'subject', 'teacher', 'academic_year')
    readonly_fields = ('source_type_display', 'student_group_display')
    date_hierarchy = 'date'
    list_select_related = ('student', 'student__group', 'subject', 'teacher', 'academic_year')
    ordering = ('-date', 'student__full_name')
    list_per_page = 50

    fieldsets = (
        ('Оценка', {
            'fields': (
                'date',
                'student',
                'student_group_display',
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

    @admin.display(description='Группа')
    def student_group_display(self, obj):
        if obj and obj.student_id:
            return obj.student.group
        return '—'

    @admin.display(description='Тип назначения')
    def source_type_display(self, obj):
        if not obj or not obj.pk:
            return 'Будет проверено при сохранении'
        if obj.is_group_subject:
            return 'Групповой предмет'
        if obj.is_individual_subject:
            return 'Индивидуальный предмет'
        return 'Нет активного назначения'


@admin.register(SubjectResult)
class SubjectResultAdmin(admin.ModelAdmin):
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
    autocomplete_fields = ('student', 'subject', 'academic_year')
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

    @admin.display(description='Группа')
    def student_group_display(self, obj):
        return obj.student.group if obj.student_id else '—'


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
class CourseApplicationAdmin(admin.ModelAdmin):
    form = CourseApplicationAdminForm
    list_display = (
        'registration_date',
        'full_name_display',
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
    list_select_related = ('student', 'user')
    actions = ('confirm_applications', 'reject_applications')
    list_per_page = 40

    fieldsets = (
        ('Статус заявки', {
            'fields': (
                'registration_date',
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

    @admin.display(description='ФИО', ordering='last_name')
    def full_name_display(self, obj):
        return obj.full_name

    @admin.display(description='Возраст')
    def age_display(self, obj):
        return obj.age if obj.birth_date else '—'

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
        for application in queryset:
            application.status = CourseApplication.STATUS_CONFIRMED
            application.save()
            processed += 1
        self.message_user(request, f'Подтверждено заявок: {processed}.')

    @admin.action(description='Отклонить выбранные заявки и удалить учеников из журнала')
    def reject_applications(self, request, queryset):
        processed = 0
        for application in queryset:
            application.status = CourseApplication.STATUS_REJECTED
            application.save()
            processed += 1
        self.message_user(request, f'Отклонено заявок: {processed}. Ученики удалены из журнала.')


@admin.register(TemporaryCredential)
class TemporaryCredentialAdmin(admin.ModelAdmin):
    list_display = ('login', 'course_application_link', 'student_phone', 'created_at')
    list_filter = ('created_at',)
    search_fields = (
        'login',
        'student_phone',
        'course_application__last_name',
        'course_application__first_name',
        'course_application__middle_name',
    )
    readonly_fields = ('created_at',)
    autocomplete_fields = ('course_application',)
    date_hierarchy = 'created_at'
    list_per_page = 50
    fieldsets = (
        ('Временный доступ', {
            'fields': (
                'course_application',
                'login',
                'temporary_password',
                'student_phone',
                'created_at',
            ),
            'description': 'Эти данные нужны только для выдачи первичного доступа пользователю.',
        }),
    )

    @admin.display(description='Заявка')
    def course_application_link(self, obj):
        return admin_change_link(obj.course_application)


@admin.register(CourseRegistrationSettings)
class CourseRegistrationSettingsAdmin(admin.ModelAdmin):
    form = CourseRegistrationSettingsForm
    list_display = ('telegram_group_url', 'updated_at')
    readonly_fields = ('updated_at',)
    fieldsets = (
        ('Регистрация на курсы', {
            'fields': ('telegram_group_url', 'updated_at'),
            'description': 'Ссылка показывается ученику после успешной регистрации на курсы.',
        }),
    )

    def has_add_permission(self, request):
        return not CourseRegistrationSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PasswordRecoveryContact)
class PasswordRecoveryContactAdmin(admin.ModelAdmin):
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
