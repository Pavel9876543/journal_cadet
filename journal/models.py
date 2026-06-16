from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from .registration_utils import normalize_parent_contacts, normalize_phone_number


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


class CourseRegistrationSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    telegram_group_url = models.URLField(
        'Ссылка на Telegram-группу',
        max_length=500,
        blank=True,
    )
    updated_at = models.DateTimeField('Дата изменения', auto_now=True)

    class Meta:
        verbose_name = 'Настройка регистрации'
        verbose_name_plural = 'Настройки регистрации'

    def __str__(self) -> str:
        return 'Настройки регистрации на курсы'


class TemporaryCredential(models.Model):
    login = models.CharField('Логин', max_length=150)
    temporary_password = models.CharField('Временный пароль', max_length=128)
    created_at = models.DateTimeField('Дата и время создания', auto_now_add=True)
    student_phone = models.CharField('Номер телефона ученика', max_length=32, blank=True)

    class Meta:
        verbose_name = 'Временные учетные данные'
        verbose_name_plural = 'Временные учетные данные'
        ordering = ['-created_at', '-id']

    def __str__(self) -> str:
        return self.login


class CourseApplication(models.Model):
    STUDENT_COURSE_GROUP_NAME = 'Ученики курсов'

    GENDER_MALE = 'male'
    GENDER_FEMALE = 'female'
    GENDER_CHOICES = (
        (GENDER_MALE, 'Мужской'),
        (GENDER_FEMALE, 'Женский'),
    )

    MUSIC_EDUCATION_NONE = 'none'
    MUSIC_EDUCATION_SELF = 'self_taught'
    MUSIC_EDUCATION_BASIC = 'basic'
    MUSIC_EDUCATION_SECONDARY = 'secondary'
    MUSIC_EDUCATION_HIGHER = 'higher'
    MUSIC_EDUCATION_CHOICES = (
        (MUSIC_EDUCATION_NONE, 'Нет'),
        (MUSIC_EDUCATION_SELF, 'Самоучка'),
        (MUSIC_EDUCATION_BASIC, 'Начальное'),
        (MUSIC_EDUCATION_SECONDARY, 'Среднее'),
        (MUSIC_EDUCATION_HIGHER, 'Высшее'),
    )

    STATUS_NEW = 'new'
    STATUS_CONFIRMED = 'confirmed'
    STATUS_REJECTED = 'rejected'
    STATUS_ENROLLED = 'enrolled'
    STATUS_CHOICES = (
        (STATUS_NEW, 'Новая'),
        (STATUS_CONFIRMED, 'Подтверждена'),
        (STATUS_REJECTED, 'Отклонена'),
        (STATUS_ENROLLED, 'Зачислен'),
    )

    registration_date = models.DateTimeField('Дата регистрации', auto_now_add=True)
    last_name = models.CharField('Фамилия', max_length=100)
    first_name = models.CharField('Имя', max_length=100)
    middle_name = models.CharField('Отчество', max_length=100)
    gender = models.CharField('Пол', max_length=10, choices=GENDER_CHOICES)
    birth_date = models.DateField('Дата рождения')
    city_church = models.CharField('Город / Церковь', max_length=255)
    instrument = models.CharField('Музыкальный инструмент / партия в оркестре', max_length=255)
    music_education = models.CharField(
        'Музыкальное образование',
        max_length=20,
        choices=MUSIC_EDUCATION_CHOICES,
        default=MUSIC_EDUCATION_NONE,
    )
    student_phone = models.CharField('Телефон ученика', max_length=32)
    parent_contacts = models.TextField('Телефон родителей', blank=True)
    comments = models.TextField('Дополнительные вопросы или комментарии', blank=True)
    status = models.CharField(
        'Статус заявки',
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_NEW,
    )

    class Meta:
        verbose_name = 'Заявка на курсы'
        verbose_name_plural = 'Заявки на курсы'
        ordering = ['-registration_date', '-id']
        indexes = [
            models.Index(fields=['status', '-registration_date'], name='course_app_status_reg_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.last_name} {self.first_name} {self.middle_name}'.strip()

    @property
    def age(self) -> int:
        from .registration_utils import calculate_age

        return calculate_age(self.birth_date)

    def clean(self) -> None:
        super().clean()
        if self.student_phone:
            self.student_phone = normalize_phone_number(self.student_phone)
            duplicate_qs = CourseApplication.objects.filter(student_phone=self.student_phone)
            if self.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.pk)
            if duplicate_qs.exists():
                raise ValidationError({'student_phone': 'Ученик с таким номером телефона уже зарегистрирован.'})
        if self.parent_contacts:
            self.parent_contacts = normalize_parent_contacts(self.parent_contacts)

    def save(self, *args, **kwargs):
        from django.db import transaction

        from .account_utils import build_course_application_login, generate_temporary_password

        is_new = self._state.adding
        self.full_clean()

        with transaction.atomic():
            super().save(*args, **kwargs)

            if is_new:
                existing_logins = set(TemporaryCredential.objects.values_list('login', flat=True))
                existing_logins.update(User.objects.values_list('username', flat=True))
                login = build_course_application_login(
                    self.last_name,
                    self.first_name,
                    existing_logins=existing_logins,
                )
                temporary_password = generate_temporary_password()

                user = User.objects.create_user(
                    username=login,
                    password=temporary_password,
                    first_name=self.first_name,
                    last_name=self.last_name,
                )
                group, _ = Group.objects.get_or_create(name=self.STUDENT_COURSE_GROUP_NAME)
                Student.objects.create(
                    full_name=f'{self.first_name} {self.last_name}'.strip(),
                    group=group,
                    user=user,
                )
                TemporaryCredential.objects.create(
                    login=login,
                    temporary_password=temporary_password,
                    student_phone=self.student_phone,
                )
