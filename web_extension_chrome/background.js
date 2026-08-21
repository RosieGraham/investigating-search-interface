/*
Service worker for the SEASON 2026 workshop extension.

Matching is the only network path. Research handlers, installation IDs and
health checks are not present. Retry lives in the content-script state machine
so this worker performs at most one fetch per message.
*/

importScripts('config.js');

const API_BASE = self.ISI_CONFIG.API_BASE.replace(/\/$/, '');
const MATCH_PATH = '/data/prompt/get/';

async function matchPost(query, topicsExclude, timeoutMs) {
  const body = new URLSearchParams();
  body.set('user_search_query', query);
  if (topicsExclude && topicsExclude.length) {
    body.set('topics_exclude', topicsExclude.join(','));
  }
  const controller = new AbortController();
  const timer = setTimeout(function () { controller.abort(); }, timeoutMs || 12000);
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
      if (message.type === 'getPrompts') {
        const result = await matchPost(
          message.query,
          message.topicsExclude || [],
          message.timeoutMs
        );
        sendResponse(result);
      } else {
        sendResponse({ ok: false, error: 'unknown message type' });
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
