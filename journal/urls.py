from django.urls import path

from .views import course_registration_api, course_registration_view, journal_view

urlpatterns = [
    path('', journal_view, name='journal'),
    path('registration/', course_registration_view, name='course_registration'),
    path('api/course-registration/', course_registration_api, name='course_registration_api'),
]
