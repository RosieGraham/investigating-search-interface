import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));

test('request lifecycle module exists', () => {
  const mod = require(path.join(here, '..', 'lib', 'request_lifecycle.js'));
  assert.equal(typeof mod.createRequestMachine, 'function');
});

test('at most two attempts, retry only on timeout or 502/503/504', async () => {
  const { createRequestMachine } = require(path.join(here, '..', 'lib', 'request_lifecycle.js'));
  const calls = [];
  const machine = createRequestMachine({
    now: (() => { let t = 0; return () => t; })(),
    delay: async () => {},
    fetchImpl: async () => {
      calls.push('fetch');
      const err = new Error('timeout');
      err.name = 'AbortError';
      throw err;
    },
  });
  machine.acknowledge('season-2026-v2');
  await machine.requestMatch({ query: 'q1', navId: 1 });
  assert.equal(calls.length, 2);
});

test('does not retry 4xx or 500', async () => {
  const { createRequestMachine } = require(path.join(here, '..', 'lib', 'request_lifecycle.js'));
  const calls = [];
  const machine = createRequestMachine({
    delay: async () => {},
    fetchImpl: async () => {
      calls.push('fetch');
      return { ok: false, status: 400, json: async () => ({ error: 'invalid_request' }) };
    },
  });
  machine.acknowledge('season-2026-v2');
  await machine.requestMatch({ query: 'q1', navId: 1 });
  assert.equal(calls.length, 1);
});

test('late response at 12000ms timeout cannot render on a later query', async () => {
  const { createRequestMachine } = require(path.join(here, '..', 'lib', 'request_lifecycle.js'));
  let finishFirst;
  const machine = createRequestMachine({
    timeoutMs: 12000,
    delay: async () => {},
    fetchImpl: () => new Promise((resolve) => {
      finishFirst = () => resolve({
        ok: true,
        status: 200,
        json: async () => ({ prompt: { id: 1 }, prompts: [{ id: 1 }], topics: [], classifier: 'matched' }),
      });
    }),
  });
  machine.acknowledge('season-2026-v2');
  const first = machine.requestMatch({ query: 'old', navId: 1 });
  machine.abort();
  machine.requestMatch({ query: 'new', navId: 2 });
  finishFirst();
  const rendered = await first;
  assert.equal(rendered, null);
});

test('late response cannot render on a later query', async () => {
  const { createRequestMachine } = require(path.join(here, '..', 'lib', 'request_lifecycle.js'));
  let finishFirst;
  const machine = createRequestMachine({
    delay: async () => {},
    fetchImpl: () => new Promise((resolve) => {
      finishFirst = () => resolve({
        ok: true,
        status: 200,
        json: async () => ({ prompt: { id: 1 }, prompts: [{ id: 1 }], topics: [], classifier: 'matched' }),
      });
    }),
  });
  machine.acknowledge('season-2026-v2');
  const first = machine.requestMatch({ query: 'old', navId: 1 });
  machine.abort();
  const secondQuery = { query: 'new', navId: 2 };
  machine.requestMatch(secondQuery);
  finishFirst();
  const rendered = await first;
  assert.equal(rendered, null);
});

test('season-2026-v1 acknowledgement does not enable matching', async () => {
  const { createRequestMachine } = require(path.join(here, '..', 'lib', 'request_lifecycle.js'));
  const calls = [];
  const machine = createRequestMachine({
    delay: async () => {},
    fetchImpl: async () => {
      calls.push('fetch');
      return {
        ok: true,
        status: 200,
        json: async () => ({ prompt: { id: 1 }, prompts: [{ id: 1 }], topics: [], classifier: 'matched' }),
      };
    },
  });
  machine.acknowledge('season-2026-v1');
  const rendered = await machine.requestMatch({ query: 'q1', navId: 1 });
  assert.equal(rendered, null);
  assert.equal(calls.length, 0);
});

test('abort invokes onAbort so the worker fetch can be cancelled', async () => {
  const { createRequestMachine } = require(path.join(here, '..', 'lib', 'request_lifecycle.js'));
  let aborted = 0;
  let finish;
  const machine = createRequestMachine({
    delay: async () => {},
    onAbort: () => { aborted += 1; },
    fetchImpl: () => new Promise((resolve) => {
      finish = () => resolve({
        ok: true,
        status: 200,
        json: async () => ({ prompts: [{ id: 1 }] }),
      });
    }),
  });
  machine.acknowledge('season-2026-v2');
  const first = machine.requestMatch({ query: 'old', navId: 1 });
  machine.abort();
  finish();
  assert.equal(aborted, 1);
  assert.equal(await first, null);
});

test('retry delay includes bounded jitter', async () => {
  const { createRequestMachine } = require(path.join(here, '..', 'lib', 'request_lifecycle.js'));
  const waited = [];
  const realRandom = Math.random;
  Math.random = () => 0.999;
  try {
    const machine = createRequestMachine({
      retryDelayMs: 350,
      retryJitterMs: 250,
      delay: async (ms) => { waited.push(ms); },
      fetchImpl: async () => {
        const err = new Error('timeout');
        err.name = 'AbortError';
        throw err;
      },
    });
    machine.acknowledge('season-2026-v2');
    await machine.requestMatch({ query: 'q', navId: 1 });
    assert.equal(waited.length, 1);
    assert.equal(waited[0], 350 + Math.floor(0.999 * 251));
  } finally {
    Math.random = realRandom;
  }
});
