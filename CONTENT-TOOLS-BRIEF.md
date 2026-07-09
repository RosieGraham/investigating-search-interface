# No-terminal operations: Cursor handoff brief

*9 July 2026. Goal: Rosie never opens a terminal to update the tool. Written for Cursor. Read the "already exists" section first, because most of the interface is there and should not be rebuilt.*

## The problem, in one line

Editing one prompt is already a browser click. Applying a package of 72 objects at once is a Python script, run from a laptop, against a database whose credentials live in an environment variable. That is the only reason a terminal is involved.

## What already exists, do not rebuild it

At `/dashboard/` (Django admin, login with the email address, not a username), `researchdata/admin.py` already provides:

- Topic admin with a `has_description` boolean column, group filter and full-text search.
- Prompt admin with `prompt_content`, `priority`, `admin_approved`, `has_seeed_url`, triggers via `filter_horizontal`, and bulk `approve` / `unapprove` actions.
- TopicGroup, Trigger, Response, NotRelevantReport, EngagementEvent admins.

So single-object editing, bulk approval and description writing are all solved.

## The architectural fact that makes this worth doing

`embedding.py` holds the topic index in a module-level global, and `_index_dirty` is flipped by a `post_save` signal wired in `apps.py`. **The signal only fires in the process that performs the save.**

This is why a script run from a laptop leaves the live classifier stale until Render restarts, and it is also why *an apply performed inside the web process needs no restart at all*: the admin's own save fires the signal in the serving process, and `_ensure_index()` rebuilds on the next query.

That single fact is the whole case for moving apply into the admin.

**Gotcha to preserve:** this only holds because `render.yaml` sets `WEB_CONCURRENCY: 1`. With two or more gunicorn workers, an admin save invalidates one worker's index and leaves the others stale, with no error. If concurrency is ever raised, the dirty flag has to become shared state (a `Setting` row holding a timestamp or fingerprint, compared per request) rather than a process global. Leave a comment saying so.

---

## Path A: shipped, needs no code review from you

`.github/workflows/apply-content-package.yml` is already written. It gives Rosie a **Run workflow** button in the GitHub web UI: pick a package filename, tick or untick dry run, read the log. On a real apply it curls a Render deploy hook so the index rebuilds on boot.

One-time setup, both in a browser, no terminal:

1. GitHub, repo Settings, Secrets and variables, Actions, New repository secret: `DATABASE_URL`, the Neon string (the same one Render already has under Environment).
2. Render, the service, Settings, Deploy Hook, copy that URL. Add it as a second secret named `RENDER_DEPLOY_HOOK`.

This unblocks her today. Path B is the durable version.

---

## Path B: a Content operations page in the admin

### B0. Refactor first. This is the important instruction.

Move the body of `apply()` out of `scripts/apply_content_package.py` into a new module:

```
django/researchdata/services/content_apply.py
    def apply_package(package: dict, *, dry_run: bool, actor=None) -> ApplyResult
```

`ApplyResult` carries `created`, `updated`, and a `changes` list of `(kind, name_or_ref, field, before, after)`. The existing script becomes a thin CLI wrapper that calls it, so the script and the admin page can never drift apart. Keep the current guarantees: idempotent, matched by name for groups and topics and by the `ref:<REF> ` token for prompts, never deletes, wrapped in `transaction.atomic`, rolls back on dry run.

### B1. The page

A staff-only view at `/dashboard/content-tools/`, linked from the admin index. Four panels.

**Upload and apply.** File input accepting a package JSON. On upload: validate against a schema (groups, topics with description and example_queries, prompts with ref, topic, style, prompt_content, priority, admin_approved, triggers), then run `apply_package(dry_run=True)` and render the diff. Show counts, and for every prompt whose `prompt_content` or `admin_approved` changes, show before and after side by side. An **Apply** button then re-runs with `dry_run=False`. Never apply on upload.

**Rebuild the classifier index.** A button calling `build_topic_index(force=True)` and `build_prompt_index(force=True)`. Warn that it takes up to a minute. It should almost never be needed once apply runs in-process, but it is the escape hatch when something looks stale.

**Spot check.** A textarea of queries, one per line, prefilled from `data/spot-check-queries-2026-07-09.txt`. Runs `classify_query` in-process and renders a table: query, top matched topic, confidence, above threshold, and the prompts that would be returned. Highlight in red any query tagged as a negative control that returned a match. This replaces `scripts/spot_check_live.py` for her, and it runs against the same process rather than over HTTP.

**Status.** Read-only: number of topics, how many have descriptions, approved prompt count, the current `CLASSIFIER_THRESHOLD`, and the timestamp of the last apply.

### B2. Audit trail

```
class ContentApply(models.Model):
    actor, filename, dry_run, created_counts (JSON), updated_counts (JSON),
    changes (JSON), created_datetime
```

Written on every apply, dry run included. Register it read-only in the admin. This is also the provenance record for the methods log.

### B3. Optional, and worth it

Move `CLASSIFIER_THRESHOLD` from an environment variable to a `Setting` row read per request, defaulting to the env value. Today, changing the threshold means editing a Render environment variable, which triggers a full deploy. It should be a number in a form. `classify_query(threshold=None)` already accepts an override, so the change is small.

### B4. Constraints, do not design around them badly

- Free Render tier: one gunicorn worker, four threads, 512MB, `--timeout 120`, no shell.
- The apply is database-bound and fast. The index rebuild embeds every topic, chunked at `batch_size=16` in `encode()`. Keep the chunking. The June out-of-memory crash was an unchunked single batch.
- With one worker, a long apply blocks the tool for its duration. At 35 topics and 140 prompts this is seconds. If the inherited 193 topics ever get descriptions, revisit.
- Do not import the ONNX model at module import time in the view. Let `_load_runtime()` stay lazy.

### B5. Safety

- `@staff_member_required` plus an explicit `is_superuser` check.
- POST with CSRF for anything that writes. Nothing destructive behind a GET.
- Dry run is the default and the only path to the Apply button.
- Cap the upload at 5MB and reject non-JSON.
- If a package would unapprove more than five prompts, require a typed confirmation. Emilia's single cut should sail through; a mistake that unapproves fifty should not.
- Never expose `DATABASE_URL` or the deploy hook in any template or log line.

### B6. Tests

Run them on local disk, not inside the mounted workspace folder, because SQLite and Django tests fail there. Cover: dry run writes nothing; apply is idempotent when run twice; a changed `prompt_content` is reported in `changes`; an unapprove is reported; a malformed package is rejected before any write; a non-staff user gets a 403.

### B7. Acceptance

Rosie can, in a browser only: upload `content-package-2026-07-09.json`, read a diff, click Apply, see the new topics matched by the spot-check panel without restarting anything, and change the threshold. No terminal, no deploy, no credentials on her laptop.

## Out of scope

Editing prompt content in bulk (the admin does single edits, and packages come from the content pipeline). Deleting anything. Touching `import_live_export`, which is destructive and stays a script.
