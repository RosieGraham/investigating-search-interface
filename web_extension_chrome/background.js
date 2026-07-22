/*
Service worker for the Investigating Search Interface extension.

All network requests go through here rather than the content script: requests
from a service worker are authorised by host_permissions (no CORS preflight
problems on the Google page), and it keeps the API origin in exactly one place.
*/

importScripts('config.js');

const API_BASE = self.ISI_CONFIG.API_BASE.replace(/\/$/, '');

/** Get (or mint) the persistent per-installation pseudonymous identifier. */
async function getInstallationId() {
  // Migration: read old key, write to new key, delete old key
  const stored = await chrome.storage.local.get(['isi_installation_id', 'isi_session_key']);
  if (stored.isi_installation_id) return stored.isi_installation_id;
  const id = stored.isi_session_key || crypto.randomUUID().replace(/-/g, '');
  await chrome.storage.local.set({ isi_installation_id: id });
  if (stored.isi_session_key) await chrome.storage.local.remove('isi_session_key');
  return id;
}

async function apiPost(path, form) {
  const body = new URLSearchParams();
  for (const [k, v] of Object.entries(form || {})) {
    if (v !== undefined && v !== null && v !== '') body.set(k, String(v));
  }
  const resp = await fetch(API_BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  });
  if (!resp.ok) throw new Error(`API ${resp.status}`);
  return resp.json();
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  (async () => {
    try {
      if (message.type === 'getPrompts') {
        // POST keeps the search query out of URL-level server/CDN/browser logs.
        const data = await apiPost('/data/prompt/get/', {
          user_search_query: message.query,
          topics_exclude: (message.topicsExclude || []).join(','),
        });
        sendResponse({ ok: true, data });
      } else if (message.type === 'postReport') {
        const data = await apiPost('/data/notrelevantreport/post/', message.form);
        sendResponse({ ok: true, data });
      } else if (message.type === 'postResponse') {
        const data = await apiPost('/data/response/post/', message.form);
        sendResponse({ ok: true, data });
      } else if (message.type === 'logEvent') {
        const installation_id = await getInstallationId();
        // Wire param stays session_key for backend compatibility; semantic
        // rename is in the extension (see getInstallationId).
        const data = await apiPost('/data/event/post/', {
          ...message.form,
          session_key: installation_id,
        });
        sendResponse({ ok: true, data });
      } else if (message.type === 'health') {
        const resp = await fetch(API_BASE + '/healthz');
        sendResponse({ ok: resp.ok });
      } else {
        sendResponse({ ok: false, error: 'unknown message type' });
      }
    } catch (e) {
      sendResponse({ ok: false, error: String(e) });
    }
  })();
  return true; // keep the message channel open for the async response
});
