import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createContext, runInContext } from 'node:vm';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const here = path.dirname(fileURLToPath(import.meta.url));
const ext = path.join(here, '..');

function read(name) {
  return fs.readFileSync(path.join(ext, name), 'utf8');
}

const MATCH = {
  ok: true,
  status: 200,
  data: {
    prompt: { id: 1, topic: 'T', prompt_content: 'hello', confidence: 0.8 },
    prompts: [{ id: 1, topic: 'T', prompt_content: 'hello', confidence: 0.8 }],
    topics: [{ id: 11, name: 'Group' }],
    classifier: 'matched',
  },
};

const SILENCE = {
  ok: true,
  status: 200,
  data: { prompt: false, prompts: [], topics: [], classifier: 'no_match' },
};

function makeDom(href, opts = {}) {
  const nodes = [];
  function matches(node, sel) {
    if (!sel) return false;
    if (sel.startsWith('#')) return node.id === sel.slice(1);
    if (sel === '[data-subtree="aimc"]') return node.attrs['data-subtree'] === 'aimc';
    if (sel.startsWith('.')) return String(node.className).split(/\s+/).includes(sel.slice(1));
    return node.tagName === sel.toUpperCase();
  }
  function search(root, sel) {
    if (matches(root, sel)) return root;
    for (const child of root.children) {
      const hit = search(child, sel);
      if (hit) return hit;
    }
    return null;
  }
  function el(tag) {
    const node = {
      tagName: String(tag).toUpperCase(),
      children: [],
      attrs: {},
      listeners: {},
      style: {},
      className: '',
      id: '',
      textContent: '',
      innerHTML: '',
      href: '',
      target: '',
      rel: '',
      type: '',
      title: '',
      parentElement: null,
      value: '',
      appendChild(child) {
        this.children.push(child);
        child.parentElement = this;
        nodes.push(child);
        return child;
      },
      insertBefore(child, ref) {
        if (opts.throwInsertOnce && !opts._threw) {
          opts._threw = true;
          throw new Error('insert failed');
        }
        if (opts.failInsertAfter != null) {
          opts._insertCount = (opts._insertCount || 0) + 1;
          if (opts._insertCount > opts.failInsertAfter) {
            throw new Error('insert failed');
          }
        }
        const i = this.children.indexOf(ref);
        if (i === -1) this.children.push(child);
        else this.children.splice(i, 0, child);
        child.parentElement = this;
        nodes.push(child);
        return child;
      },
      addEventListener(type, fn) {
        (this.listeners[type] ||= []).push(fn);
      },
      remove() {
        if (!this.parentElement) return;
        this.parentElement.children = this.parentElement.children.filter((c) => c !== this);
        const i = nodes.indexOf(this);
        if (i >= 0) nodes.splice(i, 1);
      },
      querySelector(sel) { return search(this, sel); },
      setAttribute(k, v) { this.attrs[k] = v; },
      get childNodes() { return this.children; },
      scrollIntoView(opts) {
        this.scrollIntoViewCalls = this.scrollIntoViewCalls || [];
        this.scrollIntoViewCalls.push(opts || {});
      },
    };
    node.classList = {
      add(...names) {
        const cur = new Set(String(node.className).split(/\s+/).filter(Boolean));
        names.forEach((n) => cur.add(n));
        node.className = [...cur].join(' ');
      },
      contains(name) {
        return String(node.className).split(/\s+/).includes(name);
      },
      remove(...names) {
        const cur = new Set(String(node.className).split(/\s+/).filter(Boolean));
        names.forEach((n) => cur.delete(n));
        node.className = [...cur].join(' ');
      },
    };
    nodes.push(node);
    return node;
  }
  const html = el('html');
  const body = el('body');
  html.appendChild(body);
  const center = el('div');
  center.id = 'center_col';
  body.appendChild(center);
  const rso = el('div');
  if (!opts.noRso) {
    rso.id = 'rso';
    center.appendChild(rso);
  }
  if (opts.aimc) {
    const aimc = el('div');
    aimc.attrs['data-subtree'] = 'aimc';
    body.appendChild(aimc);
  }
  const parsed = new URL(href);
  return {
    nodes,
    html,
    body,
    center,
    rso,
    el,
    location: { href, search: parsed.search, pathname: parsed.pathname },
    document: {
      documentElement: html,
      body,
      createElement: el,
      createTextNode(text) {
        const node = el('#text');
        node.tagName = '#TEXT';
        node.textContent = String(text);
        return node;
      },
      getElementById(id) { return search(html, '#' + id); },
      querySelector(sel) { return search(html, sel); },
    },
  };
}

