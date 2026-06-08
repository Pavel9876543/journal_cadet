from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from journal import account_utils
from journal.models import Teacher


class Command(BaseCommand):
    help = 'Создает/обновляет учетные записи преподавателей и выводит логины/пароли.'

    @transaction.atomic
    def handle(self, *args, **options):
        credentials = []

        for teacher in Teacher.objects.select_related('user').order_by('id'):
            username = f'teacher{teacher.id}'
            password = account_utils.generate_temporary_password()
            first_name, last_name = account_utils.split_user_name(teacher.full_name)

            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'first_name': first_name,
                    'last_name': last_name,
                    'is_staff': False,
                    'is_superuser': False,
                    'is_active': True,
                },
            )

            user.first_name = first_name
            user.last_name = last_name
            user.is_staff = False
            user.is_superuser = False
            user.is_active = True
            user.set_password(password)
            user.save()

            teacher.user = user
            teacher.save(update_fields=['user'])

            credentials.append(
                {
                    'teacher': teacher.full_name,
                    'username': username,
                    'password': password,
                    'created': created,
                }
            )

        self.stdout.write(self.style.SUCCESS('Учетные записи преподавателей готовы.'))
        for row in credentials:
            status = 'создан' if row['created'] else 'обновлен'
            self.stdout.write(
                f"{row['teacher']} | логин: {row['username']} | пароль: {row['password']} | {status}"
            )
