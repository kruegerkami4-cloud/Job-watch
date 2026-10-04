import json, os, urllib.request
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36", "Accept": "*/*"}
ADP = "cid=0acf2e94-7b5c-4b6c-a4f0-cc1f73bdb99f&ccId=19000101_000001&lang=en_US&locale=en_US"
urls = {
 "paylocity_html": ("https://recruiting.paylocity.com/Recruiting/Jobs/All/6dd17de9-20fb-4deb-bf57-c0a6116e169b", {}),
 "paylocity_feed": ("https://recruiting.paylocity.com/recruiting/v2/api/feed/jobs/6dd17de9-20fb-4deb-bf57-c0a6116e169b", {}),
 "adp_cloud": (f"https://workforcenow.cloud.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions?{ADP}&$top=20", {}),
 "adp_plain": (f"https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions?{ADP}&$top=20", {}),
 "govjobs_ajax": ("https://www.governmentjobs.com/jobs?page=1&location=54016&distance=25&sort=date&isDescendingSort=True", {"X-Requested-With": "XMLHttpRequest"}),
 "govjobs_html": ("https://www.governmentjobs.com/jobs?location=54016&distance=25&sort=date&isDescendingSort=True", {}),
 "nssf_html": ("https://jobs.nssf.org/jobs?keywords=&place=", {}),
 "nssf_rss": ("https://jobs.nssf.org/jobs/?display=rss", {}),
 "nssf_feed": ("https://jobs.nssf.org/jobs.rss", {}),
}
os.makedirs("out", exist_ok=True)
for name, (u, h) in urls.items():
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={**UA, **h}), timeout=40)
        body = r.read(); info = f"{r.status} {r.headers.get('Content-Type')} {len(body)} {r.geturl()}"
    except Exception as e:
        body = getattr(e, "read", lambda: b"")() if hasattr(e, "read") else b""; info = f"ERR {e}"
    open(f"out/{name}.txt", "wb").write(info.encode() + b"\n\n" + body[:400000])
    print(name, info)
