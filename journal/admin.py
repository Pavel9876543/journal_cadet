from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin, UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group as AuthGroup, User as AuthUser

from .models import Grade, Group, Student, Subject, SubjectResult, Teacher

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
