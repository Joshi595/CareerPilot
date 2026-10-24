"""Company career sites, learned from experience: links in postings, pages reached while applying, the person's own
links, and common hiring systems checked against the company names seen. Each site is scanned directly, at most once a day.

Origins: you · posting · posting page · apply link · applying · pasted · name check. A site switched off stays off.
"""
import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from rapidfuzz import fuzz

from careerpilot.core import sources
from careerpilot.core import profile as pf
from careerpilot.core.data import db

STRONG = {"applying", "apply link", "you", "pasted"}  # links worth a page fetch to find the hiring system behind them
PROBE = ("greenhouse", "lever", "ashby", "personio", "smartrecruiters", "recruitee", "workable", "softgarden")
NO_CTX = {"country": "", "country_name": ""}


def add(kind, ref, company, origin):
    """Insert a site unless it is known (case-insensitive ref). Never touches an existing row, so 'off' stays off."""
    ref = ref.rstrip("/") if kind != "page" else ref
    with db() as con:
        con.execute("insert into sites(kind, ref, company, origin, found) select ?,?,?,?,? where not exists "
                    "(select 1 from sites where kind=? and lower(ref)=lower(?))",
                    (kind, ref, company, origin, date.today().isoformat(), kind, ref))


def learn(url, company, origin, title="", fetch=True):
    """Remember the career site a link reveals; strong origins may fetch the page. Returns (kind, ref) or None."""
    found = sources.ats_ref(url or "")
    if not found and fetch and origin in STRONG and (url or "").startswith("http"):
        try:
            found = sources.find_ats(url) or own_page(url, company, origin, title)
        except Exception:  # learning is a bonus: never breaks a search or an application
            found = None
    if found:
        add(*found, company, origin)
    return found


def own_page(url, company, origin, title):
    """A company's own (non-ATS) careers page: its domain carries the company's name (unless the person added it), and
    the page lists >= 3 job-like links, one matching the title."""
    host = urllib.parse.urlparse(url).hostname or ""
    words = [w for w in sources.slug(re.sub(sources.LEGAL, "", company or "", flags=re.I), 60).split("-") if len(w) >= 3]
    if any(b in host for b in sources.BOARDS) or (origin != "you" and not any(w in host for w in words)):
        return None
    ref = url if origin == "you" else url.split("?")[0].rsplit("/", 1)[0] + "/"  # the posting's parent: usually the job list
    got = sources.page(company, ref, NO_CTX)
    if len(got) >= 3 and (not title or any(fuzz.token_set_ratio(norm(g["title"]), norm(title)) >= 80 for g in got)):
        return "page", ref
    return None


def learn_all(links, fetch=20):
    """learn() over (url, company, origin, title) links. Page fetches only for companies without a site, at most `fetch`."""
    slow = [l for l in links if l[0] and not learn(*l, fetch=False) and l[2] in STRONG]
    have = companies()
    slow = list({l[1].lower(): l for l in slow if l[1].lower() not in have}.values())[:fetch]
    with ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda l: learn(*l), slow))


def companies() -> set:
    """Companies with a career site that still lists jobs (an empty one may have moved: it is looked for again)."""
    with db() as con:
        return {(r[0] or "").lower() for r in con.execute("select company from sites where empty_since is null")}


def seed():
    """Every run: the target countries' pack sites (universities, institutes; new ones in a pack update arrive this way).
    Once: the person's companies.yaml, plus a free link check over every stored posting and application."""
    from careerpilot.core import countries, search
    for t in pf.Preferences.model_validate(pf.read("preferences")).targets:
        for c in countries.pack(t.code).get("career_sites") or []:
            add(c["kind"], c["ref"], c["name"], "pack")
    state = search.load_state()
    if state.get("sites_seeded"):
        return
    for c in (pf.read("companies") if (pf.folder() / "companies.yaml").exists() else None) or []:
        if c.get("kind") and c.get("ref"):
            add(c["kind"], c["ref"], c.get("name") or c["ref"], "you")
    with db() as con:
        rows = con.execute("select company, url, data from jobs where coalesce(why, '') not like '[junk]%' "
                           "and coalesce(why, '') not like '[excluded]%'").fetchall()
        apps = con.execute("select company, data from applications").fetchall()
    for r in rows:
        for url in (r["url"], json.loads(r["data"] or "{}").get("apply_url")):
            learn(url, r["company"], "posting", fetch=False)
    for a in apps:
        learn(json.loads(a["data"] or "{}").get("url"), a["company"], "posting", fetch=False)
    state = search.load_state()
    state["sites_seeded"] = True
    search.save_state(state)


EMPTY_AFTER = 7  # scans in a row without jobs (on a site that had some): it probably moved; scanned weekly from then on


