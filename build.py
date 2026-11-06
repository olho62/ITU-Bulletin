#!/usr/bin/env python3
"""
ITU Bulletin — builds the daily diary-prompt sheet as a static page.

Runs on a schedule (see .github/workflows/build.yml) and writes:
  docs/index.html              today's bulletin (what people bookmark)
  docs/archive/YYYY-MM-DD.html the last edition of each day
  docs/archive/index.html      list of past editions
  docs/status.json             when it last built and anything that went wrong

Uses the Python standard library only — nothing to install, nothing to update.
Everything you are likely to want to change lives in config.json, not here.

    python build.py                 # decide the edition from the UK clock
    python build.py --edition morning
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "docs"
LONDON = ZoneInfo("Europe/London")
USER_AGENT = "ITUBulletin/2.0 (static diary-prompt sheet)"
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

WARNINGS = []


def log(msg, warn=False):
    stamp = datetime.now(LONDON).strftime("%H:%M:%S")
    print(f"{stamp}  {msg}", flush=True)
    if warn:
        WARNINGS.append(msg)


def esc(s):
    return html.escape(str(s or ""), quote=True)


# --------------------------------------------------------------- network ----

def http_get(url, headers=None, data=None, timeout=20):
    """Return the response body as bytes, or raise. One place for all requests."""
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get_json(url, headers=None):
    try:
        return json.loads(http_get(url, headers))
    except Exception as e:  # noqa: BLE001 — any failure means "unavailable"
        log(f"unavailable: {url.split('?')[0]} ({e})", warn=True)
        return None


# ----------------------------------------------------------------- feeds ----

TAG = re.compile(r"<[^>]+>")
WP_TAIL = re.compile(r"The post .{0,300}? appeared first on .*$", re.S)


def clean(text, words=None):
    text = html.unescape(TAG.sub(" ", text or ""))
    text = WP_TAIL.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    if words:
        parts = text.split(" ")
        if len(parts) > words:
            text = " ".join(parts[:words]) + "…"
    return text


def local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_feed(raw):
    """RSS 2.0 or Atom → list of {title, link, summary}."""
    root = ET.fromstring(raw)
    items = []
    for el in root.iter():
        name = local(el.tag)
        if name not in ("item", "entry"):
            continue
        f = {"title": "", "link": "", "summary": ""}
        for child in el:
            c = local(child.tag)
            if c == "title":
                f["title"] = child.text or ""
            elif c == "link":
                f["link"] = child.get("href") or (child.text or "")
            elif c in ("description", "summary") and not f["summary"]:
                f["summary"] = child.text or ""
            elif c == "content" and not f["summary"]:
                f["summary"] = child.text or ""
        if f["title"] and f["link"]:
            items.append(f)
    return items


# Read by people recovering from critical illness, and by staff mid-shift.
# Applied to every item before anything else, so it holds even when Gemini is
# unavailable. Whole words only ("war" must not catch "warn" or "Warwick").
DISTRESSING = re.compile(
    r"\b(kill|kills|killed|killing|killer|murder\w*|manslaughter|death|deaths|dead|died|dies|dying|"
    r"fatal\w*|rape\w*|sexual\w*|assault\w*|abus\w*|stab|stabbed|stabbing\w*|shooting\w*|"
    r"shot|bomb\w*|attack\w*|terror\w*|war|wars|massacre\w*|genocide|executed|execution\w*|"
    r"assassinat\w*|suicide\w*|overdose\w*|court|courts|trial|jailed|sentenced|charged|guilty|"
    r"inquest\w*|crash\w*|collision\w*|injured|injuries|disaster\w*|earthquake\w*|hostage\w*|"
    r"missing)\b", re.I)


def fetch_feeds(feeds, prefix):
    out = []
    skipped = 0
    for n, feed in enumerate(feeds):
        raw = None
        for attempt in (1, 2):
            try:
                raw = http_get(feed["url"])
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    log(f"feed down: {feed['name']} ({e})", warn=True)
                else:
                    time.sleep(3)
        if raw is None:
            continue
        try:
            items = parse_feed(raw)
        except ET.ParseError as e:
            log(f"feed unreadable: {feed['name']} ({e})", warn=True)
            continue
        for i, it in enumerate(items[: feed["take"]]):
            title, summary = clean(it["title"]), clean(it["summary"], words=40)
            if DISTRESSING.search(title + " " + summary):
                skipped += 1
                continue
            out.append({
                "id": f"{prefix}-{n}-{i}",
                "source": feed["name"],
                "url": it["link"].strip(),
                "title": title,
                "summary": summary,
            })
    if skipped:
        log(f"{prefix}: left out {skipped} distressing item(s)")
    return out


# --------------------------------------------------------------- weather ----

SKY = {0: "clear", 1: "mostly sunny", 2: "cloudy in places", 3: "overcast",
       45: "foggy", 48: "foggy", 51: "drizzly", 53: "drizzly", 55: "drizzly",
       61: "light rain", 63: "raining", 65: "heavy rain", 71: "snowing",
       73: "snowing", 75: "heavy snow", 80: "showery", 81: "showery",
       82: "heavy showers", 95: "thundery"}


def weather(lat, lon):
    """Return {temp, sky, high, sunrise, sunset} or None. Never a made-up 0."""
    url = ("https://api.open-meteo.com/v1/forecast"
           f"?latitude={lat}&longitude={lon}"
           "&current=temperature_2m,weather_code"
           "&daily=temperature_2m_max,sunrise,sunset"
           "&timezone=Europe%2FLondon&forecast_days=1")
    d = get_json(url)
    if not d:
        return None
    try:
        cur, day = d["current"], d["daily"]
        temp = cur["temperature_2m"]
        high = day["temperature_2m_max"][0]
        if not isinstance(temp, (int, float)) or not isinstance(high, (int, float)):
            raise ValueError("no temperature in reply")
        return {
            "temp": round(temp),
            "sky": SKY.get(cur.get("weather_code"), "changeable"),
            "high": round(high),
            "sunrise": day["sunrise"][0][11:16],
            "sunset": day["sunset"][0][11:16],
        }
    except (KeyError, IndexError, TypeError, ValueError) as e:
        log(f"weather reply incomplete ({e}): {str(d)[:160]}", warn=True)
        return None


# ----------------------------------------------------------- on this day ----

def on_this_day(day):
    d = get_json(f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/selected/{day:%m}/{day:%d}")
    if not d or not d.get("selected"):
        return None
    best = None
    for e in d["selected"]:
        text = (e.get("text") or "").strip()
        if not e.get("year") or not text or len(text) > 190 or DISTRESSING.search(text):
            continue
        page = (e.get("pages") or [{}])[0]
        item = {
            "text": f"{e['year']} — {text.rstrip('.')}.",  # keeps names capitalised
            "url": page.get("content_urls", {}).get("desktop", {}).get("page", ""),
            "photo": bool(page.get("thumbnail", {}).get("source")),
        }
        if item["photo"] and item["url"]:
            return item
        best = best or item
    return best


# ------------------------------------------------------------- the model ----

DEADLINE = [None]  # whole-build time budget for model calls
OUT_OF_QUOTA = set()  # models that said "limit reached" — skipped for the rest of the build


def ask(heads, date, edition, count, scope):
    items = "".join(f"ID {h['id']} | {h['source']}\n  {h['title']}\n  {h['summary']}\n\n" for h in heads)
    return f"""You are selecting news for a sheet read by intensive care nurses in East Kent.

