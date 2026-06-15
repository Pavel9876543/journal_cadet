from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from journal.forms import DetailedPasswordChangeForm, SiteAuthenticationForm
from journal.views import export_student_credentials_xlsx

urlpatterns = [
    path(
        'admin/student-credentials/export.xlsx',
        export_student_credentials_xlsx,
        name='export_student_credentials_xlsx',
    ),
    path('admin/', admin.site.urls),
    path(
        'accounts/login/',
        auth_views.LoginView.as_view(authentication_form=SiteAuthenticationForm),
        name='login',
    ),
    path(
        'accounts/password_change/',
        auth_views.PasswordChangeView.as_view(form_class=DetailedPasswordChangeForm),
        name='password_change',
    ),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', include('journal.urls')),
]
