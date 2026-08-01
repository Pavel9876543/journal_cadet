import os
from pathlib import Path
from importlib.util import find_spec

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(env_filename: str) -> None:
    env_path = BASE_DIR / env_filename
    if not env_filename or not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip())


# Загружаем общий .env и env-файл конкретного окружения, если они есть.
# Явные переменные окружения всегда имеют приоритет над значениями из файла.
_load_env_file('.env')

DJANGO_ENV = os.getenv('DJANGO_ENV', 'development').strip().lower()
SUPPORTED_ENVIRONMENTS = {'development', 'dev', 'test', 'testing', 'production', 'prod'}
if DJANGO_ENV not in SUPPORTED_ENVIRONMENTS:
    raise ImproperlyConfigured(
        'DJANGO_ENV must be one of: development, dev, test, testing, production, prod.'
    )

env_file = os.getenv('DJANGO_ENV_FILE')
if env_file:
    _load_env_file(env_file)
elif DJANGO_ENV in {'production', 'prod'}:
    _load_env_file('.env.prod')
elif DJANGO_ENV in {'test', 'testing'}:
    _load_env_file('.env.test')
else:
    _load_env_file('.env.dev')

# DJANGO_ENV may be supplied by the selected env file itself.
DJANGO_ENV = os.getenv('DJANGO_ENV', DJANGO_ENV).strip().lower()
IS_PRODUCTION_ENV = DJANGO_ENV in {'production', 'prod'}
IS_TEST_ENV = DJANGO_ENV in {'test', 'testing'}
RELEASE_REVISION = os.getenv('RELEASE_REVISION', '').strip()


def _env_list(name: str, default: str = '') -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(',') if item.strip()]


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def _env_positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ImproperlyConfigured(f'{name} must be a positive integer.') from exc
    if value < 1:
        raise ImproperlyConfigured(f'{name} must be a positive integer.')
    return value


def _env_nonnegative_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ImproperlyConfigured(f'{name} must be a non-negative integer.') from exc
    if value < 0:
        raise ImproperlyConfigured(f'{name} must be a non-negative integer.')
    return value


DEBUG = _env_bool('DEBUG', not IS_PRODUCTION_ENV)
SECRET_KEY = os.getenv('SECRET_KEY', '')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'unsafe-dev-secret-key-for-local-debug-only'
    else:
        raise ImproperlyConfigured('SECRET_KEY must be set when DEBUG=0.')
if not DEBUG and (SECRET_KEY.startswith('change-this') or SECRET_KEY.startswith('unsafe-')):
    raise ImproperlyConfigured('SECRET_KEY must be changed for production.')

ALLOWED_HOSTS = _env_list('ALLOWED_HOSTS', '127.0.0.1,localhost' if DEBUG else '')
if not DEBUG and not ALLOWED_HOSTS:
    raise ImproperlyConfigured('ALLOWED_HOSTS must be set when DEBUG=0.')

