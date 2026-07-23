from datetime import date
from hashlib import blake2b

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, models, transaction
from django.db.models import Q
from django.utils import timezone

from .registration_utils import normalize_parent_contacts, normalize_phone_number


def default_course_starts_on() -> date:
    today = timezone.localdate()
    start_year = today.year if today.month < 9 else today.year + 1
    return date(start_year, 9, 1)


def default_course_ends_on() -> date:
    starts_on = default_course_starts_on()
    return date(starts_on.year + 1, 8, 31)


ARCHIVED_ACADEMIC_YEAR_ERROR = (
    'Архивный учебный год доступен только для просмотра. '
    'Изменения можно вносить только в активном учебном году.'
)


def academic_year_name_for_dates(starts_on: date, ends_on: date) -> str:
    if starts_on.year == ends_on.year:
        return str(starts_on.year)
    return f'{starts_on.year}/{ends_on.year}'


def academic_year_is_active(academic_year: 'AcademicYear | None') -> bool:
    if academic_year is None:
        return False
    if getattr(academic_year, 'pk', None):
        return AcademicYear.objects.filter(pk=academic_year.pk, is_active=True).exists()
    return bool(academic_year.is_active)


def validate_active_academic_year(academic_year: 'AcademicYear | None', field_name: str = 'academic_year') -> None:
    if academic_year is not None and not academic_year_is_active(academic_year):
        raise ValidationError({field_name: ARCHIVED_ACADEMIC_YEAR_ERROR})


def academic_year_for_object(obj):
    if obj is None:
        return None
    if isinstance(obj, AcademicYear):
        return obj
    if isinstance(obj, StudyGroup):
        return obj.academic_year if obj.academic_year_id else None
    if isinstance(obj, Student):
        if obj.group_id:
            return obj.group.academic_year
        active_enrollment = getattr(obj, 'active_enrollment', None)
        return active_enrollment.academic_year if active_enrollment is not None else None
    if isinstance(obj, StudentEnrollment):
        return obj.academic_year if obj.academic_year_id else None
    if isinstance(obj, GroupSubject):
        return obj.group.academic_year if obj.group_id else None
    if isinstance(obj, StudentSubject):
        return obj.academic_year if obj.academic_year_id else None
    if isinstance(obj, (Grade, SubjectResult, CourseApplication)):
        return obj.academic_year if obj.academic_year_id else None
    return None


def object_is_in_archived_academic_year(obj) -> bool:
    academic_year = academic_year_for_object(obj)
    return academic_year is not None and not academic_year_is_active(academic_year)


class AcademicYear(models.Model):
    name = models.CharField('Учебный год', max_length=20, unique=True)
    starts_on = models.DateField('Дата начала')
    ends_on = models.DateField('Дата окончания')
    is_active = models.BooleanField('Активный', default=False)

    class Meta:
        verbose_name = 'Учебный год'
        verbose_name_plural = 'Учебные годы'
        ordering = ['-starts_on']
        indexes = [
            models.Index(fields=['is_active'], name='acad_year_active_idx'),
            models.Index(fields=['starts_on', 'ends_on'], name='acad_year_dates_idx'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['name'], name='unique_acad_year_name'),
            models.UniqueConstraint(
                fields=['is_active'],
                condition=Q(is_active=True),
                name='unique_active_academic_year',
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if self.starts_on and self.ends_on and self.starts_on >= self.ends_on:
            raise ValidationError({'ends_on': 'Дата окончания должна быть позже даты начала.'})
        if self.starts_on and self.ends_on:
            overlapping_years = AcademicYear.objects.filter(
                starts_on__lte=self.ends_on,
                ends_on__gte=self.starts_on,
            )
            if self.pk:
                overlapping_years = overlapping_years.exclude(pk=self.pk)
            if overlapping_years.exists():
                raise ValidationError({
                    'starts_on': 'Период учебного года пересекается с уже существующим учебным годом.',
                    'ends_on': 'Период учебного года пересекается с уже существующим учебным годом.',
                })
        if self.pk:
            old_value = AcademicYear.objects.filter(pk=self.pk).values('is_active').first()
            if old_value and not old_value['is_active']:
                raise ValidationError(ARCHIVED_ACADEMIC_YEAR_ERROR)

    @classmethod
    def _lock_activation(cls) -> None:
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT pg_advisory_xact_lock(%s)',
                    [0x4341444554594541],
                )
        # Row locks serialize updates on databases that support SELECT FOR UPDATE.
        list(cls.objects.select_for_update().values_list('pk', flat=True))

    def save(self, *args, **kwargs):
        with transaction.atomic():
            self._lock_activation()
            previous_active_id = (
                AcademicYear.objects
                .filter(is_active=True)
                .values_list('pk', flat=True)
                .first()
            )
            self.clean_fields()
            self.clean()
            self.validate_unique()
            self.is_active = False
            self.validate_constraints()

            update_fields = kwargs.get('update_fields')
            if update_fields is not None:
                kwargs['update_fields'] = tuple(set(update_fields) | {'is_active'})

            super().save(*args, **kwargs)
            self.activate_latest(previous_active_id=previous_active_id, lock_acquired=True)
            self.is_active = AcademicYear.objects.filter(pk=self.pk, is_active=True).exists()

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            self._lock_activation()
            validate_active_academic_year(self)
            if AcademicYear.objects.count() <= 1:
                raise ValidationError('Нельзя удалить единственный учебный год.')
            result = super().delete(*args, **kwargs)
            self.activate_latest(lock_acquired=True)
            return result

    @classmethod
    def latest(cls):
        return cls.objects.order_by('-starts_on', '-ends_on', '-pk').first()

    @classmethod
    def activate_latest(cls, *, previous_active_id=None, lock_acquired=False):
        with transaction.atomic():
            if not lock_acquired:
                cls._lock_activation()

            years = cls.objects.order_by('-starts_on', '-ends_on', '-pk')
            latest_year = years.first()
            if latest_year is None:
                return None

            if previous_active_id is None:
                previous_active_id = (
                    cls.objects
                    .filter(is_active=True)
                    .values_list('pk', flat=True)
                    .first()
                )

            active_changed = previous_active_id != latest_year.pk
            if active_changed and previous_active_id:
                finalize_academic_year_snapshots(previous_active_id)

            years.exclude(pk=latest_year.pk).update(is_active=False)
            cls.objects.filter(pk=latest_year.pk).update(is_active=True)
            latest_year.is_active = True

            if active_changed:
                sync_people_with_active_academic_year(latest_year.pk)

            return latest_year

    @classmethod
    def get_or_create_for_dates(cls, starts_on: date, ends_on: date):
        academic_year = cls.objects.filter(starts_on=starts_on, ends_on=ends_on).first()
        if academic_year is not None:
            cls.activate_latest()
            academic_year.refresh_from_db(fields=['is_active'])
            return academic_year, False

        base_name = academic_year_name_for_dates(starts_on, ends_on)
        name = base_name
        suffix = 2
        existing_names = set(cls.objects.values_list('name', flat=True))
        while name in existing_names:
            name = f'{base_name} {suffix}'
            suffix += 1

        academic_year = cls.objects.create(
            name=name,
            starts_on=starts_on,
            ends_on=ends_on,
            is_active=True,
        )
        return academic_year, True

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).first()

    @classmethod
    def get_for_date(cls, date):
        if not date:
            return None
        return cls.objects.filter(starts_on__lte=date, ends_on__gte=date).first()


