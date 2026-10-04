#!/usr/bin/env python3
"""Job Watch: checks company job boards and shows every opening on one page.

Run:  python3 jobwatch.py            (fetch, build jobs.html, open it)
      python3 jobwatch.py --no-open  (fetch and build only)

Companies live in companies.json. Each entry needs a "name" and the "url" of
the company's job board. Supported boards are detected from the URL:
  Greenhouse     https://boards.greenhouse.io/<board>   (or job-boards.greenhouse.io)
  Lever          https://jobs.lever.co/<company>
  Ashby          https://jobs.ashbyhq.com/<company>
  Workday        https://<tenant>.wd5.myworkdayjobs.com/<site>
  SmartRecruiters https://jobs.smartrecruiters.com/<company>
  Paylocity      https://recruiting.paylocity.com/Recruiting/Jobs/All/<id>
  ADP            the Workforce Now career center link (has cid= in it)
  GovernmentJobs a governmentjobs.com search link (sort by date)
  RSS / Atom     any feed URL (set "type": "rss" if it isn't auto-detected)

Only the Python 3 standard library is used, so there is nothing to install.
"""

import html
import json
import re
import sys
import webbrowser
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
COMPANIES_FILE = HERE / "companies.json"
SEEN_FILE = HERE / "seen.json"
OUTPUT_FILE = HERE / "jobs.html"
NEW_DAYS = 7  # jobs first seen within this many days get a "New" badge
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


# ---------------------------------------------------------------- fetching

