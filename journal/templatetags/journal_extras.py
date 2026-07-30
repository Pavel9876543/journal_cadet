from django import template

from journal.account_utils import display_name_for_user

register = template.Library()


@register.filter
def get_item(mapping, key):
    return mapping.get(key, [])


@register.filter
def display_user_name(user):
    return display_name_for_user(user)


@register.filter
def short_person_name(value):
    """Show surname and first name without a patronymic in compact rows."""
    return ' '.join(str(value or '').split()[:2])
