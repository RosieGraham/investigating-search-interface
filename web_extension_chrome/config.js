/*
Central configuration for the Investigating Search Interface workshop extension.
Loaded first by the content script, the popup, and the service worker.
*/

const ISI_CONFIG = {
  API_BASE: 'https://investigating-search-interface.onrender.com',
  PROJECT_URL: 'https://investigating-search-interface.onrender.com/',
  PRIVACY_NOTICE_URL: 'https://investigating-search-interface.onrender.com/privacy/',
  ATTRIBUTION: 'Investigating Search Interface · SEASON 2026',
  BUILD_ID: '0.2.1-season-2026-1',
  EXTENSION_VERSION: '0.2.1',
  NOTICE_VERSION: 'season-2026-v2',
  MAX_PROMPTS: 1,
  // Must outlive two matching attempts (12000 + 350 + 12000 = 24350).
  OBSERVER_TIMEOUT: 26000,
};

if (typeof self !== 'undefined') {
  self.ISI_CONFIG = ISI_CONFIG;
}