def active() -> list[dict]:
    """Sites to scan now: on, not scanned today, and not paused (3 failures in a row pause a site for 7 days, an empty
    site is scanned weekly). A pack site meant for some job types only (student-job boards) is scanned only for people
    who look for one of them."""
    from careerpilot.core import countries
    today, week = date.today().isoformat(), (date.today() - timedelta(days=7)).isoformat()
    prefs = pf.Preferences.model_validate(pf.read("preferences"))
    other = {c["ref"] for t in prefs.targets for c in countries.pack(t.code).get("career_sites") or []
             if c.get("types") and not set(c["types"]) & set(prefs.job_types)}
    with db() as con:
        return [dict(r) for r in con.execute("select * from sites where not off and coalesce(last_scan, '') < ? and "
                                             "(fails < 3 or last_scan <= ?) and (empty_since is null or last_scan <= ?)",
                                             (today, week, week)) if r["ref"] not in other]


def record(kind, ref, count, error=""):
    """One scan's result. An error counts towards the pause; no jobs on a site that had some counts towards 'empty'
    (it may have moved to another hiring system: the next name check looks for the company again)."""
    today = date.today().isoformat()
    with db() as con:  # every right-hand side sees the row as it was before this update
        con.execute("update sites set last_scan=?, last_count=?, error=?, fails=case when ? then fails + 1 else 0 end, "
                    "zeros=case when ? then zeros when ? > 0 then 0 when last_count > 0 or zeros > 0 then zeros + 1 else 0 end, "
                    "empty_since=case when ? then empty_since when ? > 0 then null when zeros + 1 = ? and (last_count > 0 or zeros > 0) "
                    "then ? else empty_since end where kind=? and ref=?",
                    (today, count, error, bool(error), bool(error), count, bool(error), count, EMPTY_AFTER, today, kind, ref))


def scanners() -> dict:
    """Every scanner: the worldwide ones plus each target country's pack."""
    from careerpilot.core import search
    out = dict(sources.SCANNERS)
    for t in pf.Preferences.model_validate(pf.read("preferences")).targets:
        out.update(search.pack_sources(t.code)[1])
    return out


def norm(title):
    return sources.clean_title(title).lower()


def probe(limit=30) -> int:
    """Company names seen in postings, checked on common hiring systems (name without legal suffix, joined or
    hyphenated). Accepted only when a title there matches one of that company's stored titles. Returns sites found."""
    from careerpilot.core import search
    state = search.load_state()
    probed, cutoff = state.setdefault("probed", {}), (date.today() - timedelta(days=30)).isoformat()
    with db() as con:
        rows = con.execute("select company, title, coalesce(score, 0) s from jobs where coalesce(company, '') != '' and "
                           "source != 'career site' and coalesce(why, '') not like '[junk]%' and "
                           "coalesce(why, '') not like '[excluded]%' order by s desc").fetchall()
        empty = {(r[0] or "").lower(): r[1] for r in con.execute("select company, empty_since from sites where empty_since is not null")}
    have, titles = companies(), {}
    for r in rows:
        titles.setdefault(r["company"], set()).add(norm(r["title"]))
    todo = [c for c in titles if c.lower() not in have and probed.get(c.lower(), "") < max(cutoff, empty.get(c.lower(), ""))][:limit]

    def check(company):
        words = sources.slug(re.sub(sources.LEGAL, "", company, flags=re.I), 60).split("-")
        for kind in PROBE:
            for ref in dict.fromkeys(["".join(words), "-".join(words)]):
                try:
                    got = sources.SCANNERS[kind](company, ref, NO_CTX)
                except Exception:
                    continue
                if any(fuzz.token_set_ratio(norm(g["title"]), t) >= 88 for g in got for t in titles[company]):
                    return company, (kind, ref)
        return company, None

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(check, todo))
    for company, found in results:
        probed[company.lower()] = date.today().isoformat()
        if found:
            add(*found, company, "name check")
    state = search.load_state()  # the search may have saved its own state meanwhile
    state["probed"] = probed
    search.save_state(state)
    return sum(1 for _, f in results if f)


def find_job(job) -> str:
    """The same job on the company's own career site: a stored career-site twin, else a live scan of its sites. URL or ''."""
    title, company = norm(job["title"]), (job["company"] or "").lower()
    same = lambda c, t: fuzz.partial_ratio(company, (c or "").lower()) >= 85 and fuzz.token_sort_ratio(norm(t), title) >= 90
    with db() as con:
        twins = con.execute("select company, title, url from jobs where source='career site'").fetchall()
        sites = con.execute("select kind, ref, company from sites where not off").fetchall()
    for r in twins:
        if same(r["company"], r["title"]):
            return r["url"]
    scan = scanners()
    for s in sites:
        if company and s["kind"] in scan and fuzz.partial_ratio(company, (s["company"] or "").lower()) >= 85:
            try:
                got = scan[s["kind"]](s["company"], s["ref"], NO_CTX)
            except Exception:
                continue
            for g in got:
                if same(s["company"], g["title"]):
                    return g["url"]
    return ""
