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

env_file = os.getenv('DJANGO_ENV_FILE')
if env_file:
    _load_env_file(env_file)
elif os.getenv('DJANGO_ENV', '').lower() in {'production', 'prod'}:
    _load_env_file('.env.prod')
else:
    _load_env_file('.env.dev')

IS_PRODUCTION_ENV = os.getenv('DJANGO_ENV', '').lower() in {'production', 'prod'}


def _env_list(name: str, default: str = '') -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(',') if item.strip()]


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


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
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ALLOW_EMBEDDED_PREVIEW = _env_bool('ALLOW_EMBEDDED_PREVIEW', DEBUG)
X_FRAME_OPTIONS = 'SAMEORIGIN'
if ALLOW_EMBEDDED_PREVIEW:
    MIDDLEWARE.remove('django.middleware.clickjacking.XFrameOptionsMiddleware')

JAZZMIN_SETTINGS = {
    'site_title': 'Электронный журнал',
    'site_header': 'Электронный журнал',
    'site_brand': 'Журнал',
    'welcome_sign': 'Вход в админ-панель журнала',
    'copyright': 'Электронный журнал музыкальной школы',
    'search_model': ['journal.Student', 'journal.Teacher', 'journal.CourseApplication'],
    'custom_css': 'journal/admin_dashboard.css',
    'navigation_expanded': True,
    'show_ui_builder': False,
    'related_modal_active': True,
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
            'permissions': ['auth.view_user'],
        },
        {
            'name': 'Тестовые данные',
            'url': 'admin_seed_test_data',
            'icon': 'fas fa-play-circle',
            'permissions': ['journal.view_temporarycredential'],
        },
    ],
    'order_with_respect_to': [
        'journal',
        'journal.StudyGroup',
        'journal.Student',
        'journal.Teacher',
        'journal.Grade',
        'journal.SubjectResult',
        'journal.GroupSubject',
        'journal.StudentSubject',
        'journal.TeacherSubject',
        'journal.CourseApplication',
        'journal.TemporaryCredential',
        'journal.CourseRegistrationSettings',
        'journal.PasswordRecoveryContact',
        'journal.AcademicYear',
        'journal.Subject',
        'journal.Instrument',
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
        'journal.subject': 'fas fa-book',
        'journal.studygroup': 'fas fa-layer-group',
        'journal.teacher': 'fas fa-chalkboard-teacher',
        'journal.student': 'fas fa-user-graduate',
        'journal.groupsubject': 'fas fa-project-diagram',
        'journal.studentsubject': 'fas fa-user-tag',
        'journal.teachersubject': 'fas fa-chalkboard',
        'journal.grade': 'fas fa-pen',
        'journal.subjectresult': 'fas fa-clipboard-check',
        'journal.courseapplication': 'fas fa-file-signature',
        'journal.courseregistrationsettings': 'fas fa-cog',
        'journal.passwordrecoverycontact': 'fas fa-headset',
        'journal.temporarycredential': 'fas fa-key',
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
                'permissions': ['auth.view_user'],
            },
            {
                'name': 'Запуск тестовых данных',
                'url': 'admin_seed_test_data',
                'icon': 'fas fa-play-circle',
                'permissions': ['journal.view_temporarycredential'],
            },
        ],
    },
}

HAS_WHITENOISE = find_spec('whitenoise') is not None
if HAS_WHITENOISE:
    MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')

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
    }
}

DATA_TOOLS_PASSWORD = os.getenv('pas_key_data') or os.getenv('DATA_TOOLS_PASSWORD', '')

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
if HAS_WHITENOISE:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

CSRF_TRUSTED_ORIGINS = _env_list('CSRF_TRUSTED_ORIGINS')
SECURE_SSL_REDIRECT = _env_bool('SECURE_SSL_REDIRECT', False)
SESSION_COOKIE_SECURE = _env_bool('SESSION_COOKIE_SECURE', not DEBUG)
CSRF_COOKIE_SECURE = _env_bool('CSRF_COOKIE_SECURE', not DEBUG)
SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', False)
SECURE_HSTS_PRELOAD = _env_bool('SECURE_HSTS_PRELOAD', False)

if _env_bool('USE_X_FORWARDED_PROTO', False):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

TRUST_X_FORWARDED_FOR = _env_bool('TRUST_X_FORWARDED_FOR', False)
