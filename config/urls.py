from django.contrib import admin
from django.urls import include, path

from journal.views import export_student_credentials_xlsx

urlpatterns = [
    path(
        'admin/student-credentials/export.xlsx',
        export_student_credentials_xlsx,
        name='export_student_credentials_xlsx',
    ),
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', include('journal.urls')),
]
