from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from journal import account_utils
from journal.models import Student


class Command(BaseCommand):
    help = 'Создает/обновляет учетные записи учеников и выводит логины/пароли.'

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        credentials = []
        used_usernames = set(User.objects.values_list('username', flat=True))

        for student in Student.objects.select_related('user').order_by('id'):
            user_is_new = student.user is None
            user = student.user or User(is_staff=False, is_superuser=False, is_active=True)
            if user.pk and user.username in used_usernames:
                used_usernames.remove(user.username)

            first_name, last_name = account_utils.split_user_name(student.full_name)
            password = None

            if user_is_new:
                username = account_utils.build_username_from_full_name(
                    student.full_name,
                    existing_usernames=used_usernames,
                )
                password = account_utils.generate_temporary_password()
                user.username = username
            else:
                username = user.username

            user.first_name = first_name
            user.last_name = last_name
            user.is_active = True
            if password is not None:
                user.set_password(password)
            user.save()

            student.user = user
            student.save(update_fields=['user'])
            used_usernames.add(username)

            account_utils.ensure_temporary_credential_for_user(
                user,
                password=password,
                user_was_created=user_is_new,
            )

            credentials.append(
                {
                    'student': student.full_name,
                    'login': username,
                    'password': password or 'не менялся',
                }
            )

        self.stdout.write(self.style.SUCCESS('Учетные записи учеников готовы.'))
        for row in credentials:
            self.stdout.write(
                f"{row['student']} | логин: {row['login']} | пароль: {row['password']}"
            )
