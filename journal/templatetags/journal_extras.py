from django import template

from journal.account_utils import display_name_for_user

register = template.Library()


@register.filter
def get_item(mapping, key):
    return mapping.get(key, [])


@register.filter
def display_user_name(user):
    return display_name_for_user(user)
