from datetime import date
from urllib.parse import quote
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

NUMERIC_GRADE_VALUES = tuple(
    value
    for number in range(1, 6)
    for value in (str(number), f'{number}+', f'{number}-')
)
ABSENT_GRADE_VALUE = 'Н'


def normalize_grade_value(value):
    if value is None or value == '':
        return None

    normalized = str(value).strip()
    if normalized.casefold() in {'н', 'n'}:
        return ABSENT_GRADE_VALUE
    return normalized


def academic_year_name_for_dates(starts_on: date, ends_on: date) -> str:
    if starts_on.year == ends_on.year:
        return str(starts_on.year)
    return f'{starts_on.year}/{ends_on.year}'


def academic_year_is_active(academic_year: 'AcademicYear | None') -> bool:
    if academic_year is None:
        return False
    if getattr(academic_year, 'pk', None):
        active_pk = AcademicYear.active_pk()
        return academic_year.pk == active_pk
    return bool(academic_year.is_active)


def validate_active_academic_year(academic_year: 'AcademicYear | None', field_name: str = 'academic_year') -> None:
    if academic_year is not None and not academic_year_is_active(academic_year):
        raise ValidationError({field_name: ARCHIVED_ACADEMIC_YEAR_ERROR})


def academic_year_for_object(obj):
    if obj is None:
        return None
    if isinstance(obj, AcademicYear):
        return obj
    if isinstance(obj, UserAcademicYearMembership):
        return obj.academic_year if obj.academic_year_id else None
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
    if isinstance(obj, (AssessmentGroup, AssessmentItem, StudentAssessmentGroup, FinalGradeRule)):
        return obj.academic_year if obj.academic_year_id else None
    if isinstance(obj, AssessmentResult):
        return obj.item.academic_year if obj.item_id else None
    return None


def object_is_in_archived_academic_year(obj) -> bool:
    academic_year = academic_year_for_object(obj)
    # Admin querysets load the related year together with the row.  Keeping the
    # permission check in memory prevents the same lookup from being repeated
    # for every inline and every permission hook on a change page.
    return academic_year is not None and not bool(academic_year.is_active)


class AcademicYear(models.Model):
    _active_pk_cache = None
    _active_pk_cache_ready = False

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
                cls._active_pk_cache = None
                cls._active_pk_cache_ready = True
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
            cls._active_pk_cache = latest_year.pk
            cls._active_pk_cache_ready = True

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
        active_pk = cls.active_pk()
        return cls.objects.filter(pk=active_pk).first() if active_pk is not None else None

    @classmethod
    def active_pk(cls):
        if not cls._active_pk_cache_ready:
            cls._active_pk_cache = (
                cls.objects
                .filter(is_active=True)
                .values_list('pk', flat=True)
                .first()
            )
            cls._active_pk_cache_ready = True
        return cls._active_pk_cache

    @classmethod
    def get_for_date(cls, date):
        if not date:
            return None
        return cls.objects.filter(starts_on__lte=date, ends_on__gte=date).first()


class UserAcademicYearMembership(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='journal_year_memberships',
        verbose_name='Пользователь',
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name='user_memberships',
        verbose_name='Учебный год',
    )
    is_active = models.BooleanField('Активен в учебном году', default=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Изменено', auto_now=True)

    class Meta:
        verbose_name = 'Участие пользователя в учебном году'
        verbose_name_plural = 'Участие пользователей в учебных годах'
        ordering = ['-academic_year__starts_on', 'user__username']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'academic_year'],
                name='unique_user_academic_year_membership',
            ),
        ]
        indexes = [
            models.Index(fields=['academic_year', 'user'], name='user_year_membership_idx'),
            models.Index(fields=['user', 'is_active'], name='user_year_active_idx'),
        ]

    def __str__(self):
        return f'{self.user} — {self.academic_year}'

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


def ensure_user_academic_year_membership(
    user_id: int | None,
    academic_year_id: int | None,
    *,
    is_active: bool = True,
) -> None:
    if not user_id or not academic_year_id:
        return
    UserAcademicYearMembership.objects.update_or_create(
        user_id=user_id,
        academic_year_id=academic_year_id,
        defaults={'is_active': is_active},
    )


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