function installExtension(opts) {
  const href = opts.href || 'https://www.google.com/search?q=privacy+focused+search';
  const dom = makeDom(href, opts);
  const messages = [];
  const pending = [];
  const storage = { ...(opts.storage || {}) };
  const observers = [];
  const timeouts = [];
  const intervals = [];
  const windowListeners = {};
  let now = opts.now || 1_000_000;
  const chrome = {
    runtime: {
      lastError: null,
      sendMessage(message, cb) {
        messages.push(JSON.parse(JSON.stringify(message)));
        if (opts.holdFetch) {
          pending.push(cb);
          return;
        }
        queueMicrotask(() => {
          const payload = typeof opts.fetchResult === 'function'
            ? opts.fetchResult(message)
            : (opts.fetchResult || MATCH);
          cb(payload);
        });
      },
    },
    storage: {
      local: {
        get(keys, cb) {
          const out = {};
          for (const k of Array.isArray(keys) ? keys : [keys]) out[k] = storage[k];
          cb(out);
        },
        set(obj, cb) {
          Object.assign(storage, obj);
          const changes = {};
          for (const [k, v] of Object.entries(obj)) changes[k] = { newValue: v };
          queueMicrotask(() => {
            for (const fn of changedListeners) fn(changes, 'local');
            if (cb) cb();
          });
        },
      },
      onChanged: {
        addListener(fn) { changedListeners.push(fn); },
      },
    },
  };
  const changedListeners = [];
  const sandbox = {
    console,
    chrome,
    document: dom.document,
    location: dom.location,
    window: null,
    self: null,
    URL,
    MutationObserver: class {
      constructor(cb) { this.cb = cb; observers.push(this); this.observing = false; }
      observe() { this.observing = true; }
      disconnect() { this.observing = false; }
      fire() { if (this.observing) this.cb([]); }
    },
    URLSearchParams,
    Date: class extends Date {
      static now() { return now; }
    },
    addEventListener(type, fn) {
      (windowListeners[type] ||= []).push(fn);
    },
    removeEventListener() {},
    setTimeout(fn, ms) { timeouts.push({ fn, ms }); return timeouts.length; },
    setInterval(fn, ms) { intervals.push({ fn, ms }); return intervals.length; },
    clearTimeout() {},
    clearInterval() {},
    queueMicrotask,
    Promise,
    JSON,
    Object,
    Array,
    String,
    Math,
    Number,
    Error,
    Map,
    Set,
    parseInt,
    undefined,
  };
  sandbox.window = sandbox;
  sandbox.self = sandbox;
  sandbox.global = sandbox;
  sandbox.Date.now = () => now;
  const script = [
    read('config.js'),
    read('lib/acknowledgement.js'),
    read('lib/request_lifecycle.js'),
    read('content.js'),
  ].join('\n;\n');
  runInContext(script, createContext(sandbox), { filename: 'content-bundle.js' });
  return {
    sandbox,
    dom,
    chrome,
    messages,
    pending,
    storage,
    observers,
    timeouts,
    intervals,
    windowListeners,
    flushTimeouts() {
      const q = timeouts.splice(0);
      for (const item of q) item.fn();
    },
    fireObservers() {
      for (const ob of observers) ob.fire();
    },
    fireNav() {
      for (const item of intervals) item.fn();
    },
    resolvePending(result) {
      const cbs = pending.splice(0);
      for (const cb of cbs) cb(result);
    },
    setHref(next) {
      const parsed = new URL(next);
      sandbox.location.href = next;
      sandbox.location.search = parsed.search;
      dom.location.href = next;
      dom.location.search = parsed.search;
    },
    async tick() {
      await new Promise((r) => setImmediate(r));
      await new Promise((r) => setImmediate(r));
      await new Promise((r) => setImmediate(r));
    },
  };
}

function wait(ms = 0) {
  return new Promise((r) => setTimeout(r, ms));
}

function walk(node, visit) {
  visit(node);
  for (const child of node.children || []) walk(child, visit);
}

function findWhere(root, pred) {
  let hit = null;
  walk(root, (n) => {
    if (!hit && pred(n)) hit = n;
  });
  return hit;
}

function promptFetches(env) {
  return env.messages.filter((m) => m.type === 'getPrompts').length;
}

