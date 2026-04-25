from django.urls import path

from .views import journal_view

urlpatterns = [
    path('', journal_view, name='journal'),
]
