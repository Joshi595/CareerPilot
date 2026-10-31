"""Read a job posting: from a link (schema.org JobPosting data first, then the page's main text) or pasted text.

Posting text is untrusted: it is stored and shown, and goes to the AI only inside <untrusted> tags.
"""
import hashlib
import html
import json
import re
import urllib.request
from datetime import date

import trafilatura

from jobgambit.core.data import db, home

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def fetch(url: str, timeout: int = 20) -> str:
    from jobgambit.core import sources
    if not sources.public(url):
        raise ValueError("not a public web link")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en,de;q=0.8"})
    with sources.OPENER.open(req, timeout=timeout) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")


def _plain(htmltext: str) -> str:
    text = re.sub(r"<(br|/p|/li|/h\d|/div)\s*/?>", "\n", htmltext or "", flags=re.I)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def job_posting(page: str) -> dict | None:
    """The schema.org JobPosting block most career sites embed for Google Jobs."""
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for item in data if isinstance(data, list) else data.get("@graph", [data]):
            if isinstance(item, dict) and "JobPosting" in str(item.get("@type")):
                return item
    return None


def from_jsonld(jp: dict) -> dict:
    org = jp.get("hiringOrganization") or {}
    locs = jp.get("jobLocation") or []
    locs = locs if isinstance(locs, list) else [locs]
    addr = [(l.get("address") or {}) for l in locs if isinstance(l, dict)]
    place = ", ".join(filter(None, [addr[0].get("addressLocality"), addr[0].get("addressCountry") if isinstance(addr[0].get("addressCountry"), str)
                                    else (addr[0].get("addressCountry") or {}).get("name")])) if addr else ""
    country = addr[0].get("addressCountry") if addr else ""
    country = country if isinstance(country, str) else (country or {}).get("name", "")
    return {"title": html.unescape(jp.get("title", "")), "company": org.get("name", "") if isinstance(org, dict) else str(org),
            "location": place, "country": country[:2].upper() if len(country) == 2 else "",
            "posted": str(jp.get("datePosted", ""))[:10], "deadline": str(jp.get("validThrough", ""))[:10],
            "type": ", ".join(jp["employmentType"]) if isinstance(jp.get("employmentType"), list) else jp.get("employmentType", ""),
            "description": _plain(jp.get("description", ""))}


def read_url(url: str) -> dict:
    page = fetch(url)
    jp = job_posting(page)
    job = from_jsonld(jp) if jp else {}
    if len(job.get("description", "").split()) < 80:
        job["description"] = trafilatura.extract(page, include_tables=True) or job.get("description", "")
    if not job.get("title"):
        m = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
        job["title"] = html.unescape(m[1].strip()) if m else ""
    return job


def key_for(url: str, text: str) -> str:
    return hashlib.sha1((url or text[:2000]).encode()).hexdigest()[:12]


def save(job: dict) -> str:
    """Store a posting in the jobs table and its text in jobs/desc/<key>.txt. Returns the key."""
    key = job.get("key") or key_for(job.get("url", ""), job.get("description", ""))
    (home() / "jobs" / "desc").mkdir(parents=True, exist_ok=True)
    (home() / "jobs" / "desc" / f"{key}.txt").write_text(job.get("description", ""), encoding="utf-8")
    cols = ["title", "company", "location", "country", "url", "source", "posted", "deadline", "type"]
    keep = ", ".join(f"{c}=coalesce(nullif(excluded.{c}, ''), {c})" for c in cols)  # never blank out known fields
    with db() as con:
        con.execute(f"insert into jobs(key, {', '.join(cols)}, first_seen) values (?, {', '.join('?' * len(cols))}, ?) "
                    f"on conflict(key) do update set {keep}",
                    [key, *[job.get(c, "") for c in cols], date.today().isoformat()])
    if job.get("url"):  # a pasted link may lead to the company's career site
        import threading
        from jobgambit.core import sites
        threading.Thread(target=sites.learn, args=(job["url"], job.get("company", ""), "pasted", job.get("title", "")), daemon=True).start()
    return key


def get(key: str) -> dict | None:
    with db() as con:
        row = con.execute("select * from jobs where key=?", (key,)).fetchone()
    if not row:
        return None
    job = dict(row)
    desc = home() / "jobs" / "desc" / f"{key}.txt"
    job["description"] = desc.read_text(encoding="utf-8") if desc.exists() else ""
    return job