function pillText(env) {
  const node = findWhere(
    env.dom.document.documentElement,
    (n) => String(n.className).split(/\s+/).includes('isi-pill-text'),
  );
  return node ? node.textContent : null;
}

function pillDot(env) {
  return findWhere(
    env.dom.document.documentElement,
    (n) => String(n.className).split(/\s+/).includes('isi-pill-dot'),
  );
}

function signpostPill(env) {
  const badge = env.dom.document.getElementById('isi-badge');
  if (!badge) return null;
  return findWhere(badge, (n) => String(n.className).split(/\s+/).includes('isi-pill'));
}

function promptCount(env) {
  let n = 0;
  walk(env.dom.document.documentElement, (node) => {
    if (node.id === 'isi-prompts') n += 1;
  });
  return n;
}

function findDismiss(node) {
  if (node.className === 'isi-dismiss') return node;
  for (const child of node.children || []) {
    const hit = findDismiss(child);
    if (hit) return hit;
  }
  return null;
}

const ACKED = { 'isi_notice_season-2026-v2': true, isi_enabled: true };

test('success then silence removes the previous card', async () => {
  let n = 0;
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=alpha',
    fetchResult: () => {
      n += 1;
      return n === 1 ? MATCH : SILENCE;
    },
  });
  await env.tick();
  await wait(20);
  assert.ok(env.dom.document.getElementById('isi-prompts'));
  env.setHref('https://www.google.com/search?q=bravo');
  env.fireNav();
  assert.equal(env.dom.document.getElementById('isi-prompts'), null);
  assert.equal(env.dom.document.getElementById('isi-badge'), null);
  env.flushTimeouts();
  await env.tick();
  await wait(20);
  assert.equal(env.dom.document.getElementById('isi-prompts'), null);
});

test('success then error removes the previous card', async () => {
  let n = 0;
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=alpha',
    fetchResult: () => {
      n += 1;
      return n === 1 ? MATCH : { ok: false, status: 500, data: null };
    },
  });
  await env.tick();
  await wait(20);
  assert.ok(env.dom.document.getElementById('isi-prompts'));
  env.setHref('https://www.google.com/search?q=bravo');
  env.fireNav();
  assert.equal(env.dom.document.getElementById('isi-prompts'), null);
  assert.equal(env.dom.document.getElementById('isi-badge'), null);
});

test('success then timeout removes the previous card', async () => {
  let n = 0;
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=alpha',
    fetchResult: () => {
      n += 1;
      return n === 1 ? MATCH : { ok: false, aborted: true, error: 'timeout' };
    },
  });
  await env.tick();
  await wait(20);
  assert.ok(env.dom.document.getElementById('isi-prompts'));
  env.setHref('https://www.google.com/search?q=bravo');
  env.fireNav();
  assert.equal(env.dom.document.getElementById('isi-prompts'), null);
  assert.equal(env.dom.document.getElementById('isi-badge'), null);
});

test('success then unsupported layout removes the previous card', async () => {
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=alpha',
  });
  await env.tick();
  await wait(20);
  assert.ok(env.dom.document.getElementById('isi-prompts'));
  env.dom.rso.id = '';
  env.setHref('https://www.google.com/search?q=bravo');
  env.fireNav();
  assert.equal(env.dom.document.getElementById('isi-prompts'), null);
  assert.equal(env.dom.document.getElementById('isi-badge'), null);
});

test('success then exclusion removes the previous card', async () => {
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=alpha',
    holdFetch: true,
  });
  await env.tick();
  env.resolvePending(MATCH);
  await env.tick();
  await wait(20);
  assert.ok(env.dom.document.getElementById('isi-prompts'));
  env.chrome.storage.local.set({ isi_topics_exclude: [11] });
  await env.tick();
  assert.equal(env.dom.document.getElementById('isi-prompts'), null);
  assert.equal(env.dom.document.getElementById('isi-badge'), null);
});

test('insertion throw is retried on the next observer pass', async () => {
  const opts = { throwInsertOnce: true, storage: ACKED, href: 'https://www.google.com/search?q=retry' };
  const env = installExtension(opts);
  await env.tick();
  await wait(20);
  assert.equal(env.dom.document.getElementById('isi-prompts'), null);
  env.fireObservers();
  await env.tick();
  await wait(20);
  assert.ok(env.dom.document.getElementById('isi-prompts'));
});

