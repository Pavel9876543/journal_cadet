from datetime import date, timedelta
from random import Random

from django.core.management.base import BaseCommand
from django.db import transaction

from journal.models import Grade, Group, Student, Subject, Teacher


class Command(BaseCommand):
    help = 'Заполняет БД реалистичными тестовыми данными для электронного журнала.'

    @transaction.atomic
    def handle(self, *args, **options):
        # Полностью перезаписываем demo-данные для детерминированного результата.
        Grade.objects.all().delete()
        Student.objects.all().delete()
        Teacher.objects.all().delete()
        Group.objects.all().delete()
        Subject.objects.all().delete()

        subjects = {
            'Сольфеджио': Subject.objects.create(name='Сольфеджио'),
            'Фортепиано': Subject.objects.create(name='Фортепиано'),
            'Гитара': Subject.objects.create(name='Гитара'),
            'Вокал': Subject.objects.create(name='Вокал'),
            'Музыкальная литература': Subject.objects.create(name='Музыкальная литература'),
            'Гармония': Subject.objects.create(name='Гармония'),
        }

        groups = {
            'Группа A (начинающие)': Group.objects.create(name='Группа A (начинающие)'),
            'Группа B (средний уровень)': Group.objects.create(name='Группа B (средний уровень)'),
            'Группа C (продвинутые)': Group.objects.create(name='Группа C (продвинутые)'),
        }

        groups['Группа A (начинающие)'].subjects.set([
            subjects['Сольфеджио'],
            subjects['Фортепиано'],
            subjects['Музыкальная литература'],
        ])
        groups['Группа B (средний уровень)'].subjects.set([
            subjects['Сольфеджио'],
            subjects['Гитара'],
            subjects['Вокал'],
            subjects['Музыкальная литература'],
        ])
        groups['Группа C (продвинутые)'].subjects.set([
            subjects['Гармония'],
            subjects['Фортепиано'],
            subjects['Вокал'],
            subjects['Музыкальная литература'],
        ])

        teachers = {
            'Анна Морозова': Teacher.objects.create(full_name='Анна Морозова'),
            'Дмитрий Ковалёв': Teacher.objects.create(full_name='Дмитрий Ковалёв'),
            'Елена Серова': Teacher.objects.create(full_name='Елена Серова'),
            'Игорь Романов': Teacher.objects.create(full_name='Игорь Романов'),
            'Марина Белова': Teacher.objects.create(full_name='Марина Белова'),
        }

        teachers['Анна Морозова'].subjects.set([
            subjects['Сольфеджио'],
            subjects['Музыкальная литература'],
        ])
        teachers['Дмитрий Ковалёв'].subjects.set([
            subjects['Фортепиано'],
            subjects['Гармония'],
        ])
        teachers['Елена Серова'].subjects.set([
            subjects['Вокал'],
            subjects['Сольфеджио'],
        ])
        teachers['Игорь Романов'].subjects.set([
            subjects['Гитара'],
            subjects['Музыкальная литература'],
        ])
        teachers['Марина Белова'].subjects.set([
            subjects['Вокал'],
            subjects['Фортепиано'],
            subjects['Музыкальная литература'],
        ])

        student_map = {
            'Группа A (начинающие)': [
                'Артём Соколов',
                'Ксения Ильина',
                'Павел Громов',
                'София Фролова',
                'Михаил Титов',
            ],
            'Группа B (средний уровень)': [
                'Виктория Орлова',
                'Роман Карпов',
                'Алина Жукова',
                'Тимофей Фадеев',
                'Дарья Никитина',
            ],
            'Группа C (продвинутые)': [
                'Никита Мельников',
                'Полина Егорова',
                'Глеб Воронов',
                'Мария Ларионова',
                'Яна Тарасова',
            ],
        }

        students = []
        for group_name, full_names in student_map.items():
            for full_name in full_names:
                students.append(Student.objects.create(full_name=full_name, group=groups[group_name]))

        subject_teachers = {}
        for subject in Subject.objects.all():
            subject_teachers[subject.id] = list(subject.teachers.all())

        rng = Random(2026)
        today = date.today()

        for student in students:
            group_subjects = list(student.group.subjects.all())
            for subject in group_subjects:
                candidates = subject_teachers[subject.id]
                teacher = candidates[rng.randrange(len(candidates))]

                # Создаем 3 оценки по каждому предмету с реалистичным разбросом.
                for i in range(3):
                    grade_value = rng.choice([3, 4, 4, 5, 5, 5])
                    Grade.objects.create(
                        student=student,
                        subject=subject,
                        teacher=teacher,
                        date=today - timedelta(days=(i * 7 + rng.randrange(0, 6))),
                        value=grade_value,
                    )

        self.stdout.write(self.style.SUCCESS('Тестовые данные успешно созданы.'))
        self.stdout.write(
            f'Групп: {Group.objects.count()}, '
            f'учеников: {Student.objects.count()}, '
            f'предметов: {Subject.objects.count()}, '
            f'преподавателей: {Teacher.objects.count()}, '
            f'оценок: {Grade.objects.count()}'
        )
