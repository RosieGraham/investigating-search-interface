"""
Shared helpers for SEASON workshop contract tests.

These helpers encode the release assertions. Contract tests call them and
expect success. Inverted-control tests call them against a known-bad
baseline or planted fault and expect AssertionError, proving the helper
is not vacuously true.
"""

from urllib.parse import urlencode

from django.conf import settings

from .models import EngagementEvent, NotRelevantReport, Response

BUILD_ID = "0.2.1-season-2026-1"
EXT_VERSION = "0.2.1"
NOTICE_VERSION = "season-2026-v2"
POLICY_RELATIVE = "release-policy/season-2026-workshop.json"
MATCH_PATH = "/data/prompt/get/"
MARKER = "S26GATE1MARKER_synthetic_not_a_person"

WRITE_ROUTES = (
    "/data/event/post/",
    "/data/notrelevantreport/post/",
    "/data/response/post/",
)

WRITE_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


def workshop_headers(**extra):
    headers = {
        "HTTP_X_ISI_BUILD_ID": BUILD_ID,
        "HTTP_X_ISI_EXTENSION_VERSION": EXT_VERSION,
    }
    headers.update(extra)
    return headers


def write_row_counts():
    return {
        "event": EngagementEvent.objects.count(),
        "response": Response.objects.count(),
        "report": NotRelevantReport.objects.count(),
    }


def assert_json_error(resp, status, error_code):
    if resp.status_code != status:
        raise AssertionError(f"expected status {status}, got {resp.status_code}: {resp.content[:300]!r}")
    if resp.get("Cache-Control") != "no-store":
        raise AssertionError(f"expected Cache-Control no-store, got {resp.get('Cache-Control')!r}")
    try:
        body = resp.json()
    except Exception as exc:
        raise AssertionError(f"expected JSON body, got {resp.content[:300]!r}") from exc
    if body.get("error") != error_code:
        raise AssertionError(f"expected error={error_code!r}, got {body!r}")
    if "user_search_query" in str(body).lower() or MARKER.lower() in str(body).lower():
        raise AssertionError(f"error body leaked query or marker: {body!r}")


def assert_research_writes_refused(client):
    """Every method on every research-write route must 403 before creating a row."""
    before = write_row_counts()
    payloads = {
        "/data/event/post/": {"event_type": "prompt_shown", "session_key": MARKER},
        "/data/notrelevantreport/post/": {
            "active_prompt_id": "1",
            "user_search_query": MARKER,
            "classifier_confidence": "0.9",
        },
        "/data/response/post/": {
            "active_prompt_id": "1",
            "user_response_content": MARKER,
        },
    }
    for path in WRITE_ROUTES:
        for method in WRITE_METHODS:
            resp = client.generic(
                method,
                path,
                data=urlencode(payloads[path]),
                content_type="application/x-www-form-urlencoded",
            )
            if method == "HEAD":
                if resp.status_code != 403:
                    raise AssertionError(f"{method} {path} expected 403, got {resp.status_code}")
                if resp.get("Cache-Control") != "no-store":
                    raise AssertionError(f"{method} {path} expected Cache-Control no-store, got {resp.get('Cache-Control')!r}")
            else:
                assert_json_error(resp, 403, "research_writes_disabled")
    after = write_row_counts()
    if after != before:
        raise AssertionError(f"row counts changed: {before} -> {after}")
    if Response.objects.filter(response_content=MARKER).exists():
        raise AssertionError("marker written to Response")
    if NotRelevantReport.objects.filter(user_search_query=MARKER).exists():
        raise AssertionError("marker written to NotRelevantReport")
    if EngagementEvent.objects.filter(session_key=MARKER).exists():
        raise AssertionError("marker written to EngagementEvent")


def assert_research_writes_enabled_setting_is_false():
    if not hasattr(settings, "RESEARCH_WRITES_ENABLED"):
        raise AssertionError("RESEARCH_WRITES_ENABLED is not a Django setting")
    if settings.RESEARCH_WRITES_ENABLED is not False:
        raise AssertionError(
            f"RESEARCH_WRITES_ENABLED default must be False, got {settings.RESEARCH_WRITES_ENABLED!r}"
        )


def match_post(client, query, extra_fields=None, content_type="application/x-www-form-urlencoded", headers=None, **client_kw):
    fields = {"user_search_query": query}
    if extra_fields:
        fields.update(extra_fields)
    hdrs = workshop_headers() if headers is None else headers
    return client.post(
        MATCH_PATH,
        data=urlencode(fields),
        content_type=content_type,
        **hdrs,
        **client_kw,
    )