test('removed card is reinserted; dismissed card is not', async () => {
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=same',
  });
  await env.tick();
  await wait(20);
  const card = env.dom.document.getElementById('isi-prompts');
  assert.ok(card);
  card.remove();
  env.fireObservers();
  await env.tick();
  await wait(20);
  assert.ok(env.dom.document.getElementById('isi-prompts'), 'card returns after DOM replace');

  const live = env.dom.document.getElementById('isi-prompts');
  function findDismiss(node) {
    if (node.className === 'isi-dismiss') return node;
    for (const child of node.children || []) {
      const hit = findDismiss(child);
      if (hit) return hit;
    }
    return null;
  }
  const dismiss = findDismiss(live);
  dismiss.listeners.click.forEach((fn) => fn());
  assert.equal(env.dom.document.getElementById('isi-prompts'), null);
  env.fireObservers();
  await env.tick();
  assert.equal(env.dom.document.getElementById('isi-prompts'), null);
});

test('pageshow persisted restore re-runs matching', async () => {
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=back',
  });
  await env.tick();
  await wait(20);
  assert.ok(env.windowListeners.pageshow);
  env.dom.document.getElementById('isi-prompts').remove();
  const sent = env.messages.filter((m) => m.type === 'getPrompts').length;
  for (const fn of env.windowListeners.pageshow) fn({ persisted: true });
  await env.tick();
  await wait(20);
  assert.ok(env.messages.filter((m) => m.type === 'getPrompts').length >= sent);
});

test('decline then toolbar enable shows the notice again', async () => {
  const env = installExtension({
    storage: {
      'isi_notice_season-2026-v2': false,
      'isi_notice_declined_season-2026-v2': true,
      isi_enabled: false,
    },
    href: 'https://www.google.com/search?q=stuck',
  });
  await env.tick();
  env.chrome.storage.local.set({ isi_enabled: true });
  await env.tick();
  assert.ok(env.dom.document.getElementById('isi-notice-banner'));
  assert.equal(env.messages.filter((m) => m.type === 'getPrompts').length, 0);
});

test('topic groups stored locally omit editorial notes', async () => {
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=notes',
    fetchResult: {
      ok: true,
      status: 200,
      data: {
        prompt: { id: 1, topic: 'T', prompt_content: 'hello', confidence: 0.8 },
        prompts: [{ id: 1, topic: 'T', prompt_content: 'hello', confidence: 0.8 }],
        topics: [{
          id: 11,
          name: 'Group',
          excluded: 0,
          admin_notes: 'editorial S26GATE1MARKER_synthetic_not_a_person',
        }],
        classifier: 'matched',
      },
    },
  });
  await env.tick();
  await wait(20);
  const stored = env.storage.isi_topic_groups;
  assert.equal(stored.length, 1);
  assert.deepEqual(Object.keys(stored[0]).sort(), ['excluded', 'id', 'name']);
  assert.equal(JSON.stringify(stored).includes('S26GATE1MARKER_synthetic_not_a_person'), false);
});

test('hostile seeed_url is not assigned to an anchor', async () => {
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=link',
    fetchResult: {
      ok: true,
      status: 200,
      data: {
        prompt: { id: 1, topic: 'T', prompt_content: 'hello', seeed_url: 'javascript:alert(1)' },
        prompts: [{ id: 1, topic: 'T', prompt_content: 'hello', seeed_url: 'javascript:alert(1)' }],
        topics: [],
        classifier: 'matched',
      },
    },
  });
  await env.tick();
  await wait(20);
  const learn = env.dom.nodes.find((n) => n.className === 'isi-learn-more');
  assert.equal(learn, undefined);
});

test('prompt for query A does not render after navigation to B', async () => {
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=alpha',
    holdFetch: true,
  });
  await env.tick();
  env.setHref('https://www.google.com/search?q=bravo');
  env.fireNav();
  env.resolvePending(MATCH);
  await env.tick();
  assert.equal(env.dom.document.getElementById('isi-prompts'), null);
  assert.ok(env.messages.some((m) => m.type === 'abortPrompts'));
});

test('AI-mode dismiss removes the whole badge', async () => {
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=classic',
  });
  await env.tick();
  await wait(20);
  const card = env.dom.document.getElementById('isi-prompts');
  assert.ok(card);
  assert.ok(env.dom.document.getElementById('isi-badge'));
  findDismiss(card).listeners.click.forEach((fn) => fn());
  assert.equal(env.dom.document.getElementById('isi-badge'), null);
  assert.equal(env.dom.document.getElementById('isi-prompts'), null);
});

