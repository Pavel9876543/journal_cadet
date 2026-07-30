from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import AcademicYear, CourseRegistrationSettings


@receiver(post_save, sender=AcademicYear)
def ensure_registration_settings_for_academic_year(sender, instance, **kwargs):
    CourseRegistrationSettings.objects.get_or_create(academic_year=instance)