TODAY IS {date}. This is the {edition} edition. You are choosing {scope}.

Below are today's items. THESE ARE THE ONLY FACTS YOU HAVE.

{items}RULES — absolute:
1. Use ONLY information present in the item you are summarising. Add nothing.
2. Never introduce a number, date, name, place or figure not in the source text.
3. Never combine two items into one story.
4. If an item is too thin for two sentences, write one.
5. If you are unsure what an item means, do not select it.
6. Return each ID exactly as given.
7. Skip anything violent, distressing or about death — these are read by people
   recovering from critical illness, and by staff mid-shift.

Choose {count} items. Weigh each 1-4:
  4 changes lives at scale, or is what everyone is talking about
  3 substantial, still matters in a month
  2 ordinary significance
  1 small, pleasant, colour
Prefer a constructive outcome only when two stories weigh the same. Never
manufacture cheer.

Rewrite each in at most two plain sentences. Past tense. No adjectives telling the
reader how to feel.

Also choose ONE item as "event_id" — the single thing most worth a patient knowing
happened today — and give "event_phrase": at most 10 words, plain, naming it.
Example: "Spain won the World Cup". No date, no weather, no full stop.

Return JSON only:
{{"stories":[{{"id":"","headline":"","body":"","weight":0}}],"event_id":"","event_phrase":""}}
"""


def model(prompt, models):
    """Ask Gemini. Tries each model in turn; busy servers (429/500/503) get
    patient retries, a wrong model name (404) moves straight to the next one."""
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    if DEADLINE[0] is None:
        DEADLINE[0] = time.time() + 420
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }).encode()
    problems = []
    for name in [m for m in models if m and m not in OUT_OF_QUOTA]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{name}:generateContent"
        for attempt, wait in enumerate((8, 16, 30, 0), start=1):
            if time.time() > DEADLINE[0]:
                log("model: out of time, using publishers' wording", warn=True)
                return None
            try:
                raw = http_get(url, {"Content-Type": "application/json", "x-goog-api-key": key},
                               data=body, timeout=60)
                outer = json.loads(raw)
                data = json.loads(outer["candidates"][0]["content"]["parts"][0]["text"])
                if data.get("stories"):
                    if problems:
                        log(f"model: {name} answered after: {'; '.join(problems)}")
                    return data
                problems.append(f"{name} returned no stories")
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    # Limit reached. Retrying only burns more allowance.
                    OUT_OF_QUOTA.add(name)
                    problems.append(f"{name} limit reached (HTTP 429)")
                    break
                if e.code in (500, 503) and wait:
                    log(f"{name} busy (HTTP {e.code}), retrying in {wait}s")
                    time.sleep(wait)
                    continue
                problems.append(f"{name} HTTP {e.code}")
                break
            except Exception as e:  # noqa: BLE001 — timeouts, odd replies
                if wait:
                    time.sleep(wait)
                    continue
                problems.append(f"{name} error: {e}")
                break
    log("model unavailable — " + ("; ".join(problems) or "all models over their limit"), warn=True)
    return None


NUM = re.compile(r"\d[\d,.]*")


def grounded(text, corpus):
    return all(n.strip(".,").lower() in corpus for n in NUM.findall(text or ""))


def verify(data, heads):
    """Drop stories with unknown IDs; revert any story whose figures aren't in its source."""
    by_id = {h["id"]: h for h in heads}
    clean_stories = []
    for s in data.get("stories", []):
        src = by_id.get(s.get("id", ""))
        if not src:
            log("dropped a story with an unknown ID", warn=True)
            continue
        corpus = (src["title"] + " " + src["summary"]).lower()
        headline, body = s.get("headline") or src["title"], s.get("body") or src["summary"]
        if not (grounded(headline, corpus) and grounded(body, corpus)):
            log(f"ungrounded figure in '{headline[:50]}' — used publisher's wording", warn=True)
            headline, body = src["title"], src["summary"]
        try:
            weight = max(1, min(4, int(s.get("weight", 2))))
        except (TypeError, ValueError):
            weight = 2
        clean_stories.append({"headline": headline, "body": body, "weight": weight,
                              "source": src["source"], "url": src["url"]})
    data["stories"] = clean_stories

    # The event phrase goes into a handwritten diary: it must point at a real item,
    # be short, and contain no figure that item doesn't have.
    ev = by_id.get(data.get("event_id", ""))
    phrase = (data.get("event_phrase") or "").strip().rstrip(".")
    if not ev or not phrase or len(phrase.split()) > 12 or \
            not grounded(phrase, (ev["title"] + " " + ev["summary"]).lower()):
        phrase = ""
    data["event_phrase"] = phrase
    return data


