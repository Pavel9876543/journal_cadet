from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from journal.models import Teacher


class Command(BaseCommand):
    help = 'Создает/обновляет учетные записи преподавателей и выводит логины/пароли.'

    @transaction.atomic
    def handle(self, *args, **options):
        credentials = []

        for teacher in Teacher.objects.order_by('id'):
            username = f'teacher{teacher.id}'
            password = f'Music2026!T{teacher.id}'

            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'first_name': teacher.full_name,
                    'is_staff': False,
                    'is_superuser': False,
                    'is_active': True,
                },
            )

            # Для MVP пароль фиксированный и предсказуемый, чтобы легко войти в тестовый стенд.
            user.set_password(password)
            user.save(update_fields=['password'])

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