def http(url, body=None, headers=None):
    headers = {**(headers or {}), "User-Agent": USER_AGENT, "Accept": "application/json, application/xml, */*"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    with urlopen(Request(url, data=data, headers=headers), timeout=30) as resp:
        return resp.read()


def get_json(url, body=None):
    return json.loads(http(url, body))


def parse_date(value):
    """Turn the many date formats boards use into an aware datetime, or None."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):  # epoch milliseconds (Lever)
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    value = str(value).strip()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(value)  # RSS style
    except (TypeError, ValueError):
        pass
    # Workday style: "Posted Today", "Posted Yesterday", "Posted 3 Days Ago", "Posted 30+ Days Ago"
    low = value.lower()
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    if "today" in low:
        return today
    if "yesterday" in low:
        return today - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*day", low)
    if m:
        return today - timedelta(days=int(m.group(1)))
    return None


def job(company, title, url, posted=None, location=""):
    return {
        "company": company,
        "title": (title or "").strip(),
        "url": url,
        "location": (location or "").strip(),
        "posted": posted.isoformat() if posted else None,
    }


def fetch_greenhouse(c, board):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs")
    return [
        job(c, j.get("title"), j.get("absolute_url"),
            parse_date(j.get("first_published") or j.get("updated_at")),
            (j.get("location") or {}).get("name", ""))
        for j in data.get("jobs", [])
    ]


def fetch_lever(c, company):
    data = get_json(f"https://api.lever.co/v0/postings/{company}?mode=json")
    return [
        job(c, j.get("text"), j.get("hostedUrl"), parse_date(j.get("createdAt")),
            (j.get("categories") or {}).get("location", ""))
        for j in data
    ]


def fetch_ashby(c, board):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}")
    return [
        job(c, j.get("title"), j.get("jobUrl"), parse_date(j.get("publishedAt")), j.get("location", ""))
        for j in data.get("jobs", [])
        if j.get("isListed", True)
    ]


def fetch_smartrecruiters(c, company):
    data = get_json(f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100")
    out = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        out.append(job(c, j.get("name"), f"https://jobs.smartrecruiters.com/{company}/{j.get('id')}",
                       parse_date(j.get("releasedDate")),
                       ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)))
    return out


def fetch_workday(c, url):
    # https://<tenant>.wd5.myworkdayjobs.com/[en-US/]<site>  ->  /wday/cxs/<tenant>/<site>/jobs
    p = urlparse(url)
    tenant = p.netloc.split(".")[0]
    parts = [s for s in p.path.split("/") if s and not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", s)]
    if not parts:
        raise ValueError("Workday URL needs the site name after the domain")
    site = parts[0]
    base = f"{p.scheme}://{p.netloc}"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    out, offset = [], 0
    while offset < 500:  # newest postings come first; 500 is plenty
        data = get_json(api, {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""})
        postings = data.get("jobPostings", [])
        for j in postings:
            out.append(job(c, j.get("title"), f"{base}/{site}{j.get('externalPath', '')}",
                           parse_date(j.get("postedOn")), j.get("locationsText", "")))
        offset += 20
        if len(postings) < 20 or offset >= data.get("total", 0):
            break
    return out


def fetch_paylocity(c, url):
    # The public job list page embeds its data as "pageData = {...};"
    page = http(url).decode("utf-8", "replace")
    m = re.search(r"pageData\s*=\s*(\{.*?\});\s*$", page, re.M)
    if not m:
        raise ValueError("job data not found on the Paylocity page")
    out = []
    for j in json.loads(m.group(1)).get("Jobs", []):
        if j.get("IsInternal"):
            continue
        loc = j.get("JobLocation") or {}
        where = ", ".join(x for x in (loc.get("City"), loc.get("State")) if x) or j.get("LocationName", "")
        out.append(job(c, j.get("JobTitle"), f"https://recruiting.paylocity.com/Recruiting/Jobs/Details/{j.get('JobId')}",
                       parse_date(j.get("PublishedDate")), where))
    return out


def fetch_adp(c, url):
    # ADP Workforce Now career center: .../recruitment.html?cid=...&ccId=...
    p = urlparse(url)
    q = parse_qs(p.query)
    cid, cc_id = q["cid"][0], q.get("ccId", ["19000101_000001"])[0]
    base = f"{p.scheme}://{p.netloc}/mascsr/default"
    api = (f"{base}/careercenter/public/events/staffing/v1/job-requisitions"
           f"?cid={cid}&ccId={cc_id}&lang=en_US&locale=en_US")
    out, skip = [], 0
    while skip < 500:
        data = get_json(f"{api}&$top=50&$skip={skip}")
        reqs = data.get("jobRequisitions", [])
        for j in reqs:
            locs = [((l.get("nameCode") or {}).get("shortName") or "").strip() for l in j.get("requisitionLocations", [])]
            out.append(job(c, j.get("requisitionTitle"),
                           f"{base}/mdf/recruitment/recruitment.html?cid={cid}&ccId={cc_id}&jobId={j.get('itemID')}&lang=en_US",
                           parse_date(j.get("postDate")), "; ".join(x for x in locs if x)))
        skip += 50
        if len(reqs) < 50 or skip >= (data.get("meta") or {}).get("totalNumber", 0):
            break
    return out


def fetch_governmentjobs(c, url, pages=5):
    # Search results come back 10 at a time as an HTML fragment. With sort=date the
    # newest are first, so a few pages covers what's new. The list shows no posting date.
    p = urlparse(url)
    q = {k: v for k, v in parse_qs(p.query).items() if k != "page"}
    out = []
    for page in range(1, pages + 1):
        qs = urlencode({**q, "page": [str(page)]}, doseq=True)
        frag = http(f"https://www.governmentjobs.com/jobs?{qs}", headers={"X-Requested-With": "XMLHttpRequest"})
        frag = frag.decode("utf-8", "replace")
        items = frag.split('<li class="job-item"')[1:]
        for item in items:
            link = re.search(r'class="job-details-link" href="([^"]+)">([^<]+)</a>', item)
            if not link:
                continue
            org = re.search(r'class="primaryInfo job-organization">([^<]*)<', item)
            loc = re.search(r'class="job-location">([^<]*)<', item)
            where = " · ".join(html.unescape(m.group(1).strip()) for m in (org, loc) if m and m.group(1).strip())
            out.append(job(c, html.unescape(link.group(2)), "https://www.governmentjobs.com" + link.group(1), None, where))
        if len(items) < 10:
            break
    return out


def fetch_rss(c, url):
    root = ET.fromstring(http(url))
    out = []
    for item in root.iter():
        tag = item.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        fields = {ch.tag.split("}")[-1]: ch for ch in item}
        link = fields.get("link")
        href = (link.get("href") or link.text) if link is not None else ""
        title = fields.get("title")
        date = next((fields[k].text for k in ("pubDate", "published", "updated", "date") if k in fields), None)
        out.append(job(c, title.text if title is not None else "", (href or "").strip(), parse_date(date)))
    return out


def detect(entry):
    """Return (fetch_function, argument) for a companies.json entry."""
    url = entry["url"].strip().rstrip("/")
    kind = entry.get("type", "").lower()
    p = urlparse(url)
    host, path = p.netloc.lower(), [s for s in p.path.split("/") if s]
    if kind == "rss":
        return fetch_rss, url
    if "greenhouse.io" in host:
        return fetch_greenhouse, path[-1] if "boards" in path else path[0]
    if host == "jobs.lever.co":
        return fetch_lever, path[0]
    if host == "jobs.ashbyhq.com":
        return fetch_ashby, path[0]
    if host.endswith("myworkdayjobs.com"):
        return fetch_workday, url
    if host == "jobs.smartrecruiters.com":
        return fetch_smartrecruiters, path[0]
    if host == "recruiting.paylocity.com":
        return fetch_paylocity, url
    if "workforcenow" in host and host.endswith("adp.com"):
        return fetch_adp, url
    if host.endswith("governmentjobs.com"):
        return fetch_governmentjobs, url
    if kind == "" and re.search(r"(rss|atom|feed|\.xml)", url, re.I):
        return fetch_rss, url
    raise ValueError("unrecognized job board; supported: Greenhouse, Lever, Ashby, Workday, "
                     "SmartRecruiters, Paylocity, ADP, GovernmentJobs, RSS (set \"type\": \"rss\" for feeds)")


def fetch_company(entry):
    name = entry["name"]
    try:
        fn, arg = detect(entry)
        jobs = [j for j in fn(name, arg) if j["title"] and j["url"]]
        keywords = [k.lower() for k in entry.get("keywords", [])]
        if keywords:
            jobs = [j for j in jobs if any(k in j["title"].lower() for k in keywords)]
        return name, jobs, None
    except Exception as e:  # one broken board shouldn't stop the rest
        return name, [], f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------- page

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Watch</title>
<style>
:root {{ --bg:#f7f7f5; --card:#fff; --text:#1d1d1f; --muted:#6b6b70; --line:#e4e4e0; --accent:#2457d6; --new:#1a7f4b; --newbg:#e3f5ea; --err:#b3261e; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#141416; --card:#1d1d20; --text:#ececee; --muted:#9a9aa0; --line:#2d2d31; --accent:#7ea2ff; --new:#6fd39c; --newbg:#163524; --err:#ff8a80; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:24px 16px 48px; }}
h1 {{ margin:0 0 4px; font-size:24px; }}
.sub {{ color:var(--muted); margin:0 0 18px; }}
.bar {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:14px; }}
.bar input, .bar select {{ font:inherit; padding:7px 10px; border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--text); }}
.bar input[type=search] {{ flex:1 1 220px; }}
.bar label {{ white-space:nowrap; display:flex; align-items:center; gap:6px; color:var(--muted); }}
table {{ width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
th, td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ font-size:13px; color:var(--muted); font-weight:600; cursor:pointer; user-select:none; white-space:nowrap; }}
tr:last-child td {{ border-bottom:0; }}
a {{ color:var(--accent); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
.loc {{ display:block; color:var(--muted); font-size:13px; }}
.badge {{ display:inline-block; margin-left:6px; padding:1px 7px; border-radius:999px; font-size:12px; font-weight:600; color:var(--new); background:var(--newbg); }}
.date {{ white-space:nowrap; color:var(--muted); }}
.errors {{ color:var(--err); font-size:14px; margin:0 0 14px; }}
.empty {{ padding:24px; text-align:center; color:var(--muted); }}
@media (max-width:640px) {{ th:nth-child(1), td:nth-child(1) {{ display:none; }} td .co {{ display:block; }} }}
@media (min-width:641px) {{ td .co {{ display:none; }} }}
</style></head>
<body><main>
<h1>Job Watch</h1>
<p class="sub">{total} openings across {companies} companies, {new} new in the last {new_days} days. Updated <time id="upd" datetime="{updated}">{updated}</time>.</p>
{errors}
<div class="bar">
  <input id="q" type="search" placeholder="Filter by title, company, or location">
  <select id="co"><option value="">All companies</option>{options}</select>
  <label><input id="onlynew" type="checkbox"> New only</label>
</div>
<table><thead><tr><th data-k="company">Company</th><th data-k="title">Job title</th><th data-k="date">Date posted</th></tr></thead>
<tbody id="rows">{rows}</tbody></table>
<p class="empty" id="none" hidden>No jobs match.</p>
</main>
<script>
const u=document.getElementById('upd'); u.textContent=new Date(u.dateTime).toLocaleString([], {{dateStyle:'medium', timeStyle:'short'}});
const rows=[...document.querySelectorAll('#rows tr')], q=document.getElementById('q'), co=document.getElementById('co'), nw=document.getElementById('onlynew');
function apply(){{ const t=q.value.toLowerCase(); let n=0;
  rows.forEach(r=>{{ const ok=r.textContent.toLowerCase().includes(t)&&(!co.value||r.dataset.company===co.value)&&(!nw.checked||r.dataset.new==='1'); r.hidden=!ok; n+=ok; }});
  document.getElementById('none').hidden=n>0; }}
[q,co,nw].forEach(el=>el.addEventListener('input',apply));
let dir=-1; document.querySelectorAll('th').forEach(th=>th.onclick=()=>{{ const k=th.dataset.k; dir=-dir;
  rows.sort((a,b)=>a.dataset[k].localeCompare(b.dataset[k])*dir).forEach(r=>r.parentNode.appendChild(r)); }});
</script>
</body></html>
"""


def fmt_date(iso):
    return datetime.fromisoformat(iso).strftime("%b %d, %Y") if iso else "Unknown"


def build_page(jobs, company_names, errors, now):
    cutoff = now - timedelta(days=NEW_DAYS)
    rows, new_count = [], 0
    for j in jobs:
        is_new = datetime.fromisoformat(j["first_seen"]) >= cutoff
        new_count += is_new
        e = html.escape
        sort_date = j["posted"] or j["first_seen"]
        loc = f'<span class="loc">{e(j["location"])}</span>' if j["location"] else ""
        badge = '<span class="badge">New</span>' if is_new else ""
        rows.append(
            f'<tr data-company="{e(j["company"])}" data-title="{e(j["title"].lower())}" '
            f'data-date="{e(sort_date)}" data-new="{int(is_new)}">'
            f'<td>{e(j["company"])}</td>'
            f'<td><span class="loc co">{e(j["company"])}</span>'
            f'<a href="{e(j["url"])}" target="_blank" rel="noopener">{e(j["title"])}</a>{badge}{loc}</td>'
            + (f'<td class="date">{fmt_date(j["posted"])}</td></tr>' if j["posted"] else
             f'<td class="date">{fmt_date(j["first_seen"])}<span class="loc">first seen</span></td></tr>'))
    err_html = ""
    if errors:
        err_html = '<p class="errors">Could not check: ' + "; ".join(
            f"<b>{html.escape(n)}</b> ({html.escape(m)})" for n, m in errors) + "</p>"
    return PAGE.format(
        total=len(jobs), companies=len(company_names), new=new_count, new_days=NEW_DAYS,
        updated=now.isoformat(timespec="seconds"), errors=err_html,
        options="".join(f'<option value="{html.escape(n)}">{html.escape(n)}</option>' for n in sorted(company_names)),
        rows="\n".join(rows))


# ---------------------------------------------------------------- main

def main():
    companies = json.loads(COMPANIES_FILE.read_text())
    seen = json.loads(SEEN_FILE.read_text()) if SEEN_FILE.exists() else {}
    now = datetime.now(timezone.utc)
    first_run = not seen

    print(f"Checking {len(companies)} companies...")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch_company, companies))

    all_jobs, errors = [], []
    for name, jobs, err in results:
        if err:
            errors.append((name, err))
            print(f"  ! {name}: {err}")
            continue
        print(f"  {name}: {len(jobs)} jobs")
        for j in jobs:
            # On the very first run, back-date first_seen to the posting date so
            # only genuinely recent postings show as new.
            default = (j["posted"] or now.isoformat()) if first_run else now.isoformat()
            j["first_seen"] = seen.setdefault(j["url"], default)
            all_jobs.append(j)

    all_jobs.sort(key=lambda j: j["posted"] or j["first_seen"], reverse=True)
    SEEN_FILE.write_text(json.dumps(seen, indent=1))
    OUTPUT_FILE.write_text(build_page(all_jobs, [c["name"] for c in companies], errors, now), encoding="utf-8")
    print(f"Wrote {OUTPUT_FILE}")
    if "--no-open" not in sys.argv:
        webbrowser.open(OUTPUT_FILE.as_uri())


if __name__ == "__main__":
    main()