class Instrument(models.Model):
    name = models.CharField('Инструмент', max_length=100, unique=True)

    class Meta:
        verbose_name = 'Инструмент'
        verbose_name_plural = 'Инструменты'
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['name'], name='unique_instrument_name'),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if self.name:
            self.name = self.name.strip()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Subject(models.Model):
    FINAL_GRADE_TYPE_NUMERIC = 'numeric'
    FINAL_GRADE_TYPE_PASS_FAIL = 'pass_fail'
    FINAL_GRADE_TYPE_CHOICES = (
        (FINAL_GRADE_TYPE_NUMERIC, 'Пятибалльная (1-5, Н)'),
        (FINAL_GRADE_TYPE_PASS_FAIL, 'Зачет/незачет'),
    )

    name = models.CharField('Название предмета', max_length=100, unique=True)
    final_grade_type = models.CharField(
        'Тип итоговой оценки',
        max_length=20,
        choices=FINAL_GRADE_TYPE_CHOICES,
        default=FINAL_GRADE_TYPE_NUMERIC,
    )
    is_specialty = models.BooleanField('Индивидуальный предмет', default=False)
    is_active = models.BooleanField('Активен', default=True)

    class Meta:
        verbose_name = 'Предмет'
        verbose_name_plural = 'Предметы'
        ordering = ['name']
        indexes = [
            models.Index(fields=['is_active', 'name'], name='subject_active_name_idx'),
            models.Index(fields=['is_specialty'], name='subject_specialty_idx'),
        ]
        constraints = [
            models.UniqueConstraint(fields=['name'], name='unique_subject_name'),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if self.name:
            self.name = self.name.strip()
        if self.pk and self.is_specialty and self.group_subjects.exists():
            raise ValidationError({
                'is_specialty': 'Нельзя сделать предмет индивидуальным, пока он назначен группам.'
            })
        if self.pk and not self.is_specialty and self.individual_students.exists():
            raise ValidationError({
                'is_specialty': 'Нельзя сделать предмет групповым, пока он назначен индивидуальным ученикам.'
            })
        if self.pk:
            previous = Subject.objects.filter(pk=self.pk).values('final_grade_type').first()
            if previous and previous['final_grade_type'] != self.final_grade_type:
                allowed_values = self.get_final_grade_allowed_values()
                incompatible_results = SubjectResult.objects.filter(subject=self).filter(
                    (
                        Q(exam_grade__isnull=False)
                        & ~Q(exam_grade='')
                        & ~Q(exam_grade__in=allowed_values)
                    )
                    | (
                        Q(final_grade__isnull=False)
                        & ~Q(final_grade='')
                        & ~Q(final_grade__in=allowed_values)
                    )
                )
                if incompatible_results.exists():
                    raise ValidationError({
                        'final_grade_type': (
                            'Нельзя изменить тип итоговой оценки: у предмета уже есть '
                            'итоги с несовместимыми значениями.'
                        )
                    })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def get_final_grade_allowed_values(self) -> set[str]:
        if self.final_grade_type == self.FINAL_GRADE_TYPE_PASS_FAIL:
            return {'Зачет', 'Незачет'}
        return {'1', '2', '3', '4', '5', 'Н'}

    @staticmethod
    def normalize_final_grade(value):
        if value is None or value == '':
            return None

        normalized = str(value).strip()
        normalized_lower = normalized.lower().replace('ё', 'е')

        if normalized_lower == 'зачет':
            return 'Зачет'
        if normalized_lower == 'незачет':
            return 'Незачет'

        return normalized.upper()


class StudyGroup(models.Model):
    name = models.CharField('Название группы', max_length=100)
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name='study_groups',
        verbose_name='Учебный год',
    )
    subjects = models.ManyToManyField(
        Subject,
        through='GroupSubject',
        related_name='study_groups',
        blank=True,
        verbose_name='Предметы',
    )
    is_active = models.BooleanField('Активна', default=True)

    class Meta:
        verbose_name = 'Группа'
        verbose_name_plural = 'Группы'
        ordering = ['academic_year__name', 'name']
        indexes = [
            models.Index(fields=['academic_year', 'name'], name='study_group_year_name_idx'),
            models.Index(fields=['is_active'], name='study_group_active_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['academic_year', 'name'],
                name='unique_group_name_per_year',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.name} ({self.academic_year})'

    def clean(self) -> None:
        super().clean()
        if self.name:
            self.name = self.name.strip()
        if self.academic_year_id:
            validate_active_academic_year(self.academic_year)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        validate_active_academic_year(self.academic_year)
        return super().delete(*args, **kwargs)

    @property
    def students_count(self) -> int:
        return self.student_enrollments.count()

    @property
    def subjects_display(self) -> str:
        items = self.group_subjects.select_related('subject', 'teacher').filter(is_active=True)
        return ', '.join(f'{item.subject} — {item.teacher}' for item in items) or '-'


class Teacher(models.Model):
    full_name = models.CharField('ФИО преподавателя', max_length=150)
    birth_date = models.DateField('Дата рождения', null=True, blank=True)
    phone = models.CharField('Телефон', max_length=32, blank=True)
    email = models.EmailField('Email', blank=True)
    comments = models.TextField('Комментарий', blank=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='teacher_profile',
        verbose_name='Пользователь',
    )
    qualified_subjects = models.ManyToManyField(
        Subject,
        through='TeacherSubject',
        related_name='qualified_teachers',
        blank=True,
        verbose_name='Предметы, которые может вести',
    )
    is_active = models.BooleanField('Активен', default=True)

    class Meta:
        verbose_name = 'Преподаватель'
        verbose_name_plural = 'Преподаватели'
        ordering = ['full_name']
        indexes = [
            models.Index(fields=['full_name'], name='teacher_full_name_idx'),
            models.Index(fields=['is_active'], name='teacher_active_idx'),
        ]

    def __str__(self) -> str:
        return self.full_name

    def clean(self) -> None:
        super().clean()
        if self.full_name:
            self.full_name = self.full_name.strip()
        if self.phone:
            self.phone = normalize_phone_number(self.phone)
        if self.email:
            self.email = self.email.strip().lower()
        if self.comments:
            self.comments = self.comments.strip()

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        self.full_clean()
        with transaction.atomic():
            super().save(*args, **kwargs)
            self.sync_active_year_membership(create_if_missing=is_new or self.is_active)

    def sync_active_year_membership(self, *, create_if_missing: bool = False):
        academic_year = AcademicYear.get_active()
        if academic_year is None:
            return None

        membership = self.academic_year_memberships.filter(academic_year=academic_year).first()
        if membership is None and not create_if_missing:
            return None
        if membership is None:
            membership = TeacherEnrollment(
                teacher=self,
                academic_year=academic_year,
                is_active=self.is_active,
            )
        else:
            membership.is_active = self.is_active
        membership.save()
        return membership

    def membership_for_year(self, academic_year=None):
        academic_year = academic_year or AcademicYear.get_active()
        if academic_year is None or not self.pk:
            return None
        prefetched = getattr(self, 'journal_year_memberships', None)
        if prefetched is not None:
            return next(
                (item for item in prefetched if item.academic_year_id == academic_year.pk),
                None,
            )
        return self.academic_year_memberships.filter(academic_year=academic_year).first()

    @property
    def group_subjects_display(self) -> str:
        items = self.group_subjects.select_related('group', 'subject').filter(is_active=True)
        return ', '.join(f'{item.group.name}: {item.subject.name}' for item in items) or '-'

    @property
    def individual_students_count(self) -> int:
        return self.individual_subjects.filter(is_active=True).values('student_id').distinct().count()

    @property
    def age(self) -> int | None:
        if not self.birth_date:
            return None

        from .registration_utils import calculate_age

        return calculate_age(self.birth_date)


class TeacherEnrollment(models.Model):
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.PROTECT,
        related_name='academic_year_memberships',
        verbose_name='Преподаватель',
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name='teacher_enrollments',
        verbose_name='Учебный год',
    )
    is_active = models.BooleanField('Активен в учебном году', default=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Изменено', auto_now=True)

    class Meta:
        verbose_name = 'Участие преподавателя в учебном году'
        verbose_name_plural = 'Участие преподавателей в учебных годах'
        ordering = ['-academic_year__starts_on', 'teacher__full_name']
        constraints = [
            models.UniqueConstraint(
                fields=['teacher', 'academic_year'],
                name='unique_teacher_academic_year',
            ),
        ]
        indexes = [
            models.Index(fields=['academic_year', 'teacher'], name='teacher_year_membership_idx'),
            models.Index(fields=['is_active'], name='teacher_year_active_idx'),
        ]

    def __str__(self):
        return f'{self.teacher} — {self.academic_year}'

    def clean(self):
        super().clean()
        if self.academic_year_id:
            validate_active_academic_year(self.academic_year)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        validate_active_academic_year(self.academic_year)
        teacher_id = self.teacher_id
        result = super().delete(*args, **kwargs)
        if not TeacherEnrollment.objects.filter(
            teacher_id=teacher_id,
            academic_year__is_active=True,
        ).exists():
            Teacher.objects.filter(pk=teacher_id).update(is_active=False)
        return result


def ensure_teacher_academic_year_membership(teacher_id: int | None, academic_year_id: int | None) -> None:
    if not teacher_id or not academic_year_id:
        return
    membership, created = TeacherEnrollment.objects.get_or_create(
        teacher_id=teacher_id,
        academic_year_id=academic_year_id,
        defaults={'is_active': True},
    )
    if not created and not membership.is_active:
        membership.is_active = True
        membership.save(update_fields=['is_active', 'updated_at'])
    if AcademicYear.objects.filter(pk=academic_year_id, is_active=True).exists():
        Teacher.objects.filter(pk=teacher_id).update(is_active=True)


class TeacherSubject(models.Model):
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name='subject_qualifications',
        verbose_name='Преподаватель',
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.CASCADE,
        related_name='teacher_qualifications',
        verbose_name='Предмет',
    )

    class Meta:
        verbose_name = 'Квалификация преподавателя'
        verbose_name_plural = 'Квалификации преподавателей'
        ordering = ['teacher__full_name', 'subject__name']
        indexes = [
            models.Index(fields=['teacher', 'subject'], name='teacher_subject_idx'),
            models.Index(fields=['subject', 'teacher'], name='subject_teacher_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['teacher', 'subject'],
                name='unique_teacher_subject_qual',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.teacher} — {self.subject}'


class Student(models.Model):
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

    full_name = models.CharField('ФИО ученика', max_length=150)
    gender = models.CharField('Пол', max_length=10, choices=GENDER_CHOICES, blank=True)
    birth_date = models.DateField('Дата рождения', null=True, blank=True)
    city_church = models.CharField('Город / Церковь', max_length=255, blank=True)
    music_education = models.CharField(
        'Музыкальное образование',
        max_length=20,
        choices=MUSIC_EDUCATION_CHOICES,
        blank=True,
    )
    student_phone = models.CharField('Телефон ученика', max_length=32, blank=True)
    parent_contacts = models.TextField('Телефон родителей', blank=True)
    comments = models.TextField('Дополнительные вопросы или комментарии', blank=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='student_profile',
        verbose_name='Пользователь',
    )
    group = models.ForeignKey(
        StudyGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='students',
        verbose_name='Группа',
    )
    instrument = models.ForeignKey(
        Instrument,
        on_delete=models.PROTECT,
        related_name='students',
        verbose_name='Инструмент',
    )
    is_active = models.BooleanField('Активен', default=True)

    class Meta:
        verbose_name = 'Ученик'
        verbose_name_plural = 'Ученики'
        ordering = ['full_name']
        indexes = [
            models.Index(fields=['group', 'full_name'], name='student_group_name_idx'),
            models.Index(fields=['instrument'], name='student_instrument_idx'),
            models.Index(fields=['is_active'], name='student_active_idx'),
            models.Index(fields=['student_phone'], name='student_phone_idx'),
            models.Index(fields=['birth_date'], name='student_birth_date_idx'),
        ]

    def __str__(self) -> str:
        return self.full_name

    def clean(self) -> None:
        super().clean()
        if self.full_name:
            self.full_name = self.full_name.strip()
        if self.city_church:
            self.city_church = self.city_church.strip()
        if self.student_phone:
            self.student_phone = normalize_phone_number(self.student_phone)
        if self.parent_contacts:
            self.parent_contacts = normalize_parent_contacts(self.parent_contacts)
        if self.comments:
            self.comments = self.comments.strip()
        if self.full_name and self.birth_date:
            identity_name = normalize_student_identity_name(self.full_name)
            candidates = Student.objects.filter(birth_date=self.birth_date)
            if self.pk:
                candidates = candidates.exclude(pk=self.pk)
            if any(
                normalize_student_identity_name(candidate_name) == identity_name
                for candidate_name in candidates.values_list('full_name', flat=True)
            ):
                raise ValidationError({
                    'full_name': (
                        'Ученик с таким ФИО и датой рождения уже существует. '
                        'Используйте существующую карточку ученика.'
                    ),
                })
        if self.group_id:
            validate_active_academic_year(self.group.academic_year, 'group')

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        self.full_clean()
        with transaction.atomic():
            super().save(*args, **kwargs)
            self.sync_active_enrollment(
                create_if_missing=is_new or self.group_id is not None or self.is_active,
            )

    def delete(self, *args, **kwargs):
        if self.enrollments.exists():
            raise ValidationError(
                'Нельзя удалить ученика, у которого есть данные учебных лет. '
                'Снимите признак «Активен», чтобы сохранить архив.'
            )
        if self.group_id:
            validate_active_academic_year(self.group.academic_year, 'group')
        return super().delete(*args, **kwargs)

    def sync_active_enrollment(self, *, create_if_missing: bool = False):
        academic_year = self.group.academic_year if self.group_id else AcademicYear.get_active()
        if academic_year is None or not academic_year_is_active(academic_year):
            return None

        enrollment = self.enrollments.filter(academic_year=academic_year).first()
        if enrollment is None and not create_if_missing:
            return None

        snapshot_values = StudentEnrollment.snapshot_values_for_student(self)
        if enrollment is None:
            enrollment = StudentEnrollment(
                student=self,
                academic_year=academic_year,
                group=self.group,
                **snapshot_values,
            )
        enrollment.group = self.group
        for field_name, value in snapshot_values.items():
            setattr(enrollment, field_name, value)
        enrollment.save()
        return enrollment

    def enrollment_for_year(self, academic_year=None):
        if academic_year is None:
            academic_year = AcademicYear.get_active()
        if academic_year is None or not self.pk:
            return None
        prefetched = getattr(self, 'journal_enrollments', None)
        if prefetched is not None:
            return next(
                (
                    enrollment
                    for enrollment in prefetched
                    if enrollment.academic_year_id == academic_year.pk
                ),
                None,
            )
        return (
            self.enrollments
            .select_related('group', 'academic_year')
            .filter(academic_year=academic_year)
            .first()
        )

    @property
    def age(self) -> int | None:
        if not self.birth_date:
            return None

        from .registration_utils import calculate_age

        return calculate_age(self.birth_date)

    @property
    def specialty_assignment(self):
        if not self.pk:
            return None
        prefetched_assignments = getattr(self, 'active_specialty_assignments', None)
        if prefetched_assignments is not None:
            return prefetched_assignments[0] if prefetched_assignments else None
        return (
            self.individual_subjects
            .select_related('subject', 'teacher')
            .filter(
                is_specialty=True,
                is_active=True,
                academic_year__is_active=True,
            )
            .first()
        )

    @property
    def specialty_teacher(self):
        assignment = self.specialty_assignment
        return assignment.teacher if assignment else None

    @property
    def specialty_subject(self):
        assignment = self.specialty_assignment
        return assignment.subject if assignment else None

    @property
    def all_subjects_qs(self):
        if not self.pk:
            return Subject.objects.none()

        group_subject_ids = ()
        active_year = AcademicYear.get_active()
        enrollment = self.enrollment_for_year(active_year)
        if enrollment and enrollment.group_id:
            group_subject_ids = enrollment.group.group_subjects.filter(is_active=True).values_list(
                'subject_id',
                flat=True,
            )
        individual_subject_ids = self.individual_subjects.filter(
            is_active=True,
            academic_year=active_year,
        ).values_list('subject_id', flat=True)
        subject_ids = set(group_subject_ids) | set(individual_subject_ids)
        return Subject.objects.filter(pk__in=subject_ids).order_by('name')

    @property
    def subjects_display(self) -> str:
        return ', '.join(self.all_subjects_qs.values_list('name', flat=True)) or '-'


class StudentEnrollment(models.Model):
    student = models.ForeignKey(
        Student,
        on_delete=models.PROTECT,
        related_name='enrollments',
        verbose_name='Ученик',
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name='student_enrollments',
        verbose_name='Учебный год',
    )
    group = models.ForeignKey(
        StudyGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='student_enrollments',
        verbose_name='Группа',
    )
    full_name = models.CharField('ФИО ученика на этот год', max_length=150)
    gender = models.CharField('Пол', max_length=10, choices=Student.GENDER_CHOICES, blank=True)
    birth_date = models.DateField('Дата рождения', null=True, blank=True)
    city_church = models.CharField('Город / Церковь', max_length=255, blank=True)
    instrument_name = models.CharField('Инструмент', max_length=100, blank=True)
    music_education = models.CharField(
        'Музыкальное образование',
        max_length=20,
        choices=Student.MUSIC_EDUCATION_CHOICES,
        blank=True,
    )
    student_phone = models.CharField('Телефон ученика', max_length=32, blank=True)
    parent_contacts = models.TextField('Телефон родителей', blank=True)
    comments = models.TextField('Комментарий', blank=True)
    is_active = models.BooleanField('Активен в учебном году', default=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Изменено', auto_now=True)

    class Meta:
        verbose_name = 'Зачисление ученика'
        verbose_name_plural = 'Зачисления учеников'
        ordering = ['-academic_year__starts_on', 'full_name']
        constraints = [
            models.UniqueConstraint(
                fields=['student', 'academic_year'],
                name='unique_student_enrollment_year',
            ),
        ]
        indexes = [
            models.Index(fields=['academic_year', 'group'], name='enroll_year_group_idx'),
            models.Index(fields=['student', 'academic_year'], name='enroll_student_year_idx'),
            models.Index(fields=['is_active'], name='enroll_active_idx'),
        ]

    def __str__(self):
        return f'{self.full_name} — {self.academic_year}'

    @staticmethod
    def snapshot_values_for_student(student):
        return {
            'full_name': student.full_name,
            'gender': student.gender,
            'birth_date': student.birth_date,
            'city_church': student.city_church,
            'instrument_name': student.instrument.name if student.instrument_id else '',
            'music_education': student.music_education,
            'student_phone': student.student_phone,
            'parent_contacts': student.parent_contacts,
            'comments': student.comments,
            'is_active': student.is_active,
        }

    def copy_from_student(self, student):
        for field_name, value in self.snapshot_values_for_student(student).items():
            setattr(self, field_name, value)

    def clean(self):
        super().clean()
        if self.group_id and self.academic_year_id:
            if self.group.academic_year_id != self.academic_year_id:
                raise ValidationError({'group': 'Группа относится к другому учебному году.'})
        if self.academic_year_id:
            validate_active_academic_year(self.academic_year)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        validate_active_academic_year(self.academic_year)
        student_id = self.student_id
        group_id = self.group_id
        result = super().delete(*args, **kwargs)
        if group_id:
            Student.objects.filter(pk=student_id, group_id=group_id).update(group=None)
        return result


class GroupSubject(models.Model):
    group = models.ForeignKey(
        StudyGroup,
        on_delete=models.CASCADE,
        related_name='group_subjects',
        verbose_name='Группа',
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.PROTECT,
        related_name='group_subjects',
        verbose_name='Предмет',
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.PROTECT,
        related_name='group_subjects',
        verbose_name='Преподаватель',
    )
    sort_order = models.PositiveSmallIntegerField('Порядок в журнале', default=100)
    is_active = models.BooleanField('Активен', default=True)
    subject_name_snapshot = models.CharField(
        'Название предмета в учебном году',
        max_length=100,
        blank=True,
        editable=False,
    )
    teacher_name_snapshot = models.CharField(
        'ФИО преподавателя в учебном году',
        max_length=150,
        blank=True,
        editable=False,
    )
    final_grade_type_snapshot = models.CharField(
        'Тип итоговой оценки в учебном году',
        max_length=20,
        choices=Subject.FINAL_GRADE_TYPE_CHOICES,
        blank=True,
        editable=False,
    )

    class Meta:
        verbose_name = 'Предмет группы'
        verbose_name_plural = 'Предметы групп'
        ordering = ['group__name', 'sort_order', 'subject__name']
        indexes = [
            models.Index(fields=['group', 'sort_order'], name='group_subject_order_idx'),
            models.Index(fields=['teacher', 'subject'], name='group_subject_teacher_idx'),
            models.Index(fields=['subject', 'group'], name='group_subject_lookup_idx'),
            models.Index(fields=['is_active'], name='group_subject_active_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['group', 'subject'],
                name='unique_subject_per_group',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.group} — {self.subject} — {self.teacher}'

    def clean(self) -> None:
        super().clean()
        if self.group_id:
            validate_active_academic_year(self.group.academic_year, 'group')
        if self.group_id and self.subject_id and self.subject.is_specialty:
            raise ValidationError({
                'subject': 'Индивидуальный предмет нельзя назначить группе.'
            })

    def save(self, *args, **kwargs):
        previous = None
        if self.pk:
            previous = (
                type(self).objects
                .filter(pk=self.pk)
                .values('group_id', 'subject_id', 'teacher_id', 'is_active')
                .first()
            )

        self.full_clean()
        with transaction.atomic():
            self.subject_name_snapshot = self.subject.name
            self.teacher_name_snapshot = self.teacher.full_name
            self.final_grade_type_snapshot = self.subject.final_grade_type
            kwargs['update_fields'] = _with_snapshot_update_fields(
                kwargs.get('update_fields'),
                'subject_name_snapshot',
                'teacher_name_snapshot',
                'final_grade_type_snapshot',
            )
            super().save(*args, **kwargs)

            if self.is_active:
                ensure_teacher_academic_year_membership(
                    self.teacher_id,
                    self.group.academic_year_id,
                )
                ensure_teacher_subject(self.teacher_id, self.subject_id)
                if (
                    previous
                    and previous['is_active']
                    and previous['group_id'] == self.group_id
                    and previous['subject_id'] == self.subject_id
                    and previous['teacher_id'] != self.teacher_id
                ):
                    Grade.objects.filter(
                        enrollment__group_id=self.group_id,
                        academic_year_id=self.group.academic_year_id,
                        subject_id=self.subject_id,
                        teacher_id=previous['teacher_id'],
                    ).update(
                        teacher_id=self.teacher_id,
                        teacher_name_snapshot=self.teacher.full_name,
                    )

            if previous:
                remove_unused_teacher_subject(
                    previous['teacher_id'],
                    previous['subject_id'],
                )

    def delete(self, *args, **kwargs):
        if self.group_id:
            validate_active_academic_year(self.group.academic_year, 'group')
        teacher_id = self.teacher_id
        subject_id = self.subject_id
        with transaction.atomic():
            result = super().delete(*args, **kwargs)
            remove_unused_teacher_subject(teacher_id, subject_id)
            return result


class StudentSubject(models.Model):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name='individual_subjects',
        verbose_name='Ученик',
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.PROTECT,
        related_name='individual_students',
        verbose_name='Предмет',
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.PROTECT,
        related_name='individual_subjects',
        verbose_name='Преподаватель',
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name='student_subjects',
        verbose_name='Учебный год',
        editable=False,
    )
    is_specialty = models.BooleanField('Специальность', default=True)
    is_active = models.BooleanField('Активно', default=True)
    subject_name_snapshot = models.CharField(
        'Название предмета в учебном году',
        max_length=100,
        blank=True,
        editable=False,
    )
    teacher_name_snapshot = models.CharField(
        'ФИО преподавателя в учебном году',
        max_length=150,
        blank=True,
        editable=False,
    )
    final_grade_type_snapshot = models.CharField(
        'Тип итоговой оценки в учебном году',
        max_length=20,
        choices=Subject.FINAL_GRADE_TYPE_CHOICES,
        blank=True,
        editable=False,
    )

    class Meta:
        verbose_name = 'Индивидуальный предмет ученика'
        verbose_name_plural = 'Индивидуальные предметы учеников'
        ordering = ['student__full_name', 'subject__name']
        indexes = [
            models.Index(fields=['student', 'is_active'], name='student_subject_active_idx'),
            models.Index(fields=['teacher', 'subject'], name='student_subject_teacher_idx'),
            models.Index(fields=['subject', 'student'], name='subject_student_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['student', 'subject', 'academic_year'],
                name='unique_student_ind_subject',
            ),
            models.UniqueConstraint(
                fields=['student', 'academic_year'],
                condition=Q(is_specialty=True, is_active=True),
                name='unique_active_specialty',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.student} — {self.subject} — {self.teacher}'

    def clean(self) -> None:
        super().clean()
        if not self.academic_year_id and self.student_id:
            enrollment = self.student.enrollment_for_year()
            if enrollment is not None:
                self.academic_year = enrollment.academic_year
        if self.academic_year_id:
            validate_active_academic_year(self.academic_year)
        if self.student_id and self.academic_year_id:
            enrollment = self.student.enrollment_for_year(self.academic_year)
            if enrollment is None:
                raise ValidationError({
                    'student': 'Ученик не зачислен в выбранный учебный год.'
                })
        if self.subject_id and not self.subject.is_specialty:
            raise ValidationError({
                'subject': 'Групповой предмет нельзя назначить индивидуальному ученику.'
            })

    def save(self, *args, **kwargs):
        if not self.academic_year_id and self.student_id:
            enrollment = self.student.enrollment_for_year()
            if enrollment is not None:
                self.academic_year = enrollment.academic_year
        previous = None
        if self.pk:
            previous = (
                type(self).objects
                .filter(pk=self.pk)
                .values('student_id', 'subject_id', 'teacher_id', 'is_active')
                .first()
            )

        self.full_clean()
        with transaction.atomic():
            self.subject_name_snapshot = self.subject.name
            self.teacher_name_snapshot = self.teacher.full_name
            self.final_grade_type_snapshot = self.subject.final_grade_type
            kwargs['update_fields'] = _with_snapshot_update_fields(
                kwargs.get('update_fields'),
                'subject_name_snapshot',
                'teacher_name_snapshot',
                'final_grade_type_snapshot',
            )
            super().save(*args, **kwargs)

            if self.is_active:
                ensure_teacher_academic_year_membership(
                    self.teacher_id,
                    self.academic_year_id,
                )
                ensure_teacher_subject(self.teacher_id, self.subject_id)
                if (
                    previous
                    and previous['is_active']
                    and previous['student_id'] == self.student_id
                    and previous['subject_id'] == self.subject_id
                    and previous['teacher_id'] != self.teacher_id
                ):
                    Grade.objects.filter(
                        student_id=self.student_id,
                        subject_id=self.subject_id,
                        teacher_id=previous['teacher_id'],
                        academic_year_id=self.academic_year_id,
                    ).update(
                        teacher_id=self.teacher_id,
                        teacher_name_snapshot=self.teacher.full_name,
                    )

            if previous:
                remove_unused_teacher_subject(
                    previous['teacher_id'],
                    previous['subject_id'],
                )

    def delete(self, *args, **kwargs):
        if self.academic_year_id:
            validate_active_academic_year(self.academic_year)
        teacher_id = self.teacher_id
        subject_id = self.subject_id
        with transaction.atomic():
            result = super().delete(*args, **kwargs)
            remove_unused_teacher_subject(teacher_id, subject_id)
            return result


def teacher_subject_is_used(teacher_id: int | None, subject_id: int | None) -> bool:
    if not teacher_id or not subject_id:
        return False

    return (
        GroupSubject.objects.filter(
            teacher_id=teacher_id,
            subject_id=subject_id,
            is_active=True,
        ).exists()
        or StudentSubject.objects.filter(
            teacher_id=teacher_id,
            subject_id=subject_id,
            is_active=True,
        ).exists()
    )


def ensure_teacher_subject(teacher_id: int | None, subject_id: int | None) -> None:
    if not teacher_id or not subject_id:
        return

    TeacherSubject.objects.get_or_create(
        teacher_id=teacher_id,
        subject_id=subject_id,
    )


def remove_unused_teacher_subject(teacher_id: int | None, subject_id: int | None) -> None:
    if not teacher_id or not subject_id:
        return
    if teacher_subject_is_used(teacher_id, subject_id):
        return

    TeacherSubject.objects.filter(
        teacher_id=teacher_id,
        subject_id=subject_id,
    ).delete()


def _with_snapshot_update_fields(update_fields, *snapshot_fields):
    if update_fields is None:
        return None
    return tuple(set(update_fields) | set(snapshot_fields))


class Grade(models.Model):
    GRADE_1 = '1'
    GRADE_2 = '2'
    GRADE_3 = '3'
    GRADE_4 = '4'
    GRADE_5 = '5'
    GRADE_ABSENT = 'Н'
    GRADE_CHOICES = (
        (GRADE_1, '1'),
        (GRADE_2, '2'),
        (GRADE_3, '3'),
        (GRADE_4, '4'),
        (GRADE_5, '5'),
        (GRADE_ABSENT, 'Н'),
    )
    ALLOWED_VALUES = {choice[0] for choice in GRADE_CHOICES}

    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name='grades',
        verbose_name='Ученик',
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.PROTECT,
        related_name='grades',
        verbose_name='Предмет',
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.PROTECT,
        related_name='grades',
        verbose_name='Преподаватель',
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name='grades',
        verbose_name='Учебный год',
        null=True,
        blank=True,
        help_text='Если не указать, будет определён по дате оценки.',
    )
    enrollment = models.ForeignKey(
        StudentEnrollment,
        on_delete=models.PROTECT,
        related_name='grades',
        verbose_name='Зачисление ученика',
        null=True,
        blank=True,
        editable=False,
    )
    date = models.DateField('Дата оценки')
    value = models.CharField('Оценка', max_length=10, choices=GRADE_CHOICES)
    comment = models.CharField('Комментарий', max_length=255, blank=True)
    student_name_snapshot = models.CharField(
        'ФИО ученика в учебном году',
        max_length=150,
        blank=True,
        editable=False,
    )
    group_name_snapshot = models.CharField(
        'Группа в учебном году',
        max_length=100,
        blank=True,
        editable=False,
    )
    subject_name_snapshot = models.CharField(
        'Название предмета в учебном году',
        max_length=100,
        blank=True,
        editable=False,
    )
    teacher_name_snapshot = models.CharField(
        'ФИО преподавателя в учебном году',
        max_length=150,
        blank=True,
        editable=False,
    )

    class Meta:
        verbose_name = 'Оценка'
        verbose_name_plural = 'Оценки'
        ordering = ['-date', 'student__full_name']
        indexes = [
            models.Index(fields=['student', 'subject', '-date'], name='grade_student_subject_idx'),
            models.Index(fields=['teacher', '-date'], name='grade_teacher_date_idx'),
            models.Index(fields=['subject', '-date'], name='grade_subject_date_idx'),
            models.Index(fields=['academic_year', 'subject'], name='grade_year_subject_idx'),
            models.Index(fields=['date'], name='grade_date_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['student', 'subject', 'date'],
                name='unique_grade_student_subject_date',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.student} | {self.subject} | {self.value} | {self.date}'

    @property
    def student_group(self):
        if self.enrollment_id:
            return self.enrollment.group
        return self.student.group if self.student_id else None

    @property
    def is_group_subject(self) -> bool:
        if not self.student_id or not self.subject_id or not self.teacher_id:
            return False
        return GroupSubject.objects.filter(
            group_id=self.enrollment.group_id if self.enrollment_id else self.student.group_id,
            subject_id=self.subject_id,
            teacher_id=self.teacher_id,
            is_active=True,
        ).exists()

    @property
    def is_individual_subject(self) -> bool:
        if not self.student_id or not self.subject_id or not self.teacher_id:
            return False
        return StudentSubject.objects.filter(
            student_id=self.student_id,
            subject_id=self.subject_id,
            teacher_id=self.teacher_id,
            academic_year_id=self.academic_year_id,
            is_active=True,
        ).exists()

    def normalize_value(self):
        if self.value:
            self.value = str(self.value).strip().upper()

    def full_clean(self, exclude=None, validate_unique=True, validate_constraints=True):
        self.normalize_value()
        return super().full_clean(
            exclude=exclude,
            validate_unique=validate_unique,
            validate_constraints=validate_constraints,
        )

    def clean(self) -> None:
        self.normalize_value()

        super().clean()
        student = None

        if self.value:
            self.value = str(self.value).strip().upper()
        if self.value not in self.ALLOWED_VALUES:
            raise ValidationError({'value': 'Оценка должна быть 1-5 или Н.'})

        if self.date and not self.academic_year_id:
            self.academic_year = AcademicYear.get_for_date(self.date) or AcademicYear.get_active()

        if self.academic_year_id:
            validate_active_academic_year(self.academic_year)

        if self.date and self.academic_year_id:
            if not (self.academic_year.starts_on <= self.date <= self.academic_year.ends_on):
                raise ValidationError({
                    'date': (
                        'Дата оценки должна попадать в период выбранного учебного года: '
                        f'{self.academic_year.starts_on:%d.%m.%Y} - {self.academic_year.ends_on:%d.%m.%Y}.'
                    )
                })

        if self.student_id:
            student = Student.objects.select_related('group', 'group__academic_year').get(pk=self.student_id)

        if student is not None and self.academic_year_id:
            enrollment = self.enrollment
            if enrollment is None or enrollment.academic_year_id != self.academic_year_id:
                enrollment = student.enrollment_for_year(self.academic_year)
                self.enrollment = enrollment
            if enrollment is None:
                raise ValidationError({
                    'student': 'Ученик не зачислен в выбранный учебный год.'
                })
            if enrollment.student_id != student.pk:
                raise ValidationError({'student': 'Зачисление относится к другому ученику.'})

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

        if self.student_id and self.subject_id and self.teacher_id:
            if student is None:
                student = Student.objects.select_related('group').get(pk=self.student_id)

            group_assignment_exists = GroupSubject.objects.filter(
                group_id=self.enrollment.group_id if self.enrollment_id else None,
                subject_id=self.subject_id,
                teacher_id=self.teacher_id,
                is_active=True,
            ).exists()

            individual_assignment_exists = StudentSubject.objects.filter(
                student_id=self.student_id,
                subject_id=self.subject_id,
                teacher_id=self.teacher_id,
                academic_year_id=self.academic_year_id,
                is_active=True,
            ).exists()

            if not group_assignment_exists and not individual_assignment_exists:
                raise ValidationError(
                    'Этот преподаватель не назначен этому ученику по выбранному предмету. '
                    'Проверьте предметы группы или индивидуальные предметы ученика.'
                )

    def save(self, *args, **kwargs):
        self.normalize_value()
        self.full_clean()
        if self.enrollment_id:
            self.student_name_snapshot = self.enrollment.full_name
            self.group_name_snapshot = self.enrollment.group.name if self.enrollment.group_id else ''
        self.subject_name_snapshot = self.subject.name
        self.teacher_name_snapshot = self.teacher.full_name
        kwargs['update_fields'] = _with_snapshot_update_fields(
            kwargs.get('update_fields'),
            'enrollment',
            'academic_year',
            'student_name_snapshot',
            'group_name_snapshot',
            'subject_name_snapshot',
            'teacher_name_snapshot',
        )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.academic_year_id:
            validate_active_academic_year(self.academic_year)
        return super().delete(*args, **kwargs)


class SubjectResult(models.Model):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name='subject_results',
        verbose_name='Ученик',
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.PROTECT,
        related_name='subject_results',
        verbose_name='Предмет',
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        related_name='subject_results',
        verbose_name='Учебный год',
    )
    enrollment = models.ForeignKey(
        StudentEnrollment,
        on_delete=models.PROTECT,
        related_name='subject_results',
        verbose_name='Зачисление ученика',
        null=True,
        blank=True,
        editable=False,
    )
    exam_grade = models.CharField('Экзамен', max_length=10, null=True, blank=True)
    final_grade = models.CharField('Итоговая оценка', max_length=10, null=True, blank=True)
    student_name_snapshot = models.CharField(
        'ФИО ученика в учебном году',
        max_length=150,
        blank=True,
        editable=False,
    )
    group_name_snapshot = models.CharField(
        'Группа в учебном году',
        max_length=100,
        blank=True,
        editable=False,
    )
    subject_name_snapshot = models.CharField(
        'Название предмета в учебном году',
        max_length=100,
        blank=True,
        editable=False,
    )
    final_grade_type_snapshot = models.CharField(
        'Тип итоговой оценки в учебном году',
        max_length=20,
        choices=Subject.FINAL_GRADE_TYPE_CHOICES,
        blank=True,
        editable=False,
    )

    class Meta:
        verbose_name = 'Итог по предмету'
        verbose_name_plural = 'Итоги по предметам'
        ordering = ['academic_year__name', 'student__full_name', 'subject__name']
        indexes = [
            models.Index(fields=['academic_year', 'subject'], name='result_year_subject_idx'),
            models.Index(fields=['student', 'academic_year'], name='result_student_year_idx'),
            models.Index(fields=['subject', 'student'], name='result_subject_student_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['student', 'subject', 'academic_year'],
                name='unique_result_student_subject_year',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.student} | {self.subject} | {self.academic_year}'

    @property
    def student_group(self):
        if self.enrollment_id:
            return self.enrollment.group
        return self.student.group if self.student_id else None

    def clean(self) -> None:
        super().clean()
        student = None

        if self.academic_year_id:
            validate_active_academic_year(self.academic_year)

        if self.student_id and self.subject_id:
            student = Student.objects.select_related('group', 'group__academic_year').get(pk=self.student_id)
            enrollment = self.enrollment
            if enrollment is None or enrollment.academic_year_id != self.academic_year_id:
                enrollment = student.enrollment_for_year(self.academic_year)
                self.enrollment = enrollment
            if enrollment is None:
                raise ValidationError({'student': 'Ученик не зачислен в выбранный учебный год.'})
            if enrollment.student_id != student.pk:
                raise ValidationError({'student': 'Зачисление относится к другому ученику.'})
            in_group_subjects = GroupSubject.objects.filter(
                group_id=enrollment.group_id,
                subject_id=self.subject_id,
                is_active=True,
            ).exists()
            in_individual_subjects = StudentSubject.objects.filter(
                student_id=self.student_id,
                subject_id=self.subject_id,
                academic_year_id=self.academic_year_id,
                is_active=True,
            ).exists()
            if not in_group_subjects and not in_individual_subjects:
                raise ValidationError(
                    'Нельзя выставить итог по предмету, который не назначен группе ученика '
                    'и не назначен ученику индивидуально.'
                )

        if self.subject_id:
            allowed_values = self.subject.get_final_grade_allowed_values()
            for field_name in ('exam_grade', 'final_grade'):
                value = getattr(self, field_name)
                normalized = Subject.normalize_final_grade(value)

                if normalized is None:
                    setattr(self, field_name, None)
                    continue

                if normalized not in allowed_values:
                    raise ValidationError({
                        field_name: 'Недопустимое значение итоговой оценки для выбранного предмета.'
                    })

                setattr(self, field_name, normalized)

    def save(self, *args, **kwargs):
        self.full_clean()
        if self.enrollment_id:
            self.student_name_snapshot = self.enrollment.full_name
            self.group_name_snapshot = self.enrollment.group.name if self.enrollment.group_id else ''
        self.subject_name_snapshot = self.subject.name
        self.final_grade_type_snapshot = self.subject.final_grade_type
        kwargs['update_fields'] = _with_snapshot_update_fields(
            kwargs.get('update_fields'),
            'enrollment',
            'student_name_snapshot',
            'group_name_snapshot',
            'subject_name_snapshot',
            'final_grade_type_snapshot',
        )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.academic_year_id:
            validate_active_academic_year(self.academic_year)
        return super().delete(*args, **kwargs)


class CourseRegistrationSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    telegram_group_url = models.URLField(
        'Ссылка на Telegram-группу',
        max_length=500,
        blank=True,
    )
    minimum_registration_age = models.PositiveSmallIntegerField(
        'Минимальный возраст для регистрации',
        default=14,
        help_text='Возраст считается на дату начала активного учебного года.',
    )
    updated_at = models.DateTimeField('Дата изменения', auto_now=True)

    class Meta:
        verbose_name = 'Настройка регистрации'
        verbose_name_plural = 'Настройки регистрации'

    def __str__(self) -> str:
        return 'Настройки регистрации на курсы'

    @classmethod
    def load(cls):
        settings_obj, _created = cls.objects.get_or_create(pk=1)
        return settings_obj

    def clean(self) -> None:
        super().clean()

        if self.telegram_group_url:
            self.telegram_group_url = self.telegram_group_url.strip()

        errors = {}
        if self.minimum_registration_age is None:
            errors['minimum_registration_age'] = 'Укажите минимальный возраст для регистрации.'
        elif self.minimum_registration_age > 120:
            errors['minimum_registration_age'] = 'Минимальный возраст не должен быть больше 120 лет.'

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PasswordRecoveryContact(models.Model):
    name = models.CharField('Имя администратора', max_length=150)
    phone = models.CharField('Номер телефона', max_length=32)
    messengers = models.CharField(
        'Мессенджеры',
        max_length=255,
        help_text='Укажите один или несколько мессенджеров, например: Telegram, WhatsApp.',
    )
    is_active = models.BooleanField('Показывать пользователям', default=True)
    display_order = models.PositiveSmallIntegerField('Порядок показа', default=0)
    updated_at = models.DateTimeField('Дата изменения', auto_now=True)

    class Meta:
        db_table = 'journal_password_recovery_settings'
        verbose_name = 'Контакт администратора'
        verbose_name_plural = 'Настройки восстановления пароля'
        ordering = ['display_order', 'name', 'pk']

    def __str__(self) -> str:
        return f'{self.name}: {self.phone}'

    def clean(self) -> None:
        super().clean()
        if self.name:
            self.name = self.name.strip()
        if self.phone:
            self.phone = normalize_phone_number(self.phone)
        if self.messengers:
            self.messengers = self.messengers.strip()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def phone_uri(self) -> str:
        digits = ''.join(character for character in self.phone if character.isdigit())
        return f'tel:+{digits}' if digits else ''


class TemporaryCredential(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='temporary_credentials',
        verbose_name='Пользователь',
        help_text='Пользователь, которому выданы временные учетные данные.',
    )
    course_application = models.OneToOneField(
        'CourseApplication',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='temporary_credential',
        verbose_name='Заявка на курсы',
        help_text='Заявка, по которой были выданы временные учетные данные.',
    )
    login = models.CharField('Логин', max_length=150)
    temporary_password = models.CharField('Временный пароль', max_length=128)
    created_at = models.DateTimeField('Дата и время создания', auto_now_add=True)
    student_phone = models.CharField('Телефон', max_length=32, blank=True)

    class Meta:
        verbose_name = 'Временные учетные данные'
        verbose_name_plural = 'Временные учетные данные'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['user'], name='temp_cred_user_idx'),
            models.Index(fields=['login'], name='temp_cred_login_idx'),
            models.Index(fields=['student_phone'], name='temp_cred_phone_idx'),
            models.Index(fields=['-created_at'], name='temp_cred_created_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user'],
                condition=Q(user__isnull=False),
                name='unique_temp_credential_user',
            ),
            models.UniqueConstraint(fields=['login'], name='unique_temp_credential_login'),
        ]

    def __str__(self) -> str:
        return self.login


