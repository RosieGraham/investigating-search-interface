"""Runtime classifier configuration read from the database with env fallbacks."""

from django.conf import settings
from django.core.cache import cache

CLASSIFIER_THRESHOLD_KEY = 'CLASSIFIER_THRESHOLD'
CLASSIFIER_MARGIN_KEY = 'CLASSIFIER_MARGIN'
_CACHE_KEY = 'researchdata:classifier_threshold'
_MARGIN_CACHE_KEY = 'researchdata:classifier_margin'
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


def get_classifier_margin() -> float:
    """Required top1 minus top2 gap before a match is served.

    Default 0.0 preserves the historical behaviour (threshold only). The
    margin exists because scores from the int8 encoder carry runtime noise
    larger than many observed top-1 to top-2 gaps: a decision inside the
    noise band is a coin toss, and abstaining is the honest move.
    """
    cached = cache.get(_MARGIN_CACHE_KEY)
    if cached is not None:
        return cached

    from .models import Setting

    row = Setting.objects.filter(key=CLASSIFIER_MARGIN_KEY).first()
    value = 0.0
    if row:
        try:
            value = float(row.value)
        except (TypeError, ValueError):
            value = 0.0

    cache.set(_MARGIN_CACHE_KEY, value, _CACHE_TTL)
    return value


def set_classifier_margin(value: float) -> None:
    from .models import Setting

    Setting.objects.update_or_create(
        key=CLASSIFIER_MARGIN_KEY,
        defaults={'value': str(value)},
    )
    cache.delete(_MARGIN_CACHE_KEY)


CLASSIFIER_BLEND_ALPHA_KEY = 'CLASSIFIER_BLEND_ALPHA'
_ALPHA_CACHE_KEY = 'researchdata:classifier_blend_alpha'


def get_classifier_blend_alpha() -> float:
    """Weight of the description score in the blended topic score:
    alpha * desc + (1 - alpha) * max(example queries).

    0.35 sits mid-plateau in the ablation sweep (0.25 to 0.40 all perform
    equivalently); 1.0 reproduces description-only scoring."""
    cached = cache.get(_ALPHA_CACHE_KEY)
    if cached is not None:
        return cached

    from .models import Setting

    row = Setting.objects.filter(key=CLASSIFIER_BLEND_ALPHA_KEY).first()
    value = settings.CLASSIFIER_BLEND_ALPHA
    if row:
        try:
            value = float(row.value)
        except (TypeError, ValueError):
            value = settings.CLASSIFIER_BLEND_ALPHA
    value = min(max(value, 0.0), 1.0)

    cache.set(_ALPHA_CACHE_KEY, value, _CACHE_TTL)
    return value


def set_classifier_blend_alpha(value: float) -> None:
    from .models import Setting

    Setting.objects.update_or_create(
        key=CLASSIFIER_BLEND_ALPHA_KEY,
        defaults={'value': str(value)},
    )
    cache.delete(_ALPHA_CACHE_KEY)


SERVE_PLACEHOLDERS_KEY = 'SERVE_PLACEHOLDERS'
_SERVE_PLACEHOLDERS_CACHE_KEY = 'researchdata:serve_placeholders'


def get_serve_placeholders() -> bool:
    """Evaluation-scaffold flag: serve a placeholder card (matched_by=
    'placeholder') for a topic that matched above threshold but has no
    approved prompt yet, instead of silently skipping it. Default False
    preserves historical behaviour. See Working Docs/Placeholder-card
    serving toggle - build spec - 13 July 2026.md."""
    cached = cache.get(_SERVE_PLACEHOLDERS_CACHE_KEY)
    if cached is not None:
        return cached

    from .models import Setting

    row = Setting.objects.filter(key=SERVE_PLACEHOLDERS_KEY).first()
    value = (row.value.strip().lower() == 'true') if row and row.value is not None else False

    cache.set(_SERVE_PLACEHOLDERS_CACHE_KEY, value, _CACHE_TTL)
    return value


def set_serve_placeholders(on: bool) -> None:
    from .models import Setting

    Setting.objects.update_or_create(
        key=SERVE_PLACEHOLDERS_KEY,
        defaults={'value': 'true' if on else 'false'},
    )
    cache.delete(_SERVE_PLACEHOLDERS_CACHE_KEY)
