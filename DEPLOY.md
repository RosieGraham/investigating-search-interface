# Deployment runbook: from zero accounts to live

Written for Rosie, June 2026. Total cost: £0/month on the free tiers
(optionally ~£7/month for an always-awake service around the September
conference). Time: roughly an hour the first time, most of it account signups.

Once set up, your entire deployment workflow is: **edit code (or have Cursor
edit it) → commit → push → Render deploys automatically.** No Mike, no
tickets, no waiting.

---

## Stage 1 - GitHub (where the code lives)

1. Create an account at [github.com/signup](https://github.com/signup)
   (free plan). Use your personal email rather than bham.ac.uk, since this
   needs to outlive any institutional affiliation.
2. Install [GitHub Desktop](https://desktop.github.com) and sign in.
3. In GitHub Desktop: **File → Add local repository** → choose this
   `investigating-search-interface` folder. It will offer to create a
   repository - accept, keep the name, set it **Public** (the old repo is
   public MIT, and public keeps it citable in bids).
4. Write a first commit message ("Initial rebuild, June 2026") → **Commit**
   → **Publish repository**.

That's it: the code is now yours, under your account.

## Stage 2 - Neon (the database)

1. Sign up at [neon.com](https://neon.com) (free plan - persistent, unlike
   Render's own free Postgres which self-deletes after 30 days).
2. Create a project called `investigating-search-interface`, region
   **Europe (Frankfurt)**.
3. On the project dashboard, find **Connection string** and copy the
   `postgresql://...` URL (choose the "pooled" variant if offered).
   Keep it somewhere safe for Stage 3 - treat it like a password.

## Stage 3 - Render (the application)

1. Sign up at [render.com](https://render.com) **using "Sign in with
   GitHub"** - this links the two accounts and enables push-to-deploy.
2. Click **New + → Blueprint**, choose your
   `investigating-search-interface` repository. Render reads `render.yaml`
   and proposes the service. Region Frankfurt, plan Free.
3. When prompted for environment variables, paste the Neon connection
   string into `DATABASE_URL`. (The blueprint prompt only asks for
   variables declared in render.yaml - the admin-user ones come next.)
4. After the service is created, open it from the dashboard, go to the
   **Environment** tab, click **Edit / Add Environment Variable**, and add
   three variables for the first deploy only:
   - `DJANGO_SUPERUSER_USERNAME`: your admin username
   - `DJANGO_SUPERUSER_EMAIL`: your email
   - `DJANGO_SUPERUSER_PASSWORD`: a strong password (you'll change it)
   Save - Render redeploys automatically when environment variables change.
5. The first build takes a few minutes (it downloads the 22MB model).
   Watch the logs; success ends with gunicorn workers booting.
5. Your service URL appears at the top, e.g.
   `https://investigating-search-interface.onrender.com`. Open
   `https://<your-url>/healthz` - you should see `{"status": "ok"}`.
6. Log in at `https://<your-url>/dashboard/` - **the username is your
   EMAIL ADDRESS, all lowercase** (this project's user model forces
   username = email so people log in with email; whatever you put in
   DJANGO_SUPERUSER_USERNAME gets replaced). Then **change the password**
   (top right → Change password) and delete the three `DJANGO_SUPERUSER_*`
   variables in Render → Environment. Note: while those variables exist,
   every deploy resets the account password back to the variable's value -
   another reason to delete them promptly.

## Stage 4 - Data

The live export from the old system (`data/live-export-2026-06-11.json`,
kept out of git) needs a one-time import. Render's free tier has no shell,
so import locally INTO Neon:

```bash
cd django
DATABASE_URL="<your Neon connection string>" DEBUG=true \
  python manage.py migrate
DATABASE_URL="<your Neon connection string>" DEBUG=true \
  python manage.py import_live_export ../data/live-export-2026-06-11.json
DATABASE_URL="<your Neon connection string>" DEBUG=true \
  python ../scripts/apply_topic_descriptions.py
```

(If you ever need a fresher export while the old system is still up:
open the old admin in Chrome, paste `scripts/export_from_admin.js` into
DevTools Console, and it downloads a new JSON.)

After importing, restart the Render service (Manual Deploy → Clear cache
not required; just "Restart") so it rebuilds the topic index from the new
data.

## Stage 5 - Keep-awake (free tier only)

Render free services sleep after 15 idle minutes; the first search after
that gets no prompt (the extension stays silent rather than erroring, but
still). Two options:

- **Free:** create a monitor at [uptimerobot.com](https://uptimerobot.com)
  (or cron-job.org) pinging `https://<your-url>/healthz` every 5-10
  minutes. 750 free instance-hours/month is enough to run 24/7.
- **Paid (~£7/month):** upgrade the service to Starter for September;
  downgrade after the conference.

## Stage 6 - The extension

1. In `web_extension_chrome/config.js`, set
   `API_BASE: 'https://<your-url>.onrender.com'`
2. In `manifest.json`, optionally narrow
   `https://*.onrender.com/*` to your exact URL.
3. Chrome → `chrome://extensions` → Developer mode → **Load unpacked** →
   select `web_extension_chrome/`.
4. Search Google for "is keto safe long term" - a prompt card should
   appear above the results. (Try `&udm=50` on the URL to see the AI Mode
   badge fallback.)
5. Commit and push these two config changes.

## Stage 7 - Courtesies

- Tell Mike the project has moved to your own infrastructure, thank him,
  and ask whether the bham.ac.uk deployment can be retired at his
  convenience (nothing depends on it any more).
- The old extension v1.1.1 (popup) keeps working against the old server
  until that retirement; nothing breaks in the interim.

---

## Day-to-day afterwards

| Task | How |
|---|---|
| Edit prompts/topics/descriptions | Django admin at `/dashboard/` - no code, no deploy |
| Approve a prompt | tick `admin_approved` (only approved prompts are served) |
| Change code | edit → commit in GitHub Desktop → push → auto-deploy |
| Change threshold | Render → Environment → `CLASSIFIER_THRESHOLD` → save (auto-restarts) |
| Watch logs | Render dashboard → Logs |
| Re-calibrate | `scripts/calibrate_threshold.py` locally against Neon |

## Troubleshooting

- **First deploy sits "Queued" for 10-20 minutes**: normal on the free
  build pool - the June 2026 first deploy took 18 minutes. While no deploy
  has ever gone live, the public URL shows a plain "Not Found" page; that's
  Render's placeholder, not an app error.
- **A second deploy waits behind the first**: changing environment
  variables or clicking Manual Deploy queues a deploy that starts only
  after the current one finishes. Expected behaviour.
- **Environment variable changes only apply through a deploy.** Each
  deploy snapshots the environment when it is created; "Restart service"
  re-uses the old snapshot. If you add variables, make sure a NEW deploy
  runs afterwards (saving usually triggers one - check the deploys list).
- **A deploy stuck "in progress" for 30+ minutes**: cancel it (the "..."
  menu on its row in the Deploys tab - the live service is unaffected),
  then Manual Deploy -> "Deploy latest commit". Rebuilds after the first
  are much faster thanks to the build cache.
- **Deploy failed at download_model**: transient HuggingFace issue - retry
  the deploy. The model is cached between successful builds.
- **Admin login loops / CSRF error**: confirm the service URL appears in
  Render's `RENDER_EXTERNAL_URL` (it's set automatically; only an issue on
  custom domains - then add it to `ALLOWED_HOSTS` and CSRF origins via env).
- **Prompts never appear**: check `/healthz`, then the popup's status row,
  then Render logs for `Classifier` lines. The API returning
  `"classifier": "unavailable"` means the model didn't download - redeploy.
- **First search after idle shows nothing**: that's the free-tier cold
  start; see Stage 5.
