from django.urls import path

from .views import (
    assessment_options_api,
    assessment_filter_options_api,
    assignment_options_api,
    course_registration_api,
    course_registration_view,
    grade_options_api,
    healthcheck_view,
    journal_view,
)

urlpatterns = [
    path('health/', healthcheck_view, name='healthcheck'),
    path('', journal_view, name='journal'),
    path('registration/', course_registration_view, name='course_registration'),
    path('api/course-registration/', course_registration_api, name='course_registration_api'),
    path('api/grade-options/', grade_options_api, name='grade_options_api'),
    path('api/assignment-options/', assignment_options_api, name='assignment_options_api'),
    path('api/assessment-options/', assessment_options_api, name='assessment_options_api'),
    path(
        'api/assessment-filter-options/',
        assessment_filter_options_api,
        name='assessment_filter_options_api',
    ),
]
