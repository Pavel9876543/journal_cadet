from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models


class Subject(models.Model):
    FINAL_GRADE_TYPE_NUMERIC = 'numeric'
    FINAL_GRADE_TYPE_PASS_FAIL = 'pass_fail'
    FINAL_GRADE_TYPE_CHOICES = (
        (FINAL_GRADE_TYPE_NUMERIC, 'Пятибалльная (1-5, Н)'),
        (FINAL_GRADE_TYPE_PASS_FAIL, 'Зачет/незачет (зачет, незачет)'),
    )

    name = models.CharField('Название предмета', max_length=100, unique=True)
    final_grade_type = models.CharField(
        'Тип итоговой оценки',
        max_length=20,
        choices=FINAL_GRADE_TYPE_CHOICES,
        default=FINAL_GRADE_TYPE_NUMERIC,
    )
    students = models.ManyToManyField(
        'Student',
        related_name='individual_subjects',
        blank=True,
        verbose_name='Индивидуальные ученики',
    )

    class Meta:
        verbose_name = 'Предмет'
        verbose_name_plural = 'Предметы'
        ordering = ['name']

    def __str__(self) -> str:
        return self.name

    def get_final_grade_allowed_values(self):
        if self.final_grade_type == self.FINAL_GRADE_TYPE_PASS_FAIL:
            return {'Зачет', 'Незачет'}
        return {'1', '2', '3', '4', '5', 'Н'}


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
    ALLOWED_VALUES = {'1', '2', '3', '4', '5', 'Н'}

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='grades', verbose_name='Ученик')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='grades', verbose_name='Предмет')
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE, related_name='grades', verbose_name='Преподаватель')
    date = models.DateField('Дата оценки')
    value = models.CharField(
        'Оценка',
        max_length=1,
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

        if self.student_id and self.subject_id:
            in_group_subjects = self.student.group.subjects.filter(pk=self.subject_id).exists()
            in_individual_subject = self.subject.students.filter(pk=self.student_id).exists()
            if not in_group_subjects and not in_individual_subject:
                raise ValidationError('Ученик не может получить оценку по предмету вне своей группы или индивидуального списка.')

        if self.teacher_id and self.subject_id and not self.teacher.subjects.filter(pk=self.subject_id).exists():
            raise ValidationError('Преподаватель не ведет выбранный предмет.')

        if self.value:
            self.value = str(self.value).strip().upper()
        if self.value not in self.ALLOWED_VALUES:
            raise ValidationError('Оценка должна быть 1-5 или Н.')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class SubjectResult(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='subject_results', verbose_name='Ученик')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='subject_results', verbose_name='Предмет')
    exam_grade = models.CharField(
        'Экзамен',
        max_length=10,
        null=True,
        blank=True,
    )
    final_grade = models.CharField(
        'Итоговая оценка',
        max_length=10,
        null=True,
        blank=True,
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

    def clean(self) -> None:
        allowed_values = self.subject.get_final_grade_allowed_values()
        for field_name in ('exam_grade', 'final_grade'):
            value = getattr(self, field_name)
            if value is None or value == '':
                setattr(self, field_name, None)
                continue

            normalized = str(value).strip()
            if normalized.lower() == 'зачет':
                normalized = 'Зачет'
            elif normalized.lower() == 'незачет':
                normalized = 'Незачет'

            if normalized not in allowed_values:
                raise ValidationError('Недопустимое значение итоговой оценки для выбранного предмета.')

            setattr(self, field_name, normalized)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
