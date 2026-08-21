import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';

const here = path.dirname(fileURLToPath(import.meta.url));
const manifest = JSON.parse(fs.readFileSync(path.join(here, '..', 'manifest.json'), 'utf8'));

const EXPECTED_GOOGLE_MATCHES = [
  'https://www.google.com/search*',
  'https://www.google.co.uk/search*',
  'https://www.google.ie/search*',
  'https://www.google.at/search*',
  'https://www.google.be/search*',
  'https://www.google.ca/search*',
  'https://www.google.ch/search*',
  'https://www.google.com.au/search*',
  'https://www.google.de/search*',
  'https://www.google.dk/search*',
  'https://www.google.es/search*',
  'https://www.google.fi/search*',
  'https://www.google.fr/search*',
  'https://www.google.it/search*',
  'https://www.google.nl/search*',
  'https://www.google.no/search*',
  'https://www.google.pl/search*',
  'https://www.google.pt/search*',
  'https://www.google.se/search*',
];

test('content script matches are the agreed explicit Google host list', () => {
  const matches = manifest.content_scripts[0].matches;
  for (const pattern of EXPECTED_GOOGLE_MATCHES) {
    assert.equal(matches.includes(pattern), true, `missing ${pattern}`);
  }
  assert.deepEqual(matches, EXPECTED_GOOGLE_MATCHES);
  assert.equal(matches.some((item) => item.includes('google.*')), false);
});
