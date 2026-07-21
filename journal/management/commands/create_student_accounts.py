from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from journal import account_utils
from journal.models import Student, TemporaryCredential


class Command(BaseCommand):
    help = 'Создает/обновляет учетные записи учеников и выводит логины/пароли.'

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        credentials = []
        used_usernames = set(User.objects.values_list('username', flat=True))

        for student in Student.objects.select_related('user').order_by('id'):
            user = student.user or User(is_staff=False, is_superuser=False, is_active=True)
            if user.pk and user.username in used_usernames:
                used_usernames.remove(user.username)

            username = account_utils.build_username_from_full_name(
                student.full_name,
                existing_usernames=used_usernames,
            )
            password = account_utils.generate_temporary_password()
            first_name, last_name = account_utils.split_user_name(student.full_name)

            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            user.is_staff = False
            user.is_superuser = False
            user.is_active = True
            user.set_password(password)
            user.save()

            student.user = user
            student.save(update_fields=['user'])
            used_usernames.add(username)

            credential = TemporaryCredential.objects.filter(login=username).order_by('id').first()
            if credential is None:
                TemporaryCredential.objects.create(
                    login=username,
                    temporary_password=password,
                    student_phone=student.student_phone,
                )
            else:
                credential.temporary_password = password
                credential.student_phone = student.student_phone
                credential.save(update_fields=['temporary_password', 'student_phone'])
                TemporaryCredential.objects.filter(login=username).exclude(pk=credential.pk).delete()

            credentials.append(
                {
                    'student': student.full_name,
                    'login': username,
                    'password': password,
                }
            )

        self.stdout.write(self.style.SUCCESS('Учетные записи учеников готовы.'))
        for row in credentials:
            self.stdout.write(
                f"{row['student']} | логин: {row['login']} | пароль: {row['password']}"
            )