class CourseRegistrationRateLimit(models.Model):
    cache_key = models.CharField('Ключ ограничения', max_length=255, unique=True)
    attempts = models.PositiveSmallIntegerField('Количество попыток', default=0)
    window_started_at = models.DateTimeField('Начало окна')
    updated_at = models.DateTimeField('Дата изменения', auto_now=True)

    class Meta:
        verbose_name = 'Ограничение регистрации'
        verbose_name_plural = 'Ограничения регистрации'
        indexes = [
            models.Index(fields=['cache_key'], name='course_reg_rate_key_idx'),
            models.Index(fields=['window_started_at'], name='course_reg_rate_window_idx'),
        ]

    def __str__(self) -> str:
        return self.cache_key


def normalize_student_identity_name(value: str) -> str:
    return ' '.join((value or '').split()).casefold().replace('ё', 'е')


def student_identity_lock_key(full_name: str, birth_date: date) -> int:
    identity = f'{normalize_student_identity_name(full_name)}|{birth_date.isoformat()}'.encode('utf-8')
    return int.from_bytes(blake2b(identity, digest_size=8).digest(), 'big', signed=True)


def lock_student_identity(full_name: str, birth_date: date) -> None:
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cursor:
        cursor.execute(
            'SELECT pg_advisory_xact_lock(%s)',
            [student_identity_lock_key(full_name, birth_date)],
        )


