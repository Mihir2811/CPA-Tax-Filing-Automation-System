from django import template

register = template.Library()

@register.filter
def hide_tsj_not_specified(value):
    """
    Filter to hide 'TSJ not specified' and replace with '-'
    """
    if isinstance(value, str) and value.strip() == 'TSJ not specified':
        return '-'
    return value

@register.filter
def folder_name(value):
    """
    Convert client name to folder format: 'John Doe' -> 'JOHN_DOE'
    """
    if isinstance(value, str):
        return value.upper().replace(' ', '_')
    return value
