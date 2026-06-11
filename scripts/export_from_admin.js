/*
Export all research data from the ORIGINAL Ethical Interface Django admin.

Use this if you need a fresher export while the old bham.ac.uk system is
still running (the June 2026 migration used exactly this method).

How to run:
  1. Log in to https://ethical-interface.bham.ac.uk/dashboard/ in Chrome
  2. Open DevTools (F12) -> Console tab
  3. Paste this entire file and press Enter
  4. Wait (a few minutes - it fetches every prompt page and ~207 trigger
     list pages); progress logs appear in the console
  5. A file `investigating-search-data.json` downloads automatically
  6. Put it in this repo's data/ folder and run:
       python manage.py import_live_export data/investigating-search-data.json

Read-only: only GET requests, nothing is modified.
*/

(async () => {
  const BASE = '/dashboard/researchdata';

  const getDoc = async (url) => {
    const r = await fetch(url, { credentials: 'same-origin' });
    if (!r.ok) throw new Error(`${url} -> ${r.status}`);
    return new DOMParser().parseFromString(await r.text(), 'text/html');
  };
  const idFromHref = (a) => {
    const m = a.getAttribute('href').match(/(\d+)\/change/);
    return m ? parseInt(m[1], 10) : null;
  };

  console.log('1/4 topic groups + id lists...');
  const topic_groups = [];
  let doc = await getDoc(`${BASE}/topicgroup/?all=`);
  doc.querySelectorAll('#result_list tbody tr').forEach((tr) => {
    const a = tr.querySelector('th a');
    topic_groups.push({ id: idFromHref(a), name: a.textContent.trim() });
  });

  const topicIds = [];
  doc = await getDoc(`${BASE}/topic/?all=`);
  doc.querySelectorAll('#result_list tbody tr').forEach((tr) =>
    topicIds.push(idFromHref(tr.querySelector('th a')))
  );

  const promptIds = [];
  doc = await getDoc(`${BASE}/prompt/?all=`);
  doc.querySelectorAll('#result_list tbody tr').forEach((tr) =>
    promptIds.push(idFromHref(tr.querySelector('th a') || tr.querySelector('a')))
  );

  console.log(`2/4 ${topicIds.length} topic pages...`);
  const topics = [];
  for (let i = 0; i < topicIds.length; i += 10) {
    const batch = await Promise.all(
      topicIds.slice(i, i + 10).map(async (id) => {
        const d = await getDoc(`${BASE}/topic/${id}/change/`);
        const tg = d.querySelector('#id_topic_group option[selected], #id_topic_group option:checked');
        return {
          id,
          name: d.querySelector('#id_name')?.getAttribute('value') ?? d.querySelector('#id_name')?.value,
          topic_group_id: tg ? parseInt(tg.value, 10) : null,
          admin_notes: (d.querySelector('#id_admin_notes')?.textContent || '').trim(),
        };
      })
    );
    topics.push(...batch);
    await new Promise((r) => setTimeout(r, 100));
  }

  console.log(`3/4 ${promptIds.length} prompt pages (the slow part)...`);
  const prompts = [];
  for (let i = 0; i < promptIds.length; i += 8) {
    const batch = await Promise.all(
      promptIds.slice(i, i + 8).map(async (id) => {
        const d = await getDoc(`${BASE}/prompt/${id}/change/`);
        const topicOpt = d.querySelector('#id_topic option[selected], #id_topic option:checked');
        const pr = d.querySelector('#id_priority')?.getAttribute('value');
        return {
          id,
          topic_id: topicOpt ? parseInt(topicOpt.value, 10) : null,
          prompt_content: d.querySelector('#id_prompt_content')?.textContent ?? '',
          response_required: !!d.querySelector('#id_response_required')?.hasAttribute('checked'),
          priority: pr ? parseInt(pr, 10) : null,
          admin_approved: !!d.querySelector('#id_admin_approved')?.hasAttribute('checked'),
          admin_notes: (d.querySelector('#id_admin_notes')?.textContent || '').trim(),
          trigger_ids: Array.from(d.querySelectorAll('#id_triggers option[selected]')).map((o) =>
            parseInt(o.value, 10)
          ),
        };
      })
    );
    prompts.push(...batch);
    if (i % 40 === 0) console.log(`  ...${Math.min(i + 8, promptIds.length)}/${promptIds.length}`);
    await new Promise((r) => setTimeout(r, 150));
  }

  console.log('4/4 trigger list pages...');
  const triggers = [];
  // Find the page count from the paginator, then walk pages 1..N
  doc = await getDoc(`${BASE}/trigger/`);
  const pagText = doc.querySelector('.paginator')?.textContent || '';
  const pageNums = [...pagText.matchAll(/\d+/g)].map((m) => parseInt(m[0], 10));
  const lastPage = Math.max(1, ...pageNums.filter((n) => n < 10000));
  for (let p = 1; p <= lastPage; p += 7) {
    const ps = [];
    for (let k = p; k < Math.min(p + 7, lastPage + 1); k++) ps.push(k);
    const results = await Promise.all(
      ps.map(async (pp) => {
        const d = await getDoc(`${BASE}/trigger/?p=${pp}`);
        return Array.from(d.querySelectorAll('#result_list tbody tr')).map((tr) => {
          const a = tr.querySelector('th.field-id a') || tr.querySelector('th a');
          return {
            id: idFromHref(a),
            text: tr.querySelector('td.field-trigger_text')?.textContent.trim() ?? a.textContent.trim(),
          };
        });
      })
    );
    results.forEach((rows) => triggers.push(...rows));
    if (p % 35 === 1) console.log(`  ...page ${Math.min(p + 6, lastPage)}/${lastPage}`);
    await new Promise((r) => setTimeout(r, 100));
  }

  const data = {
    exported_at: new Date().toISOString(),
    source: location.host + ' Django admin (read-only scrape)',
    topic_groups,
    topics,
    prompts,
    triggers,
  };
  console.log('Done:', {
    topic_groups: topic_groups.length,
    topics: topics.length,
    prompts: prompts.length,
    triggers: triggers.length,
  });

  const blob = new Blob([JSON.stringify(data, null, 1)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'investigating-search-data.json';
  document.body.appendChild(a);
  a.click();
  a.remove();
})();
