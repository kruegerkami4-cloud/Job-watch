# Job Watch

Checks your target companies' job boards and lists every opening on one page:
company, job title, date posted, and a link to the posting (where you apply).
Jobs that appeared in the last 7 days get a green **New** badge.

## Run it

It runs on GitHub every morning and publishes the page with GitHub Pages. To run it yourself instead, you need Python 3 (already on Mac and most Linux; on Windows install it from python.org).

```
python3 jobwatch.py
```

It checks every company, writes `jobs.html` next to the script, and opens it in your browser.
On Windows use `python jobwatch.py`.

## Add your companies

Edit `companies.json`. Each company needs a name and its job board URL:

```json
{"name": "Stripe", "url": "https://boards.greenhouse.io/stripe"}
```

Supported job boards (detected from the URL):

| Board | URL looks like |
|---|---|
| Greenhouse | `https://boards.greenhouse.io/<company>` or `job-boards.greenhouse.io/<company>` |
| Lever | `https://jobs.lever.co/<company>` |
| Ashby | `https://jobs.ashbyhq.com/<company>` |
| Workday | `https://<company>.wd5.myworkdayjobs.com/<SiteName>` |
| SmartRecruiters | `https://jobs.smartrecruiters.com/<company>` |
| Paylocity | `https://recruiting.paylocity.com/Recruiting/Jobs/All/<id>` |
| ADP Workforce Now | the career center link (contains `cid=`) |
| GovernmentJobs | a governmentjobs.com search link, sorted by date |
| RSS / Atom feed | any feed URL; add `"type": "rss"` if it isn't detected |

Tip: on a company's careers page, click into any job. The address bar usually shows which board it uses.

Optional: add `"keywords": ["analyst", "marketing"]` to a company to only keep titles containing one of those words.

## Files

- `jobwatch.py` the app (standard library only, nothing to install)
- `companies.json` your company list
- `seen.json` created on first run; remembers when each job was first seen, so "New" works
- `jobs.html` the page it builds
