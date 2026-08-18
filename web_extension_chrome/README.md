# Browser extension (Chrome, Manifest V3) — SEASON 2026 workshop build

Content-script architecture: prompts are injected directly into the Google
results page; no click on the toolbar icon is needed. This build matches at
most one prompt and does not collect research data.

## Files

- `manifest.json` - MV3 manifest (content script + service worker + popup)
- `config.js` - API origin, build ID, notice version, privacy and project URLs
- `lib/acknowledgement.js` - versioned notice `season-2026-v1`
- `lib/request_lifecycle.js` - at most two matching attempts per query
- `content.js` - injection, layout detection, notice banner, retry wiring
- `content.css` - prompt card + badge styles (light/dark)
- `background.js` - service worker; matching POST only
- `popup.html/js/css` - on/off and topic-group exclusions (no backend calls)

## Load for development

1. Chrome -> `chrome://extensions` -> enable Developer mode
2. "Load unpacked" -> select this `web_extension_chrome/` folder
3. Acknowledge the notice on a Google search; at most one prompt appears when a topic matches

Matching identity is windowed. Local backends before 20 August 2026 need the
server clock freeze described in the repository `AGENTS.md`.

## Page layouts handled (verified June 2026)

| Layout | Detection | Behaviour |
|---|---|---|
| Classic (`udm=14`) | `#rso` present, no AI block | card injected before `#rso` |
| Hybrid (default) | `#rso` + `[data-subtree="aimc"]` | card injected before `#rso`, below the AI response |
| AI Mode (`udm=50`) | AI block only, no `#rso` | floating badge, expandable panel |

Selectors live in one `SELECTORS` object at the top of `content.js`.
