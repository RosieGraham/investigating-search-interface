import json
from pathlib import Path

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from . import models
from .classifier_config import get_classifier_threshold, set_classifier_threshold
from .embedding import ClassifierUnavailable, classify_query, embed_query, rank_prompts
from .services.content_apply import apply_package, count_unapproves, validate_package

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
UNAPPROVE_CONFIRM_TEXT = "UNAPPROVE"
SPOT_CHECK_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "spot-check-queries-2026-07-09.txt"
DATA_SPOT_CHECK = Path(__file__).resolve().parents[2] / "data" / "spot-check-queries-2026-07-09.txt"


def _default_spot_check_queries() -> str:
    for path in (DATA_SPOT_CHECK, SPOT_CHECK_FIXTURE):
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""


def _parse_spot_check_line(line: str) -> tuple[str, bool]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") and not stripped.lower().startswith("#neg"):
        if stripped.startswith("#"):
            return "", False
    negative = False
    if stripped.lower().startswith("#neg "):
        negative = True
        stripped = stripped[5:].strip()
    elif stripped.lower().startswith("#negative "):
        negative = True
        stripped = stripped[10:].strip()
    return stripped, negative


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
    context = {
        "title": "Content operations",
        "spot_check_queries": request.session.get(
            "content_tools_spot_queries", _default_spot_check_queries()
        ),
        "preview": request.session.get("content_tools_preview"),
        "package_filename": request.session.get("content_tools_filename", ""),
        "unapprove_count": request.session.get("content_tools_unapprove_count", 0),
        **_status_context(),
    }
    if extra:
        context.update(extra)
    return context


@staff_member_required
@require_http_methods(["GET", "POST"])
def content_tools(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden("Superuser access required.")

    if request.method == "GET":
        return render(request, "researchdata/content_tools.html", _build_context(request))

    action = request.POST.get("action")
    if not action and request.FILES.get("package"):
        action = "upload_preview"
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
    messages.error(request, "Unknown action.")
    return redirect("content-tools")


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

    request.session["content_tools_package"] = package
    request.session["content_tools_filename"] = upload.name
    request.session["content_tools_unapprove_count"] = count_unapproves(package)
    request.session["content_tools_preview"] = {
        "created": result.created,
        "updated": result.updated,
        "prompt_changes": _preview_prompt_changes(result.changes),
        "change_count": len(result.changes),
    }
    messages.info(request, "Dry run complete. Review the diff below, then click Apply.")
    return redirect("content-tools")


def _handle_apply(request):
    package = request.session.get("content_tools_package")
    filename = request.session.get("content_tools_filename", "package.json")
    if not package:
        messages.error(request, "Upload a package and review the dry run before applying.")
        return redirect("content-tools")

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

    for key in ("content_tools_package", "content_tools_filename", "content_tools_preview", "content_tools_unapprove_count"):
        request.session.pop(key, None)

    messages.success(
        request,
        f"Applied {filename}: created {result.created}, updated {result.updated}.",
    )
    return redirect("content-tools")


def _handle_rebuild_index(request):
    try:
        from .embedding import build_prompt_index, build_topic_index

        build_topic_index(force=True)
        build_prompt_index(force=True)
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
