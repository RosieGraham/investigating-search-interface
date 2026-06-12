/*
Central configuration for the Investigating Search Interface extension.
Loaded first by the content script, the popup, and the service worker.

After deploying the backend, change API_BASE to the Render URL, e.g.
  https://investigating-search-interface.onrender.com
*/

const ISI_CONFIG = {
  API_BASE: 'https://investigating-search-interface.onrender.com',
  PROJECT_URL: 'https://github.com/bear-rsg/ethical-interface',
  ATTRIBUTION: 'Investigating Search Interface · SEEED',
  MAX_PROMPTS: 3,
  // How long (ms) to keep watching for the results container on dynamic pages
  OBSERVER_TIMEOUT: 12000,
};

// Make the config visible to the MV3 service worker (importScripts) and to
// content scripts / popup (plain global). No module system on purpose: this
// file must work in every extension context.
if (typeof self !== 'undefined') {
  self.ISI_CONFIG = ISI_CONFIG;
}