def fallback(heads, n):
    return {"stories": [{"headline": h["title"], "body": h["summary"], "weight": None,
                         "source": h["source"], "url": h["url"]} for h in heads[:n]],
            "event_phrase": ""}


def select(heads, date, edition, count, n, scope, models):
    if not heads:
        return {"stories": [], "event_phrase": ""}, False
    got = model(ask(heads, date, edition, count, scope), models)
    if got:
        checked = verify(got, heads)
        if checked["stories"]:
            return checked, True
        log(f"{scope}: nothing survived checking — used publishers' wording", warn=True)
    return fallback(heads, n), False


def copy_line(day, phrase, wx):
    line = f"{day:%A} {day.day} {day:%B}."
    if wx:
        line += f" {wx['sky'].capitalize()} outside, about {wx['temp']} degrees."
    if phrase:
        line += f" {phrase}."
    return line


# ------------------------------------------------------------- rendering ----

def story_html(s):
    bars = ""
    if s.get("weight"):
        w = s["weight"]
        bars = f'<p class="bars" aria-label="weight {w} of 4">{"&#9632;" * w}{"&#9633;" * (4 - w)}</p>'
    return (f'<article><h3>{esc(s["headline"])}</h3>{bars}<p>{esc(s["body"])}</p>'
            f'<p class="src"><a href="{esc(s["url"])}" target="_blank" rel="noopener">'
            f'{esc(s["source"])}</a></p></article>')


