from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from journal.forms import DetailedPasswordChangeForm, SiteAuthenticationForm
from journal.views import export_student_credentials_xlsx, export_all_data_excel, password_help_view

from journal.admin_tools import (
    admin_delete_database_view,
    admin_data_tools_view,
    admin_export_test_credentials_excel_view,
    admin_guide_view,
    admin_seed_test_data_view,
)

urlpatterns = [
    path(
        'admin/journal/data-tools/',
        admin_data_tools_view,
        name='admin_data_tools',
    ),
    path(
        'admin/journal/guide/',
        admin_guide_view,
        name='admin_guide',
    ),
    path(
        'admin/journal/data-tools/seed/',
        admin_seed_test_data_view,
        name='admin_seed_test_data',
    ),
    path(
        'admin/journal/data-tools/delete-database/',
        admin_delete_database_view,
        name='admin_delete_database',
    ),
    path(
        'admin/journal/data-tools/export-credentials.xlsx',
        admin_export_test_credentials_excel_view,
        name='admin_export_test_credentials_excel',
    ),
    path(
        'admin/student-credentials/export.xlsx',
        export_student_credentials_xlsx,
        name='export_student_credentials_xlsx',
    ),
    path(
        'admin/export-all-data.xlsx',
        export_all_data_excel,
        name='admin_export_all_data_excel',
    ),
    path('admin/', admin.site.urls),
    path(
        'accounts/login/',
        auth_views.LoginView.as_view(authentication_form=SiteAuthenticationForm),
        name='login',
    ),
    path(
        'accounts/password-help/',
        password_help_view,
        name='password_help',
    ),
    path(
        'accounts/password_change/',
        auth_views.PasswordChangeView.as_view(form_class=DetailedPasswordChangeForm),
        name='password_change',
    ),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', include('journal.urls')),
]