class CourseApplication(models.Model):
    STUDENT_COURSE_GROUP_NAME = 'Ученики курсов'
    DEFAULT_INSTRUMENT_NAME = 'Не указан'

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

    STATUS_CONFIRMED = 'confirmed'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = (
        (STATUS_CONFIRMED, 'Подтверждена'),
        (STATUS_REJECTED, 'Отклонена'),
    )

    registration_date = models.DateTimeField('Дата регистрации', auto_now_add=True)
    last_name = models.CharField('Фамилия', max_length=100)
    first_name = models.CharField('Имя', max_length=100)
    middle_name = models.CharField('Отчество', max_length=100, blank=True)
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
        default=STATUS_CONFIRMED,
        help_text=(
            'При отклонении удаляются только неиспользуемые записи этого учебного года; '
            'общий аккаунт и данные прошлых лет сохраняются.'
        ),
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='course_applications',
        verbose_name='Учебный год',
        editable=False,
        help_text='Учебный год, в рамках которого подана заявка.',
    )

    student = models.ForeignKey(
        Student,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='course_applications',
        verbose_name='Ученик в журнале',
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='course_applications',
        verbose_name='Пользователь ученика',
        editable=False,
    )
    generated_login = models.CharField(
        'Созданный логин',
        max_length=150,
        blank=True,
        editable=False,
        help_text='Логин, созданный для ученика по этой заявке.',
    )
    journal_created_at = models.DateTimeField(
        'Дата создания ученика в журнале',
        null=True,
        blank=True,
        editable=False,
    )
    journal_removed_at = models.DateTimeField(
        'Дата удаления ученика из журнала',
        null=True,
        blank=True,
        editable=False,
    )

    class Meta:
        verbose_name = 'Заявка на курсы'
        verbose_name_plural = 'Заявки на курсы'
        ordering = ['-registration_date', '-id']
        indexes = [
            models.Index(fields=['status', '-registration_date'], name='course_app_status_reg_idx'),
            models.Index(fields=['student_phone'], name='course_app_phone_idx'),
            models.Index(fields=['academic_year', 'student_phone'], name='course_app_year_phone_idx'),
            models.Index(fields=['generated_login'], name='course_app_login_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['academic_year', 'student_phone'],
                name='unique_course_app_phone_per_year',
            ),
        ]

    def __str__(self) -> str:
        return self.full_name

    @property
    def full_name(self) -> str:
        return ' '.join(
            part.strip()
            for part in (self.last_name, self.first_name, self.middle_name)
            if part and part.strip()
        )

    @property
    def age(self) -> int:
        from .registration_utils import calculate_age

        active_year = AcademicYear.get_active()
        reference_date = (
            self.academic_year.starts_on
            if self.academic_year_id
            else (active_year.starts_on if active_year else date.today())
        )
        return calculate_age(self.birth_date, today=reference_date)

    @property
    def has_journal_student(self) -> bool:
        return bool(self.student_id and self.user_id)

    def clean(self) -> None:
        super().clean()

        for field_name in ('last_name', 'first_name', 'middle_name', 'city_church', 'instrument'):
            value = getattr(self, field_name, '')
            if value:
                setattr(self, field_name, value.strip())

        if self.student_phone:
            self.student_phone = normalize_phone_number(self.student_phone)

        if self.academic_year_id is None:
            self.academic_year = AcademicYear.get_active()

        if self.academic_year_id is None:
            raise ValidationError('Сначала создайте активный учебный год.')

        if not academic_year_is_active(self.academic_year):
            raise ValidationError(ARCHIVED_ACADEMIC_YEAR_ERROR)

        if self.student_phone:
            duplicate_qs = CourseApplication.objects.filter(
                student_phone=self.student_phone,
                academic_year=self.academic_year,
            )
            if self.pk:
                duplicate_qs = duplicate_qs.exclude(pk=self.pk)
            if duplicate_qs.exists():
                raise ValidationError({
                    'student_phone': ValidationError(
                        'Ученик с таким номером телефона уже зарегистрирован.',
                        code='duplicate_phone_for_year',
                    ),
                })

        if self.parent_contacts:
            self.parent_contacts = normalize_parent_contacts(self.parent_contacts)

    def save(self, *args, **kwargs):
        self.full_clean()

        with transaction.atomic():
            super().save(*args, **kwargs)

            if self.status == self.STATUS_CONFIRMED:
                self.create_student_in_journal()
            elif self.status == self.STATUS_REJECTED:
                self.remove_student_from_journal()

    def delete(self, *args, **kwargs):
        if self.academic_year_id:
            validate_active_academic_year(self.academic_year)
        with transaction.atomic():
            self.remove_student_from_journal(clear_application_links=False)
            return super().delete(*args, **kwargs)

    def create_student_in_journal(self) -> None:
        """
        Создает ученика, пользователя и временные учетные данные по подтвержденной заявке.
        Метод безопасно вызывать повторно: если записи уже существуют, дубли не создаются.
        """
        from .account_utils import build_course_application_login, generate_temporary_password

        if not self.pk:
            return

        UserModel = get_user_model()
        lock_student_identity(self.full_name, self.birth_date)
        existing_student = self._get_existing_student()
        existing_user = self._get_existing_user(UserModel)
        if existing_user is None and existing_student is not None and existing_student.user_id:
            existing_user = existing_student.user

        created_user = None
        created_student = None
        temporary_password = None

        if existing_user is None:
            existing_logins = set(TemporaryCredential.objects.values_list('login', flat=True))
            existing_logins.update(UserModel.objects.values_list('username', flat=True))

            preferred_login = self.generated_login.strip() if self.generated_login else ''
            if preferred_login and preferred_login not in existing_logins:
                login = preferred_login
            else:
                login = build_course_application_login(
                    self.last_name,
                    self.first_name,
                    existing_logins=existing_logins,
                )

            temporary_password = generate_temporary_password()
            existing_user = UserModel.objects.create_user(
                username=login,
                password=temporary_password,
                first_name=self.first_name,
                last_name=self.last_name,
            )
            created_user = existing_user
        else:
            login = existing_user.username
            UserModel.objects.filter(pk=existing_user.pk).update(
                first_name=self.first_name,
                last_name=self.last_name,
            )

        course_year = self.academic_year or AcademicYear.get_active()
        if course_year is None:
            raise ValidationError('Сначала создайте активный учебный год.')
        validate_active_academic_year(course_year)

        group, _ = StudyGroup.objects.get_or_create(
            name=self.STUDENT_COURSE_GROUP_NAME,
            academic_year=course_year,
            defaults={'is_active': True},
        )
        if not group.is_active:
            group.is_active = True
            group.save(update_fields=['is_active'])
        instrument_name = self.instrument.strip() or self.DEFAULT_INSTRUMENT_NAME
        instrument, _ = Instrument.objects.get_or_create(name=instrument_name)

        enrollment_existed = (
            existing_student is not None
            and StudentEnrollment.objects.filter(
                student=existing_student,
                academic_year=course_year,
            ).exists()
        )

        if existing_student is None:
            existing_student = Student.objects.create(
                full_name=self.full_name,
                gender=self.gender,
                birth_date=self.birth_date,
                city_church=self.city_church,
                group=group,
                instrument=instrument,
                music_education=self.music_education,
                student_phone=self.student_phone,
                parent_contacts=self.parent_contacts,
                comments=self.comments,
                user=existing_user,
                is_active=True,
            )
            created_student = existing_student
        else:
            existing_student.full_name = self.full_name
            existing_student.gender = self.gender
            existing_student.birth_date = self.birth_date
            existing_student.city_church = self.city_church
            existing_student.group = group
            existing_student.instrument = instrument
            existing_student.music_education = self.music_education
            existing_student.student_phone = self.student_phone
            existing_student.parent_contacts = self.parent_contacts
            existing_student.comments = self.comments
            existing_student.user = existing_user
            existing_student.is_active = True
            existing_student.save()

        temporary_credential = self._get_existing_temporary_credential(login)
        if temporary_credential is None and temporary_password is not None:
            TemporaryCredential.objects.create(
                user=existing_user,
                course_application=self,
                login=login,
                temporary_password=temporary_password,
                student_phone=self.student_phone,
            )
        elif temporary_credential is not None:
            updates = []
            if temporary_credential.user_id != existing_user.pk:
                temporary_credential.user = existing_user
                updates.append('user')
            if temporary_credential.login != login:
                temporary_credential.login = login
                updates.append('login')
            if (
                temporary_password is not None
                and temporary_credential.course_application_id != self.pk
            ):
                temporary_credential.course_application = self
                updates.append('course_application')
            if temporary_credential.student_phone != self.student_phone:
                temporary_credential.student_phone = self.student_phone
                updates.append('student_phone')
            if updates:
                temporary_credential.save(update_fields=updates)

        CourseApplication.objects.filter(pk=self.pk).update(
            student=existing_student,
            user=existing_user,
            generated_login=login,
            academic_year=course_year,
            journal_created_at=(
                timezone.now()
                if created_user or created_student or not enrollment_existed
                else self.journal_created_at
            ),
            journal_removed_at=None,
        )

        self.student = existing_student
        self.user = existing_user
        self.generated_login = login
        self.academic_year = course_year
        if created_user or created_student or not enrollment_existed:
            self.journal_created_at = timezone.now()
        self.journal_removed_at = None

    def remove_student_from_journal(self, *, clear_application_links: bool = True) -> None:
        """
        Удаляет ученика из электронного журнала при отклонении заявки.
        Сама заявка не удаляется.
        """
        if not self.pk:
            return

        login = self.generated_login or ''
        student = self._get_existing_student()
        user = self._get_existing_user(get_user_model())

        replacement_application = self._other_confirmed_application(
            student=student,
            user=user,
        )
        credential_qs = TemporaryCredential.objects.filter(course_application_id=self.pk)
        if replacement_application is not None:
            # One person may have more than one application in the same year
            # (for example, after correcting the phone number). Rejecting or
            # deleting one of them must not remove the shared account,
            # enrollment or the only temporary credential used by the other
            # confirmed application.
            credential_qs.update(
                course_application=replacement_application,
                student_phone=replacement_application.student_phone,
            )
            if clear_application_links:
                CourseApplication.objects.filter(pk=self.pk).update(
                    student=None,
                    user=None,
                    journal_removed_at=timezone.now(),
                )
                self.student = None
                self.user = None
                self.journal_removed_at = timezone.now()
            return

        credential_qs.delete()

        if student is not None:
            enrollment = student.enrollment_for_year(self.academic_year)
            if enrollment is not None:
                Grade.objects.filter(enrollment=enrollment).delete()
                SubjectResult.objects.filter(enrollment=enrollment).delete()
                StudentSubject.objects.filter(
                    student=student,
                    academic_year=self.academic_year,
                ).delete()
                Student.objects.filter(pk=student.pk).update(group=None)
                enrollment.delete()

            if not student.enrollments.exists():
                student.delete()
                if user is not None and not user.is_staff and not user.is_superuser:
                    user.delete()
            else:
                Student.objects.filter(pk=student.pk).update(
                    group=None,
                    is_active=False,
                )

        if clear_application_links:
            CourseApplication.objects.filter(pk=self.pk).update(
                student=None,
                user=None,
                journal_removed_at=timezone.now(),
            )
            self.student = None
            self.user = None
            self.journal_removed_at = timezone.now()

    def _other_confirmed_application(self, *, student, user):
        if self.academic_year_id is None:
            return None

        candidates = (
            CourseApplication.objects
            .select_for_update()
            .filter(
                academic_year_id=self.academic_year_id,
                status=self.STATUS_CONFIRMED,
            )
            .exclude(pk=self.pk)
        )
        shared_identity = Q()
        if student is not None:
            shared_identity |= Q(student_id=student.pk)
        if user is not None:
            shared_identity |= Q(user_id=user.pk)
        if not shared_identity:
            return None
        return candidates.filter(shared_identity).order_by('registration_date', 'pk').first()

    def _get_existing_user(self, UserModel):
        if self.user_id:
            user = UserModel.objects.filter(pk=self.user_id).first()
            if user is not None:
                return user

        if self.generated_login:
            user = UserModel.objects.filter(username=self.generated_login).first()
            if user is not None:
                return user

        return None

    def _get_existing_student(self):
        if self.student_id:
            student = Student.objects.filter(pk=self.student_id).first()
            if student is not None:
                return student

        if self.user_id:
            student = Student.objects.filter(user_id=self.user_id).first()
            if student is not None:
                return student

        if self.generated_login:
            student = Student.objects.filter(user__username=self.generated_login).first()
            if student is not None:
                return student

        if self.birth_date and self.full_name:
            candidates = (
                Student.objects
                .select_for_update()
                .filter(birth_date=self.birth_date)
                .select_related('user')
                .order_by('pk')
            )
            identity_name = normalize_student_identity_name(self.full_name)
            for student in candidates:
                if normalize_student_identity_name(student.full_name) == identity_name:
                    return student

        return None

    def _get_existing_temporary_credential(self, login: str):
        credential = TemporaryCredential.objects.filter(course_application_id=self.pk).first()
        if credential is not None:
            return credential

        if login:
            credential = TemporaryCredential.objects.filter(login=login).first()
            if credential is not None:
                return credential

        return None


