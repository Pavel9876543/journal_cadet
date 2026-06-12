from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin, UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group as AuthGroup, User as AuthUser

from .forms import CourseApplicationAdminForm, CourseRegistrationSettingsForm
from .models import (
    CourseApplication,
    CourseRegistrationSettings,
    Grade,
    Group,
    Student,
    Subject,
    SubjectResult,
    Teacher,
    TemporaryCredential,
    TemporaryStudentCredential,
)

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
    pass


@admin.register(AuthGroup)
class AuthGroupAdmin(BaseGroupAdmin):
    pass


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ('name',)
    filter_horizontal = ('subjects',)


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'final_grade_type')
    filter_horizontal = ('students',)


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'user')
    filter_horizontal = ('subjects',)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'group', 'user')
    list_filter = ('group',)


@admin.register(Grade)
class GradeAdmin(admin.ModelAdmin):
    list_display = ('student', 'subject', 'teacher', 'date', 'value')
    list_filter = ('subject', 'teacher', 'date', 'student__group')


@admin.register(SubjectResult)
class SubjectResultAdmin(admin.ModelAdmin):
    list_display = ('student', 'subject', 'exam_grade', 'final_grade')
    list_filter = ('subject', 'student__group')


@admin.register(CourseApplication)
class CourseApplicationAdmin(admin.ModelAdmin):
    form = CourseApplicationAdminForm
    list_display = (
        'registration_date',
        'last_name',
        'first_name',
        'middle_name',
        'status',
        'age',
        'student_phone',
        'city_church',
    )
    list_filter = ('status', 'gender', 'music_education', 'registration_date')
    search_fields = ('last_name', 'first_name', 'middle_name', 'student_phone', 'city_church', 'instrument')
    readonly_fields = ('registration_date', 'age')
    date_hierarchy = 'registration_date'
    fieldsets = (
        (
            'Основные данные',
            {
                'fields': (
                    'registration_date',
                    'status',
                    'last_name',
                    'first_name',
                    'middle_name',
                    'gender',
                    'birth_date',
                    'age',
                )
            },
        ),
        (
            'Контакты и обучение',
            {
                'fields': (
                    'city_church',
                    'instrument',
                    'music_education',
                    'student_phone',
                    'parent_contacts',
                    'comments',
                )
            },
        ),
    )


@admin.register(TemporaryCredential)
class TemporaryCredentialAdmin(admin.ModelAdmin):
    list_display = ('login', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('login',)
    readonly_fields = ('created_at',)


@admin.register(TemporaryStudentCredential)
class TemporaryStudentCredentialAdmin(admin.ModelAdmin):
    list_display = ('login', 'student_phone')
    search_fields = ('login', 'student_phone')


@admin.register(CourseRegistrationSettings)
class CourseRegistrationSettingsAdmin(admin.ModelAdmin):
    form = CourseRegistrationSettingsForm
    list_display = ('telegram_group_url', 'updated_at')
    readonly_fields = ('updated_at',)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