test('silenceKey is committed only after a valid empty response', async () => {
  const errored = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=err',
    fetchResult: { ok: false, status: 500, data: null },
  });
  await errored.tick();
  await wait(20);
  const afterError = promptFetches(errored);
  errored.fireObservers();
  await errored.tick();
  await wait(20);
  assert.ok(promptFetches(errored) > afterError, 'error must not commit silenceKey');

  const quiet = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=quiet-key',
    fetchResult: SILENCE,
  });
  await quiet.tick();
  await wait(20);
  const afterQuiet = promptFetches(quiet);
  quiet.fireObservers();
  await quiet.tick();
  await wait(20);
  assert.equal(promptFetches(quiet), afterQuiet, 'valid empty response commits silenceKey');
});

test('reinsertion failure caps fresh match fetches for one navigation', async () => {
  const opts = {
    storage: ACKED,
    href: 'https://www.google.com/search?q=cap',
    failInsertAfter: 1,
  };
  const env = installExtension(opts);
  await env.tick();
  await wait(20);
  assert.ok(env.dom.document.getElementById('isi-prompts'));
  assert.equal(promptFetches(env), 1);
  env.dom.document.getElementById('isi-prompts').remove();
  env.fireObservers();
  await env.tick();
  await wait(20);
  assert.equal(promptFetches(env), 2, 'one extra fetch after failed reinsertion');
  env.fireObservers();
  await env.tick();
  await wait(20);
  env.fireObservers();
  await env.tick();
  await wait(20);
  assert.equal(promptFetches(env), 2, 'cap stops further fetches');
});

test('results mode prompt shows the signpost and keeps the card in the flow', async () => {
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=prompt-pill',
  });
  await env.tick();
  await wait(20);
  assert.ok(env.dom.document.getElementById('isi-prompts'));
  assert.equal(env.dom.document.getElementById('isi-prompts').parentElement, env.dom.center);
  assert.equal(pillText(env), 'Reflection prompt for this search');
});

test('results mode empty prompts show the quiet signpost and no card', async () => {
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=quiet-pill',
    fetchResult: SILENCE,
  });
  await env.tick();
  await wait(20);
  assert.equal(env.dom.document.getElementById('isi-prompts'), null);
  assert.equal(pillText(env), 'No prompt for this search');
});

test('results mode request error shows the error signpost', async () => {
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=error-pill',
    fetchResult: { ok: false, status: 500, data: null },
  });
  await env.tick();
  await wait(20);
  assert.equal(env.dom.document.getElementById('isi-prompts'), null);
  assert.equal(pillText(env), "Couldn't reach the server");
});

test('results mode timeout after retries shows the error signpost', async () => {
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=timeout-pill',
    fetchResult: { ok: false, aborted: true, error: 'timeout' },
  });
  await env.tick();
  await wait(20);
  env.flushTimeouts();
  await env.tick();
  await wait(20);
  assert.equal(pillText(env), "Couldn't reach the server");
});

test('error signpost does not blink; prompt and quiet signposts do', async () => {
  const promptEnv = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=blink-prompt',
  });
  await promptEnv.tick();
  await wait(20);
  const promptDot = pillDot(promptEnv);
  assert.ok(String(promptDot.className).split(/\s+/).includes('isi-blink'));
  assert.equal(String(promptDot.className).split(/\s+/).includes('isi-pill-dot-muted'), false);

  const quietEnv = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=blink-quiet',
    fetchResult: SILENCE,
  });
  await quietEnv.tick();
  await wait(20);
  const quietDot = pillDot(quietEnv);
  assert.ok(String(quietDot.className).split(/\s+/).includes('isi-blink'));
  assert.equal(String(quietDot.className).split(/\s+/).includes('isi-pill-dot-muted'), false);

  const errorEnv = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=blink-error',
    fetchResult: { ok: false, status: 500, data: null },
  });
  await errorEnv.tick();
  await wait(20);
  const errorDot = pillDot(errorEnv);
  assert.ok(String(errorDot.className).split(/\s+/).includes('isi-pill-dot-muted'));
  assert.equal(String(errorDot.className).split(/\s+/).includes('isi-blink'), false);
});