def sync_people_with_active_academic_year(academic_year_id: int) -> None:
    """Restore current student and teacher state for the active academic year."""
    enrolled_student_ids = StudentEnrollment.objects.filter(
        academic_year_id=academic_year_id,
    ).values('student_id')
    Student.objects.exclude(pk__in=enrolled_student_ids).update(
        group=None,
        is_active=False,
    )
    Student.objects.filter(group__isnull=False).exclude(
        group__academic_year_id=academic_year_id,
    ).update(group=None)

    enrollments = list(
        StudentEnrollment.objects
        .filter(academic_year_id=academic_year_id)
        .only('student_id', 'group_id', 'is_active')
    )
    students_by_id = {
        student.pk: student
        for student in Student.objects.filter(
            pk__in=[enrollment.student_id for enrollment in enrollments],
        )
    }
    students_to_update = []
    for enrollment in enrollments:
        student = students_by_id.get(enrollment.student_id)
        if student is None:
            continue
        student.group_id = enrollment.group_id
        student.is_active = enrollment.is_active
        students_to_update.append(student)

    if students_to_update:
        Student.objects.bulk_update(
            students_to_update,
            ('group', 'is_active'),
            batch_size=500,
        )

    active_teacher_ids = TeacherEnrollment.objects.filter(
        academic_year_id=academic_year_id,
        is_active=True,
    ).values('teacher_id')
    Teacher.objects.exclude(pk__in=active_teacher_ids).update(is_active=False)
    Teacher.objects.filter(pk__in=active_teacher_ids).update(is_active=True)