INSTALLED_APPS = [
    'jazzmin',
    'django.contrib.admin',
    'journal.command_overrides',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'journal.apps.JournalConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'journal.middleware.RequestIdMiddleware',
    'django.middleware.gzip.GZipMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'journal.middleware.ErrorLoggingMiddleware',
    'journal.middleware.UserFriendlyErrorResponseMiddleware',
    'journal.middleware.NoCacheHtmlMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ALLOW_EMBEDDED_PREVIEW = _env_bool('ALLOW_EMBEDDED_PREVIEW', DEBUG)
X_FRAME_OPTIONS = 'DENY'
if ALLOW_EMBEDDED_PREVIEW:
    MIDDLEWARE.remove('django.middleware.clickjacking.XFrameOptionsMiddleware')

JAZZMIN_SETTINGS = {
    'site_title': 'Электронный журнал',
    'site_header': 'Электронный журнал',
    'site_brand': 'Журнал',
    'welcome_sign': 'Вход в админ-панель журнала',
    # Jazzmin itself adds "Copyright © <current year>" and the rights notice.
    'copyright': 'Электронный журнал музыкальных курсов',
    'search_model': ['journal.Student', 'journal.Teacher', 'journal.CourseApplication'],
    'custom_css': 'journal/admin_dashboard.css',
    'custom_js': 'journal/admin_responsive.js',
    'navigation_expanded': True,
    'show_ui_builder': False,
    'related_modal_active': True,
    'changeform_format': 'horizontal_tabs',
    'changeform_format_overrides': {
        'journal.academicyear': 'collapsible',
        'journal.instrument': 'single',
        'journal.courseregistrationsettings': 'single',
        'journal.passwordrecoverycontact': 'single',
        'journal.temporarycredential': 'single',
        'auth.group': 'collapsible',
    },
    'topmenu_links': [
        {
            'name': 'Панель',
            'url': 'admin:index',
            'icon': 'fas fa-th-large',
            'permissions': ['auth.view_user'],
        },
        {
            'name': 'Журнал',
            'url': 'journal',
            'icon': 'fas fa-table',
            'permissions': ['journal.view_grade'],
        },
        {
            'name': 'Инструменты',
            'url': 'admin_data_tools',
            'icon': 'fas fa-database',
            'permissions': ['journal.view_temporarycredential'],
        },
        {
            'name': 'Инструкция',
            'url': 'admin_guide',
            'icon': 'fas fa-question-circle',
            'permissions': ['auth.delete_user'],
        },
    ],
    'order_with_respect_to': [
        'journal',
        # Основные справочники и ежедневная работа.
        'journal.StudyGroup',
        'journal.Student',
        'journal.Teacher',
        'journal.Subject',
        'journal.AcademicYear',
        'journal.Instrument',
        'journal.OrchestraPart',
        # Обычный журнал и итоги.
        'journal.Grade',
        'journal.SubjectResult',
        # Сдача произведений: настройка -> назначение -> результат -> итог.
        'journal.AssessmentElement',
        'journal.AssessmentGroup',
        'journal.AssessmentItem',
        'journal.StudentAssessmentGroup',
        'journal.AssessmentResult',
        'journal.FinalGradeRule',
        # Учебные назначения.
        'journal.GroupSubject',
        'journal.StudentSubject',
        'journal.TeacherSubject',
        # Курсы и доступы.
        'journal.CourseApplication',
        'journal.CourseRegistrationSettings',
        'journal.TemporaryCredential',
        'journal.ErrorLog',
        'journal.PasswordRecoveryContact',
        # Сервисные действия.
        'Запуск тестовых данных',
        'Выгрузить все данные в Excel',
        'Инструменты данных',
        'auth',
        'auth.User',
        'auth.Group',
    ],
    'icons': {
        'auth': 'fas fa-users-cog',
        'auth.user': 'fas fa-user-shield',
        'auth.group': 'fas fa-user-lock',
        'journal': 'fas fa-book-open',
        'journal.academicyear': 'fas fa-calendar-alt',
        'journal.instrument': 'fas fa-guitar',
        'journal.orchestrapart': 'fas fa-music',
        'journal.subject': 'fas fa-book',
        'journal.studygroup': 'fas fa-layer-group',
        'journal.teacher': 'fas fa-chalkboard-teacher',
        'journal.student': 'fas fa-user-graduate',
        'journal.groupsubject': 'fas fa-project-diagram',
        'journal.studentsubject': 'fas fa-user-tag',
        'journal.teachersubject': 'fas fa-chalkboard',
        'journal.grade': 'fas fa-pen',
        'journal.subjectresult': 'fas fa-clipboard-check',
        'journal.assessmentelement': 'fas fa-list-alt',
        'journal.assessmentgroup': 'fas fa-object-group',
        'journal.assessmentitem': 'fas fa-music',
        'journal.studentassessmentgroup': 'fas fa-user-plus',
        'journal.assessmentresult': 'fas fa-check-double',
        'journal.finalgraderule': 'fas fa-calculator',
        'journal.courseapplication': 'fas fa-file-signature',
        'journal.courseregistrationsettings': 'fas fa-cog',
        'journal.passwordrecoverycontact': 'fas fa-headset',
        'journal.temporarycredential': 'fas fa-key',
        'journal.errorlog': 'fas fa-bug',
    },
    'custom_links': {
        'journal': [
            {
                'name': 'Выгрузить все данные в Excel',
                'url': 'admin_export_all_data_excel',
                'icon': 'fas fa-file-excel',
                'permissions': ['auth.view_user'],
            },
            {
                'name': 'Инструменты данных',
                'url': 'admin_data_tools',
                'icon': 'fas fa-database',
                'permissions': ['journal.view_temporarycredential'],
            },
            {
                'name': 'Инструкция администратора',
                'url': 'admin_guide',
                'icon': 'fas fa-question-circle',
                'permissions': ['auth.delete_user'],
            },
        ],
    },
}

HAS_WHITENOISE = find_spec('whitenoise') is not None
if HAS_WHITENOISE:
    MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')
if DEBUG:
    # This must wrap WhiteNoise so local static responses cannot bypass it.
    MIDDLEWARE.insert(1, 'journal.middleware.NoCacheDevelopmentStaticMiddleware')

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'journal.birthday_notifications.birthday_notifications',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# По умолчанию оставляем SQLite, но можно переопределить через env-переменные.
DB_ENGINE = os.getenv('DB_ENGINE', 'django.db.backends.sqlite3')
DATABASES = {
    'default': {
        'ENGINE': DB_ENGINE,
        'NAME': os.getenv('DB_NAME', str(BASE_DIR / 'db.sqlite3')),
        'USER': os.getenv('DB_USER', ''),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', ''),
        'PORT': os.getenv('DB_PORT', ''),
        # Persistent PostgreSQL connections reduce connection setup overhead in production.
        # Development and tests keep the default short-lived connections.
        'CONN_MAX_AGE': _env_nonnegative_int(
            'DB_CONN_MAX_AGE',
            60 if IS_PRODUCTION_ENV else 0,
        ),
        'CONN_HEALTH_CHECKS': IS_PRODUCTION_ENV,
    }
}

# Кэш намеренно отключён во всех непроизводственных окружениях.
# Это исключает устаревшие данные во время разработки и делает поведение тестов
# детерминированным. В production используется отдельная база Redis.
CACHE_ENABLED = IS_PRODUCTION_ENV
if CACHE_ENABLED:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': os.getenv('REDIS_URL', 'redis://redis:6379/1'),
            'TIMEOUT': _env_positive_int('CACHE_DEFAULT_TIMEOUT', 300),
            'OPTIONS': {
                'socket_connect_timeout': 3,
                'socket_timeout': 3,
                'retry_on_timeout': True,
            },
            'KEY_PREFIX': os.getenv('CACHE_KEY_PREFIX', 'cadet-journal'),
        },
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
        },
    }

