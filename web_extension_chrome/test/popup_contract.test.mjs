import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const here = path.dirname(fileURLToPath(import.meta.url));
const popup = fs.readFileSync(path.join(here, '..', 'popup.js'), 'utf8');

test('popup must not send health or empty prompt lookup', () => {
  assert.equal(popup.includes("type: 'health'"), false);
  assert.equal(popup.includes("query: ''"), false);
});

test('popup keeps local topic exclusion controls', () => {
  assert.equal(popup.includes('isi_topics_exclude'), true);
});
