"""Runtime classifier configuration read from the database with env fallbacks."""

from django.conf import settings
from django.core.cache import cache

CLASSIFIER_THRESHOLD_KEY = 'CLASSIFIER_THRESHOLD'
_CACHE_KEY = 'researchdata:classifier_threshold'
_CACHE_TTL = 60


def get_classifier_threshold() -> float:
    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return cached

    from .models import Setting

    row = Setting.objects.filter(key=CLASSIFIER_THRESHOLD_KEY).first()
    if row:
        try:
            value = float(row.value)
        except (TypeError, ValueError):
            value = settings.CLASSIFIER_THRESHOLD
    else:
        value = settings.CLASSIFIER_THRESHOLD

    cache.set(_CACHE_KEY, value, _CACHE_TTL)
    return value


def set_classifier_threshold(value: float) -> None:
    from .models import Setting

    Setting.objects.update_or_create(
        key=CLASSIFIER_THRESHOLD_KEY,
        defaults={'value': str(value)},
    )
    cache.delete(_CACHE_KEY)
