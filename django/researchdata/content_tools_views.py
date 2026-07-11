import hashlib
import json
from pathlib import Path

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from . import evaluation, models
from .classifier_config import (
    get_classifier_margin,
    get_classifier_threshold,
    set_classifier_threshold,
)
from .embedding import ClassifierUnavailable, classify_query, embed_query, rank_prompts
from .services.content_apply import apply_package, count_unapproves, validate_package

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
UNAPPROVE_CONFIRM_TEXT = "UNAPPROVE"
SPOT_CHECK_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "spot-check-queries-2026-07-09.txt"
DATA_SPOT_CHECK = Path(__file__).resolve().parents[2] / "data" / "spot-check-queries-2026-07-09.txt"
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _latest_labelled_file():
    candidates = sorted(DATA_DIR.glob("labelled-queries-*.csv"))
    return candidates[-1] if candidates else None


def _default_spot_check_queries() -> str:
    for path in (DATA_SPOT_CHECK, SPOT_CHECK_FIXTURE):
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""


def _package_digest(package: dict) -> str:
    """Stable fingerprint so Apply can only run against the previewed bytes."""
    canonical = json.dumps(package, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_spot_check_line(line: str) -> tuple[str, bool]:
    stripped = line.strip()
    if not stripped:
        return "", False

    lower = stripped.lower()
    if lower.startswith("#neg ") or lower.startswith("#negative "):
        if lower.startswith("#neg "):
            query = stripped[5:].strip()
        else:
            query = stripped[10:].strip()
        if not query:
            return "", False
        return query, True

    # Bare "#neg" / "#negative" (no query) and all other #-lines are comments.
    if stripped.startswith("#"):
        return "", False
    return stripped, False


def _spot_check_rows(query_text: str):
    rows = []
    threshold = get_classifier_threshold()
    for line in query_text.splitlines():
        query, negative = _parse_spot_check_line(line)
        if not query:
            continue
        row = {
            "query": query,
            "negative_control": negative,
            "top_topic": "",
            "confidence": None,
            "above_threshold": False,
            "prompts": [],
            "error": "",
            "highlight": False,
        }
        try:
            matches = classify_query(query, threshold=0.0, top_k=1)
            if matches:
                topic_id, confidence = matches[0]
                topic = models.Topic.objects.select_related("topic_group").filter(id=topic_id).first()
                row["top_topic"] = topic.name if topic else f"topic #{topic_id}"
                row["confidence"] = round(confidence, 4)
                row["above_threshold"] = confidence >= threshold
                query_vec = embed_query(query)
                candidate_map = {
                    p.id: p
                    for p in models.Prompt.objects.filter(
                        admin_approved=True, topic_id=topic_id
                    ).select_related("topic")
                }
                if candidate_map:
                    ranked = rank_prompts(query_vec, list(candidate_map.keys()))
                    row["prompts"] = [
                        textwrap_short(candidate_map[pid].prompt_content)
                        for pid, _ in ranked[:3]
                        if pid in candidate_map
                    ]
            row["highlight"] = negative and bool(matches) and row["above_threshold"]
        except ClassifierUnavailable:
            row["error"] = "Classifier unavailable"
        except Exception:
            row["error"] = "Classification error"
        rows.append(row)
    return rows


def textwrap_short(text: str, width: int = 120) -> str:
    import textwrap

    return textwrap.shorten(text.replace("\n", " "), width=width, placeholder="...")


def _status_context():
    last_apply = models.ContentApply.objects.filter(dry_run=False).order_by("-created_datetime").first()
    return {
        "topic_count": models.Topic.objects.count(),
        "described_count": models.Topic.objects.exclude(description__isnull=True)
        .exclude(description="")
        .count(),
        "approved_prompt_count": models.Prompt.objects.filter(admin_approved=True).count(),
        "classifier_threshold": get_classifier_threshold(),
        "last_apply_at": last_apply.created_datetime if last_apply else None,
    }


def _record_apply(request, filename: str, dry_run: bool, result):
    models.ContentApply.objects.create(
        actor=request.user,
        filename=filename,
        dry_run=dry_run,
        created_counts=result.created,
        updated_counts=result.updated,
        changes=[list(change) for change in result.changes],
    )


def _preview_prompt_changes(changes):
    interesting = []
    for kind, name_or_ref, field_name, before, after in changes:
        if kind == "prompt" and field_name in ("prompt_content", "admin_approved"):
            interesting.append(
                {
                    "ref": name_or_ref,
                    "field": field_name,
                    "before": before,
                    "after": after,
                }
            )
    return interesting


def _build_context(request, extra=None):
    labelled = _latest_labelled_file()
    context = {
        "title": "Content operations",
        "spot_check_queries": request.session.get(
            "content_tools_spot_queries", _default_spot_check_queries()
        ),
        "preview": request.session.get("content_tools_preview"),
        "package_filename": request.session.get("content_tools_filename", ""),
        "unapprove_count": request.session.get("content_tools_unapprove_count", 0),
        "labelled_file_name": labelled.name if labelled else None,
        "classifier_margin": get_classifier_margin(),
        "recent_evaluations": models.MatchingEvaluation.objects.all()[:10],
        # Template flag: hides write forms from staff editors. Cosmetic only;
        # the POST gate in content_tools() is the security boundary.
        "can_manage_content": request.user.is_superuser,
        **_status_context(),
    }
    if extra:
        context.update(extra)
    return context


# Actions that change the live system: package upload/apply, index rebuild,
# decision-rule changes. Staff editors get the READ actions only (spot check,
# run evaluation). This gate, not template hiding, is the security boundary.
SUPERUSER_ACTIONS = frozenset({"upload_preview", "apply", "rebuild_index", "set_threshold"})


@staff_member_required
@require_http_methods(["GET", "POST"])
def content_tools(request):
    if request.method == "GET":
        return render(request, "researchdata/content_tools.html", _build_context(request))

    action = request.POST.get("action")
    if not action and request.FILES.get("package"):
        action = "upload_preview"
    if action in SUPERUSER_ACTIONS and not request.user.is_superuser:
        return HttpResponseForbidden(
            "Superuser access required for this action. Staff accounts may run "
            "the spot check and the evaluation, but cannot change content or "
            "the decision rule."
        )
    if action == "upload_preview":
        return _handle_upload_preview(request)
    if action == "apply":
        return _handle_apply(request)
    if action == "rebuild_index":
        return _handle_rebuild_index(request)
    if action == "spot_check":
        return _handle_spot_check(request)
    if action == "set_threshold":
        return _handle_set_threshold(request)
    if action == "run_evaluation":
        return _handle_run_evaluation(request)
    messages.error(request, "Unknown action.")
    return redirect("content-tools")


def _handle_run_evaluation(request):
    """Run the matching evaluation harness against the committed labelled set.

    Read-only with respect to content: the only write is the
    MatchingEvaluation provenance row, which is the point (a time series).
    Uses the live threshold and margin so the browser run measures exactly
    what users are currently getting.
    """
    labelled = _latest_labelled_file()
    if labelled is None:
        messages.error(request, "No labelled query CSV found in data/.")
        return redirect("content-tools")
    try:
        rows = evaluation.load_labelled_csv(labelled)
        result = evaluation.evaluate(
            rows,
            threshold=get_classifier_threshold(),
            margin=get_classifier_margin(),
        )
    except ClassifierUnavailable:
        messages.error(request, "Classifier model is not available on this instance.")
        return redirect("content-tools")
    except Exception:
        messages.error(request, "Evaluation failed; see the application log.")
        return redirect("content-tools")

    from django.conf import settings as django_settings

    record = models.MatchingEvaluation.objects.create(
        git_sha=evaluation.git_sha(),
        model_id=django_settings.EMBEDDING_MODEL_ID,
        model_artifact=evaluation.model_artifact_fingerprint(),
        runtime=evaluation.runtime_descriptor(),
        index_fingerprint=evaluation.current_index_fingerprint(),
        labelled_file=labelled.name,
        threshold=get_classifier_threshold(),
        margin=get_classifier_margin(),
        metrics=result["metrics"],
        results=result["rows"],
        notes=f"Browser run by {request.user}",
    )
    messages.success(request, f"Evaluation #{record.id} complete.")
    return render(
        request,
        "researchdata/content_tools.html",
        _build_context(request, {
            "evaluation_metrics": result["metrics"],
            "evaluation_confusion": result["confusion"],
            "evaluation_record": record,
        }),
    )


def _handle_upload_preview(request):
    upload = request.FILES.get("package")
    if not upload:
        messages.error(request, "Choose a JSON package file to upload.")
        return redirect("content-tools")
    if upload.size > MAX_UPLOAD_BYTES:
        messages.error(request, "Package file is too large (limit 5 MB).")
        return redirect("content-tools")
    if not upload.name.lower().endswith(".json"):
        messages.error(request, "Only .json package files are accepted.")
        return redirect("content-tools")

    try:
        raw = upload.read().decode("utf-8")
        package = json.loads(raw)
        validate_package(package)
    except UnicodeDecodeError:
        messages.error(request, "Package file must be UTF-8 text.")
        return redirect("content-tools")
    except json.JSONDecodeError:
        messages.error(request, "File is not valid JSON.")
        return redirect("content-tools")
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if exc.messages else str(exc))
        return redirect("content-tools")

    result = apply_package(package, dry_run=True, actor=request.user)
    _record_apply(request, upload.name, dry_run=True, result=result)

    # Pre-apply matching guard: score the candidate content against the
    # EXISTING index before anything is written. A candidate whose example
    # queries are already strongly captured by an unrelated topic will fight
    # it after apply; a candidate whose queries all land on one existing
    # topic probably duplicates it. Advisory, never blocking: content
    # editors overrule machines here, but they do it knowingly.
    guard_warnings = []
    try:
        from .classifier_config import get_classifier_threshold as _thr
        from .diagnostics import probe_candidate_topics

        existing_names = set(
            models.Topic.objects.values_list("name", flat=True))
        candidates = [t for t in package.get("topics", [])
                      if t.get("name") not in existing_names]
        threshold = _thr()
        for report in probe_candidate_topics(candidates):
            capturers = {}
            for row in report["queries"]:
                if row["current_score"] >= threshold:
                    capturers.setdefault(row["current_top1"], []).append(
                        f"{row['query']} ({row['current_score']:.2f})")
            for topic_name, captured in capturers.items():
                guard_warnings.append(
                    f"New topic '{report['candidate']}': existing topic "
                    f"'{topic_name}' already answers {len(captured)} of its "
                    f"example queries above threshold: {'; '.join(captured[:3])}"
                )
    except ClassifierUnavailable:
        guard_warnings.append(
            "Classifier unavailable on this instance: the pre-apply matching "
            "guard did not run.")
    except Exception:
        guard_warnings.append(
            "Pre-apply matching guard failed; see the application log.")

    digest = _package_digest(package)
    # Store canonical JSON (not a Python dict) so Apply re-parses the same bytes
    # the dry run saw. Digest is also echoed in the Apply form to catch two-tab races.
    request.session["content_tools_package_json"] = json.dumps(
        package, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    request.session["content_tools_package_digest"] = digest
    request.session["content_tools_filename"] = upload.name
    request.session["content_tools_unapprove_count"] = count_unapproves(package)
    request.session["content_tools_preview"] = {
        "created": result.created,
        "updated": result.updated,
        "prompt_changes": _preview_prompt_changes(result.changes),
        "change_count": len(result.changes),
        "digest": digest,
        "guard_warnings": guard_warnings,
    }
    # Drop any legacy key from earlier builds.
    request.session.pop("content_tools_package", None)
    messages.info(request, "Dry run complete. Review the diff below, then click Apply.")
    return redirect("content-tools")


def _handle_apply(request):
    package_json = request.session.get("content_tools_package_json")
    filename = request.session.get("content_tools_filename", "package.json")
    session_digest = request.session.get("content_tools_package_digest")
    form_digest = request.POST.get("package_digest", "").strip()
    if not package_json or not session_digest:
        messages.error(request, "Upload a package and review the dry run before applying.")
        return redirect("content-tools")
    if form_digest != session_digest:
        messages.error(
            request,
            "The previewed package no longer matches this Apply form "
            "(another upload may have replaced it). Upload and preview again.",
        )
        return redirect("content-tools")

    try:
        package = json.loads(package_json)
    except json.JSONDecodeError:
        messages.error(request, "Stored package is corrupt. Upload and preview again.")
        return redirect("content-tools")
    if _package_digest(package) != session_digest:
        messages.error(request, "Stored package digest mismatch. Upload and preview again.")
        return redirect("content-tools")

    # Recompute against live DB at apply time (not the preview-time count).
    unapprove_count = count_unapproves(package)
    if unapprove_count > 5:
        if request.POST.get("confirm_text", "").strip() != UNAPPROVE_CONFIRM_TEXT:
            messages.error(
                request,
                f"This package would unapprove {unapprove_count} prompts. "
                f"Type {UNAPPROVE_CONFIRM_TEXT} to confirm.",
            )
            return redirect("content-tools")

    result = apply_package(package, dry_run=False, actor=request.user)
    _record_apply(request, filename, dry_run=False, result=result)

    for key in (
        "content_tools_package",
        "content_tools_package_json",
        "content_tools_package_digest",
        "content_tools_filename",
        "content_tools_preview",
        "content_tools_unapprove_count",
    ):
        request.session.pop(key, None)

    # Rebuild eagerly so a research participant does not pay the first-query
    # cost. Topic saves already marked the indexes dirty; this clears them
    # before the response returns. Failures are non-fatal: lazy rebuild remains.
    rebuild_note = ""
    try:
        _rebuild_indexes_locked()
        rebuild_note = " Classifier indexes rebuilt."
    except ClassifierUnavailable:
        rebuild_note = " Classifier model unavailable; indexes will rebuild on the next query."

    messages.success(
        request,
        f"Applied {filename}: created {result.created}, updated {result.updated}.{rebuild_note}",
    )
    return redirect("content-tools")


def _rebuild_indexes_locked():
    """Force-rebuild under the same lock as lazy _ensure_* paths."""
    from .embedding import _lock, build_prompt_index, build_topic_index

    with _lock:
        build_topic_index(force=True)
        build_prompt_index(force=True)


def _handle_rebuild_index(request):
    try:
        _rebuild_indexes_locked()
        messages.success(request, "Classifier indexes rebuilt. This can take up to a minute.")
    except ClassifierUnavailable:
        messages.error(request, "Classifier model is not available on this instance.")
    return redirect("content-tools")


def _handle_spot_check(request):
    query_text = request.POST.get("spot_check_queries", "")
    request.session["content_tools_spot_queries"] = query_text
    rows = _spot_check_rows(query_text)
    return render(
        request,
        "researchdata/content_tools.html",
        _build_context(request, {"spot_check_results": rows}),
    )


def _handle_set_threshold(request):
    raw = request.POST.get("classifier_threshold", "").strip()
    try:
        value = float(raw)
    except ValueError:
        messages.error(request, "Threshold must be a number.")
        return redirect("content-tools")
    if not 0 <= value <= 1:
        messages.error(request, "Threshold must be between 0 and 1.")
        return redirect("content-tools")
    set_classifier_threshold(value)
    messages.success(request, f"Classifier threshold set to {value}.")
    return redirect("content-tools")