def render(cfg, b, base):
    """b = everything the build gathered. base = '' for the live page, '../' inside archive/."""
    day = b["day"]
    dow = day.weekday()
    prompt = cfg["prompts"][DAYS[dow]]
    qs = "".join(f"<li>{esc(q)}</li>" for q in prompt["questions"])
    chips = ""
    for i in range(1, 7):
        d = (dow + i) % 7
        chips += (f'<div class="chip"><b>{DAYS[d][:3].title()}</b>'
                  f'<span>{esc(cfg["prompts"][DAYS[d]]["theme"])}</span></div>')
    anyday = cfg["any_day"]
    chips += (f'<div class="chip any"><b>Any day</b><span>{esc(anyday["theme"])}</span>'
              f'<em>{esc(anyday["note"])}</em></div>')

    otd = b["otd"]
    if otd:
        otd_html = esc(otd["text"])
        if otd["url"]:
            otd_html += (f' <a href="{esc(otd["url"])}" target="_blank" rel="noopener">'
                         f'{"See it" if otd["photo"] else "Read more"} &rarr;</a>')
    else:
        otd_html = "Unavailable today."

    shared_html = "".join(story_html(s) for s in b["shared"]) or '<p class="none">No national items this edition.</p>'
    archive = base + "archive/"

    tabs, panels = "", ""
    for key, s in b["sites"].items():
        m, wx = s["meta"], s["wx"]
        wxline = (f'{wx["temp"]}&deg;C, {esc(wx["sky"])}, high {wx["high"]}&deg;C. Sunset {wx["sunset"]}.'
                  if wx else "Weather unavailable.")
        tabs += (f'<button class="tab" data-site="{esc(key)}">{esc(m["label"])} '
                 f'<em>{esc(m["unit"])}</em></button>')
        local_html = "".join(story_html(x) for x in s["local"]) or '<p class="none">No local items today.</p>'
        panels += f"""<div class="panel" data-site="{esc(key)}">
<div class="copy"><span class="lab">Copy into the diary</span><p>{esc(s["copy"])}</p></div>
<div class="grid">
<section><h2>Today&rsquo;s prompt</h2>
<div class="today"><p class="d">{DAYS[dow][:3].title()}</p><h3>{esc(prompt["theme"])}</h3><ul>{qs}</ul></div>
<div class="chips">{chips}</div>
<p class="standing">{cfg["standing_note"]}</p></section>
<section><h2>{esc(m["label"])} and around</h2>{local_html}
<h2 class="gap">Britain and the world</h2>{shared_html}</section>
<section class="rail"><h2><a href="{archive}">Past editions &rarr;</a></h2>
<div><h4>Outside in {esc(m["label"])}</h4><p>{wxline}</p></div>
<div><h4>On this day</h4><p>{otd_html}</p></div></section>
</div></div>
"""

    first = next(iter(b["sites"]))
    sun = b["sites"][first]["wx"] or {"sunrise": "07:00", "sunset": "17:00"}
    sources = sorted({f["name"] for s in cfg["sites"].values() for f in s["feeds"]})
    summ = ("Summaries are generated automatically from those reports."
            if b["used_model"] else "Summaries are the publishers' own wording.")
    date_str = f"{day:%A} {day.day} {day:%B %Y}"
    sites_js = json.dumps(list(b["sites"].keys()))

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="light"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{esc(cfg["title"])} — {date_str}</title>
<style>{CSS}</style></head><body><div class="shell">

