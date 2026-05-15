from django import forms

from .models import Grade, Student, Subject, Teacher


class GradeCreateForm(forms.ModelForm):
    class Meta:
        model = Grade
        fields = ['student', 'subject', 'teacher', 'date', 'value']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, teacher=None, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.teacher = teacher

        # Для преподавателя оставляем только его предметы; для админа показываем предметы выбранной группы.
        subject_qs = Subject.objects.all()
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

    def save(self, commit=True):
        grade = super().save(commit=False)
        if self.teacher is not None:
            grade.teacher = self.teacher
        if commit:
            grade.save()
        return grade
