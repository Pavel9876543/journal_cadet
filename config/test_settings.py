from .settings import *  # noqa: F403


# Password semantics are covered by the suite; production-strength hashing only
# adds CPU cost when hundreds of short-lived users are created during tests.
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

