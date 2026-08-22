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

function wait(ms = 0) {
  return new Promise((r) => setTimeout(r, ms));
}

function loadWorker(opts = {}) {
  const fetches = [];
  const storage = { ...(opts.storage || {}) };
  const sandbox = {
    console,
    fetch: async (url, init) => {
      fetches.push({ url, init });
      return { ok: true, status: 200, json: async () => ({ prompts: [] }) };
    },
    setTimeout: (fn) => { fn(); return 1; },
    clearTimeout() {},
    AbortController,
    URLSearchParams,
    Map,
    Date,
    Number,
    Array,
    String,
    JSON,
    Math,
    Promise,
    Error,
    self: null,
    chrome: {
      storage: {
        local: {
          get(keys, cb) {
            const out = {};
            for (const k of keys) out[k] = storage[k];
            cb(out);
          },
        },
      },
      runtime: {
        onMessage: {
          addListener(fn) { sandbox._listener = fn; },
        },
      },
    },
    importScripts(name) {
      runInContext(read(name), sandbox, { filename: name });
    },
  };
  sandbox.self = sandbox;
  runInContext(read('background.js'), createContext(sandbox), { filename: 'background.js' });
  return { sandbox, fetches, storage };
}

async function dispatch(worker, message, sender) {
  let responded;
  worker.sandbox._listener(message, sender || { tab: { id: 1 } }, (value) => { responded = value; });
  await wait(20);
  return responded;
}

test('getPrompts without acknowledgement does not fetch', async () => {
  const worker = loadWorker({ storage: { isi_enabled: true } });
  const responded = await dispatch(worker, { type: 'getPrompts', query: 'pre-consent', topicsExclude: [] });
  assert.equal(worker.fetches.length, 0);
  assert.equal(responded.ok, false);
  assert.equal(responded.error, 'not_acknowledged');
});

test('getPrompts with acknowledgement and a tab sender does fetch', async () => {
  const worker = loadWorker({
    storage: {
      'isi_notice_season-2026-v2': true,
      isi_enabled: true,
    },
  });
  const responded = await dispatch(worker, {
    type: 'getPrompts',
    requestId: 'r1',
    query: 'weather',
    topicsExclude: [1],
  });
  assert.equal(worker.fetches.length, 1);
  assert.equal(responded.ok, true);
  assert.ok(worker.fetches[0].init.body.includes('user_search_query=weather'));
});

test('getPrompts without a tab sender is refused', async () => {
  const worker = loadWorker({
    storage: { 'isi_notice_season-2026-v2': true, isi_enabled: true },
  });
  const responded = await dispatch(worker, { type: 'getPrompts', query: 'x' }, {});
  assert.equal(worker.fetches.length, 0);
  assert.equal(responded.error, 'invalid_sender');
});

test('getPrompts with a non-array exclusion list is refused', async () => {
  const worker = loadWorker({
    storage: { 'isi_notice_season-2026-v2': true, isi_enabled: true },
  });
  const responded = await dispatch(worker, {
    type: 'getPrompts',
    query: 'x',
    topicsExclude: '1,2',
  });
  assert.equal(worker.fetches.length, 0);
  assert.equal(responded.error, 'invalid_exclusions');
});
