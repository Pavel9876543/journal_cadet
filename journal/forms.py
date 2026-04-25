from django import forms

from .models import Grade, Student, Subject


class GradeCreateForm(forms.ModelForm):
    class Meta:
        model = Grade
        fields = ['student', 'subject', 'date', 'value']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, teacher=None, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.teacher = teacher

        # Предметы ограничены только теми, которые ведет авторизованный преподаватель.
        subject_qs = Subject.objects.none()
        if teacher is not None:
            subject_qs = teacher.subjects.all()
            if group is not None:
                subject_qs = subject_qs.filter(groups=group)
        self.fields['subject'].queryset = subject_qs.distinct()

        # Ученики ограничены выбранной группой.
        student_qs = Student.objects.none()
        if group is not None:
            student_qs = group.students.all()
        self.fields['student'].queryset = student_qs

    def clean_subject(self):
        subject = self.cleaned_data['subject']
        if self.teacher and not self.teacher.subjects.filter(pk=subject.pk).exists():
            raise forms.ValidationError('Нельзя выставлять оценки по предметам другого преподавателя.')
        return subject

    def save(self, commit=True):
        grade = super().save(commit=False)
        if self.teacher is not None:
            grade.teacher = self.teacher
        if commit:
            grade.save()
        return grade