<header><h1>{esc(cfg["title"])}</h1><div class="stamp">{date_str} &nbsp;&middot;&nbsp;
<b>{b["edition"]} edition, updated {b["built"]:%H:%M}</b> &nbsp;&middot;&nbsp;
Writing after midnight? Date it tomorrow &nbsp;<button id="tog" type="button">Switch to dark</button></div></header>

<nav class="tabs">{tabs}</nav>
{panels}
<footer><span>Local news from {esc(", ".join(sources))}.
National and world news from BBC News. Anniversaries from Wikipedia. Weather from Open-Meteo.
Each story links to the original report. {summ}</span>
</footer>

</div><script>
const SUNSET="{sun["sunset"]}",SUNRISE="{sun["sunrise"]}",SITES={sites_js};
const mins=s=>(+s.slice(0,2))*60+(+s.slice(3,5));
const night=()=>{{const n=new Date(),m=n.getHours()*60+n.getMinutes();return m>=mins(SUNSET)||m<mins(SUNRISE);}};
// A manual choice is remembered for 12 hours (it survives the half-hourly
// reload), then the page goes back to following sunrise and sunset.
const KEY="itu-theme";
function saved(){{try{{const s=JSON.parse(localStorage.getItem(KEY)||"null");
  return s&&Date.now()-s.t<12*3600e3?s.dark:null;}}catch(e){{return null;}}}}
let manual=saved();
function apply(){{const d=manual!==null?manual:night();
  document.documentElement.dataset.theme=d?"dark":"light";
  document.getElementById("tog").textContent=d?"Switch to light":"Switch to dark";}}
document.getElementById("tog").onclick=()=>{{manual=document.documentElement.dataset.theme!=="dark";
  try{{localStorage.setItem(KEY,JSON.stringify({{dark:manual,t:Date.now()}}));}}catch(e){{}}apply();}};
apply();setInterval(apply,120000);
function show(site){{
  document.querySelectorAll(".panel").forEach(p=>p.hidden=p.dataset.site!==site);
  document.querySelectorAll(".tab").forEach(t=>t.classList.toggle("on",t.dataset.site===site));
  if(history.replaceState)history.replaceState(null,"","#"+site);}}
document.querySelectorAll(".tab").forEach(t=>t.onclick=()=>show(t.dataset.site));
const want=location.hash.slice(1);show(SITES.includes(want)?want:SITES[0]);
// The page rebuilds three times a day; pick up a new edition if left open.
setInterval(()=>{{if(!document.hidden)location.reload();}},30*60*1000);
</script></body></html>
"""


def archive_index(cfg, dates):
    rows = "".join(
        f'<li><a href="{d}.html">{datetime.strptime(d, "%Y-%m-%d"):%A} '
        f'{int(d[8:])} {datetime.strptime(d, "%Y-%m-%d"):%B %Y}</a></li>'
        for d in dates) or "<li>Nothing archived yet.</li>"
    return f"""<!DOCTYPE html><html lang="en" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Past editions — {esc(cfg["title"])}</title><style>{CSS}
