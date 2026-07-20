import os
from pathlib import Path
from importlib.util import find_spec

BASE_DIR = Path(__file__).resolve().parent.parent

# Загружаем переменные из локальных env-файлов, если они есть.
# Приоритет: уже заданные переменные окружения > значения из файлов.
for env_filename in ('.env.dev', '.env.prod'):
    env_path = BASE_DIR / env_filename
    if not env_path.exists():
        continue
    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip())

SECRET_KEY = os.getenv('SECRET_KEY', 'unsafe-dev-secret-key')
DEBUG = os.getenv('DEBUG', '1') == '1'


def _env_list(name: str, default: str = '') -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(',') if item.strip()]


ALLOWED_HOSTS = _env_list('ALLOWED_HOSTS', '*')

INSTALLED_APPS = [
    'jazzmin',
    'django.contrib.admin',
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
    ],
    'order_with_respect_to': [
        'journal',
        'journal.StudyGroup',
        'journal.Student',
        'journal.Teacher',
        'journal.Grade',
        'journal.SubjectResult',
        'journal.CourseApplication',
        'journal.TemporaryCredential',
        'journal.CourseRegistrationSettings',
        'journal.AcademicYear',
        'journal.Subject',
        'journal.Instrument',
        'Выгрузить все данные в Excel',
        'Инструменты данных',
        'auth',
        'auth.User',
        'auth.Group',
    ],
    'hide_models': [
        'journal.GroupSubject',
        'journal.StudentSubject',
        'journal.TeacherSubject',
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
        'journal.grade': 'fas fa-pen',
        'journal.subjectresult': 'fas fa-clipboard-check',
        'journal.courseapplication': 'fas fa-file-signature',
        'journal.courseregistrationsettings': 'fas fa-cog',
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
