from datetime import date

from django import forms

from .models import (
    CourseApplication,
    CourseRegistrationSettings,
    Grade,
    Student,
    Subject,
    Teacher,
)
from .registration_utils import calculate_age, minimum_birth_date_for_age, normalize_parent_contacts, normalize_phone_number


class GradeCreateForm(forms.ModelForm):
    class Meta:
        model = Grade
        fields = ['student', 'subject', 'teacher', 'date', 'value']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'value': forms.Select(
                choices=(
                    ('1', '1'),
                    ('2', '2'),
                    ('3', '3'),
                    ('4', '4'),
                    ('5', '5'),
                    ('Н', 'Н'),
                )
            ),
        }

    def __init__(self, *args, teacher=None, group=None, students_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.teacher = teacher

        subject_qs = Subject.objects.all()
        if teacher is not None:
            subject_qs = teacher.subjects.all()
        if group is not None:
            subject_qs = subject_qs.filter(groups=group)
        self.fields['subject'].queryset = subject_qs.distinct()

        student_qs = Student.objects.none()
        if students_queryset is not None:
            student_qs = students_queryset
        elif group is not None:
            student_qs = group.students.all()
        self.fields['student'].queryset = student_qs

        if teacher is not None:
            self.fields.pop('teacher')
        else:
            teacher_qs = Teacher.objects.all()
            if group is not None:
                teacher_qs = teacher_qs.filter(subjects__groups=group).distinct()
            self.fields['teacher'].queryset = teacher_qs.order_by('full_name')

    def clean_subject(self):
        subject = self.cleaned_data['subject']
        if self.teacher and not self.teacher.subjects.filter(pk=subject.pk).exists():
            raise forms.ValidationError('Нельзя выставлять оценки по предметам другого преподавателя.')
        return subject

    def clean(self):
        cleaned_data = super().clean()
        student = cleaned_data.get('student')
        subject = cleaned_data.get('subject')

        if student and subject:
            in_group_subjects = student.group.subjects.filter(pk=subject.pk).exists()
            in_individual_subject = subject.students.filter(pk=student.pk).exists()
            if not in_group_subjects and not in_individual_subject:
                raise forms.ValidationError('Ученик не может получить оценку по предмету вне своей группы или индивидуального списка.')

        return cleaned_data

    def clean_value(self):
        value = str(self.cleaned_data['value']).strip().upper()
        if value not in Grade.ALLOWED_VALUES:
            raise forms.ValidationError('Оценка должна быть 1-5 или Н.')
        return value

    def save(self, commit=True):
        grade = super().save(commit=False)
        if self.teacher is not None:
            grade.teacher = self.teacher
        if commit:
            grade.save()
        return grade


class BaseCourseApplicationForm(forms.ModelForm):
    def __init__(self, *args, age_limit: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.age_limit = age_limit

        self.fields['last_name'].widget.attrs.update({'autocomplete': 'family-name'})
        self.fields['first_name'].widget.attrs.update({'autocomplete': 'given-name'})
        self.fields['middle_name'].widget.attrs.update({'autocomplete': 'additional-name'})
        self.fields['gender'].widget = forms.RadioSelect(choices=CourseApplication.GENDER_CHOICES)
        self.fields['birth_date'].widget = forms.DateInput(attrs={'type': 'date'})
        self.fields['city_church'].widget.attrs.update(
            {'placeholder': 'Например, Тамбов или Воронеж, Отрожка'}
        )
        self.fields['instrument'].help_text = (
            'Укажите партию, на которой вы играете в оркестре (например: Домра малая II, Баян I, Балалайка прима). '
            'Если ранее в оркестре не играли, укажите инструмент, на котором планируете обучаться.'
        )
        self.fields['music_education'].widget = forms.Select(choices=CourseApplication.MUSIC_EDUCATION_CHOICES)
        self.fields['student_phone'].widget = forms.TextInput(
            attrs={
                'type': 'tel',
                'inputmode': 'tel',
                'placeholder': '+7 (999) 123-45-67',
                'autocomplete': 'tel',
            }
        )
        self.fields['parent_contacts'].widget = forms.Textarea(
            attrs={
                'rows': 4,
                'placeholder': 'Иванов Иван Иванович - +7 (999) 123-45-67\nИванова Мария Петровна - +7 (999) 987-65-43',
            }
        )
        self.fields['comments'].widget = forms.Textarea(attrs={'rows': 4})
        self.fields['comments'].required = False

        if self.age_limit:
            self.fields['birth_date'].widget.attrs['max'] = minimum_birth_date_for_age(14).isoformat()
            self.fields['birth_date'].widget.attrs['data-age-limit'] = '14'

    class Meta:
        model = CourseApplication
        fields = [
            'last_name',
            'first_name',
            'middle_name',
            'gender',
            'birth_date',
            'city_church',
            'instrument',
            'music_education',
            'student_phone',
            'parent_contacts',
            'comments',
        ]
        widgets = {
            'comments': forms.Textarea(attrs={'rows': 4}),
        }

    def clean_last_name(self):
        return self.cleaned_data['last_name'].strip()

    def clean_first_name(self):
        return self.cleaned_data['first_name'].strip()

    def clean_middle_name(self):
        return self.cleaned_data['middle_name'].strip()

    def clean_city_church(self):
        return self.cleaned_data['city_church'].strip()

    def clean_instrument(self):
        return self.cleaned_data['instrument'].strip()

    def clean_student_phone(self):
        return normalize_phone_number(self.cleaned_data['student_phone'])

    def clean_parent_contacts(self):
        return normalize_parent_contacts(self.cleaned_data.get('parent_contacts', ''))

    def clean_birth_date(self):
        birth_date = self.cleaned_data['birth_date']
        if self.age_limit and calculate_age(birth_date) < 14:
            raise forms.ValidationError('Регистрация на курсы доступна только с 14 лет.')
        return birth_date

    def clean_comments(self):
        return self.cleaned_data.get('comments', '').strip()

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save()
        return instance


class CourseApplicationPublicForm(BaseCourseApplicationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, age_limit=True, **kwargs)


class CourseApplicationAdminForm(BaseCourseApplicationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, age_limit=False, **kwargs)


class CourseRegistrationSettingsForm(forms.ModelForm):
    class Meta:
        model = CourseRegistrationSettings
        fields = ['telegram_group_url']
        widgets = {
            'telegram_group_url': forms.URLInput(
                attrs={
                    'placeholder': 'https://t.me/your_group_or_invite_link',
                }
            ),
        }

    def clean_telegram_group_url(self):
        value = self.cleaned_data['telegram_group_url'].strip()
        if not value:
            raise forms.ValidationError('Укажите ссылку на Telegram-группу.')
        return value
