from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from journal import account_utils
from journal.models import Teacher, TemporaryCredential


class Command(BaseCommand):
    help = 'Создает/обновляет учетные записи преподавателей и выводит логины/пароли.'

    @transaction.atomic
    def handle(self, *args, **options):
        credentials = []
        used_usernames = set(User.objects.values_list('username', flat=True))

        for teacher in Teacher.objects.select_related('user').order_by('id'):
            user = teacher.user or User(is_staff=False, is_superuser=False, is_active=True)
            if user.pk and user.username in used_usernames:
                used_usernames.remove(user.username)

            login = account_utils.build_display_name_from_full_name(teacher.full_name)
            username = account_utils.build_username_from_full_name(
                teacher.full_name,
                existing_usernames=used_usernames,
            )
            password = account_utils.generate_temporary_password()
            first_name, last_name = account_utils.split_user_name(teacher.full_name)

            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            user.is_staff = False
            user.is_superuser = False
            user.is_active = True
            user.set_password(password)
            user.save()

            teacher.user = user
            teacher.save(update_fields=['user'])
            used_usernames.add(username)

            TemporaryCredential.objects.create(
                login=login,
                temporary_password=password,
            )

            credentials.append(
                {
                    'teacher': teacher.full_name,
                    'login': login,
                    'password': password,
                }
            )

        self.stdout.write(self.style.SUCCESS('Учетные записи преподавателей готовы.'))
        for row in credentials:
            self.stdout.write(
                f"{row['teacher']} | логин: {row['login']} | пароль: {row['password']}"
            )
