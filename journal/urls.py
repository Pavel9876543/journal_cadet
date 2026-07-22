from django.urls import path

from .views import (
    assignment_options_api,
    course_registration_api,
    course_registration_view,
    grade_options_api,
    journal_view,
)

urlpatterns = [
    path('', journal_view, name='journal'),
    path('registration/', course_registration_view, name='course_registration'),
    path('api/course-registration/', course_registration_api, name='course_registration_api'),
    path('api/grade-options/', grade_options_api, name='grade_options_api'),
    path('api/assignment-options/', assignment_options_api, name='assignment_options_api'),
]
