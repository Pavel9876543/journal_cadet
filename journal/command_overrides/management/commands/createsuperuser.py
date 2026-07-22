from __future__ import annotations

import os

from django.contrib.auth.management.commands import createsuperuser as django_createsuperuser
from django.contrib.auth.management.commands.createsuperuser import Command as DjangoCreateSuperUserCommand
from django.contrib.auth.models import Group

from journal.account_utils import ensure_temporary_credential_for_user


class Command(DjangoCreateSuperUserCommand):
    def handle(self, *args, **options):
        self._created_superuser_username = options.get(self.UserModel.USERNAME_FIELD)
        self._captured_superuser_password = None

        if options.get('interactive'):
            original_getpass = django_createsuperuser.getpass.getpass

            def capture_password(prompt='Password: ', *password_args, **password_kwargs):
                value = original_getpass(prompt, *password_args, **password_kwargs)
                if 'again' in str(prompt).lower():
                    self._captured_superuser_password = value
                return value

            django_createsuperuser.getpass.getpass = capture_password
            try:
                result = super().handle(*args, **options)
            finally:
                django_createsuperuser.getpass.getpass = original_getpass
        else:
            if self._created_superuser_username is None:
                self._created_superuser_username = os.environ.get(
                    f'DJANGO_SUPERUSER_{self.UserModel.USERNAME_FIELD.upper()}',
                )
            self._captured_superuser_password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')
            result = super().handle(*args, **options)

        self._store_temporary_credential()
        return result

    def get_input_data(self, field, message, default=None):
        value = super().get_input_data(field, message, default)
        if field.name == self.UserModel.USERNAME_FIELD and value:
            self._created_superuser_username = value
        return value

    def _store_temporary_credential(self) -> None:
        username = self._created_superuser_username
        if not username:
            return

        user = self.UserModel._default_manager.filter(
            **{self.UserModel.USERNAME_FIELD: username},
        ).first()
        if user is None or not user.is_superuser:
            return

        admin_group, _created = Group.objects.get_or_create(name='Администратор')
        user.groups.add(admin_group)
        ensure_temporary_credential_for_user(
            user,
            password=self._captured_superuser_password,
            reset_missing_password=self._captured_superuser_password is None,
        )
