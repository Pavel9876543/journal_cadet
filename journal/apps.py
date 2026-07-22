from django.apps import AppConfig


class JournalConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'journal'
    verbose_name = 'Журнал'

    def ready(self):
        self._allow_spaces_in_usernames()

    @staticmethod
    def _allow_spaces_in_usernames():
        from django.contrib.auth import get_user_model
        from django.contrib.auth.validators import ASCIIUsernameValidator, UnicodeUsernameValidator

        UserModel = get_user_model()
        username_field = UserModel._meta.get_field(UserModel.USERNAME_FIELD)
        username_field.validators = [
            validator
            for validator in username_field.validators
            if not isinstance(validator, (ASCIIUsernameValidator, UnicodeUsernameValidator))
        ]