.arclist{{columns:3;column-gap:40px;margin:20px 0 0;padding:0;list-style:none;max-width:900px}}
.arclist li{{padding:5px 0;font-size:16px;break-inside:avoid}}
.arclist a{{color:var(--blue);text-decoration:none}}.arclist a:hover{{text-decoration:underline}}
@media(max-width:820px){{.arclist{{columns:1}}}}</style></head><body><div class="shell">
<header><h1>Past editions</h1><div class="stamp"><a href="../" style="color:var(--blue)">&larr; Back to today</a></div></header>
<ul class="arclist">{rows}</ul></div></body></html>
"""


# ------------------------------------------------------------------ build ----

def pick_edition(cfg, now, forced):
    """Return (edition, anchor datetime) or (None, None) if it isn't an edition time.

    GitHub's clock is UTC, so the workflow runs at both possible UTC times and this
    decides which run is the real one for the UK clock (GMT or BST)."""
    def anchor(t, day):
        h, m = map(int, t.split(":"))
        return datetime(day.year, day.month, day.day, h, m, tzinfo=LONDON)

    if forced:
        if forced not in cfg["editions"]:
            sys.exit(f"Unknown edition '{forced}'. Choose from: {', '.join(cfg['editions'])}")
        return forced, now

    # The newest edition that is due. Runs are scheduled a few minutes early, so an
    # edition counts as due from 15 minutes before its time. A run that GitHub starts
    # late — even hours late — still builds the edition it was meant to.
    due = []
    for name, t in cfg["editions"].items():
        for day in (now.date(), now.date() - timedelta(days=1)):
            a = anchor(t, day)
            if a - timedelta(minutes=15) <= now:
                due.append((a, name))
    if not due:
        return None, None
    a, name = max(due)
    return name, a


def edition_key(edition, anchor):
    return f"{anchor:%Y-%m-%d} {edition}"


def already_built(key):
    try:
        s = json.loads((OUT / "status.json").read_text(encoding="utf-8"))
        return s.get("ok") and s.get("key") == key
    except (OSError, ValueError):
        return False


SEEN_FILE = "seen.json"   # in docs/: which stories appeared on which day
SEEN_DAYS = 4


def load_seen(day):
    """{url: 'YYYY-MM-DD'} for stories shown recently, trimmed to the last few days."""
    cutoff = (day - timedelta(days=SEEN_DAYS)).isoformat()
    try:
        seen = json.loads((OUT / SEEN_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # First run with this feature: learn from the archived editions instead.
        seen = {}
        for page in sorted((OUT / "archive").glob("????-??-??.html")):
            if page.stem >= cutoff:
                for u in re.findall(r'class="src"><a href="([^"]+)"', page.read_text(encoding="utf-8")):
                    seen.setdefault(html.unescape(u), page.stem)
    return {u: d for u, d in seen.items() if d >= cutoff}


def prefer_fresh(heads, seen, day, need, label):
    """Stories shown on an EARLIER day go to the back of the queue. They are only
    offered again if there are not enough new ones to fill the section. Stories
    already shown today stay eligible, so one day's editions can share a big story."""
    today = day.isoformat()
    fresh = [h for h in heads if seen.get(h["url"], today) >= today]
    stale = [h for h in heads if seen.get(h["url"], today) < today]
    if stale:
        log(f"{label}: {len(stale)} story(ies) already shown on an earlier day"
            + ("" if len(fresh) >= need else f" — only {len(fresh)} new, so some may repeat"))
    return fresh if len(fresh) >= need else fresh + stale


def build(cfg, edition, anchor):
    day = anchor.date()
    seen = load_seen(day)
    date_str = f"{day:%A} {day.day} {day:%B %Y}"
    models = [os.environ.get("GEMINI_MODEL") or cfg["model"], cfg.get("fallback_model")]
    log(f"building {edition} edition for {date_str}"
        + ("" if os.environ.get("GEMINI_API_KEY") else " (no Gemini key — publishers' wording)"))

    shared_heads = fetch_feeds(cfg["shared_feeds"], "sh")
    if not shared_heads:
        log("ABORT — every national feed failed; leaving the last edition up", warn=True)
        return None
    shared_heads = prefer_fresh(shared_heads, seen, day, 4, "national")
    shared, used = select(shared_heads, date_str, edition, "FOUR", 4, "UK and world news", models)
    used_model = used

    otd = on_this_day(day)
    sites = {}
    for key, site in cfg["sites"].items():
        heads = fetch_feeds(site["feeds"], key)
        if not heads:
            log(f"no local headlines for {site['label']}", warn=True)
        heads = prefer_fresh(heads, seen, day, 3, site["label"])
        local_sel, used = select(heads, date_str, edition, "THREE", 3,
                                 f"local news for {site['label']}", models)
        used_model = used_model or used
        wx = weather(site["lat"], site["lon"])
        sites[key] = {"meta": site, "local": local_sel["stories"], "wx": wx,
                      "copy": copy_line(day, shared.get("event_phrase", ""), wx)}

    return {"day": day, "edition": edition, "built": datetime.now(LONDON),
            "shared": shared["stories"], "sites": sites, "otd": otd,
            "used_model": used_model}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edition", help="morning / afternoon / night (default: decide from the UK clock)")
    args = ap.parse_args()

    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    now = datetime.now(LONDON)
    forced = (args.edition or "").strip().lower() or None
    if forced == "auto":
        forced = None
    edition, anchor = pick_edition(cfg, now, forced)
    if not edition:
        log("no edition due yet — nothing to do")
        return 0
    # A hand-picked edition (a test, say) must not stop the real one being built later.
    key = "manual" if forced else edition_key(edition, anchor)
    if not forced and already_built(key):
        log(f"the {edition} edition for {anchor:%a %d %b} is already up — nothing to do")
        return 0

    b = build(cfg, edition, anchor)
    if not b:
        write_status(edition, key, ok=False)
        return 1

    (OUT / "archive").mkdir(parents=True, exist_ok=True)
    (OUT / ".nojekyll").touch()
    (OUT / "index.html").write_text(render(cfg, b, ""), encoding="utf-8")
    (OUT / "archive" / f"{b['day']:%Y-%m-%d}.html").write_text(render(cfg, b, "../"), encoding="utf-8")

    keep = cfg.get("archive_days", 365)
    dates = sorted((p.stem for p in (OUT / "archive").glob("????-??-??.html")), reverse=True)
    for old in dates[keep:]:
        (OUT / "archive" / f"{old}.html").unlink()
    (OUT / "archive" / "index.html").write_text(archive_index(cfg, dates[:keep]), encoding="utf-8")

    shown = [s["url"] for s in b["shared"]] + [x["url"] for s in b["sites"].values() for x in s["local"]]
    seen = load_seen(b["day"])
    for u in shown:
        seen.setdefault(u, b["day"].isoformat())
    (OUT / SEEN_FILE).write_text(json.dumps(seen, indent=0, sort_keys=True), encoding="utf-8")

    write_status(edition, key, ok=True, used_model=b["used_model"])
    log(f"done — {len(b['shared'])} national stories, {len(b['sites'])} sites, "
        f"{len(WARNINGS)} warning(s)")
    return 0


