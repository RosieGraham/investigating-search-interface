/*
Central configuration for the Investigating Search Interface workshop extension.
Loaded first by the content script, the popup, and the service worker.
*/

const ISI_CONFIG = {
  API_BASE: 'https://investigating-search-interface.onrender.com',
  PROJECT_URL: 'https://github.com/RosieGraham/investigating-search-interface',
  PRIVACY_NOTICE_URL: 'https://investigating-search-interface.onrender.com/privacy/',
  ATTRIBUTION: 'Investigating Search Interface · SEASON 2026',
  BUILD_ID: 'season-2026-workshop-1',
  EXTENSION_VERSION: '2.2.0',
  NOTICE_VERSION: 'season-2026-v1',
  MAX_PROMPTS: 1,
  OBSERVER_TIMEOUT: 12000,
};

if (typeof self !== 'undefined') {
  self.ISI_CONFIG = ISI_CONFIG;
}
