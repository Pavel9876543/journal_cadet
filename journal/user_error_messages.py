from __future__ import annotations

from collections.abc import Iterable


_STATUS_MESSAGES = {
    400: 'Проверьте заполнение формы и исправьте выделенные поля.',
    401: 'Войдите в систему и повторите действие.',
    403: 'У вашей учётной записи нет прав для этого действия.',
    404: 'Запрошенная страница или запись не найдена.',
    409: 'Данные уже изменились. Обновите страницу и повторите действие.',
    500: 'Произошла внутренняя ошибка. Сообщите администратору код ошибки.',
}

_REQUIRED_SELECTIONS = {
    'enrollment': 'ученика',
    'assessed_by': 'преподавателя, который выставил результат',
    'item': 'произведение или элемент',
    'status': 'результат',
}


def default_user_error_message(status_code: int | None = None) -> str:
    if status_code is not None and status_code in _STATUS_MESSAGES:
        return _STATUS_MESSAGES[status_code]
    if status_code is not None and status_code >= 500:
        return _STATUS_MESSAGES[500]
    return 'Не удалось выполнить действие. Проверьте введённые данные и повторите попытку.'


def _normalise_label(value: str) -> str:
    return ' '.join(str(value or '').replace('*', '').split()).rstrip(':')


def _format_validation_message(error) -> str:
    message = str(getattr(error, 'message', error) or '').strip()
    params = getattr(error, 'params', None)
    if params:
        try:
            message = message % params
        except (KeyError, TypeError, ValueError):
            pass
    return message


def _human_join(items: Iterable[str]) -> str:
    values = [item for item in items if item]
    if not values:
        return ''
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f'{values[0]} и {values[1]}'
    return f'{", ".join(values[:-1])} и {values[-1]}'


def build_admin_form_user_message(form, inline_formsets=()) -> str:
    """Build a short user-facing validation summary without codes or internals."""
    required_selections: list[str] = []
    required_fields: list[str] = []
    other_messages: list[str] = []

    def collect(source_form) -> None:
        for field_name, errors in source_form.errors.as_data().items():
            if field_name == '__all__':
                label = ''
            else:
                field = source_form.fields.get(field_name)
                label = _normalise_label(getattr(field, 'label', '') or field_name)
            for error in errors:
                code = str(getattr(error, 'code', '') or '')
                if code == 'required':
                    selection = _REQUIRED_SELECTIONS.get(field_name)
                    if selection is not None:
                        if selection not in required_selections:
                            required_selections.append(selection)
                    else:
                        required_label = label or 'обязательное поле'
                        if required_label not in required_fields:
                            required_fields.append(required_label)
                    continue
                message = _format_validation_message(error)
                if not message:
                    continue
                readable = f'{label}: {message}' if label else message
                if readable not in other_messages:
                    other_messages.append(readable)

    collect(form)
    for formset in inline_formsets:
        for inline_form in formset.forms:
            if inline_form.errors:
                collect(inline_form)
        for error in formset.non_form_errors().as_data():
            message = _format_validation_message(error)
            if message and message not in other_messages:
                other_messages.append(message)

    parts: list[str] = []
    if required_selections:
        parts.append('Выберите ' + _human_join(required_selections) + '.')
    if required_fields:
        quoted = [f'«{label}»' for label in required_fields]
        parts.append('Заполните ' + _human_join(quoted) + '.')
    if other_messages:
        details = other_messages[:2]
        parts.append('Проверьте данные: ' + _human_join(details) + '.')
    if not parts:
        parts.append('Проверьте выделенные поля и повторите сохранение.')
    return 'Не удалось сохранить запись. ' + ' '.join(parts)
