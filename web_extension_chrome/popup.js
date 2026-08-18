/* Settings popup: enable/disable and per-topic-group exclusions.
   Exclusions are a local preference, sent with matching requests.
   This popup does not call the backend. */

const enabledToggle = document.getElementById('enabled-toggle');
const topicsList = document.getElementById('topics-list');
const projectLink = document.getElementById('project-link');
const privacyLink = document.getElementById('privacy-link');

projectLink.href = ISI_CONFIG.PROJECT_URL;
privacyLink.href = ISI_CONFIG.PRIVACY_NOTICE_URL;

async function init() {
  const stored = await chrome.storage.local.get([
    'isi_enabled',
    'isi_topics_exclude',
    'isi_topic_groups',
  ]);
  enabledToggle.checked = stored.isi_enabled !== false;
  const excluded = new Set(stored.isi_topics_exclude || []);

  enabledToggle.addEventListener('change', () => {
    chrome.storage.local.set({ isi_enabled: enabledToggle.checked });
  });

  const groups = stored.isi_topic_groups;
  if (!Array.isArray(groups) || groups.length === 0) {
    topicsList.textContent = 'Topic list appears after your first search.';
    return;
  }

  topicsList.replaceChildren();
  for (const group of groups) {
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
