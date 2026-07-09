"""Runtime classifier configuration read from the database with env fallbacks."""

from django.conf import settings

CLASSIFIER_THRESHOLD_KEY = 'CLASSIFIER_THRESHOLD'


def get_classifier_threshold() -> float:
    from .models import Setting

    row = Setting.objects.filter(key=CLASSIFIER_THRESHOLD_KEY).first()
    if row:
        try:
            return float(row.value)
        except (TypeError, ValueError):
            pass
    return settings.CLASSIFIER_THRESHOLD


def set_classifier_threshold(value: float) -> None:
    from .models import Setting

    Setting.objects.update_or_create(
        key=CLASSIFIER_THRESHOLD_KEY,
        defaults={'value': str(value)},
    )
