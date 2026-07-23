import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

from journal.account_utils import (
    ensure_temporary_credential_for_user,
    user_has_temporary_credential,
)


class Command(BaseCommand):
    help = "Create or update a superuser from environment variables."

    @transaction.atomic
    def handle(self, *args, **options):
        username = os.getenv("DJANGO_SUPERUSER_USERNAME", "").strip()
        email = os.getenv("DJANGO_SUPERUSER_EMAIL", "").strip()
        password = os.getenv("DJANGO_SUPERUSER_PASSWORD", "")

        if not username or not password:
            self.stdout.write(
                self.style.WARNING(
                    "Skipping superuser creation: DJANGO_SUPERUSER_USERNAME or DJANGO_SUPERUSER_PASSWORD is empty."
                )
            )
            return

        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "is_active": True,
                "is_staff": True,
                "is_superuser": True,
            },
        )

        changed = False
        if created:
            user.set_password(password)
            changed = True
        else:
            if email and user.email != email:
                user.email = email
                changed = True
            if not user.is_staff:
                user.is_staff = True
                changed = True
            if not user.is_superuser:
                user.is_superuser = True
                changed = True
            if not user.is_active:
                user.is_active = True
                changed = True
            if os.getenv("DJANGO_SUPERUSER_ROTATE_PASSWORD", "0") == "1":
                user.set_password(password)
                changed = True

        if changed:
            user.save()

        admin_group, _created = Group.objects.get_or_create(name="Администратор")
        user.groups.add(admin_group)

        rotate_password = os.getenv("DJANGO_SUPERUSER_ROTATE_PASSWORD", "0") == "1"
        credential_password = None
        if created or rotate_password:
            credential_password = password
        elif not user_has_temporary_credential(user) and user.check_password(password):
            # Recreate a deleted row only when the configured password is still
            # the user's real password. Never store a password that cannot log in.
            credential_password = password

        if credential_password is not None:
            ensure_temporary_credential_for_user(user, password=credential_password)

        # Defensive: explicitly grant every model permission in addition to is_superuser.
        all_perms = Permission.objects.all()
        if user.user_permissions.count() != all_perms.count():
            user.user_permissions.set(all_perms)

        if created:
            self.stdout.write(self.style.SUCCESS(f"Superuser '{username}' created."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Superuser '{username}' verified/updated."))
