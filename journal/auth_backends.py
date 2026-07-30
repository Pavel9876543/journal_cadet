from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class CaseInsensitiveModelBackend(ModelBackend):
    """Authenticate usernames without changing their stored letter case."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        username_field = UserModel.USERNAME_FIELD
        if username is None:
            username = kwargs.get(username_field)
        if username is None or password is None:
            return None

        entered_username = str(username)
        normalized_username = entered_username.casefold()
        manager = UserModel._default_manager

        # Database iexact is fast and sufficient for ASCII identifiers. SQLite
        # does not fold Cyrillic reliably, so non-ASCII names get a small
        # Unicode-aware fallback over usernames only.
        candidates = list(
            manager.filter(
                **{f'{username_field}__iexact': entered_username},
            ).order_by('pk')
        )
        candidate_ids = {user.pk for user in candidates}

        if not entered_username.isascii():
            unicode_match_ids = [
                user_id
                for user_id, stored_username in manager.values_list(
                    'pk', username_field,
                )
                if str(stored_username).casefold() == normalized_username
                and user_id not in candidate_ids
            ]
            if unicode_match_ids:
                candidates.extend(
                    manager.filter(pk__in=unicode_match_ids).order_by('pk')
                )

        if not candidates:
            # Keep timing close to a failed password check for an existing user.
            UserModel().set_password(password)
            return None

        for user in candidates:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
        return None
