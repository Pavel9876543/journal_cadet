import os


os.environ.setdefault('DJANGO_ENV', 'test')
os.environ.setdefault('DJANGO_ENV_FILE', '.env.test')

from .settings import *  # noqa: E402,F403


# Password semantics are covered by the suite; production-strength hashing only
# adds CPU cost when hundreds of short-lived users are created during tests.
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]