DATA_TOOLS_PASSWORD = os.getenv('pas_key_data') or os.getenv('DATA_TOOLS_PASSWORD', '')
ENABLE_DESTRUCTIVE_DATA_TOOLS = _env_bool(
    'ENABLE_DESTRUCTIVE_DATA_TOOLS',
    DEBUG and not IS_PRODUCTION_ENV,
)

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

AUTHENTICATION_BACKENDS = [
    'journal.auth_backends.CaseInsensitiveModelBackend',
]

LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = os.getenv('TIME_ZONE', 'UTC')
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
_static_root_value = os.getenv('STATIC_ROOT', '').strip()
STATIC_ROOT = (
    Path(_static_root_value).expanduser()
    if _static_root_value
    else BASE_DIR / 'staticfiles'
)
if not STATIC_ROOT.is_absolute():
    STATIC_ROOT = BASE_DIR / STATIC_ROOT
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': (
            'journal.static_storage.DevelopmentStaticFilesStorage'
            if DEBUG
            else (
                'whitenoise.storage.CompressedManifestStaticFilesStorage'
                if HAS_WHITENOISE
                else 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage'
            )
        ),
    },
}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

CSRF_TRUSTED_ORIGINS = _env_list('CSRF_TRUSTED_ORIGINS')
CSRF_FAILURE_VIEW = 'journal.views.csrf_failure_view'
SECURE_SSL_REDIRECT = _env_bool('SECURE_SSL_REDIRECT', False)
SESSION_COOKIE_SECURE = _env_bool('SESSION_COOKIE_SECURE', not DEBUG)
CSRF_COOKIE_SECURE = _env_bool('CSRF_COOKIE_SECURE', not DEBUG)
SECURE_HSTS_SECONDS = _env_nonnegative_int('SECURE_HSTS_SECONDS', 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', False)
SECURE_HSTS_PRELOAD = _env_bool('SECURE_HSTS_PRELOAD', False)

if _env_bool('USE_X_FORWARDED_PROTO', False):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

TRUST_X_FORWARDED_FOR = _env_bool('TRUST_X_FORWARDED_FOR', False)
TRUSTED_PROXY_COUNT = _env_positive_int('TRUSTED_PROXY_COUNT', 1)


ERROR_LOG_MAX_RECORDS = _env_positive_int('ERROR_LOG_MAX_RECORDS', 1000)
if ERROR_LOG_MAX_RECORDS > 1000:
    raise ImproperlyConfigured('ERROR_LOG_MAX_RECORDS cannot be greater than 1000.')
ERROR_LOGGING_ENABLED = _env_bool('ERROR_LOGGING_ENABLED', not IS_TEST_ENV)
DJANGO_LOG_LEVEL = os.getenv('DJANGO_LOG_LEVEL', 'INFO').strip().upper()
if DJANGO_LOG_LEVEL not in {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}:
    raise ImproperlyConfigured(
        'DJANGO_LOG_LEVEL must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL.'
    )

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'console': {
            'format': '{asctime} {levelname} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'console',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': DJANGO_LOG_LEVEL,
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'journal': {
            'handlers': ['console'],
            'level': DJANGO_LOG_LEVEL,
            'propagate': False,
        },
    },
}

if ERROR_LOGGING_ENABLED:
    LOGGING['handlers']['database_errors'] = {
        'class': 'journal.error_logging.DatabaseErrorHandler',
        'level': 'ERROR',
        'max_records': ERROR_LOG_MAX_RECORDS,
    }
    for logger_name in ('django.request', 'django.security', 'journal'):
        LOGGING['loggers'][logger_name]['handlers'].append('database_errors')
