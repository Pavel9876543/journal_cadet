from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin, UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group as AuthGroup, User as AuthUser

from .models import Grade, Group, Student, Subject, Teacher


class ReadOnlySuperuserMixin:
    # Суперпользователь работает в режиме просмотра без изменения данных.
    def has_add_permission(self, request):
        if request.user.is_superuser:
            return False
        return super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if request.user.is_superuser:
            return False
        return super().has_delete_permission(request, obj)

    def has_view_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        return super().has_view_permission(request, obj)


try:
    admin.site.unregister(AuthUser)
except admin.sites.NotRegistered:
    pass

try:
    admin.site.unregister(AuthGroup)
except admin.sites.NotRegistered:
    pass


@admin.register(AuthUser)
class UserAdmin(ReadOnlySuperuserMixin, BaseUserAdmin):
    pass


@admin.register(AuthGroup)
class AuthGroupAdmin(ReadOnlySuperuserMixin, BaseGroupAdmin):
    pass


@admin.register(Group)
class GroupAdmin(ReadOnlySuperuserMixin, admin.ModelAdmin):
    list_display = ('name',)
    filter_horizontal = ('subjects',)


@admin.register(Subject)
class SubjectAdmin(ReadOnlySuperuserMixin, admin.ModelAdmin):
    list_display = ('name',)


@admin.register(Teacher)
class TeacherAdmin(ReadOnlySuperuserMixin, admin.ModelAdmin):
    list_display = ('full_name', 'user')
    filter_horizontal = ('subjects',)


@admin.register(Student)
class StudentAdmin(ReadOnlySuperuserMixin, admin.ModelAdmin):
    list_display = ('full_name', 'group', 'user')
    list_filter = ('group',)


@admin.register(Grade)
class GradeAdmin(ReadOnlySuperuserMixin, admin.ModelAdmin):
    list_display = ('student', 'subject', 'teacher', 'date', 'value')
    list_filter = ('subject', 'teacher', 'date', 'student__group')
