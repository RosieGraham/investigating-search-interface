/*
Investigating Search Interface - SEASON 2026 workshop content script.

Injects one reflection prompt into Google search result pages.
Three page layouts are handled (verified against live Google, June 2026):

  1. "classic"  (udm=14 or legacy): #rso holds .MjjYud result wrappers.
  2. "hybrid"   (current default): an AI response block ([data-subtree="aimc"])
     sits above #rso.
  3. "ai"       (udm=50, conversational AI Mode): no #rso; badge fallback.

Workshop rules: no research writes, no installation ID, no Not Relevant or
response box. Matching starts only after notice season-2026-v2 is acknowledged.
*/

(() => {
  'use strict';

  const FIRST_ATTEMPT_TIMEOUT_MS = 12000;
  const RETRY_DELAY_MS = 350;
  const RETRY_JITTER_MS = 250;
  const MAX_MATCH_FETCHES_PER_NAV = 2;
  const SIGNPOST = {
    prompt: 'Reflection prompt for this search',
    quiet: 'No prompt for this search',
    error: "Couldn't reach the server",
  };

  const SELECTORS = {
    resultsContainer: '#rso',
    resultsFallback: '#search',
    aiContainer: '[data-subtree="aimc"]',
  };

  const STATE = {
    injectedKey: null,
    dismissedKey: null,
    silenceKey: null,
    pendingKey: null,
    lastInject: null,
    activeRequestId: null,
    observer: null,
    matchFetchesThisNav: 0,
    settings: { enabled: true, topicsExclude: [] },
  };

  const ack = self.ISIAcknowledgement.createAcknowledgement({
    noticeVersion: ISI_CONFIG.NOTICE_VERSION,
    storage: {
      get: (key) => new Promise((resolve) => {
        chrome.storage.local.get([key], (items) => resolve(items[key]));
      }),
      set: (obj) => new Promise((resolve) => {
        chrome.storage.local.set(obj, resolve);
      }),
    },
  });

  const machine = self.ISIRequestLifecycle.createRequestMachine({
    timeoutMs: FIRST_ATTEMPT_TIMEOUT_MS,
    retryDelayMs: RETRY_DELAY_MS,
    retryJitterMs: RETRY_JITTER_MS,
    delay: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    onAbort: () => {
      if (STATE.activeRequestId) {
        send({ type: 'abortPrompts', requestId: STATE.activeRequestId });
      }
    },
    fetchImpl: async (req) => {
      const requestId = `${req.navId}:${Date.now()}:${Math.random()}`;
      STATE.activeRequestId = requestId;
      const result = await send({
        type: 'getPrompts',
        requestId,
        query: req.query,
        topicsExclude: STATE.settings.topicsExclude,
        timeoutMs: FIRST_ATTEMPT_TIMEOUT_MS,
      });
      if (STATE.activeRequestId === requestId) STATE.activeRequestId = null;
      if (result && result.aborted) {
        const err = new Error('timeout');
        err.name = 'AbortError';
        throw err;
      }
      return {
        ok: !!(result && result.ok),
        status: result && result.status,
        json: async () => result && result.data,
      };
    },
  });

  const getQuery = () => new URLSearchParams(location.search).get('q')?.trim() || '';

  const send = (message) =>
    new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(message, (resp) => {
          if (chrome.runtime.lastError) resolve({ ok: false, error: chrome.runtime.lastError.message });
          else resolve(resp || { ok: false });
        });
      } catch (e) {
        resolve({ ok: false, error: String(e) });
      }
    });

  function currentMode() {
    if (document.querySelector(SELECTORS.resultsContainer)) {
      return document.querySelector(SELECTORS.aiContainer) ? 'hybrid' : 'classic';
    }
    if (document.querySelector(SELECTORS.aiContainer)) return 'ai';
    return 'unknown';
  }

  function appendContentText(parent, html) {
    const parts = String(html).split(/<br\s*\/?>(?:\s*)/i);
    parts.forEach((part, i) => {
      if (i > 0) parent.appendChild(document.createElement('br'));
      const textarea = document.createElement('textarea');
      textarea.innerHTML = part.replace(/<[^>]*>/g, '');
      parent.appendChild(document.createTextNode(textarea.value));
    });
  }

  function allowedLearnMoreUrl(url) {
    if (!url || typeof url !== 'string') return null;
    try {
      const parsed = new URL(url);
      if (parsed.protocol !== 'https:') return null;
      const allowed = (ISI_CONFIG.ALLOWED_LEARN_MORE_ORIGINS || []).map((origin) => origin.replace(/\/$/, ''));
      if (!allowed.includes(parsed.origin)) return null;
      return parsed.href;
    } catch (e) {
      return null;
    }
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  function buildPromptBlock(prompt) {
    const block = el('div', 'isi-prompt');

    const topicRow = el('div', 'isi-topic-row');
    topicRow.appendChild(el('span', 'isi-topic-chip', prompt.topic));
    if (typeof prompt.confidence === 'number') {
      topicRow.appendChild(el('span', 'isi-confidence', `match ${Math.round(prompt.confidence * 100)}%`));
    }
    block.appendChild(topicRow);

    const content = el('div', 'isi-content');
    appendContentText(content, prompt.prompt_content);
    block.appendChild(content);

    const actions = el('div', 'isi-actions');
    if (prompt.seeed_url) {
      const safe = allowedLearnMoreUrl(prompt.seeed_url);
      if (safe) {
        const learn = el('a', 'isi-learn-more', 'Learn more');
        learn.href = safe;
        learn.target = '_blank';
        learn.rel = 'noopener noreferrer';
        actions.appendChild(learn);
      }
    }
    if (actions.childNodes.length) block.appendChild(actions);
    return block;
  }

  function buildCard(prompts, query) {
    const card = el('div', null);
    card.id = 'isi-prompts';

    const header = el('div', 'isi-header');
    const brand = el('div', 'isi-brand');
    const mono = el('span', 'isi-mono');
    mono.appendChild(document.createTextNode('iS'));
    mono.appendChild(el('span', 'isi-mono-dot', '.'));
    brand.appendChild(mono);
    brand.appendChild(el('span', 'isi-wordmark', 'Investigating'));
    const qLabel = el('span', 'isi-query-label',
      ' ' + (query.length > 32 ? query.slice(0, 30) + '…' : query));
    brand.appendChild(qLabel);
    brand.appendChild(el('span', 'isi-caret'));
    header.appendChild(brand);
    const dismiss = el('button', 'isi-dismiss', '×');
    dismiss.type = 'button';
    dismiss.title = 'Hide for this search';
    dismiss.addEventListener('click', () => {
      STATE.dismissedKey = `${getQuery()}|${currentMode()}`;
      STATE.injectedKey = null;
      document.getElementById('isi-badge')?.remove();
      card.remove();
    });
    header.appendChild(dismiss);
    card.appendChild(header);

    prompts.forEach((p) => card.appendChild(buildPromptBlock(p)));

    const footer = el('div', 'isi-footer');
    const attribution = el('a', 'isi-attribution', ISI_CONFIG.ATTRIBUTION);
    attribution.href = ISI_CONFIG.PROJECT_URL;
    attribution.target = '_blank';
    attribution.rel = 'noopener noreferrer';
    footer.appendChild(attribution);
    footer.appendChild(
      el('span', 'isi-disclaimer', 'Alpha build 0.2.1 · prompts appear alongside your results and never change them')
    );
    card.appendChild(footer);
    return card;
  }

  function buildSignpost(kind, onClick) {
    const badge = el('div', 'isi-signpost');
    badge.id = 'isi-badge';
    const interactive = kind === 'prompt' && typeof onClick === 'function';
    const pill = el(interactive ? 'button' : 'div', interactive ? 'isi-pill' : 'isi-pill isi-pill-static');
    if (interactive) pill.type = 'button';
    const dotClass = kind === 'error' ? 'isi-pill-dot isi-pill-dot-muted' : 'isi-pill-dot isi-blink';
    pill.appendChild(el('span', dotClass));
    pill.appendChild(el('span', 'isi-pill-text', SIGNPOST[kind]));
    if (interactive) pill.addEventListener('click', onClick);
    badge.appendChild(pill);
    return badge;
  }

  function mountSignpost(kind, onClick) {
    document.querySelector('#isi-badge')?.remove();
    document.body.appendChild(buildSignpost(kind, onClick));
  }

  function buildBadge(prompts, query) {
    const badge = el('div', null);
    badge.id = 'isi-badge';

    const pill = el('button', 'isi-pill');
    pill.type = 'button';
    pill.appendChild(el('span', 'isi-pill-dot'));
    pill.appendChild(
      el('span', 'isi-pill-text', `Reflection prompt available (${prompts.length})`)
    );

    const panel = buildCard(prompts, query);
    panel.classList.add('isi-panel');
    panel.style.display = 'none';

    pill.addEventListener('click', () => {
      const open = panel.style.display !== 'none';
      panel.style.display = open ? 'none' : 'block';
    });

    badge.appendChild(panel);
    badge.appendChild(pill);
    return badge;
  }

  function injectForResults(prompts, query) {
    const rso = document.querySelector(SELECTORS.resultsContainer);
    const anchor = rso || document.querySelector(SELECTORS.resultsFallback);
    if (!anchor || !anchor.parentElement) return false;
    document.querySelector('#isi-prompts')?.remove();
    document.querySelector('#isi-badge')?.remove();
    const card = buildCard(prompts, query);
    anchor.parentElement.insertBefore(card, anchor);
    mountSignpost('prompt', () => {
      const live = document.getElementById('isi-prompts');
      if (live && typeof live.scrollIntoView === 'function') {
        live.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    });
    return true;
  }

  function injectBadge(prompts, query) {
    document.querySelector('#isi-prompts')?.remove();
    document.querySelector('#isi-badge')?.remove();
    document.body.appendChild(buildBadge(prompts, query));
    return true;
  }

  function fallbackBadgeFromStore() {
    if (!STATE.lastInject) return false;
    try {
      return injectBadge(STATE.lastInject.prompts, STATE.lastInject.query);
    } catch (e) {
      return false;
    }
  }

  async function run() {
    if (!STATE.settings.enabled) return;
    if (!ack.canSend()) return;
    const query = getQuery();
    if (!query) return;

    const mode = currentMode();
    if (mode === 'unknown') return;

    const key = `${query}|${mode}`;
    if (STATE.dismissedKey === key) return;
    if (STATE.silenceKey === key) return;
    const cardPresent = document.querySelector('#isi-prompts');
    if (STATE.injectedKey === key && cardPresent) return;
    if (STATE.injectedKey === key && STATE.lastInject) {
      try {
        const stored = STATE.lastInject;
        const restored = stored.mode === 'ai'
          ? injectBadge(stored.prompts, stored.query)
          : injectForResults(stored.prompts, stored.query);
        if (restored) return;
      } catch (e) {
        /* reinsertion failed; maybe one more fetch, then the badge panel */
      }
      if (STATE.matchFetchesThisNav >= MAX_MATCH_FETCHES_PER_NAV) {
        if (fallbackBadgeFromStore()) STATE.injectedKey = key;
        return;
      }
    }
    if (STATE.pendingKey === key) return;
    if (STATE.matchFetchesThisNav >= MAX_MATCH_FETCHES_PER_NAV) {
      if (STATE.lastInject && fallbackBadgeFromStore()) STATE.injectedKey = key;
      return;
    }

    const requestKey = key;
    STATE.pendingKey = requestKey;
    STATE.matchFetchesThisNav += 1;

    const resp = await machine.requestMatch({ query, navId: requestKey });

    if (STATE.pendingKey === requestKey) STATE.pendingKey = null;

    const currentQuery = getQuery();
    const currentKey = `${currentQuery}|${currentMode()}`;
    if (currentKey !== requestKey) return;
    if (!resp || !resp.ok) {
      document.querySelector('#isi-prompts')?.remove();
      mountSignpost('error');
      return;
    }

    const data = resp.data || {};
    if (Array.isArray(data.topics)) {
      chrome.storage.local.set({
        isi_topic_groups: data.topics.map((group) => ({
          id: group.id,
          name: group.name,
          excluded: group.excluded,
        })),
      });
    }
    if (!Array.isArray(data.prompts) || data.prompts.length === 0) {
      STATE.silenceKey = requestKey;
      document.querySelector('#isi-prompts')?.remove();
      mountSignpost('quiet');
      return;
    }
    const prompts = data.prompts.slice(0, ISI_CONFIG.MAX_PROMPTS);

    let inserted = false;
    let missingAnchor = false;
    try {
      if (mode === 'ai') {
        inserted = injectBadge(prompts, query);
      } else {
        inserted = injectForResults(prompts, query);
        missingAnchor = !inserted;
      }
    } catch (e) {
      inserted = false;
    }
    if (!inserted && missingAnchor) {
      try {
        inserted = injectBadge(prompts, query);
      } catch (e) {
        inserted = false;
      }
    }
    if (inserted) {
      STATE.injectedKey = requestKey;
      STATE.lastInject = { prompts, query, mode };
    }
  }

  function watchForLayout() {
    if (STATE.observer) STATE.observer.disconnect();
    const started = Date.now();
    STATE.observer = new MutationObserver(() => {
      if (Date.now() - started > ISI_CONFIG.OBSERVER_TIMEOUT) {
        STATE.observer.disconnect();
        return;
      }
      if (currentMode() !== 'unknown') {
        run();
      }
    });
    STATE.observer.observe(document.documentElement, { childList: true, subtree: true });
  }

  function watchForNavigation() {
    let lastHref = location.href;
    const onChange = () => {
      if (location.href === lastHref) return;
      lastHref = location.href;
      STATE.injectedKey = null;
      STATE.pendingKey = null;
      STATE.silenceKey = null;
      STATE.dismissedKey = null;
      STATE.lastInject = null;
      STATE.matchFetchesThisNav = 0;
      clearCards();
      machine.abort();
      setTimeout(() => {
        run();
        watchForLayout();
      }, 400);
    };
    if (typeof navigation !== 'undefined' && navigation.addEventListener) {
      navigation.addEventListener('navigatesuccess', onChange);
    }
    window.addEventListener('popstate', onChange);
    window.addEventListener('pageshow', (event) => {
      if (!event.persisted) return;
      STATE.injectedKey = null;
      STATE.pendingKey = null;
      STATE.silenceKey = null;
      STATE.dismissedKey = null;
      STATE.matchFetchesThisNav = 0;
      run();
    });
    setInterval(onChange, 1500);
  }

  function startAfterAck() {
    machine.acknowledge(ISI_CONFIG.NOTICE_VERSION);
    run();
    watchForLayout();
    watchForNavigation();
  }

  function showNoticeBanner() {
    if (document.getElementById('isi-notice-banner')) return;

    const banner = el('div', 'isi-consent-banner');
    banner.id = 'isi-notice-banner';

    banner.appendChild(el('p', 'isi-consent-title', 'Investigating Search Interface · SEASON 2026'));
    banner.appendChild(el(
      'p',
      null,
      "This workshop release sends the text of your search to the project's server so it can select one reflection prompt. If you have excluded any topics, that choice is sent too. Your search is used only to select that prompt and is not kept. This release does not record your activity, collect responses, or give your installation an identifier."
    ));

    const noticeUrl = ISI_CONFIG.PRIVACY_NOTICE_URL || '';
    if (noticeUrl) {
      const noticePara = el('p', null);
      const noticeLink = el('a', null, 'Read the privacy notice');
      noticeLink.href = noticeUrl;
      noticeLink.target = '_blank';
      noticeLink.rel = 'noopener noreferrer';
      noticePara.appendChild(noticeLink);
      banner.appendChild(noticePara);
    }

    const actions = el('div', 'isi-consent-actions');
    const accept = el('button', 'isi-consent-accept', 'I understand, show prompts');
    accept.type = 'button';
    const decline = el('button', 'isi-consent-decline', 'No thanks');
    decline.type = 'button';
    actions.appendChild(accept);
    actions.appendChild(decline);
    banner.appendChild(actions);

    const rso = document.querySelector(SELECTORS.resultsContainer);
    const anchor = rso || document.querySelector(SELECTORS.resultsFallback);
    if (anchor && anchor.parentElement) {
      anchor.parentElement.insertBefore(banner, anchor);
    } else {
      document.body.appendChild(banner);
    }

    accept.addEventListener('click', () => {
      ack.acknowledge();
      banner.remove();
      STATE.settings.enabled = true;
      startAfterAck();
    });

    decline.addEventListener('click', () => {
      ack.decline();
      machine.withdrawAcknowledgement();
      banner.remove();
      STATE.settings.enabled = false;
    });
  }

  function clearCards() {
    document.querySelector('#isi-prompts')?.remove();
    document.querySelector('#isi-badge')?.remove();
    document.querySelector('#isi-notice-banner')?.remove();
  }

  chrome.storage.local.get(
    ['isi_enabled', 'isi_topics_exclude', ack.ackKey, ack.declineKey],
    (stored) => {
      STATE.settings.enabled = stored.isi_enabled !== false;
      STATE.settings.topicsExclude = stored.isi_topics_exclude || [];
      const bootResult = ack.boot(stored);
      Promise.resolve(bootResult).then(() => {
        if (ack.isDeclined() || stored.isi_enabled === false) return;
        if (!ack.isAcknowledged()) {
          showNoticeBanner();
          return;
        }
        startAfterAck();
      });
    }
  );

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    if (changes.isi_enabled) {
      STATE.settings.enabled = changes.isi_enabled.newValue !== false;
      if (!STATE.settings.enabled) {
        machine.abort();
        clearCards();
      } else if (ack.isDeclined()) {
        ack.clearDecline();
        showNoticeBanner();
      } else if (!ack.isAcknowledged()) {
        showNoticeBanner();
      } else {
        STATE.injectedKey = null;
        STATE.pendingKey = null;
        STATE.silenceKey = null;
        STATE.matchFetchesThisNav = 0;
        run();
      }
    }
    if (changes.isi_topics_exclude) {
      STATE.settings.topicsExclude = changes.isi_topics_exclude.newValue || [];
      STATE.injectedKey = null;
      STATE.pendingKey = null;
      STATE.silenceKey = null;
      STATE.dismissedKey = null;
      STATE.matchFetchesThisNav = 0;
      clearCards();
      machine.abort();
      run();
    }
    if (changes[ack.declineKey] && changes[ack.declineKey].newValue) {
      machine.withdrawAcknowledgement();
      clearCards();
    }
  });
})();
