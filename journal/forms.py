from django import forms

from .models import Grade, Student, Subject, Teacher


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
