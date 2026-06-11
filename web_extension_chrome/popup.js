/* Settings popup: enable/disable, anonymous event logging opt-out, and
   per-topic-group exclusions (stored locally, sent with every prompt request). */

const enabledToggle = document.getElementById('enabled-toggle');
const eventsToggle = document.getElementById('events-toggle');
const topicsList = document.getElementById('topics-list');
const apiStatus = document.getElementById('api-status');
const projectLink = document.getElementById('project-link');

projectLink.href = ISI_CONFIG.PROJECT_URL;

function send(message) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(message, (resp) => {
      if (chrome.runtime.lastError) resolve({ ok: false });
      else resolve(resp || { ok: false });
    });
  });
}

async function init() {
  const stored = await chrome.storage.local.get(['isi_enabled', 'isi_topics_exclude', 'isi_log_events']);
  enabledToggle.checked = stored.isi_enabled !== false;
  eventsToggle.checked = stored.isi_log_events !== false;
  const excluded = new Set(stored.isi_topics_exclude || []);

  enabledToggle.addEventListener('change', () => {
    chrome.storage.local.set({ isi_enabled: enabledToggle.checked });
  });
  eventsToggle.addEventListener('change', () => {
    chrome.storage.local.set({ isi_log_events: eventsToggle.checked });
  });

  // Health + topic groups (an empty query returns the topic group list)
  const health = await send({ type: 'health' });
  apiStatus.textContent = health.ok ? 'connected' : 'offline (prompts paused)';
  apiStatus.classList.toggle('ok', !!health.ok);

  const resp = await send({ type: 'getPrompts', query: '', topicsExclude: [] });
  if (!resp.ok || !resp.data || !Array.isArray(resp.data.topics)) {
    topicsList.textContent = 'Topic list unavailable.';
    return;
  }

  topicsList.replaceChildren();
  for (const group of resp.data.topics) {
    const label = document.createElement('label');
    label.className = 'row topic-row';
    const span = document.createElement('span');
    span.textContent = group.name;
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = !excluded.has(group.id);
    box.addEventListener('change', async () => {
      const current = await chrome.storage.local.get('isi_topics_exclude');
      const set = new Set(current.isi_topics_exclude || []);
      if (box.checked) set.delete(group.id);
      else set.add(group.id);
      chrome.storage.local.set({ isi_topics_exclude: [...set] });
    });
    label.appendChild(span);
    label.appendChild(box);
    topicsList.appendChild(label);
  }
}

init();
