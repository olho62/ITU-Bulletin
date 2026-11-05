# ITU Bulletin

A daily diary-prompt sheet for the East Kent intensive care units. It rebuilds itself
at 06:00, 13:00 and 23:30 UK time and is published as a single bookmarkable page.

- **No server and no WordPress.** GitHub runs `build.py` on a timer and publishes the result free on GitHub Pages.
- **No patient data.** Only public news, weather, anniversaries and the diary prompts.
- **Nothing to install or update.** The script uses plain Python with no add-on packages.
- **Everything editable lives in `config.json`:** sites, feeds, prompts and edition times.

---

## First-time setup (about 15 minutes)

### 1. Create the repository
1. Sign up at [github.com](https://github.com) (free).
2. Click **+** (top right) → **New repository**.
3. Name it `itu-bulletin`, choose **Public**, then click **Create repository**.

### 2. Upload the files
1. On the new repository's page, click **uploading an existing file**.
2. Unzip `itu-bulletin.zip`, then drag **everything inside the folder** into the upload box.
3. Click **Commit changes**.

> **Check the `.github` folder arrived.** It's the scheduler, and Macs hide it.
> On a Mac, press **Cmd + Shift + .** in Finder to show it before dragging.
>
> If it's still missing after upload, add it by hand:
> 1. Click **Add file → Create new file**.
> 2. Type `.github/workflows/build.yml` as the name.
> 3. Paste in the contents of that file and commit.

### 3. Turn on the web page
Go to **Settings → Pages**. Under **Build and deployment → Source**, choose **GitHub Actions**.

### 4. Add your Gemini key (optional)
1. Go to **Settings → Secrets and variables → Actions → New repository secret**.
2. Set **Name** to `GEMINI_API_KEY`.
3. Set **Secret** to the same key that's in your WordPress `wp-config.php`.

Secrets are encrypted and never appear in the code or on the page.
Without a key, the bulletin still works. It uses each publisher's own headline and
summary, in newest-first order, with no importance bars.

### 5. Build the first edition
1. Open the **Actions** tab. If asked, click **I understand my workflows, go ahead and enable them**.
2. Click **Build bulletin → Run workflow**, pick **morning**, then **Run workflow**.
3. Wait about a minute until the run shows a green tick.

### 6. Bookmark it
The page lives at:

```
https://YOUR-USERNAME.github.io/itu-bulletin/
```

Each unit can bookmark its own tab:

| Unit | Address ending |
|---|---|
| QEQM | `…/itu-bulletin/#margate` |
| KCH | `…/itu-bulletin/#canterbury` |
| William Harvey | `…/itu-bulletin/#ashford` |

**Before telling anyone, open it on a ward PC** to make sure the trust's web filter allows it.

---

## Day to day

| I want to… | Do this |
|---|---|
| Rebuild now | **Actions → Build bulletin → Run workflow** |
| See if the last build had problems | Open `…/itu-bulletin/status.json`, or the latest run in **Actions** |
| Change a diary prompt | Open `config.json` → pencil icon → edit → **Commit changes** (applies from the next edition) |
| Add a fourth unit | Copy one block under `"sites"` in `config.json`, then change the name, unit, latitude/longitude and feeds |
| Change edition times | Edit `"editions"` in `config.json` **and** the two `cron` lines in `.github/workflows/build.yml` (they're in UTC; see the comment there) |
| Give someone else access | **Settings → Collaborators → Add people** |
| Hand it over entirely | **Settings → General → Transfer ownership** |

If a scheduled build fails, GitHub emails you, and the page keeps the last good edition.

---

## How it stays reliable

**Feeds and services**
- If one feed is down, the build carries on without it.
- If every national feed is down, the build stops and leaves the previous edition up.
- If the weather service gives an incomplete answer, the page says **"Weather unavailable"** instead of a false reading. This was the cause of the old "0 degrees".
- Anniversaries about wars, deaths and disasters are skipped.

**Gemini safeguards** (only when a key is set)
- Gemini can only pick stories by their ID. Anything it invents is dropped.
- Every number it writes is checked against the original report. If a number doesn't match, that story reverts to the publisher's wording.
- The diary line has to point at a real story and be 12 words or fewer.
- If Gemini is busy, the build retries. If it's still unavailable, or if nothing passes the checks, the page uses the publishers' wording rather than leaving a tab empty.

**Keeping the schedule running**
- GitHub pauses timers in repositories with no activity for 60 days. Each build saves the new page as a commit, which counts as activity.
- If GitHub ever does pause it, you'll get an email, and one click in **Actions** restarts it.

**Archive**
- The last edition of each day is kept for a year at `…/itu-bulletin/archive/`.