def finalize_academic_year_snapshots(academic_year_id: int) -> None:
    """Fix the final display values before an academic year becomes read-only."""
    updated_at = timezone.now()

    enrollments = list(
        StudentEnrollment.objects
        .filter(academic_year_id=academic_year_id)
        .select_related('student__instrument')
    )
    for enrollment in enrollments:
        enrollment.copy_from_student(enrollment.student)
        enrollment.updated_at = updated_at
    if enrollments:
        StudentEnrollment.objects.bulk_update(
            enrollments,
            (
                'full_name',
                'gender',
                'birth_date',
                'city_church',
                'instrument_name',
                'music_education',
                'student_phone',
                'parent_contacts',
                'comments',
                'is_active',
                'updated_at',
            ),
            batch_size=500,
        )

    group_assignments = list(
        GroupSubject.objects
        .filter(group__academic_year_id=academic_year_id)
        .select_related('subject', 'teacher')
    )
    for assignment in group_assignments:
        assignment.subject_name_snapshot = assignment.subject.name
        assignment.teacher_name_snapshot = assignment.teacher.full_name
        assignment.final_grade_type_snapshot = assignment.subject.final_grade_type
    if group_assignments:
        GroupSubject.objects.bulk_update(
            group_assignments,
            (
                'subject_name_snapshot',
                'teacher_name_snapshot',
                'final_grade_type_snapshot',
            ),
            batch_size=500,
        )

    individual_assignments = list(
        StudentSubject.objects
        .filter(academic_year_id=academic_year_id)
        .select_related('subject', 'teacher')
    )
    for assignment in individual_assignments:
        assignment.subject_name_snapshot = assignment.subject.name
        assignment.teacher_name_snapshot = assignment.teacher.full_name
        assignment.final_grade_type_snapshot = assignment.subject.final_grade_type
    if individual_assignments:
        StudentSubject.objects.bulk_update(
            individual_assignments,
            (
                'subject_name_snapshot',
                'teacher_name_snapshot',
                'final_grade_type_snapshot',
            ),
            batch_size=500,
        )

    grades = list(
        Grade.objects
        .filter(academic_year_id=academic_year_id)
        .select_related('student', 'enrollment__group', 'subject', 'teacher')
    )
    for grade in grades:
        enrollment = grade.enrollment
        grade.student_name_snapshot = (
            enrollment.full_name
            if enrollment is not None
            else grade.student.full_name
        )
        grade.group_name_snapshot = (
            enrollment.group.name
            if enrollment is not None and enrollment.group_id
            else grade.group_name_snapshot
        )
        grade.subject_name_snapshot = grade.subject.name
        grade.teacher_name_snapshot = grade.teacher.full_name
    if grades:
        Grade.objects.bulk_update(
            grades,
            (
                'student_name_snapshot',
                'group_name_snapshot',
                'subject_name_snapshot',
                'teacher_name_snapshot',
            ),
            batch_size=500,
        )

    results = list(
        SubjectResult.objects
        .filter(academic_year_id=academic_year_id)
        .select_related('student', 'enrollment__group', 'subject')
    )
    for result in results:
        enrollment = result.enrollment
        result.student_name_snapshot = (
            enrollment.full_name
            if enrollment is not None
            else result.student.full_name
        )
        result.group_name_snapshot = (
            enrollment.group.name
            if enrollment is not None and enrollment.group_id
            else result.group_name_snapshot
        )
        result.subject_name_snapshot = result.subject.name
        result.final_grade_type_snapshot = result.subject.final_grade_type
    if results:
        SubjectResult.objects.bulk_update(
            results,
            (
                'student_name_snapshot',
                'group_name_snapshot',
                'subject_name_snapshot',
                'final_grade_type_snapshot',
            ),
            batch_size=500,
        )
