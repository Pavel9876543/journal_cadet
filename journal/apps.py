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
        from .account_utils import username_with_spaces_validator

        UserModel = get_user_model()
        username_field = UserModel._meta.get_field(UserModel.USERNAME_FIELD)
        remaining_validators = [
            validator
            for validator in username_field.validators
            if not isinstance(validator, (ASCIIUsernameValidator, UnicodeUsernameValidator))
        ]
        username_field.validators = [
            username_with_spaces_validator,
            *remaining_validators,
        ]
