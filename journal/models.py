from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Subject(models.Model):
    name = models.CharField('Название предмета', max_length=100, unique=True)

    class Meta:
        verbose_name = 'Предмет'
        verbose_name_plural = 'Предметы'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class Group(models.Model):
    name = models.CharField('Название группы', max_length=100, unique=True)
    subjects = models.ManyToManyField(Subject, related_name='groups', verbose_name='Предметы')

    class Meta:
        verbose_name = 'Группа'
        verbose_name_plural = 'Группы'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name


class Teacher(models.Model):
    full_name = models.CharField('ФИО преподавателя', max_length=150)
    user = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='teacher_profile',
        verbose_name='Пользователь',
    )
    subjects = models.ManyToManyField(Subject, related_name='teachers', verbose_name='Предметы')

    class Meta:
        verbose_name = 'Преподаватель'
        verbose_name_plural = 'Преподаватели'
        ordering = ['full_name']

    def __str__(self) -> str:
        return self.full_name


class Student(models.Model):
    full_name = models.CharField('ФИО ученика', max_length=150)
    user = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='student_profile',
        verbose_name='Пользователь',
    )
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='students', verbose_name='Группа')

    class Meta:
        verbose_name = 'Ученик'
        verbose_name_plural = 'Ученики'
        ordering = ['full_name']

    def __str__(self) -> str:
        return self.full_name


class Grade(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='grades', verbose_name='Ученик')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='grades', verbose_name='Предмет')
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE, related_name='grades', verbose_name='Преподаватель')
    date = models.DateField('Дата оценки')
    value = models.PositiveSmallIntegerField(
        'Оценка',
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )

    class Meta:
        verbose_name = 'Оценка'
        verbose_name_plural = 'Оценки'
        ordering = ['-date']
        constraints = [
            models.UniqueConstraint(
                fields=['student', 'subject', 'date'],
                name='unique_student_subject_grade_per_day',
            )
        ]

    def __str__(self) -> str:
        return f'{self.student} | {self.subject} | {self.value}'

    def clean(self) -> None:
        # Запрещаем более одной оценки в день по одному предмету для одного ученика.
        if self.student_id and self.subject_id and self.date:
            duplicate_qs = Grade.objects.filter(
                student_id=self.student_id,
                subject_id=self.subject_id,
                date=self.date,
            )
            if self.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.pk)
            if duplicate_qs.exists():
                raise ValidationError('Нельзя поставить несколько оценок в один день по одному предмету одному ученику.')

        # Запрещаем ставить оценку по предмету, которого нет у группы ученика.
        if self.student_id and self.subject_id and not self.student.group.subjects.filter(pk=self.subject_id).exists():
            raise ValidationError('Ученик не может получить оценку по предмету вне своей группы.')

        # Проверяем, что преподаватель ведет выбранный предмет.
        if self.teacher_id and self.subject_id and not self.teacher.subjects.filter(pk=self.subject_id).exists():
            raise ValidationError('Преподаватель не ведет выбранный предмет.')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class SubjectResult(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='subject_results', verbose_name='Ученик')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='subject_results', verbose_name='Предмет')
    exam_grade = models.PositiveSmallIntegerField(
        'Экзамен',
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    final_grade = models.PositiveSmallIntegerField(
        'Итоговая оценка',
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )

    class Meta:
        verbose_name = 'Итог по предмету'
        verbose_name_plural = 'Итоги по предметам'
        ordering = ['student__full_name', 'subject__name']
        constraints = [
            models.UniqueConstraint(fields=['student', 'subject'], name='unique_student_subject_result')
        ]

    def __str__(self) -> str:
        return f'{self.student} | {self.subject}'
