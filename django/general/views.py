"""Public workshop pages. These views must not read the database."""

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_safe


def _anonymous_html(template_name):
    html = render_to_string(template_name)
    return HttpResponse(html, content_type="text/html; charset=utf-8")


@require_safe
def workshop_home(request):
    """Anonymous landing page for Store Homepage and Support URLs."""
    return _anonymous_html("general/home.html")


@require_safe
def workshop_cookies(request):
    """Anonymous cookies notice. No context, no database."""
    return _anonymous_html("general/cookies.html")


@require_safe
def workshop_privacy_notice(request):
    """Anonymous SEASON 2026 privacy notice. No context, no database."""
    return _anonymous_html("general/privacy.html")
