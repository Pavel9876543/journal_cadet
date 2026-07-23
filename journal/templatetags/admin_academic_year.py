from django import template

from journal.academic_year_context import get_admin_academic_year_context

register = template.Library()


@register.simple_tag(takes_context=True)
def admin_academic_year_context(context):
    request = context.get('request')
    if request is None:
        return {}
    return get_admin_academic_year_context(request)
