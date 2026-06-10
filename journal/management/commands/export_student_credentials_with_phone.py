from csv import writer
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone

from journal.models import TemporaryStudentCredential


class Command(BaseCommand):
    help = 'Экспортирует временные учетные данные учеников в CSV и удаляет их после выгрузки.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output',
            default='',
            help='Путь к CSV-файлу. Если не указан, файл создается как YYYY_MM_students.csv в текущем каталоге.',
        )
        parser.add_argument(
            '--keep-data',
            action='store_true',
            help='Не удалять записи после успешного экспорта.',
        )

    def handle(self, *args, **options):
        output = options['output'].strip()
        keep_data = options['keep_data']

        if output:
            output_path = Path(output)
        else:
            output_path = Path.cwd() / f"{timezone.localdate():%Y_%m}_students.csv"

        output_path.parent.mkdir(parents=True, exist_ok=True)

        rows = list(
            TemporaryStudentCredential.objects.order_by('id').values_list(
                'login',
                'temporary_password',
                'phone_number',
            )
        )

        with output_path.open('w', encoding='utf-8', newline='') as stream:
            csv_writer = writer(stream)
            csv_writer.writerow(['login', 'temporary_password', 'phone_number'])
            for login, temporary_password, phone_number in rows:
                csv_writer.writerow([login, temporary_password, phone_number])

        if not keep_data:
            TemporaryStudentCredential.objects.all().delete()

        self.stdout.write(self.style.SUCCESS(str(output_path)))