class OrchestraPart(models.Model):
    instrument = models.ForeignKey(
        Instrument,
        on_delete=models.CASCADE,
        related_name='orchestra_parts',
        verbose_name='Инструмент',
    )
    name = models.CharField('Название партии', max_length=255)
    is_active = models.BooleanField('Активна', default=True)

    class Meta:
        verbose_name = 'Партия оркестра'
        verbose_name_plural = 'Партии оркестра'
        ordering = ['instrument__name', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['instrument', 'name'],
                name='unique_orchestra_part_instrument_name',
            ),
        ]
        indexes = [
            models.Index(
                fields=['instrument', 'is_active', 'name'],
                name='orch_part_instrument_idx',
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if self.name:
            self.name = self.name.strip()

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class Subject(models.Model):
    ASSESSMENT_MODE_STANDARD = 'standard'
    ASSESSMENT_MODE_ELEMENTS = 'elements'
    ASSESSMENT_MODE_CHOICES = (
        (ASSESSMENT_MODE_STANDARD, 'Обычный журнал'),
        (ASSESSMENT_MODE_ELEMENTS, 'Сдача произведений / элементов'),
    )

    FINAL_GRADE_TYPE_NUMERIC = 'numeric'
    FINAL_GRADE_TYPE_PASS_FAIL = 'pass_fail'
    FINAL_GRADE_TYPE_CHOICES = (
        (FINAL_GRADE_TYPE_NUMERIC, 'Пятибалльная (1-5, Н)'),
        (FINAL_GRADE_TYPE_PASS_FAIL, 'Зачет/незачет'),
    )

    name = models.CharField('Название предмета', max_length=100, unique=True)
    assessment_mode = models.CharField(
        'Режим аттестации',
        max_length=20,
        choices=ASSESSMENT_MODE_CHOICES,
        default=ASSESSMENT_MODE_STANDARD,
        help_text='Специальный режим включается явно и не зависит от названия предмета.',
    )
    final_grade_type = models.CharField(
        'Тип итоговой оценки',
        max_length=20,
        choices=FINAL_GRADE_TYPE_CHOICES,
        default=FINAL_GRADE_TYPE_NUMERIC,
        help_text='Используется для подсказок; сами оценки хранятся строками.',
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
            models.Index(fields=['assessment_mode'], name='subject_assessment_mode_idx'),
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
        if self.pk:
            previous_grade_type = (
                type(self).objects
                .filter(pk=self.pk)
                .values_list('final_grade_type', flat=True)
                .first()
            )
            if previous_grade_type and previous_grade_type != self.final_grade_type:
                allowed_values = self.get_final_grade_allowed_values()
                result_values = self.subject_results.values_list(
                    'exam_grade',
                    'final_grade',
                )
                has_incompatible_results = any(
                    value not in {None, ''} and self.normalize_final_grade(value) not in allowed_values
                    for result in result_values
                    for value in result
                )
                if has_incompatible_results:
                    raise ValidationError({
                        'final_grade_type': (
                            'Нельзя изменить тип итоговой оценки: '
                            'существующие результаты ему не соответствуют.'
                        ),
                    })
        if self.pk and self.is_specialty and self.group_subjects.exists():
            raise ValidationError({
                'is_specialty': 'Нельзя сделать предмет индивидуальным, пока он назначен группам.'
            })
        if self.pk and not self.is_specialty and self.individual_students.exists():
            raise ValidationError({
                'is_specialty': 'Нельзя сделать предмет групповым, пока он назначен индивидуальным ученикам.'
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def uses_element_assessment(self) -> bool:
        return self.assessment_mode == self.ASSESSMENT_MODE_ELEMENTS

    def get_final_grade_allowed_values(self) -> set[str]:
        if self.final_grade_type == self.FINAL_GRADE_TYPE_PASS_FAIL:
            return {'Зачет', 'Незачет', 'Не аттестован'}
        return {*NUMERIC_GRADE_VALUES, ABSENT_GRADE_VALUE}

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

        return normalize_grade_value(normalized)

    def validate_final_grade(self, value):
        normalized = self.normalize_final_grade(value)
        if normalized is None:
            return None
        if normalized not in self.get_final_grade_allowed_values():
            if self.final_grade_type == self.FINAL_GRADE_TYPE_PASS_FAIL:
                message = 'Допустимы значения: Зачет, Незачет или Не аттестован.'
            else:
                message = 'Допустимы оценки от 1 до 5 со знаком +/− либо Н.'
            raise ValidationError(message)
        return normalized


class StudyGroup(models.Model):
    name = models.CharField('Название группы', max_length=100)
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
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
    birth_date = models.DateField('Дата рождения', null=True, blank=True, db_index=True)
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
        on_delete=models.CASCADE,
        related_name='academic_year_memberships',
        verbose_name='Преподаватель',
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
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
        result = super().save(*args, **kwargs)
        ensure_user_academic_year_membership(
            self.teacher.user_id,
            self.academic_year_id,
            is_active=self.is_active,
        )
        return result

    def delete(self, *args, **kwargs):
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

    MUSIC_EDUCATION_SELF = 'self_taught'
    MUSIC_EDUCATION_BASIC = 'basic'
    MUSIC_EDUCATION_SECONDARY = 'secondary'
    MUSIC_EDUCATION_HIGHER = 'higher'
    # Compatibility alias for old code and fixtures. Existing database value
    # ``none`` is converted to ``self_taught`` by migration 0020.
    MUSIC_EDUCATION_NONE = MUSIC_EDUCATION_SELF
    MUSIC_EDUCATION_CHOICES = (
        (MUSIC_EDUCATION_SELF, 'Самоучка'),
        (MUSIC_EDUCATION_BASIC, 'Музыкальная школа'),
        (MUSIC_EDUCATION_SECONDARY, 'Колледж'),
        (MUSIC_EDUCATION_HIGHER, 'Институт'),
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
        on_delete=models.CASCADE,
        related_name='students',
        verbose_name='Инструмент из справочника',
        null=True,
        blank=True,
    )
    custom_instrument = models.CharField(
        'Собственный инструмент',
        max_length=255,
        blank=True,
        help_text='Заполняется только когда подходящего инструмента нет в справочнике.',
    )
    orchestra_part = models.ForeignKey(
        OrchestraPart,
        on_delete=models.CASCADE,
        related_name='students',
        verbose_name='Партия в оркестре',
        null=True,
        blank=True,
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
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(instrument__isnull=False, custom_instrument='')
                    | (Q(instrument__isnull=True) & ~Q(custom_instrument=''))
                ),
                name='student_exactly_one_instrument_source',
            ),
            models.CheckConstraint(
                condition=(
                    Q(orchestra_part__isnull=True)
                    | Q(instrument__isnull=False, custom_instrument='')
                ),
                name='student_part_requires_reference_instrument',
            ),
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
        self.custom_instrument = (self.custom_instrument or '').strip()
        if bool(self.instrument_id) == bool(self.custom_instrument):
            raise ValidationError({
                'instrument': 'Выберите инструмент из справочника или укажите собственный, но не оба варианта.',
                'custom_instrument': 'Укажите собственный инструмент только при пустом справочном значении.',
            })
        if self.custom_instrument:
            self.orchestra_part = None
        elif self.orchestra_part_id and not OrchestraPart.objects.filter(
            pk=self.orchestra_part_id,
            instrument_id=self.instrument_id,
        ).exists():
            raise ValidationError({
                'orchestra_part': 'Выбранная партия не относится к инструменту ученика.',
            })
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
                subject__is_specialty=True,
                is_active=True,
                academic_year__is_active=True,
            )
            .first()
        )

    @property
    def instrument_display(self) -> str:
        if self.instrument_id:
            return self.instrument.name
        return self.custom_instrument or 'Не указан'

    @property
    def orchestra_part_display(self) -> str:
        return self.orchestra_part.name if self.orchestra_part_id else ''

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
        on_delete=models.CASCADE,
        related_name='enrollments',
        verbose_name='Ученик',
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
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
    orchestra_part = models.CharField('Партия в оркестре', max_length=255, blank=True)
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
            models.Index(
                fields=['academic_year', 'is_active', 'group'],
                name='enroll_year_active_group_idx',
            ),
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
            'instrument_name': student.instrument_display,
            'orchestra_part': student.orchestra_part_display,
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
        result = super().save(*args, **kwargs)
        ensure_user_academic_year_membership(
            self.student.user_id,
            self.academic_year_id,
            is_active=self.is_active,
        )
        return result

    def delete(self, *args, **kwargs):
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
        on_delete=models.CASCADE,
        related_name='group_subjects',
        verbose_name='Предмет',
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
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
        on_delete=models.CASCADE,
        related_name='individual_students',
        verbose_name='Предмет',
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name='individual_subjects',
        verbose_name='Преподаватель',
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name='student_subjects',
        verbose_name='Учебный год',
        editable=False,
    )
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
            models.Index(
                fields=['academic_year', 'is_active', 'student'],
                name='stud_subj_year_active_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['student', 'subject', 'academic_year'],
                name='unique_student_ind_subject',
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
        or AssessmentItem.objects.filter(
            responsible_teacher_id=teacher_id,
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
    GRADE_CHOICES = tuple((value, value) for value in NUMERIC_GRADE_VALUES) + (
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
        on_delete=models.CASCADE,
        related_name='grades',
        verbose_name='Предмет',
    )
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name='grades',
        verbose_name='Преподаватель',
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name='grades',
        verbose_name='Учебный год',
        null=True,
        blank=True,
        help_text='Если не указать, будет определён по дате оценки.',
    )
    enrollment = models.ForeignKey(
        StudentEnrollment,
        on_delete=models.CASCADE,
        related_name='grades',
        verbose_name='Зачисление ученика',
        null=True,
        blank=True,
        editable=False,
    )
    date = models.DateField('Дата оценки')
    value = models.CharField(
        'Оценка',
        max_length=64,
        help_text='Произвольное строковое значение, например 5+, 4-, N или Зачет.',
    )
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
            models.Index(
                fields=['enrollment', 'subject', '-date'],
                name='grade_enroll_subj_date_idx',
            ),
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
        self.value = normalize_grade_value(self.value)

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

        if not self.value:
            raise ValidationError({'value': 'Укажите оценку.'})
        if self.value not in self.ALLOWED_VALUES:
            raise ValidationError({
                'value': 'Допустимы оценки от 1 до 5 со знаком +/− либо Н.',
            })

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

        if self.subject_id and self.subject.uses_element_assessment:
            raise ValidationError({
                'subject': 'Для этого предмета результаты выставляются отдельно по произведениям.'
            })

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
        on_delete=models.CASCADE,
        related_name='subject_results',
        verbose_name='Предмет',
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name='subject_results',
        verbose_name='Учебный год',
    )
    enrollment = models.ForeignKey(
        StudentEnrollment,
        on_delete=models.CASCADE,
        related_name='subject_results',
        verbose_name='Зачисление ученика',
        null=True,
        blank=True,
        editable=False,
    )
    exam_grade = models.CharField('Экзамен', max_length=64, null=True, blank=True)
    final_grade = models.CharField('Итоговая оценка', max_length=64, null=True, blank=True)
    is_auto_calculated = models.BooleanField('Рассчитано автоматически', default=False)
    calculation_details = models.JSONField('Детали расчёта', default=dict, blank=True)
    calculated_at = models.DateTimeField('Дата автоматического расчёта', null=True, blank=True)
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
            models.Index(
                fields=['enrollment', 'subject'],
                name='result_enroll_subject_idx',
            ),
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
            in_assessment_groups = StudentAssessmentGroup.objects.filter(
                student_id=self.student_id,
                assessment_group__subject_id=self.subject_id,
                academic_year_id=self.academic_year_id,
                is_active=True,
            ).exists()
            preserves_automatic_history = self.is_auto_calculated and bool(self.pk)
            if (
                not in_group_subjects
                and not in_individual_subjects
                and not in_assessment_groups
                and not preserves_automatic_history
            ):
                raise ValidationError(
                    'Нельзя выставить итог по предмету, который не назначен группе ученика '
                    'или ученику напрямую, в том числе через группу произведений.'
                )

        if self.subject_id:
            for field_name in ('exam_grade', 'final_grade'):
                value = getattr(self, field_name)
                if field_name == 'final_grade' and self.is_auto_calculated:
                    normalized = (
                        str(value).strip()
                        if value not in {None, ''}
                        else None
                    )
                else:
                    normalized = self.subject.validate_final_grade(value)
                setattr(self, field_name, normalized)

    def save(self, *args, allow_auto_update: bool = False, **kwargs):
        if self.subject_id and self.subject.uses_element_assessment and not allow_auto_update:
            previous_final = None
            if self.pk:
                previous_final = type(self).objects.filter(pk=self.pk).values_list('final_grade', flat=True).first()
            if self.final_grade and self.final_grade != previous_final:
                raise ValidationError({
                    'final_grade': 'Итог по этому предмету рассчитывается автоматически.'
                })
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
        return super().delete(*args, **kwargs)


class AssessmentGroup(models.Model):
    name = models.CharField('Название группы произведений', max_length=150)
    description = models.TextField('Описание', blank=True)
    subject = models.ForeignKey(
        Subject,
        on_delete=models.CASCADE,
        related_name='assessment_groups',
        verbose_name='Предмет',
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name='assessment_groups',
        verbose_name='Учебный год',
    )
    sort_order = models.PositiveIntegerField('Порядок отображения', default=100)
    is_active = models.BooleanField('Активна', default=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Изменено', auto_now=True)

    class Meta:
        verbose_name = 'Группа произведений'
        verbose_name_plural = 'Группы произведений'
        ordering = ['academic_year__starts_on', 'subject__name', 'sort_order', 'name', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['subject', 'academic_year', 'name'],
                name='unique_assessment_group_subject_year_name',
            ),
        ]
        indexes = [
            models.Index(fields=['academic_year', 'subject'], name='assess_group_year_subject_idx'),
            models.Index(fields=['is_active', 'sort_order'], name='assess_group_active_order_idx'),
            models.Index(
                fields=['academic_year', 'is_active', 'sort_order'],
                name='assess_group_year_active_idx',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.name} — {self.subject} — {self.academic_year}'

    def clean(self) -> None:
        super().clean()
        self.name = (self.name or '').strip()
        if not self.name:
            raise ValidationError({'name': 'Введите название группы произведений.'})
        if self.subject_id and not self.subject.uses_element_assessment:
            raise ValidationError({
                'subject': 'Группы произведений доступны только для предмета со специальным режимом.'
            })
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                'subject_id', 'academic_year_id'
            ).first()
            if previous and (
                previous['subject_id'] != self.subject_id
                or previous['academic_year_id'] != self.academic_year_id
            ) and (
                self.items.exists()
                or self.student_assignments.exists()
                or self.final_grade_rules.exists()
            ):
                raise ValidationError(
                    'Нельзя менять предмет или учебный год используемой группы. '
                    'Создайте новую группу, чтобы сохранить исторические данные.'
                )
        if self.academic_year_id:
            validate_active_academic_year(self.academic_year)

    def save(self, *args, **kwargs):
        previous_is_active = None
        if self.pk:
            previous_is_active = type(self).objects.filter(pk=self.pk).values_list(
                'is_active', flat=True
            ).first()
        self.full_clean()
        result = super().save(*args, **kwargs)
        if previous_is_active is not None and previous_is_active != self.is_active:
            from .assessment_services import recalculate_group_finals
            recalculate_group_finals(self)
        return result


class AssessmentElement(models.Model):
    subject = models.ForeignKey(
        Subject,
        on_delete=models.CASCADE,
        related_name='assessment_element_catalog',
        verbose_name='Предмет',
    )
    title = models.CharField('Произведение / элемент', max_length=255)
    description = models.TextField('Описание', blank=True)
    is_active = models.BooleanField('Активно', default=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Изменено', auto_now=True)

    class Meta:
        verbose_name = 'Произведение / элемент'
        verbose_name_plural = 'Произведения / элементы'
        ordering = ['subject__name', 'title', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['subject', 'title'],
                name='unique_assessment_element_subject_title',
            ),
        ]
        indexes = [
            models.Index(
                fields=['subject', 'is_active', 'title'],
                name='assess_element_subject_idx',
            ),
        ]

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        super().clean()
        self.title = (self.title or '').strip()
        self.description = (self.description or '').strip()
        if not self.title:
            raise ValidationError({'title': 'Введите название произведения или элемента.'})
        if self.subject_id and not self.subject.uses_element_assessment:
            raise ValidationError({
                'subject': 'Каталог произведений доступен только для предмета со специальным режимом.'
            })

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class AssessmentItem(models.Model):
    element = models.ForeignKey(
        AssessmentElement,
        on_delete=models.PROTECT,
        related_name='group_placements',
        verbose_name='Произведение / элемент',
        null=True,
        blank=True,
        help_text='Выберите значение только из справочника произведений / элементов.',
    )
    title = models.CharField(
        'Название произведения (снимок)',
        max_length=255,
        editable=False,
    )
    description = models.TextField('Описание (снимок)', blank=True, editable=False)
    subject = models.ForeignKey(
        Subject,
        on_delete=models.CASCADE,
        related_name='assessment_items',
        verbose_name='Предмет',
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name='assessment_items',
        verbose_name='Учебный год',
    )
    group = models.ForeignKey(
        AssessmentGroup,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='Группа произведений',
    )
    responsible_teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name='responsible_assessment_items',
        verbose_name='Ответственный преподаватель-дирижёр',
        blank=True,
        null=True,
    )
    sort_order = models.PositiveIntegerField('Порядок отображения', default=100)
    is_required = models.BooleanField('Обязательное произведение', default=True)
    is_active = models.BooleanField('Активно', default=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Изменено', auto_now=True)

    class Meta:
        verbose_name = 'Произведение в группе'
        verbose_name_plural = 'Произведения в группах'
        ordering = ['group__sort_order', 'sort_order', 'title', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['group', 'title'],
                name='unique_assessment_item_group_title',
            ),
            models.UniqueConstraint(
                fields=['group', 'element'],
                condition=Q(element__isnull=False),
                name='unique_assessment_item_group_element',
            ),
        ]
        indexes = [
            models.Index(fields=['academic_year', 'subject', 'group'], name='assess_item_year_subj_idx'),
            models.Index(fields=['responsible_teacher', 'is_active'], name='assess_item_teacher_active_idx'),
            models.Index(fields=['group', 'sort_order'], name='assess_item_group_order_idx'),
            models.Index(fields=['group', 'element'], name='assess_item_group_element_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.title} — {self.group.name}'

    def clean(self) -> None:
        super().clean()
        if self.element_id:
            self.title = self.element.title
            self.description = self.element.description
        else:
            self.title = (self.title or '').strip()
            self.description = (self.description or '').strip()
        if not self.title:
            raise ValidationError({'element': 'Выберите произведение или элемент из справочника.'})
        if self.group_id:
            # A work group owns its subject and academic year.  Synchronizing
            # these duplicated fields also makes reassignment safe when an
            # admin form still contains values from the previous group.
            self.subject = self.group.subject
            self.academic_year = self.group.academic_year
        if self.element_id and self.subject_id and self.element.subject_id != self.subject_id:
            raise ValidationError({
                'element': 'Выбранное произведение относится к другому предмету.'
            })
        if self.subject_id and not self.subject.uses_element_assessment:
            raise ValidationError({'subject': 'Произведения доступны только в специальном режиме предмета.'})
        if self.academic_year_id:
            validate_active_academic_year(self.academic_year)
        if self.responsible_teacher_id and self.subject_id and self.academic_year_id:
            if not TeacherEnrollment.objects.filter(
                teacher=self.responsible_teacher,
                academic_year=self.academic_year,
                is_active=True,
            ).exists():
                raise ValidationError({
                    'responsible_teacher': 'Преподаватель не зачислен в выбранный учебный год.'
                })

    def save(self, *args, **kwargs):
        if not self.element_id and self.title and (self.subject_id or self.group_id):
            subject = self.group.subject if self.group_id else self.subject
            element, _ = AssessmentElement.objects.get_or_create(
                subject=subject,
                title=self.title.strip(),
                defaults={
                    'description': (self.description or '').strip(),
                    'is_active': True,
                },
            )
            self.element = element
        previous_group_id = None
        previous_subject_id = None
        previous_year_id = None
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                'group_id', 'subject_id', 'academic_year_id'
            ).first()
            if previous:
                previous_group_id = previous['group_id']
                previous_subject_id = previous['subject_id']
                previous_year_id = previous['academic_year_id']
        self.full_clean()
        result = super().save(*args, **kwargs)
        ensure_teacher_subject(self.responsible_teacher_id, self.subject_id)
        from .assessment_services import recalculate_group_finals, recalculate_subject_finals
        recalculate_group_finals(self.group)
        if previous_group_id and previous_group_id != self.group_id:
            old_group = AssessmentGroup.objects.filter(pk=previous_group_id).first()
            if old_group:
                recalculate_group_finals(old_group)
        elif previous_subject_id and (
            previous_subject_id != self.subject_id or previous_year_id != self.academic_year_id
        ):
            old_subject = Subject.objects.filter(pk=previous_subject_id).first()
            old_year = AcademicYear.objects.filter(pk=previous_year_id).first()
            if old_subject and old_year:
                recalculate_subject_finals(old_subject, old_year)
        return result

    def delete(self, *args, **kwargs):
        group = self.group
        result = super().delete(*args, **kwargs)
        from .assessment_services import recalculate_group_finals
        recalculate_group_finals(group)
        return result


class StudentAssessmentGroup(models.Model):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name='assessment_group_assignments',
        verbose_name='Ученик',
    )
    assessment_group = models.ForeignKey(
        AssessmentGroup,
        on_delete=models.CASCADE,
        related_name='student_assignments',
        verbose_name='Группа произведений',
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name='student_assessment_group_assignments',
        verbose_name='Учебный год',
    )
    enrollment = models.ForeignKey(
        StudentEnrollment,
        on_delete=models.CASCADE,
        related_name='assessment_group_assignments',
        verbose_name='Зачисление ученика',
        editable=False,
        blank=True,
        null=True,
    )
    is_active = models.BooleanField('Назначение активно', default=True)
    created_at = models.DateTimeField('Назначено', auto_now_add=True)
    updated_at = models.DateTimeField('Изменено', auto_now=True)

    class Meta:
        verbose_name = 'Назначение группы произведений ученику'
        verbose_name_plural = 'Назначения групп произведений ученикам'
        ordering = ['student__full_name', 'assessment_group__subject__name', 'assessment_group__sort_order']
        constraints = [
            models.UniqueConstraint(
                fields=['student', 'assessment_group', 'academic_year'],
                name='unique_student_assessment_group_year',
            ),
        ]
        indexes = [
            models.Index(fields=['student', 'academic_year', 'is_active'], name='stud_assess_group_act_idx'),
            models.Index(fields=['assessment_group', 'is_active'], name='assess_group_stud_act_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.student} — {self.assessment_group.name}'

    def clean(self) -> None:
        super().clean()
        if self.assessment_group_id:
            if self.academic_year_id and self.assessment_group.academic_year_id != self.academic_year_id:
                raise ValidationError({'assessment_group': 'Группа относится к другому учебному году.'})
            if not self.academic_year_id:
                self.academic_year = self.assessment_group.academic_year
            if self.is_active and not self.assessment_group.is_active:
                raise ValidationError({'assessment_group': 'Нельзя назначить неактивную группу.'})
        if self.student_id and self.academic_year_id:
            enrollment = self.student.enrollment_for_year(self.academic_year)
            if enrollment is None:
                raise ValidationError({'student': 'Ученик не зачислен в выбранный учебный год.'})
            self.enrollment = enrollment
        if self.academic_year_id:
            validate_active_academic_year(self.academic_year)

    def save(self, *args, **kwargs):
        previous = None
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                'student_id', 'assessment_group_id', 'academic_year_id', 'is_active'
            ).first()
        self.full_clean()
        result = super().save(*args, **kwargs)
        from .assessment_services import (
            recalculate_group_finals,
            recalculate_student_subject_final,
        )
        recalculate_group_finals(self.assessment_group)
        recalculate_student_subject_final(
            self.student,
            self.assessment_group.subject,
            self.academic_year,
        )
        if previous and (
            previous['student_id'] != self.student_id
            or previous['assessment_group_id'] != self.assessment_group_id
            or previous['academic_year_id'] != self.academic_year_id
            or previous['is_active'] != self.is_active
        ):
            previous_group = AssessmentGroup.objects.filter(
                pk=previous['assessment_group_id']
            ).select_related('subject', 'academic_year').first()
            previous_student = Student.objects.filter(pk=previous['student_id']).first()
            if previous_group and previous_student:
                recalculate_student_subject_final(
                    previous_student,
                    previous_group.subject,
                    previous_group.academic_year,
                )
        return result

    def delete(self, *args, **kwargs):
        group = self.assessment_group
        student = self.student
        academic_year = self.academic_year
        subject = group.subject
        result = super().delete(*args, **kwargs)
        from .assessment_services import recalculate_student_subject_final
        recalculate_student_subject_final(student, subject, academic_year)
        return result


class AssessmentResult(models.Model):
    STATUS_PASSED = 'passed'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = (
        (STATUS_PASSED, 'Зачёт'),
        (STATUS_FAILED, 'Незачёт'),
    )

    enrollment = models.ForeignKey(
        StudentEnrollment,
        on_delete=models.CASCADE,
        related_name='assessment_results',
        verbose_name='Зачисление ученика',
    )
    item = models.ForeignKey(
        AssessmentItem,
        on_delete=models.CASCADE,
        related_name='results',
        verbose_name='Произведение / элемент',
    )
    status = models.CharField('Результат', max_length=16, choices=STATUS_CHOICES)
    assessed_by = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name='assessment_results_given',
        verbose_name='Преподаватель, выставивший результат',
    )
    comment = models.TextField('Комментарий', blank=True)
    assessed_at = models.DateTimeField('Дата результата', default=timezone.now)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Изменено', auto_now=True)

    class Meta:
        verbose_name = 'Результат сдачи произведения'
        verbose_name_plural = 'Результаты сдачи произведений'
        ordering = ['item__sort_order', 'enrollment__full_name']
        constraints = [
            models.UniqueConstraint(
                fields=['enrollment', 'item'],
                name='unique_assessment_result_enrollment_item',
            ),
        ]
        indexes = [
            models.Index(fields=['item', 'status'], name='assess_result_item_status_idx'),
            models.Index(fields=['enrollment', 'status'], name='assess_res_enroll_stat_idx'),
            models.Index(fields=['assessed_by', '-assessed_at'], name='assess_result_teacher_date_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.enrollment.full_name} — {self.item.title}: {self.get_status_display()}'

    def clean(self) -> None:
        super().clean()
        if self.enrollment_id and self.item_id:
            if self.enrollment.academic_year_id != self.item.academic_year_id:
                raise ValidationError({'item': 'Результат и зачисление относятся к разным учебным годам.'})
            if not StudentAssessmentGroup.objects.filter(
                student_id=self.enrollment.student_id,
                assessment_group_id=self.item.group_id,
                academic_year_id=self.item.academic_year_id,
                is_active=True,
            ).exists():
                raise ValidationError({'item': 'Произведение не назначено этому ученику.'})
        if self.assessed_by_id and self.item_id:
            previous_assessed_by_id = None
            if self.pk:
                previous_assessed_by_id = type(self).objects.filter(pk=self.pk).values_list(
                    'assessed_by_id', flat=True
                ).first()
            author_is_being_set = not self.pk or previous_assessed_by_id != self.assessed_by_id
            if author_is_being_set and self.item.responsible_teacher_id != self.assessed_by_id:
                raise ValidationError({
                    'assessed_by': 'Результат может выставить только текущий ответственный преподаватель.'
                })
            if not TeacherSubject.objects.filter(
                teacher_id=self.assessed_by_id,
                subject_id=self.item.subject_id,
            ).exists():
                raise ValidationError({'assessed_by': 'Преподаватель не связан с предметом произведения.'})

    def save(self, *args, **kwargs):
        recalculate = kwargs.pop('recalculate', True)
        self.full_clean()
        result = super().save(*args, **kwargs)
        if recalculate:
            from .assessment_services import recalculate_student_subject_final
            recalculate_student_subject_final(
                self.enrollment.student,
                self.item.subject,
                self.item.academic_year,
            )
        return result

    def delete(self, *args, **kwargs):
        student = self.enrollment.student
        subject = self.item.subject
        academic_year = self.item.academic_year
        result = super().delete(*args, **kwargs)
        from .assessment_services import recalculate_student_subject_final
        recalculate_student_subject_final(student, subject, academic_year)
        return result


class FinalGradeRule(models.Model):
    RULE_COUNT = 'count'
    RULE_ALL_REQUIRED = 'all_required'
    RULE_DEFAULT = 'default'
    RULE_TYPE_CHOICES = (
        (RULE_COUNT, 'По количеству зачётов'),
        (RULE_ALL_REQUIRED, 'Все обязательные произведения'),
        (RULE_DEFAULT, 'Значение по умолчанию'),
    )

    subject = models.ForeignKey(
        Subject,
        on_delete=models.CASCADE,
        related_name='final_grade_rules',
        verbose_name='Предмет',
    )
    academic_year = models.ForeignKey(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name='final_grade_rules',
        verbose_name='Учебный год',
    )
    assessment_group = models.ForeignKey(
        AssessmentGroup,
        on_delete=models.CASCADE,
        related_name='final_grade_rules',
        verbose_name='Группа произведений',
        help_text='Оставьте пустым для общего правила предмета.',
        blank=True,
        null=True,
    )
    rule_type = models.CharField('Тип правила', max_length=20, choices=RULE_TYPE_CHOICES)
    passed_count = models.PositiveIntegerField('Количество зачётов', blank=True, null=True)
    condition_value = models.BooleanField(
        'Условие выполнено',
        blank=True,
        null=True,
        help_text='Для правила «Все обязательные произведения»: да или нет.',
    )
    grade = models.CharField('Итоговая оценка', max_length=64)
    priority = models.PositiveIntegerField('Приоритет', default=100)
    is_active = models.BooleanField('Активно', default=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Изменено', auto_now=True)

    class Meta:
        verbose_name = 'Правило итоговой оценки'
        verbose_name_plural = 'Правила итоговых оценок'
        ordering = ['academic_year__starts_on', 'subject__name', 'priority', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=[
                    'subject', 'academic_year', 'assessment_group',
                    'rule_type', 'passed_count', 'condition_value',
                ],
                name='unique_final_grade_rule_condition',
                nulls_distinct=False,
            ),
        ]
        indexes = [
            models.Index(fields=['academic_year', 'subject', 'is_active'], name='final_rule_year_subject_idx'),
        ]

    def __str__(self) -> str:
        return f'{self.subject} — {self.get_rule_type_display()} → {self.grade}'

    def clean(self) -> None:
        super().clean()
        if self.assessment_group_id:
            if not self.subject_id:
                self.subject = self.assessment_group.subject
            if not self.academic_year_id:
                self.academic_year = self.assessment_group.academic_year
        self.grade = (self.grade or '').strip()
        if not self.grade:
            raise ValidationError({'grade': 'Введите строковое значение итоговой оценки.'})
        if self.subject_id and not self.subject.uses_element_assessment:
            raise ValidationError({'subject': 'Правила доступны только для специального режима предмета.'})
        if self.assessment_group_id:
            if self.assessment_group.subject_id != self.subject_id:
                raise ValidationError({'assessment_group': 'Группа относится к другому предмету.'})
            if self.assessment_group.academic_year_id != self.academic_year_id:
                raise ValidationError({'assessment_group': 'Группа относится к другому учебному году.'})
        errors = {}
        if self.rule_type == self.RULE_COUNT:
            if self.passed_count is None:
                errors['passed_count'] = 'Укажите количество зачётов.'
            self.condition_value = None
        elif self.rule_type == self.RULE_ALL_REQUIRED:
            if self.condition_value is None:
                errors['condition_value'] = 'Укажите, выполнено ли условие.'
            self.passed_count = None
        elif self.rule_type == self.RULE_DEFAULT:
            self.passed_count = None
            self.condition_value = None
        if errors:
            raise ValidationError(errors)
        if self.academic_year_id:
            validate_active_academic_year(self.academic_year)

    def save(self, *args, **kwargs):
        self.full_clean()
        result = super().save(*args, **kwargs)
        from .assessment_services import recalculate_subject_finals
        recalculate_subject_finals(self.subject, self.academic_year)
        return result

    def delete(self, *args, **kwargs):
        subject = self.subject
        academic_year = self.academic_year
        result = super().delete(*args, **kwargs)
        from .assessment_services import recalculate_subject_finals
        recalculate_subject_finals(subject, academic_year)
        return result


class CourseRegistrationSettings(models.Model):
    REGISTRATION_MODE_OPEN = 'open'
    REGISTRATION_MODE_AUTOMATIC = 'automatic'
    REGISTRATION_MODE_CLOSED = 'closed'
    REGISTRATION_MODE_CHOICES = (
        (REGISTRATION_MODE_OPEN, 'Открыта вручную (лимит не учитывается)'),
        (REGISTRATION_MODE_AUTOMATIC, 'Автоматически до достижения лимита'),
        (REGISTRATION_MODE_CLOSED, 'Завершена вручную'),
    )

    academic_year = models.OneToOneField(
        AcademicYear,
        on_delete=models.CASCADE,
        related_name='registration_settings',
        verbose_name='Учебный год',
    )
    telegram_group_url = models.URLField(
        'Ссылка на Telegram-группу',
        max_length=500,
        blank=True,
    )
    minimum_registration_age = models.PositiveSmallIntegerField(
        'Минимальный возраст для регистрации',
        default=14,
        help_text='Допускаются ученики, которым в год начала курсов исполнится указанный возраст.',
    )
    registration_mode = models.CharField(
        'Режим регистрации',
        max_length=16,
        choices=REGISTRATION_MODE_CHOICES,
        default=REGISTRATION_MODE_OPEN,
        help_text=(
            'Ручное открытие и завершение имеют приоритет над лимитом. '
            'В автоматическом режиме регистрация завершится при достижении лимита.'
        ),
    )
    application_limit = models.PositiveIntegerField(
        'Лимит зарегистрированных учеников',
        null=True,
        blank=True,
        help_text='Отклонённые заявки в лимите не учитываются.',
    )
    updated_at = models.DateTimeField('Дата изменения', auto_now=True)

    class Meta:
        verbose_name = 'Настройка регистрации'
        verbose_name_plural = 'Настройки регистрации'

    def __str__(self) -> str:
        return f'Настройки регистрации на курсы — {self.academic_year}'

    @classmethod
    def load(cls, academic_year=None):
        academic_year = academic_year or AcademicYear.get_active() or AcademicYear.latest()
        if academic_year is None:
            raise cls.DoesNotExist('Сначала создайте учебный год.')
        settings_obj, _created = cls.objects.get_or_create(academic_year=academic_year)
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

        if self.application_limit is not None and self.application_limit < 1:
            errors['application_limit'] = 'Лимит должен быть не меньше одного ученика.'
        elif (
            self.registration_mode == self.REGISTRATION_MODE_AUTOMATIC
            and self.application_limit is None
        ):
            errors['application_limit'] = 'Укажите лимит для автоматического режима.'

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def registered_applications_count(self, academic_year=None) -> int:
        academic_year = academic_year or self.academic_year
        if academic_year is None:
            return 0
        return (
            CourseApplication.objects
            .filter(academic_year=academic_year)
            .exclude(status=CourseApplication.STATUS_REJECTED)
            .count()
        )

    def registration_is_open(self, academic_year=None) -> bool:
        academic_year = academic_year or self.academic_year
        if academic_year is None:
            return False
        if self.registration_mode == self.REGISTRATION_MODE_CLOSED:
            return False
        if self.registration_mode == self.REGISTRATION_MODE_OPEN:
            return True
        if self.application_limit is None:
            return False
        return self.registered_applications_count(academic_year) < self.application_limit


class PasswordRecoveryContact(models.Model):
    name = models.CharField('Имя администратора', max_length=150)
    phone = models.CharField('Номер телефона', max_length=32)
    messengers = models.CharField(
        'Мессенджеры',
        max_length=255,
        help_text='Укажите один или несколько мессенджеров, например: Telegram, WhatsApp.',
    )
    messenger_username = models.CharField(
        'Имя пользователя в Telegram',
        max_length=100,
        blank=True,
        help_text=(
            'Укажите имя Telegram без символа @. Для Telegram оно имеет приоритет '
            'над номером телефона; для остальных мессенджеров всегда используется телефон.'
        ),
    )
    is_active = models.BooleanField('Показывать пользователям', default=True)
    display_order = models.PositiveSmallIntegerField('Порядок показа', default=0)
    updated_at = models.DateTimeField('Дата изменения', auto_now=True)

    class Meta:
        db_table = 'journal_password_recovery_settings'
        verbose_name = 'Контакт администратора'
        verbose_name_plural = 'Настройки восстановления пароля'
        ordering = ['display_order', 'name', 'pk']
        indexes = [
            models.Index(
                fields=['is_active', 'display_order'],
                name='recovery_active_order_idx',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.name}: {self.phone}'

    def clean(self) -> None:
        super().clean()
        if self.name:
            self.name = self.name.strip()
        if self.phone:
            self.phone = normalize_phone_number(self.phone)
        if self.messengers:
            self.messengers = ', '.join(
                item.strip()
                for item in self.messengers.split(',')
                if item.strip()
            )
        if self.messenger_username:
            self.messenger_username = self.messenger_username.strip().lstrip('@')

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def phone_digits(self) -> str:
        return ''.join(character for character in self.phone if character.isdigit())

    @property
    def phone_uri(self) -> str:
        return f'tel:+{self.phone_digits}' if self.phone_digits else ''

    @staticmethod
    def _is_telegram_name(name: str) -> bool:
        normalized = name.casefold().replace('ё', 'е')
        return 'telegram' in normalized or normalized in {'tg', 'телеграм'}

    @property
    def has_telegram_messenger(self) -> bool:
        return any(
            self._is_telegram_name(raw_name.strip())
            for raw_name in self.messengers.split(',')
            if raw_name.strip()
        )

    @property
    def messenger_links(self) -> list[dict[str, str]]:
        username = quote(self.messenger_username, safe='._-') if self.messenger_username else ''
        digits = self.phone_digits
        links = []
        for raw_name in self.messengers.split(','):
            name = raw_name.strip()
            if not name:
                continue
            normalized = name.casefold().replace('ё', 'е')
            if self._is_telegram_name(name):
                url = f'https://t.me/{username}' if username else (
                    f'tg://resolve?phone=%2B{digits}' if digits else self.phone_uri
                )
            elif 'whatsapp' in normalized or 'ватсап' in normalized or 'вотсап' in normalized:
                url = f'https://wa.me/{digits}' if digits else self.phone_uri
            elif 'viber' in normalized or 'вайбер' in normalized:
                url = f'viber://chat?number=%2B{digits}' if digits else self.phone_uri
            else:
                url = self.phone_uri
            links.append({'name': name, 'url': url})
        return links


class AccountProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='journal_account_profile',
        verbose_name='Пользователь',
    )
    birth_date = models.DateField('Дата рождения', null=True, blank=True, db_index=True)

    class Meta:
        verbose_name = 'Дополнительные данные пользователя'
        verbose_name_plural = 'Дополнительные данные пользователей'

    def __str__(self) -> str:
        return self.user.get_full_name() or self.user.username


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


class ErrorLog(models.Model):
    MAX_RECORDS = 1000

    created_at = models.DateTimeField('Дата и время', auto_now_add=True)
    level = models.CharField('Уровень', max_length=20, default='ERROR')
    logger_name = models.CharField('Источник', max_length=255, blank=True)
    message = models.TextField('Техническое сообщение')
    user_message = models.TextField(
        'Сообщение для пользователя',
        blank=True,
        default='',
    )
    exception = models.TextField('Трассировка', blank=True)
    request_id = models.CharField('Код ошибки', max_length=64, blank=True, db_index=True)
    status_code = models.PositiveSmallIntegerField('HTTP-статус', null=True, blank=True)
    method = models.CharField('HTTP-метод', max_length=16, blank=True)
    path = models.CharField('Путь запроса', max_length=512, blank=True)
    user_label = models.CharField('Пользователь', max_length=150, blank=True)
    metadata = models.JSONField('Дополнительные данные', default=dict, blank=True)

    class Meta:
        verbose_name = 'Журнал ошибки'
        verbose_name_plural = 'Журнал ошибок'
        ordering = ['-created_at', '-pk']
        indexes = [
            models.Index(fields=['-created_at'], name='error_log_created_idx'),
            models.Index(fields=['level', '-created_at'], name='error_log_level_idx'),
        ]

    def __str__(self) -> str:
        reference = f' [{self.request_id}]' if self.request_id else ''
        timestamp = (
            self.created_at.strftime('%d.%m.%Y %H:%M:%S')
            if self.created_at
            else 'Без даты'
        )
        return f'{timestamp} {self.level}{reference}: {self.message[:80]}'

    @classmethod
    def prune_old_entries(cls, max_records: int | None = None) -> int:
        """Keep only the newest error records and return the deleted count."""
        limit = (
            cls.MAX_RECORDS
            if max_records is None
            else max(1, min(int(max_records), cls.MAX_RECORDS))
        )
        cutoff_id = (
            cls.objects.order_by('-pk')
            .values_list('pk', flat=True)[limit:limit + 1]
            .first()
        )
        if cutoff_id is None:
            return 0
        deleted, _ = cls.objects.filter(pk__lte=cutoff_id).delete()
        return deleted


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

    MUSIC_EDUCATION_SELF = 'self_taught'
    MUSIC_EDUCATION_BASIC = 'basic'
    MUSIC_EDUCATION_SECONDARY = 'secondary'
    MUSIC_EDUCATION_HIGHER = 'higher'
    # Compatibility alias for old code and fixtures. Existing database value
    # ``none`` is converted to ``self_taught`` by migration 0020.
    MUSIC_EDUCATION_NONE = MUSIC_EDUCATION_SELF
    MUSIC_EDUCATION_CHOICES = (
        (MUSIC_EDUCATION_SELF, 'Самоучка'),
        (MUSIC_EDUCATION_BASIC, 'Музыкальная школа'),
        (MUSIC_EDUCATION_SECONDARY, 'Колледж'),
        (MUSIC_EDUCATION_HIGHER, 'Институт'),
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
    instrument = models.CharField(
        'Музыкальный инструмент',
        max_length=255,
        help_text='Отображаемое значение, синхронизированное со структурированными полями.',
    )
    instrument_reference = models.ForeignKey(
        Instrument,
        on_delete=models.CASCADE,
        related_name='course_applications',
        verbose_name='Инструмент из справочника',
        null=True,
        blank=True,
    )
    custom_instrument = models.CharField('Собственный инструмент', max_length=255, blank=True)
    orchestra_part = models.ForeignKey(
        OrchestraPart,
        on_delete=models.CASCADE,
        related_name='course_applications',
        verbose_name='Партия в оркестре',
        null=True,
        blank=True,
    )
    music_education = models.CharField(
        'Музыкальное образование',
        max_length=20,
        choices=MUSIC_EDUCATION_CHOICES,
        default=MUSIC_EDUCATION_SELF,
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
        on_delete=models.CASCADE,
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
            models.Index(
                fields=['academic_year', 'status', '-registration_date'],
                name='course_app_year_status_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['academic_year', 'student_phone'],
                name='unique_course_app_phone_per_year',
            ),
            models.CheckConstraint(
                condition=(
                    Q(instrument_reference__isnull=False, custom_instrument='')
                    | (Q(instrument_reference__isnull=True) & ~Q(custom_instrument=''))
                ),
                name='course_app_exactly_one_instrument_source',
            ),
            models.CheckConstraint(
                condition=(
                    Q(orchestra_part__isnull=True)
                    | Q(instrument_reference__isnull=False, custom_instrument='')
                ),
                name='course_app_part_requires_reference',
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

    def sync_instrument_display(self) -> None:
        """
        Синхронизирует старое строковое поле instrument
        со структурированными полями инструмента.
        """
        self.custom_instrument = (self.custom_instrument or '').strip()

        if self.instrument_reference_id:
            self.instrument = self.instrument_reference.name.strip()
        else:
            self.instrument = self.custom_instrument

    def clean(self) -> None:
        super().clean()

        for field_name in (
            'last_name',
            'first_name',
            'middle_name',
            'city_church',
        ):
            value = getattr(self, field_name, '')

            if isinstance(value, str):
                setattr(self, field_name, value.strip())

        self.custom_instrument = (self.custom_instrument or '').strip()

        has_reference = bool(self.instrument_reference_id)
        has_custom = bool(self.custom_instrument)

        # Должен быть выбран ровно один вариант:
        # справочный инструмент или собственное название.
        if has_reference == has_custom:
            if has_reference:
                message = (
                    'Нельзя одновременно выбрать инструмент из справочника '
                    'и указать собственный инструмент.'
                )
            else:
                message = (
                    'Выберите инструмент из справочника '
                    'или укажите другой инструмент.'
                )

            raise ValidationError({
                'instrument_reference': message,
                'custom_instrument': message,
            })

        if has_custom:
            self.orchestra_part = None
        elif self.orchestra_part_id and not OrchestraPart.objects.filter(
            pk=self.orchestra_part_id,
            instrument_id=self.instrument_reference_id,
        ).exists():
            raise ValidationError({
                'orchestra_part': 'Выбранная партия не относится к выбранному инструменту.',
            })

        self.sync_instrument_display()

        if self.student_phone:
            self.student_phone = normalize_phone_number(
                self.student_phone,
            )

        if self.academic_year_id is None:
            self.academic_year = AcademicYear.get_active()

        if self.academic_year_id is None:
            raise ValidationError(
                'Сначала создайте активный учебный год.',
            )

        if not academic_year_is_active(self.academic_year):
            raise ValidationError(
                ARCHIVED_ACADEMIC_YEAR_ERROR,
            )

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
                        'Ученик с таким номером телефона '
                        'уже зарегистрирован.',
                        code='duplicate_phone_for_year',
                    ),
                })

        if self.parent_contacts:
            self.parent_contacts = normalize_parent_contacts(
                self.parent_contacts,
            )

    def save(self, *args, **kwargs):
        # Проверка полей внутри full_clean выполняется раньше clean(),
        # поэтому обязательное поле instrument заполняется заранее.
        self.sync_instrument_display()
        self.full_clean()

        with transaction.atomic():
            super().save(*args, **kwargs)

            if self.status == self.STATUS_CONFIRMED:
                self.create_student_in_journal()
            elif self.status == self.STATUS_REJECTED:
                self.remove_student_from_journal()

    def delete(self, *args, **kwargs):
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
        instrument = self.instrument_reference
        custom_instrument = '' if instrument is not None else self.custom_instrument.strip()

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
                custom_instrument=custom_instrument,
                orchestra_part=self.orchestra_part,
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
            existing_student.custom_instrument = custom_instrument
            existing_student.orchestra_part = self.orchestra_part
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
        .select_related('student__instrument', 'student__orchestra_part')
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
                'orchestra_part',
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
