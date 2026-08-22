/*
Service worker for the SEASON 2026 workshop extension.

Matching is the only network path. Research handlers, installation IDs and
health checks are not present. Retry lives in the content-script state machine
so this worker performs at most one fetch per message, and aborts it when the
content script invalidates the request.
*/

importScripts('config.js');

const API_BASE = self.ISI_CONFIG.API_BASE.replace(/\/$/, '');
const MATCH_PATH = '/data/prompt/get/';
const ACK_KEY = 'isi_notice_' + self.ISI_CONFIG.NOTICE_VERSION;
const DECLINE_KEY = 'isi_notice_declined_' + self.ISI_CONFIG.NOTICE_VERSION;
const MAX_QUERY_CODEPOINTS = 2048;
const inflight = new Map();

function storageGet(keys) {
  return new Promise(function (resolve) {
    chrome.storage.local.get(keys, resolve);
  });
}

function isAcknowledged(stored) {
  return !!(stored && stored[ACK_KEY]) && !stored[DECLINE_KEY] && stored.isi_enabled !== false;
}

function validSender(sender) {
  return !!(sender && sender.tab && sender.tab.id);
}

function validQuery(query) {
  return typeof query === 'string' && query.length > 0 && query.length <= MAX_QUERY_CODEPOINTS;
}

function validExclusions(value) {
  if (value == null) return true;
  if (!Array.isArray(value)) return false;
  return value.every(function (item) {
    return typeof item === 'number' && Number.isFinite(item);
  });
}

async function matchPost(query, topicsExclude, timeoutMs, signal) {
  const body = new URLSearchParams();
  body.set('user_search_query', query);
  if (topicsExclude && topicsExclude.length) {
    body.set('topics_exclude', topicsExclude.join(','));
  }
  const controller = new AbortController();
  const timer = setTimeout(function () { controller.abort(); }, timeoutMs || 12000);
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener('abort', function () { controller.abort(); });
  }
  try {
    const resp = await fetch(API_BASE + MATCH_PATH, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-ISI-Build-ID': self.ISI_CONFIG.BUILD_ID,
        'X-ISI-Extension-Version': self.ISI_CONFIG.EXTENSION_VERSION,
      },
      body: body.toString(),
      redirect: 'error',
      signal: controller.signal,
    });
    let data = null;
    try {
      data = await resp.json();
    } catch (e) {
      data = null;
    }
    return { ok: resp.ok, status: resp.status, data: data };
  } catch (e) {
    if (e && e.name === 'AbortError') {
      const err = new Error('timeout');
      err.name = 'AbortError';
      throw err;
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

chrome.runtime.onMessage.addListener(function (message, sender, sendResponse) {
  (async function () {
    try {
      if (message.type === 'abortPrompts') {
        const controller = inflight.get(message.requestId);
        if (controller) controller.abort();
        sendResponse({ ok: true, aborted: true });
        return;
      }
      if (message.type !== 'getPrompts') {
        sendResponse({ ok: false, error: 'unknown message type' });
        return;
      }
      if (!validSender(sender)) {
        sendResponse({ ok: false, error: 'invalid_sender' });
        return;
      }
      if (!validQuery(message.query)) {
        sendResponse({ ok: false, error: 'invalid_query' });
        return;
      }
      if (!validExclusions(message.topicsExclude)) {
        sendResponse({ ok: false, error: 'invalid_exclusions' });
        return;
      }
      const stored = await storageGet([ACK_KEY, DECLINE_KEY, 'isi_enabled']);
      if (!isAcknowledged(stored)) {
        sendResponse({ ok: false, error: 'not_acknowledged' });
        return;
      }
      const requestId = message.requestId || ('anon:' + Date.now());
      const controller = new AbortController();
      inflight.set(requestId, controller);
      try {
        const result = await matchPost(
          message.query,
          message.topicsExclude || [],
          message.timeoutMs,
          controller.signal
        );
        sendResponse(result);
      } finally {
        inflight.delete(requestId);
      }
    } catch (e) {
      sendResponse({
        ok: false,
        error: String(e),
        aborted: !!(e && e.name === 'AbortError'),
      });
    }
  })();
  return true;
});