def write_status(edition, key, ok, used_model=False):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "status.json").write_text(json.dumps({
        "built": datetime.now(LONDON).isoformat(timespec="minutes"),
        "edition": edition, "key": key, "ok": ok,
        "summaries": "model" if used_model else "publisher",
        "warnings": WARNINGS,
    }, indent=2), encoding="utf-8")


# ------------------------------------------------------------------- CSS ----

CSS = """
:root{--blue:#005EB8;--dark-blue:#003087;--aqua:#00A499;--yellow:#FFB81C;
--black:#231F20;--grey:#425563;--mid:#768692;--pale:#E8EDEE;--paper:#FFFFFF;
--line:#D8DDE0;--faint:#F4F6F7}
html[data-theme="dark"]{--blue:#41B6E6;--dark-blue:#0A1B33;--aqua:#00B3A8;--yellow:#FFB81C;
--black:#EAF0F6;--grey:#A9BBD0;--mid:#7E93AE;--pale:#13284A;--paper:#0A1B33;
--line:#1E3A63;--faint:#0E2240}
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;background:var(--paper);color:var(--black);font-size:16px;line-height:1.45;
font-family:"Frutiger W01",Arial,Helvetica,sans-serif;transition:background .5s,color .5s}
.shell{max-width:1680px;margin:0 auto;padding:0 28px 18px;display:flex;flex-direction:column;min-height:100%}
header{display:flex;align-items:baseline;justify-content:space-between;gap:20px;
border-bottom:4px solid var(--blue);padding:14px 0 10px;flex-wrap:wrap}
h1{font-size:25px;font-weight:700;margin:0;color:var(--dark-blue)}
html[data-theme="dark"] h1{color:var(--blue)}
.stamp{font-size:14px;color:var(--grey);text-align:right;line-height:1.5}
.stamp b{color:var(--blue);text-transform:capitalize}
.tabs{display:flex;gap:2px;margin-top:12px;border-bottom:2px solid var(--line)}
.tab{font:inherit;font-size:16px;font-weight:700;background:var(--faint);border:none;
border-bottom:4px solid transparent;color:var(--grey);padding:10px 20px;cursor:pointer;margin-bottom:-2px}
.tab em{font-style:normal;font-weight:400;font-size:13px;color:var(--mid);margin-left:5px}
.tab:hover{color:var(--blue)}
.tab.on{background:var(--paper);color:var(--dark-blue);border-bottom-color:var(--blue)}
html[data-theme="dark"] .tab.on{color:var(--blue)}
.tab:focus-visible{outline:3px solid var(--yellow);outline-offset:-3px}
.panel[hidden]{display:none}
.copy{background:var(--blue);color:#fff;padding:14px 20px;margin:14px 0 16px;
display:flex;align-items:baseline;gap:20px;flex-wrap:wrap}
html[data-theme="dark"] .copy{background:#005EB8}
.copy .lab{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:1.2px;opacity:.9;white-space:nowrap}
.copy p{margin:0;font-size:25px;font-weight:600;line-height:1.35;flex:1;min-width:260px}
.grid{display:grid;grid-template-columns:1.15fr 1.25fr .78fr;gap:26px;align-items:start}
h2{font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:1.4px;color:var(--blue);
margin:0 0 9px;padding-bottom:6px;border-bottom:2px solid var(--line)}
h2.gap{margin-top:16px}
h2 a{color:inherit;text-decoration:none}
h2 a:hover{border-bottom:1px solid var(--blue)}
.today{background:var(--pale);border-left:5px solid var(--aqua);padding:13px 16px 15px;margin-bottom:12px}
.today .d{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1.2px;color:var(--aqua);margin:0 0 3px}
.today h3{font-size:21px;font-weight:700;margin:0 0 8px}
.today ul{margin:0;padding:0;list-style:none}
.today li{font-size:17px;line-height:1.45;padding:4px 0 4px 16px;position:relative}
.today li::before{content:"";position:absolute;left:0;top:13px;width:8px;height:2px;background:var(--aqua)}
.chips{display:grid;grid-template-columns:1fr 1fr;gap:7px}
.chip{background:var(--faint);border-left:3px solid var(--line);padding:7px 10px}
.chip b{display:block;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--mid)}
.chip span{display:block;font-size:14px;font-weight:600;color:var(--grey);line-height:1.3;margin-top:1px}
.chip.any{grid-column:1/-1;border-left-color:var(--yellow)}
.chip.any b{color:#8a6200}html[data-theme="dark"] .chip.any b{color:var(--yellow)}
.chip.any em{display:block;font-style:normal;font-size:13px;color:var(--mid);margin-top:2px}
.standing{font-size:14px;line-height:1.5;color:var(--grey);margin:11px 0 0;padding-top:9px;border-top:1px solid var(--line)}
article{margin-bottom:13px}
article h3{font-size:18px;font-weight:700;margin:0 0 2px;line-height:1.3;color:var(--dark-blue)}
html[data-theme="dark"] article h3{color:var(--blue)}
.bars{font-size:11px;letter-spacing:3px;color:var(--blue);margin:0 0 3px}
article p{margin:0;font-size:16px;line-height:1.5}
.none{color:var(--mid);font-size:15px;margin:0 0 12px}
.src{margin-top:3px !important;font-size:12px !important}
.src a{color:var(--mid);text-decoration:none;border-bottom:1px solid var(--line)}
.src a:hover{color:var(--blue);border-color:var(--blue)}
.rail>div{margin-bottom:13px}
.rail h4{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1.2px;color:var(--blue);margin:0 0 3px}
.rail p{margin:0;font-size:15px;line-height:1.45}
.rail p a{color:var(--blue);text-decoration:none;font-weight:600;white-space:nowrap}
.rail p a:hover{text-decoration:underline}
footer{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;border-top:1px solid var(--line);
margin-top:auto;padding-top:9px;font-size:13px;color:var(--mid)}
#tog{font:inherit;font-size:13px;background:none;border:1px solid var(--line);color:var(--grey);padding:3px 10px;cursor:pointer;margin-left:6px;white-space:nowrap}
#tog:hover{border-color:var(--blue);color:var(--blue)}
#tog:focus-visible{outline:3px solid var(--yellow);outline-offset:1px}
@media(max-width:1200px){.grid{grid-template-columns:1fr 1fr;gap:20px}.rail{grid-column:1/-1}}
@media(max-width:820px){.shell{padding:0 16px 16px}.grid{grid-template-columns:1fr}.copy p{font-size:21px}
h1{font-size:21px}.stamp{text-align:left}.tabs{flex-wrap:wrap}.tab{padding:9px 14px;font-size:15px}}
@media(prefers-reduced-motion:reduce){body{transition:none}}
"""

if __name__ == "__main__":
    sys.exit(main())
