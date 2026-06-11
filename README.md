# Investigating Search Interface

A Chrome extension + Django backend that surfaces short, expert-authored
ethical reflection prompts alongside Google search results, matched to the
user's query by semantic vector classification. A University of Birmingham
research project led by Rosie Graham, connected to the planned SEEED
encyclopaedia (Search Engine Ethics Encyclopaedia and Database).

Formerly the **Ethical Interface**. The original codebase was developed by
Mike Allaway (BEAR Software, University of Birmingham) and remains at
[bear-rsg/ethical-interface](https://github.com/bear-rsg/ethical-interface);
this repository is the June 2026 rebuild: self-hostable, classifier-first,
content-script extension. MIT licensed, attribution preserved (LICENSE.md).

## How it works

```
Google results page
  └─ content script detects layout (classic / hybrid / AI Mode)
       └─ service worker calls the API with the query
            └─ Django: SBERT vector classifier (ONNX, no PyTorch)
                 ├─ match above threshold -> prompt(s) returned
                 ├─ no match -> legacy trigger fallback
                 └─ nothing -> extension stays silent
```

Prompts appear above the organic results (never replacing or reranking
anything), with attribution, a "Learn more in SEEED" link when available,
and a "Not relevant" reporter that feeds threshold calibration.

## Repository layout

- `django/` - backend (Django 5.2): models, admin, API, classifier
  (`researchdata/embedding.py`), management commands, tests
- `web_extension_chrome/` - Manifest V3 extension (see its README)
- `scripts/` - threshold calibration, topic description batches, data export
- `data/` - local data files (gitignored: research content stays out of the
  public repo)
- `render.yaml`, `render-start.sh` - one-click-ish deployment to Render
- `DEPLOY.md` - **start here**: step-by-step from zero accounts to live
- `ARCHITECTURE.md` - decisions and rationale

## Local development quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cd django
DEBUG=true python manage.py migrate
DEBUG=true python manage.py download_model       # ~22MB ONNX model
DEBUG=true python manage.py import_live_export   # if data/ has an export
DEBUG=true python manage.py build_topic_index
DEBUG=true python manage.py createsuperuser
DEBUG=true python manage.py runserver
```

Admin: http://localhost:8000/dashboard/ · API test:
`http://localhost:8000/data/prompt/get/?user_search_query=keto+diet+safe`

Run tests: `DEBUG=true python manage.py test researchdata`

## Key commands

| Command | Purpose |
|---|---|
| `manage.py download_model` | fetch ONNX model + tokenizer (build step) |
| `manage.py build_topic_index` | (re)embed all topics |
| `manage.py import_live_export [file]` | import data exported from the old system |
| `scripts/calibrate_threshold.py` | sweep thresholds against real data |
| `scripts/apply_topic_descriptions.py` | apply a JSON batch of topic descriptions |
| `scripts/export_from_admin.js` | re-export data from the old admin (browser console) |
