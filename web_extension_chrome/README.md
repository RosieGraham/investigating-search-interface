# Browser extension (Chrome, Manifest V3)

Content-script architecture: prompts are injected directly into the Google
results page; no click on the toolbar icon is needed.

## Files

- `manifest.json` - MV3 manifest (content script + service worker + popup)
- `config.js` - **the one file to edit**: set `API_BASE` to your Render URL
- `content.js` - injection, layout detection (classic / hybrid / AI Mode),
  MutationObserver and SPA-navigation handling
- `content.css` - prompt card + badge styles (light/dark)
- `background.js` - service worker; all API calls route through here
- `popup.html/js/css` - settings: on/off, topic-group exclusions, event opt-out
- `jquery-3.7.1.min.js` - legacy from v1, no longer referenced; delete when convenient

## Load for development

1. Edit `config.js` if your backend is not at `http://localhost:8000`
2. Chrome -> `chrome://extensions` -> enable Developer mode
3. "Load unpacked" -> select this `web_extension_chrome/` folder
4. Search Google; prompts appear above results when a topic matches

## Page layouts handled (verified June 2026)

| Layout | Detection | Behaviour |
|---|---|---|
| Classic (`udm=14`) | `#rso` present, no AI block | card injected before `#rso` |
| Hybrid (default) | `#rso` + `[data-subtree="aimc"]` | card injected before `#rso`, below the AI response |
| AI Mode (`udm=50`) | AI block only, no `#rso` | floating badge, expandable panel (full inline support is Phase 2) |

Selectors live in one `SELECTORS` object at the top of `content.js`.

## Before Chrome Web Store submission

- Narrow `host_permissions` from `https://*.onrender.com/*` to your exact
  service URL
- Replace the placeholder icon if desired
- Zip the folder excluding `jquery-3.7.1.min.js` and `local_settings.example.js`