test('clicking the results signpost scrolls to the card and does not duplicate it', async () => {
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=scroll-pill',
  });
  await env.tick();
  await wait(20);
  const card = env.dom.document.getElementById('isi-prompts');
  const pill = signpostPill(env);
  assert.equal(promptCount(env), 1);
  pill.listeners.click.forEach((fn) => fn());
  assert.equal(promptCount(env), 1);
  assert.ok(card.scrollIntoViewCalls && card.scrollIntoViewCalls.length >= 1);
  assert.equal(card.scrollIntoViewCalls[0].block, 'center');
});

test('no signpost before acknowledgement', async () => {
  const env = installExtension({
    storage: { isi_enabled: true },
    href: 'https://www.google.com/search?q=pre-ack',
  });
  await env.tick();
  await wait(20);
  assert.ok(env.dom.document.getElementById('isi-notice-banner'));
  assert.equal(env.dom.document.getElementById('isi-badge'), null);
  assert.equal(promptFetches(env), 0);
});

test('no signpost when disabled', async () => {
  const env = installExtension({
    storage: { ...ACKED, isi_enabled: false },
    href: 'https://www.google.com/search?q=disabled',
  });
  await env.tick();
  await wait(20);
  assert.equal(env.dom.document.getElementById('isi-badge'), null);
  assert.equal(env.dom.document.getElementById('isi-prompts'), null);
  assert.equal(promptFetches(env), 0);
});

test('disabling from the toolbar removes the signpost', async () => {
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=disable-live',
  });
  await env.tick();
  await wait(20);
  assert.ok(env.dom.document.getElementById('isi-badge'));
  env.chrome.storage.local.set({ isi_enabled: false });
  await env.tick();
  await wait(20);
  assert.equal(env.dom.document.getElementById('isi-badge'), null);
  assert.equal(env.dom.document.getElementById('isi-prompts'), null);
});

test('no signpost when declined', async () => {
  const env = installExtension({
    storage: {
      'isi_notice_season-2026-v2': false,
      'isi_notice_declined_season-2026-v2': true,
      isi_enabled: true,
    },
    href: 'https://www.google.com/search?q=declined',
  });
  await env.tick();
  await wait(20);
  assert.equal(env.dom.document.getElementById('isi-badge'), null);
  assert.equal(env.dom.document.getElementById('isi-prompts'), null);
  assert.equal(env.dom.document.getElementById('isi-notice-banner'), null);
  assert.equal(promptFetches(env), 0);
});

test('dismissing the card removes the signpost pill', async () => {
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=dismiss-pill',
  });
  await env.tick();
  await wait(20);
  const card = env.dom.document.getElementById('isi-prompts');
  assert.ok(env.dom.document.getElementById('isi-badge'));
  findDismiss(card).listeners.click.forEach((fn) => fn());
  assert.equal(env.dom.document.getElementById('isi-badge'), null);
  assert.equal(env.dom.document.getElementById('isi-prompts'), null);
});

test('missing results anchor falls back to the badge panel', async () => {
  const env = installExtension({
    storage: ACKED,
    href: 'https://www.google.com/search?q=no-anchor',
  });
  env.dom.rso.parentElement = null;
  await env.tick();
  await wait(20);
  const badge = env.dom.document.getElementById('isi-badge');
  assert.ok(badge);
  const panel = env.dom.document.getElementById('isi-prompts');
  assert.ok(panel);
  assert.equal(panel.style.display, 'none');
  const pill = signpostPill(env);
  assert.ok(pill.listeners.click);
  pill.listeners.click.forEach((fn) => fn());
  assert.equal(panel.style.display, 'block');
});

test('AI mode keeps the existing count badge and toggle panel', async () => {
  const env = installExtension({
    storage: ACKED,
    noRso: true,
    aimc: true,
    href: 'https://www.google.com/search?q=ai-mode',
  });
  await env.tick();
  await wait(20);
  const badge = env.dom.document.getElementById('isi-badge');
  assert.ok(badge);
  assert.equal(String(badge.className).split(/\s+/).includes('isi-signpost'), false);
  assert.equal(pillText(env), 'Reflection prompt available (1)');
  const panel = env.dom.document.getElementById('isi-prompts');
  assert.ok(panel);
  assert.equal(panel.style.display, 'none');
  signpostPill(env).listeners.click.forEach((fn) => fn());
  assert.equal(panel.style.display, 'block');
  signpostPill(env).listeners.click.forEach((fn) => fn());
  assert.equal(panel.style.display, 'none');
  findDismiss(panel).listeners.click.forEach((fn) => fn());
  assert.equal(env.dom.document.getElementById('isi-badge'), null);
});
