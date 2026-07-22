from csv import writer

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from journal.account_utils import display_name_for_user
from journal.models import TemporaryCredential


class Command(BaseCommand):
    help = 'Экспортирует временные учетные данные всех пользователей в CSV.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='-',
            help='Путь к CSV-файлу или - для вывода в stdout.',
        )

    def handle(self, *args, **options):
        output = options['output']
        rows = (
            TemporaryCredential.objects
            .select_related('user', 'user__student_profile', 'user__teacher_profile')
            .prefetch_related('user__groups')
            .order_by('created_at', 'id')
        )

        if output == '-':
            stream = self.stdout
            close_stream = False
        else:
            stream = open(output, 'w', encoding='utf-8', newline='')
            close_stream = True

        try:
            csv_writer = writer(stream)
            csv_writer.writerow(['role', 'name', 'login', 'temporary_password', 'created_at', 'phone'])
            for credential in rows:
                user = self._credential_user(credential)
                csv_writer.writerow([
                    self._role_for_user(user, credential),
                    display_name_for_user(user) or credential.login,
                    credential.login,
                    credential.temporary_password,
                    credential.created_at.isoformat(),
                    self._phone_for_credential(credential, user),
                ])
        finally:
            if close_stream:
                stream.close()

    def _credential_user(self, credential):
        if credential.user_id:
            return credential.user
        if not credential.login:
            return None
        return get_user_model().objects.filter(username=credential.login).first()

    def _role_for_user(self, user, credential):
        if user is None:
            return 'student' if credential.course_application_id or credential.student_phone else 'user'

        group_names = set(user.groups.values_list('name', flat=True))
        if user.is_superuser or user.is_staff or 'Администратор' in group_names:
            return 'admin'
        if 'Преподаватель' in group_names or hasattr(user, 'teacher_profile'):
            return 'teacher'
        if 'Ученик' in group_names or hasattr(user, 'student_profile'):
            return 'student'
        return 'user'

    def _phone_for_credential(self, credential, user):
        if credential.student_phone:
            return credential.student_phone
        if user is not None and hasattr(user, 'student_profile'):
            return user.student_profile.student_phone
        return ''
