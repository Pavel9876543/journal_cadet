from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import Iterable

from django.http import JsonResponse
from django.shortcuts import render


@dataclass(frozen=True)
class ErrorPresentation:
    title: str
    message: str
    suggestions: tuple[str, ...]


ERROR_PRESENTATIONS = {
    401: ErrorPresentation(
        'Требуется авторизация',
        'Для выполнения этого действия необходимо войти в систему.',
        (
            'Откройте страницу входа и авторизуйтесь.',
            'Если сеанс завершился, войдите повторно.',
        ),
    ),
    400: ErrorPresentation(
        'Некорректный запрос',
        'Сервер не смог безопасно обработать отправленные данные.',
        (
            'Проверьте заполнение обязательных полей и формат введённых значений.',
            'Обновите страницу и повторите действие.',
            'Не используйте устаревшую вкладку после длительного перерыва.',
        ),
    ),
    403: ErrorPresentation(
        'Недостаточно прав',
        'У вашей учётной записи нет разрешения на выполнение этого действия.',
        (
            'Убедитесь, что вошли под нужной учётной записью.',
            'Обратитесь к администратору, если доступ должен быть предоставлен.',
            'Не повторяйте отправку формы из старой вкладки.',
        ),
    ),
    404: ErrorPresentation(
        'Страница не найдена',
        'Запрошенная страница была удалена, перемещена или адрес указан неверно.',
        (
            'Проверьте адрес страницы.',
            'Откройте нужный раздел через главное меню.',
            'Обновите список, если запись могла быть удалена другим пользователем.',
        ),
    ),
    405: ErrorPresentation(
        'Действие не поддерживается',
        'Для этой страницы использован неподходящий способ отправки запроса.',
        (
            'Вернитесь на предыдущую страницу и повторите действие через кнопку интерфейса.',
            'Обновите страницу, если форма была открыта давно.',
        ),
    ),
    408: ErrorPresentation(
        'Время ожидания истекло',
        'Запрос не был завершён вовремя.',
        (
            'Проверьте подключение к интернету.',
            'Повторите действие через несколько секунд.',
        ),
    ),
    409: ErrorPresentation(
        'Данные уже изменились',
        'Сохранение невозможно, потому что связанные данные были изменены параллельно.',
        (
            'Обновите страницу, чтобы получить актуальные данные.',
            'Повторите изменение после проверки текущего состояния записи.',
        ),
    ),
    413: ErrorPresentation(
        'Слишком большой объём данных',
        'Размер отправленных данных превышает допустимый предел.',
        (
            'Уменьшите размер файла или количество отправляемых данных.',
            'Разделите операцию на несколько частей.',
        ),
    ),
    415: ErrorPresentation(
        'Неподдерживаемый формат данных',
        'Сервер не может обработать данные в отправленном формате.',
        (
            'Проверьте тип загружаемого файла или формат запроса.',
            'Повторите действие через стандартную форму интерфейса.',
        ),
    ),
    422: ErrorPresentation(
        'Не удалось проверить данные',
        'Запрос понятен серверу, но некоторые значения не прошли проверку.',
        (
            'Проверьте значения во всех полях формы.',
            'Исправьте ошибки, указанные рядом с полями, и повторите сохранение.',
        ),
    ),
    429: ErrorPresentation(
        'Слишком много запросов',
        'За короткое время выполнено слишком много попыток.',
        (
            'Подождите несколько минут перед повторной попыткой.',
            'Не нажимайте кнопку отправки несколько раз подряд.',
        ),
    ),
    500: ErrorPresentation(
        'Не удалось выполнить операцию',
        'Произошла внутренняя ошибка. Данные об ошибке сохранены для администратора.',
        (
            'Повторите действие после обновления страницы.',
            'Если ошибка повторяется, сообщите администратору код ошибки с этой страницы.',
            'Не отправляйте одну и ту же форму многократно, пока не выяснена причина.',
        ),
    ),
    502: ErrorPresentation(
        'Сервис временно недоступен',
        'Промежуточный сервер не получил корректный ответ от приложения.',
        ('Подождите несколько секунд и обновите страницу.',),
    ),
    503: ErrorPresentation(
        'Сервис временно недоступен',
        'Приложение или база данных временно не готовы обрабатывать запросы.',
        (
            'Подождите несколько минут и повторите действие.',
            'Сообщите администратору код ошибки, если проблема сохраняется.',
        ),
    ),
    504: ErrorPresentation(
        'Сервис отвечает слишком долго',
        'Вышло время ожидания ответа от приложения.',
        (
            'Проверьте подключение к интернету.',
            'Повторите действие позже.',
        ),
    ),
}


def request_wants_json(request) -> bool:
    accept = request.headers.get('Accept', '').lower()
    return (
        request.path.startswith('/api/')
        or 'application/json' in accept
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )


def _request_id(request) -> str:
    return str(getattr(request, 'request_id', ''))


def render_error_response(
    request,
    status_code: int,
    *,
    code: str | None = None,
    title: str | None = None,
    message: str | None = None,
    suggestions: Iterable[str] | None = None,
    retry_url: str | None = None,
):
    try:
        default_title = HTTPStatus(status_code).phrase
    except ValueError:
        default_title = 'Ошибка'

    presentation = ERROR_PRESENTATIONS.get(
        status_code,
        ErrorPresentation(
            default_title,
            'Не удалось выполнить запрос.',
            ('Обновите страницу и повторите действие.',),
        ),
    )
    resolved_title = title or presentation.title
    resolved_message = message or presentation.message
    resolved_suggestions = tuple(suggestions or presentation.suggestions)
    request_id = _request_id(request)
    error_code = code or f'http_{status_code}'

    if request_wants_json(request):
        return JsonResponse(
            {
                'success': False,
                'error': {
                    'code': error_code,
                    'status': status_code,
                    'message': resolved_message,
                    'request_id': request_id,
                    'suggestions': list(resolved_suggestions),
                },
            },
            status=status_code,
        )

    return render(
        request,
        'errors/error.html',
        {
            'status_code': status_code,
            'error_code': error_code,
            'title': resolved_title,
            'message': resolved_message,
            'suggestions': resolved_suggestions,
            'request_id': request_id,
            'retry_url': retry_url,
        },
        status=status_code,
    )


def bad_request(request, exception=None):
    return render_error_response(request, 400)


def permission_denied(request, exception=None):
    return render_error_response(request, 403)


def page_not_found(request, exception=None):
    return render_error_response(request, 404)


def server_error(request):
    return render_error_response(request, 500, retry_url=request.get_full_path())
