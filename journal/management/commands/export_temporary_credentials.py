from csv import writer

from django.core.management.base import BaseCommand

from journal.models import TemporaryCredential


class Command(BaseCommand):
    help = 'Экспортирует временные учетные данные учеников в CSV.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='-',
            help='Путь к CSV-файлу или - для вывода в stdout.',
        )

    def handle(self, *args, **options):
        output = options['output']
        rows = TemporaryCredential.objects.order_by('created_at', 'id').values_list(
            'login',
            'temporary_password',
            'created_at',
        )

        if output == '-':
            stream = self.stdout
            close_stream = False
        else:
            stream = open(output, 'w', encoding='utf-8', newline='')
            close_stream = True

        try:
            csv_writer = writer(stream)
            csv_writer.writerow(['login', 'temporary_password', 'created_at'])
            for login, temporary_password, created_at in rows:
                csv_writer.writerow([login, temporary_password, created_at.isoformat()])
        finally:
            if close_stream:
                stream.close()
