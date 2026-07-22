/*
Investigating Search Interface - content script.

Injects ethical reflection prompts directly into Google search result pages.
Three page layouts are handled (verified against live Google, June 2026):

  1. "classic"  (udm=14 or legacy): #rso holds .MjjYud result wrappers.
     -> inject the prompt card container immediately before #rso.
  2. "hybrid"   (current default): an AI response block ([data-subtree="aimc"])
     sits above #rso. Organic results still exist.
     -> same injection point: before #rso (below the AI block, above results).
  3. "ai"       (udm=50, conversational AI Mode): no #rso at all.
     -> graceful fallback: a small persistent badge that expands into the
        prompt panel. Full inline support for AI Mode is Phase 2.

Design rules (from the project's evidence base):
  - inject at the top of results, never hide or rerank anything
  - show two or three prompts where available, not one
  - blend with the page but carry clear attribution
All DOM selectors live in SELECTORS below so they can be updated in one place
when Google shifts its markup.
*/

(() => {
  'use strict';

  const SELECTORS = {
    resultsContainer: '#rso',          // organic results list (classic + hybrid)
    resultsFallback: '#search',        // wider results region (anchor of last resort)
    aiContainer: '[data-subtree="aimc"]', // AI Mode / AI response block
  };

  const STATE = {
    lastKey: null,        // `${query}|${mode}` of the last completed injection
    pendingKey: null,     // in-flight request key (dedup during API round-trip)
    observer: null,
    settings: { enabled: true, topicsExclude: [], logEvents: true },
  };

  // ---------- utilities ----------

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

  const logEvent = (event_type, extra = {}) => {
    if (!STATE.settings.logEvents) return;
    send({ type: 'logEvent', form: { event_type, serp_mode: currentMode(), ...extra } });
  };

  function currentMode() {
    if (document.querySelector(SELECTORS.resultsContainer)) {
      return document.querySelector(SELECTORS.aiContainer) ? 'hybrid' : 'classic';
    }
    if (document.querySelector(SELECTORS.aiContainer)) return 'ai';
    return 'unknown';
  }

  /** Render text that may contain <br> markers, without using innerHTML. */
  function appendContentText(parent, html) {
    const parts = String(html).split(/<br\s*\/?>(?:\s*)/i);
    parts.forEach((part, i) => {
      if (i > 0) parent.appendChild(document.createElement('br'));
      // Decode the handful of entities Django's escaping may have produced.
      const textarea = document.createElement('textarea');
      textarea.innerHTML = part.replace(/<[^>]*>/g, ''); // strip any other tags
      parent.appendChild(document.createTextNode(textarea.value));
    });
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  // ---------- prompt card UI ----------

  function buildPromptBlock(prompt, query) {
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
      const learn = el('a', 'isi-learn-more', 'Learn more in SEEED');
      learn.href = prompt.seeed_url;
      learn.target = '_blank';
      learn.rel = 'noopener noreferrer';
      learn.addEventListener('click', () =>
        logEvent('learn_more_clicked', { prompt_id: prompt.id, topic_id: prompt.topic_id })
      );
      actions.appendChild(learn);
    }

    const notRelevant = el('button', 'isi-not-relevant', 'Not relevant to my search');
    notRelevant.type = 'button';
    const disclosure = el('span', 'isi-report-disclosure', 'Reports your query to improve matching');
    notRelevant.appendChild(disclosure);
    notRelevant.addEventListener('click', async () => {
      notRelevant.disabled = true;
      await send({
        type: 'postReport',
        form: {
          active_prompt_id: prompt.id,
          user_search_query: query,
          classifier_confidence: prompt.confidence ?? '',
        },
      });
      logEvent('not_relevant_reported', { prompt_id: prompt.id, topic_id: prompt.topic_id });
      block.replaceChildren(el('div', 'isi-thanks', 'Thanks - your report helps tune the matching.'));
      setTimeout(() => block.remove(), 2500);
    });
    actions.appendChild(notRelevant);
    block.appendChild(actions);

    if (prompt.response_required) {
      const respWrap = el('div', 'isi-response');
      const textarea = el('textarea', 'isi-response-text');
      textarea.placeholder = 'Type your response here...';
      const submit = el('button', 'isi-response-submit', 'Submit response');
      submit.type = 'button';
      submit.addEventListener('click', async () => {
        if (!textarea.value.trim()) return;
        submit.disabled = true;
        await send({
          type: 'postResponse',
          form: { active_prompt_id: prompt.id, user_response_content: textarea.value.trim() },
        });
        logEvent('response_submitted', { prompt_id: prompt.id, topic_id: prompt.topic_id });
        respWrap.replaceChildren(el('div', 'isi-thanks', 'Response recorded - thank you.'));
      });
      respWrap.appendChild(textarea);
      respWrap.appendChild(submit);
      block.appendChild(respWrap);
    }

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
      logEvent('prompt_dismissed');
      card.remove();
    });
    header.appendChild(dismiss);
    card.appendChild(header);

    prompts.forEach((p) => card.appendChild(buildPromptBlock(p, query)));

    const footer = el('div', 'isi-footer');
    const attribution = el('a', 'isi-attribution', ISI_CONFIG.ATTRIBUTION);
    attribution.href = ISI_CONFIG.PROJECT_URL;
    attribution.target = '_blank';
    attribution.rel = 'noopener noreferrer';
    footer.appendChild(attribution);
    footer.appendChild(
      el('span', 'isi-disclaimer', 'Research project · prompts appear alongside your results and never change them')
    );
    card.appendChild(footer);
    return card;
  }

  // ---------- AI Mode badge fallback ----------

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
      if (!open) logEvent('badge_opened');
    });

    badge.appendChild(panel);
    badge.appendChild(pill);
    return badge;
  }

  // ---------- injection ----------

  function injectForResults(prompts, query) {
    const rso = document.querySelector(SELECTORS.resultsContainer);
    const anchor = rso || document.querySelector(SELECTORS.resultsFallback);
    if (!anchor) return false;
    document.querySelector('#isi-prompts')?.remove();
    document.querySelector('#isi-badge')?.remove();
    const card = buildCard(prompts, query);
    anchor.parentElement.insertBefore(card, anchor);
    logEvent('prompt_shown', {
      prompt_id: prompts[0].id,
      topic_id: prompts[0].topic_id,
      classifier_confidence: prompts[0].confidence ?? '',
    });
    return true;
  }

  function injectBadge(prompts, query) {
    document.querySelector('#isi-prompts')?.remove();
    document.querySelector('#isi-badge')?.remove();
    document.body.appendChild(buildBadge(prompts, query));
    logEvent('badge_shown', {
      prompt_id: prompts[0].id,
      topic_id: prompts[0].topic_id,
      classifier_confidence: prompts[0].confidence ?? '',
    });
    return true;
  }

  // ---------- orchestration ----------

  async function run() {
    if (!STATE.settings.enabled) return;
    const query = getQuery();
    if (!query) return;

    const mode = currentMode();
    if (mode === 'unknown') return; // layout not ready yet; observer will re-call

    const key = `${query}|${mode}`;
    if (STATE.lastKey === key) return; // already handled this query+layout
    if (STATE.pendingKey === key) return; // same request already in flight

    // Capture the key we're about to request; commit lastKey only after the
    // response is still for the page the user is viewing (stale-response guard).
    const requestKey = key;
    STATE.pendingKey = requestKey;

    const resp = await send({
      type: 'getPrompts',
      query,
      topicsExclude: STATE.settings.topicsExclude,
    });

    if (STATE.pendingKey === requestKey) STATE.pendingKey = null;

    // User navigated during the API call: discard the stale response.
    const currentQuery = getQuery();
    const currentKey = `${currentQuery}|${currentMode()}`;
    if (currentKey !== requestKey) return;

    STATE.lastKey = requestKey;

    if (!resp.ok || !resp.data || !Array.isArray(resp.data.prompts) || resp.data.prompts.length === 0) {
      return; // no confident match (or API asleep/cold-starting): show nothing
    }
    const prompts = resp.data.prompts.slice(0, ISI_CONFIG.MAX_PROMPTS);

    if (mode === 'ai') injectBadge(prompts, query);
    else injectForResults(prompts, query);
  }

  /** Watch for the results/AI container appearing (dynamic rendering). */
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

  /** Detect SPA-style navigation (AI Mode updates without full page loads). */
  function watchForNavigation() {
    let lastHref = location.href;
    const onChange = () => {
      if (location.href === lastHref) return;
      lastHref = location.href;
      STATE.lastKey = null;
      STATE.pendingKey = null;
      setTimeout(() => {
        run();
        watchForLayout();
      }, 400); // give the new view a moment to start rendering
    };
    if (typeof navigation !== 'undefined' && navigation.addEventListener) {
      navigation.addEventListener('navigatesuccess', onChange);
    }
    window.addEventListener('popstate', onChange);
    setInterval(onChange, 1500); // belt and braces: some updates skip both APIs
  }

  function startAfterConsent() {
    run();
    watchForLayout();
    watchForNavigation();
  }

  /** One-time consent interstitial before any query leaves the browser. */
  function showConsentBanner() {
    if (document.getElementById('isi-consent-banner')) return;

    const banner = el('div', 'isi-consent-banner');
    banner.id = 'isi-consent-banner';

    banner.appendChild(el('p', 'isi-consent-title', 'Investigating Search Interface'));
    banner.appendChild(el(
      'p',
      null,
      'This extension sends your search queries to a University of Birmingham '
      + 'research server to find relevant reflection prompts. Queries are not stored '
      + 'unless you click "Not relevant" on a prompt. A pseudonymous installation '
      + 'identifier links your activity for research purposes.'
    ));

    const noticeUrl = (typeof ISI_CONFIG !== 'undefined' && ISI_CONFIG.PRIVACY_NOTICE_URL) || '';
    if (noticeUrl) {
      const noticePara = el('p', null);
      const noticeLink = el('a', null, 'Read the full privacy notice');
      noticeLink.href = noticeUrl;
      noticeLink.target = '_blank';
      noticeLink.rel = 'noopener noreferrer';
      noticePara.appendChild(noticeLink);
      banner.appendChild(noticePara);
    }

    const actions = el('div', 'isi-consent-actions');
    const accept = el('button', 'isi-consent-accept', 'I understand, enable the extension');
    accept.type = 'button';
    accept.id = 'isi-consent-accept';
    const decline = el('button', 'isi-consent-decline', 'No thanks');
    decline.type = 'button';
    decline.id = 'isi-consent-decline';
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
      chrome.storage.local.set({ isi_consent_given: true, isi_enabled: true });
      banner.remove();
      STATE.settings.enabled = true;
      startAfterConsent();
    });

    decline.addEventListener('click', () => {
      chrome.storage.local.set({ isi_enabled: false });
      banner.remove();
      STATE.settings.enabled = false;
    });
  }

  // ---------- boot ----------

  chrome.storage.local.get(
    ['isi_enabled', 'isi_topics_exclude', 'isi_log_events', 'isi_consent_given'],
    (stored) => {
      STATE.settings.enabled = stored.isi_enabled !== false;
      STATE.settings.topicsExclude = stored.isi_topics_exclude || [];
      STATE.settings.logEvents = stored.isi_log_events !== false;

      if (!stored.isi_consent_given) {
        // Previously declined: stay quiet until they re-enable via the popup.
        if (stored.isi_enabled === false) return;
        showConsentBanner();
        return; // do not call run() until consent is given
      }

      startAfterConsent();
    }
  );

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    if (changes.isi_enabled) {
      STATE.settings.enabled = changes.isi_enabled.newValue !== false;
      if (!STATE.settings.enabled) {
        document.querySelector('#isi-prompts')?.remove();
        document.querySelector('#isi-badge')?.remove();
        document.querySelector('#isi-consent-banner')?.remove();
      } else {
        chrome.storage.local.get(['isi_consent_given'], (s) => {
          if (!s.isi_consent_given) {
            showConsentBanner();
            return;
          }
          STATE.lastKey = null;
          STATE.pendingKey = null;
          run();
        });
      }
    }
    if (changes.isi_topics_exclude) {
      STATE.settings.topicsExclude = changes.isi_topics_exclude.newValue || [];
      STATE.lastKey = null;
      STATE.pendingKey = null;
      run();
    }
    if (changes.isi_log_events) {
      STATE.settings.logEvents = changes.isi_log_events.newValue !== false;
    }
  });
})();
