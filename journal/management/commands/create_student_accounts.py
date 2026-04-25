from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from journal.models import Student


class Command(BaseCommand):
    help = 'Создает/обновляет учетные записи учеников и выводит логины/пароли.'

    @transaction.atomic
    def handle(self, *args, **options):
        credentials = []

        for student in Student.objects.order_by('id'):
            username = f'student{student.id}'
            password = f'Music2026!S{student.id}'

            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'first_name': student.full_name,
                    'is_staff': False,
                    'is_superuser': False,
                    'is_active': True,
                },
            )

            # Для MVP пароль фиксированный и предсказуемый, чтобы легко войти в тестовый стенд.
            user.set_password(password)
            user.save(update_fields=['password'])

            student.user = user
            student.save(update_fields=['user'])

            credentials.append(
                {
                    'student': student.full_name,
                    'username': username,
                    'password': password,
                    'created': created,
                }
            )

        self.stdout.write(self.style.SUCCESS('Учетные записи учеников готовы.'))
        for row in credentials:
            status = 'создан' if row['created'] else 'обновлен'
            self.stdout.write(
                f"{row['student']} | логин: {row['username']} | пароль: {row['password']} | {status}"
            )
