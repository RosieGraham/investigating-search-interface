import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));

test('acknowledgement module exists', () => {
  const mod = require(path.join(here, '..', 'lib', 'acknowledgement.js'));
  assert.equal(typeof mod.createAcknowledgement, 'function');
});

test('no backend request before season-2026-v2 is acknowledged', () => {
  const { createAcknowledgement } = require(path.join(here, '..', 'lib', 'acknowledgement.js'));
  const requests = [];
  const ack = createAcknowledgement({
    noticeVersion: 'season-2026-v2',
    send: (msg) => requests.push(msg),
  });
  ack.boot({ isi_consent_given: true, isi_enabled: true });
  assert.equal(ack.canSend(), false);
  assert.equal(requests.length, 0);
});

test('old 2.1 consent does not acknowledge the workshop notice', () => {
  const { createAcknowledgement } = require(path.join(here, '..', 'lib', 'acknowledgement.js'));
  const ack = createAcknowledgement({ noticeVersion: 'season-2026-v2' });
  ack.boot({ isi_consent_given: true, isi_installation_id: 'old' });
  assert.equal(ack.isAcknowledged(), false);
});

test('season-2026-v1 acknowledgement does not satisfy season-2026-v2', () => {
  const { createAcknowledgement } = require(path.join(here, '..', 'lib', 'acknowledgement.js'));
  const ack = createAcknowledgement({ noticeVersion: 'season-2026-v2' });
  ack.boot({ 'isi_notice_season-2026-v1': true });
  assert.equal(ack.isAcknowledged(), false);
});

test('decline persists across reload', () => {
  const { createAcknowledgement } = require(path.join(here, '..', 'lib', 'acknowledgement.js'));
  const store = {};
  const ack = createAcknowledgement({
    noticeVersion: 'season-2026-v2',
    storage: {
      get: async (k) => store[k],
      set: async (obj) => Object.assign(store, obj),
    },
  });
  ack.decline();
  const again = createAcknowledgement({
    noticeVersion: 'season-2026-v2',
    storage: {
      get: async (k) => store[k],
      set: async (obj) => Object.assign(store, obj),
    },
  });
  return again.boot({}).then(() => {
    assert.equal(again.canSend(), false);
    assert.equal(again.isDeclined(), true);
  });
});

test('clearDecline lets the notice be shown again', () => {
  const { createAcknowledgement } = require(path.join(here, '..', 'lib', 'acknowledgement.js'));
  const store = {};
  const ack = createAcknowledgement({
    noticeVersion: 'season-2026-v2',
    storage: {
      get: async (k) => store[k],
      set: async (obj) => Object.assign(store, obj),
    },
  });
  ack.decline();
  assert.equal(ack.isDeclined(), true);
  ack.clearDecline();
  assert.equal(ack.isDeclined(), false);
  assert.equal(ack.isAcknowledged(), false);
  assert.equal(store['isi_notice_declined_season-2026-v2'], false);
});

test('inverted: pre-acknowledgement send must make the contract fail', () => {
  const { createAcknowledgement } = require(path.join(here, '..', 'lib', 'acknowledgement.js'));
  const requests = [];
  const ack = createAcknowledgement({
    noticeVersion: 'season-2026-v2',
    send: (msg) => requests.push(msg),
    allowPreAck: true,
  });
  ack.boot({});
  ack.sendIfAllowed({ type: 'getPrompts', query: 'q' });
  assert.equal(requests.length, 1);
  assert.throws(() => {
    assert.equal(requests.length, 0);
  });
});
