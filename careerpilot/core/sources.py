"""Where postings come from. Worldwide: the big boards through JobSpy, free remote-job APIs, and company career
sites on common hiring systems. Country packs (countries/<cc>/sources.py) add national boards.

A board is `fn(query, ctx) -> [job dicts]` with ctx = {country, country_name, location, days, remote}.
A scanner is `fn(company_name, ref, ctx) -> [job dicts]`: every posting on one company's career site.
Scanners and liveness rules are adapted from the original pipeline and career-ops (MIT). Nothing here applies or sends.
"""
import gzip
import hashlib
import html
import ipaddress
import json
import logging
import re
import socket
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
# Sites whose bot shield stalls any client that doesn't connect like a browser, whatever its User-Agent
# (StepStone's job pages since 2026-10: every request hung until the timeout). These go through curl_cffi as Chrome.
BROWSER_ONLY = re.compile(r"^https?://([^/]+\.)?stepstone\.", re.I)


# ---------------------------------------------------------------- http + text
def web(url) -> str:
    """The link if it is a web link (http/https), else "". Links come from other people's pages: a javascript: link
    shown on the Board would run inside CareerPilot when clicked, a file: link would read this computer's files."""
    url = str(url or "").strip()
    return url if re.match(r"https?://", url, re.I) else ""


def public(url) -> bool:
    """A web link to the internet, not to this computer or the local network (router, other local apps).
    ponytail: checks the name once before the request; a DNS-rebinding host could still answer differently next time."""
    host = urllib.parse.urlsplit(web(url)).hostname
    if not host:
        return False
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(host, None)}
    except (socket.gaierror, UnicodeError):
        return True  # unresolvable here (e.g. only a proxy knows the name): the request itself will fail or go via the proxy
    return all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs)


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect is followed only to another public web link."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public(newurl):
            raise urllib.error.URLError(f"redirect to a non-public address refused: {newurl[:80]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(_PublicRedirects)


def http(url, data=None, headers=None, timeout=25):
    h = {"User-Agent": UA, "Accept-Language": "en,de;q=0.8", "Accept-Encoding": "gzip", **(headers or {})}
    url = urllib.parse.quote(url, safe="%/:=&?~#+!$,;'@()*[]")
    if BROWSER_ONLY.match(url):
        from curl_cffi import requests as browser
        h = {k: v for k, v in h.items() if k not in ("User-Agent", "Accept-Encoding")}  # its own, matching Chrome's
        try:
            r = browser.request("POST" if data is not None else "GET", url, json=data, headers=h, timeout=timeout, impersonate="chrome")
        except Exception:  # slow or unreachable: no answer, never a crash
            return 0, "", url
        # ponytail: checks where the redirects ended, not each hop; the answer from a private address is never used
        return (r.status_code, r.text, r.url) if public(r.url) else (0, "", url)
    if not public(url):  # file:, ftp:, this computer, the local network: never fetched
        return 0, "", url
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=h)
    try:
        with OPENER.open(req, timeout=timeout) as r:
            body = r.read()
            body = gzip.decompress(body) if "gzip" in (r.headers.get("Content-Encoding") or "") else body
            return r.status, body.decode("utf-8", "replace"), r.url
    except urllib.error.HTTPError as e:
        return e.code, "", url
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:  # slow or unreachable: no answer, never a crash
        return 0, "", url


def get_json(url, data=None, headers=None):
    status, body, _ = http(url, data=data, headers={"accept": "application/json",
                                                    **({"content-type": "application/json"} if data else {}), **(headers or {})})
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    return json.loads(body)


def plain(text):
    """Markup as readable text with paragraphs and bullets."""
    text = re.sub(r"(?is)<(script|style|nav|header|footer)\b.*?</\1>", " ", str(text or ""))
    text = re.sub(r"(?i)<li\b[^>]*>", "\n• ", text)
    text = re.sub(r"(?i)<(br|/?p|/?div|/?h\d|/?ul|/?ol|/li|/?tr)\b[^>]*>", "\n", text)
    lines = (" ".join(line.split()) for line in html.unescape(re.sub(r"<[^>]+>", " ", text)).splitlines())
    return "\n".join(line for line in lines if line.strip("• "))


GENDER = r"\s*[\(\[]\s*(?:all genders?|alle geschlechter|[mwfdxih*]{1,3}(?:\s*[/|,]\s*(?:[mwfdxih*]|div|divers|nb)){1,3})\s*[\)\]]"


def clean_title(title):
    return re.sub(GENDER, "", html.unescape(str(title or "")), flags=re.I).strip(" -–|")


def slug(text, cap):
    s = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()).strip("-")
    return s if len(s) <= cap else s[:cap].rstrip("-") + "-" + hashlib.sha1(s.encode()).hexdigest()[:6]


BOARDS = ("linkedin.", "indeed.", "glassdoor.", "xing.", "stepstone.", "arbeitnow.", "arbeitsagentur.", "monster.", "naukri.",
          "bayt.", "ziprecruiter.", "himalayas.", "remoteok.", "jobicy.")  # job boards, not a company's own site
LEGAL = r"\b(gmbh|ag|se|kg|co|inc|ltd|llc|plc|group|gruppe|sa|sas|bv|nv|pvt|limited)\b\.?"  # legal suffixes of company names


def job_key(company, title):
    company = re.sub(LEGAL, "", str(company), flags=re.I)
    return f"{slug(company, 40) or 'unknown'}_{slug(clean_title(title), 60) or 'untitled'}"


def job(title, company, location, url, source, posted="", desc="", country=""):
    return {"key": job_key(company, title), "title": clean_title(title), "company": str(company or "").strip(),
            "location": str(location or "").strip(), "url": web(url), "source": source, "posted": str(posted or "")[:10],
            "desc": plain(desc) if desc else "", "country": country}


# ---------------------------------------------------------------- liveness (career-ops liveness-core)
EXPIRED = [r"diese stelle (ist )?(nicht mehr|bereits) (verfugbar|besetzt|vergeben|aktiv)",
           r"(stellenangebot|stellenanzeige) (ist )?nicht mehr (verfugbar|aktiv|online)",
           r"bewerbungsfrist (ist )?(abgelaufen|vorbei)", r"no longer accepting applications",
           r"(job|position|posting|role|vacancy) (is )?no longer (available|open|active)",
           r"this (job|role|position|vacancy) (has been|is) closed", r"applications? (are|is|have been) closed",
           r"has (already )?been filled", r"\bjob expired\b", r"offre (n'est plus|expiree|pourvue)", r"oferta (caducada|cerrada|expirada)"]
CONDITIONAL = r"\b(until|once|when|after|if|sobald|bis|wenn|nachdem)\b"
CHALLENGE = r"just a moment|verify you are human|ray id|captcha|access denied"
APPLY = r"\b(apply now|apply for this|jetzt bewerben|online bewerben|bewerben|postuler|aplicar|candidatar)\b"


def fold(text):
    return " ".join(unicodedata.normalize("NFKD", plain(text)).encode("ascii", "ignore").decode().lower().split())


def classify(status, text, url="", final_url=""):
    """(verdict, reason), verdict active | expired | uncertain. Unclear is never 'expired'."""
    if status in (404, 410):
        return "expired", f"HTTP {status}"
    if re.search(r"error(page|type)?\W*=?\W*404|/404\b|not-?found|[?&]error=true", final_url.lower()):
        return "expired", f"redirected to an error page"
    job_id = re.search(r"\d{5,}", url)
    if final_url and job_id and job_id.group(0) not in final_url:
        return "uncertain", "job id lost in a redirect"
    t = fold(text)
    if status in (403, 429) or status >= 500 or re.search(CHALLENGE, t):
        return "uncertain", f"HTTP {status} or a bot check"
    for pat in EXPIRED:
        m = re.search(pat, t)
        if m and not re.search(CONDITIONAL, " ".join(t[:m.start()].split()[-10:])):
            return "expired", m.group(0)
    if re.search(APPLY, t):
        return "active", "apply button found"
    return ("uncertain", "short page") if len(t) < 300 else ("active", "no expiry text")


def alive(url):
    try:
        status, page, final = http(url, timeout=20)
    except (urllib.error.URLError, TimeoutError) as e:
        return "uncertain", f"network: {e}"
    return classify(status, page, url, final)


# ---------------------------------------------------------------- big boards through JobSpy
JOBSPY_ERRORS = {}
_catch = logging.Handler(logging.ERROR)
# JobSpy logs as "jobspy.<site>" (older versions "JobSpy:<site>"): the site is the last part either way
_catch.emit = lambda record: JOBSPY_ERRORS.setdefault(re.split(r"[:.]", record.name)[-1].lower(), []).append(record.getMessage())
logging.getLogger("jobspy").addHandler(_catch)  # every site's logger sends its errors up here
# Regional boards JobSpy reads, by target country.
REGIONAL = {"naukri": {"IN"}, "bayt": {"AE", "SA", "QA", "KW", "BH", "OM", "EG", "JO", "LB"}, "bdjobs": {"BD"},
            "hellowork": {"FR"}, "zip_recruiter": {"US", "CA"}}
GLASSDOOR = {"AR", "AU", "AT", "BE", "BR", "CA", "FR", "DE", "HK", "IN", "IE", "IT", "MY", "MX", "NL", "NZ", "SG", "ES", "CH", "GB",
             "US", "VN"}


def jobspy(site, query, ctx, n=60):
    from jobspy import scrape_jobs
    df = scrape_jobs(site_name=[site], search_term=query, google_search_term=f"{query} jobs in {ctx['location']}",
                     location=ctx["location"], country_indeed=ctx["country_name"], hours_old=ctx["days"] * 24,
                     results_wanted=n, is_remote=ctx.get("remote") == "remote", description_format="html",
                     distance=ctx.get("distance") or 50,  # miles around the place (the person's radius)
                     job_type="internship" if ctx.get("kind") == "internship" else None,  # Indeed, Glassdoor, Google
                     fetch_description=site in ("glassdoor",), verbose=0)
    val = lambda v: "" if v is None or str(v) == "nan" else str(v)
    return [job(val(r.get("title")), val(r.get("company")), val(r.get("location")), val(r.get("job_url_direct")) or val(r.get("job_url")),
                site, val(r.get("date_posted")), val(r.get("description")), ctx["country"])
            for r in df.to_dict("records") if val(r.get("title"))]


BLOCKED = {}  # board -> the day it answered 429: no more requests to it that day


def parse_applicants(page: str) -> dict | None:
    """{n, text} from LinkedIn's public job page: "39 applicants" (39), "Over 200 applicants" (200), "Be among the first
    25 applicants" (0: hardly anyone yet)."""
    cap = re.search(r'class="num-applicants__caption[^"]*"[^>]*>\s*([^<]+?)\s*<', page or "")
    if not cap:
        return None
    text = " ".join(cap[1].split())
    n = re.search(r"\d[\d,.]*", text)
    return {"n": 0 if re.search(r"among the first|unter den ersten", text, re.I) else int(re.sub(r"\D", "", n[0])) if n else 0,
            "text": text}


def applicants(url: str) -> dict | None:
    """How many applied, for a LinkedIn posting (its public page, no login; other boards don't show it). One request;
    after a 429 none for the rest of the day. None when unknown."""
    jid = re.search(r"linkedin\.com/jobs/view/(?:[^/?#]*-)?(\d{6,})", url or "")
    if not jid or BLOCKED.get("linkedin") == date.today().isoformat():
        return None
    status, page, _ = http(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid[1]}", timeout=20)
    if status == 429:
        BLOCKED["linkedin"] = date.today().isoformat()
        return None
    got = parse_applicants(page) if status == 200 else None
    return {**got, "at": date.today().isoformat()} if got else None


def global_boards(country: str) -> dict:
    # ponytail: Google Jobs left out: JobSpy 1.4 gets "no job data returned" from it (2026-10), so it only used up 18
    # searches a run. Put "google": lambda q, c: jobspy("google", q, c, 30) back when a JobSpy update reads it again.
    boards = {"linkedin": lambda q, c: jobspy("linkedin", q, c, 50), "indeed": lambda q, c: jobspy("indeed", q, c, 100)}
    if country in GLASSDOOR:
        boards["glassdoor"] = lambda q, c: jobspy("glassdoor", q, c, 30)
    for site, where in REGIONAL.items():
        if country in where:
            boards[site] = (lambda s: lambda q, c: jobspy(s, q, c, 40))(site)
    return boards


# ---------------------------------------------------------------- free remote-job feeds (keyless); each asks to link back to the posting
def jobicy(query, ctx):
    d = get_json(f"https://jobicy.com/api/v2/remote-jobs?count=50&tag={urllib.parse.quote(query)}")
    return [job(r.get("jobTitle"), r.get("companyName"), r.get("jobGeo") or "Remote", r.get("url"), "jobicy", r.get("pubDate"),
                r.get("jobDescription") or "") for r in d.get("jobs", [])]


def himalayas(query, ctx):
    d = get_json(f"https://himalayas.app/jobs/api/search?q={urllib.parse.quote(query)}&limit=50")
    return [job(r.get("title"), r.get("companyName"), ", ".join(r.get("locationRestrictions") or []) or "Remote",
                r.get("applicationLink") or r.get("guid"), "himalayas",
                datetime.fromtimestamp(r["pubDate"]).date().isoformat() if isinstance(r.get("pubDate"), (int, float)) else "",
                r.get("description") or "") for r in d.get("jobs", [])]


def remoteok(query, ctx):
    rows = get_json("https://remoteok.com/api")[1:]  # first item is the legal notice
    words = query.lower().split()
    return [job(r.get("position"), r.get("company"), r.get("location") or "Remote", r.get("url"), "remoteok", r.get("date"),
                r.get("description") or "") for r in rows
            if all(w in f"{r.get('position', '')} {' '.join(r.get('tags') or [])}".lower() for w in words)]


REMOTE_BOARDS = {"jobicy": jobicy, "himalayas": himalayas, "remoteok": remoteok}


# ---------------------------------------------------------------- company career sites (public JSON of each hiring system)
def greenhouse(name, ref, ctx):
    d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{ref}/jobs?content=true")
    return [job(r.get("title"), name, (r.get("location") or {}).get("name"), r.get("absolute_url"), "career site",
                r.get("updated_at"), html.unescape(r.get("content") or "")) for r in d["jobs"]]


def ashby(name, ref, ctx):
    d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{ref}")
    return [job(r.get("title"), name, " / ".join([r.get("location") or ""] + [s.get("location", "") for s in r.get("secondaryLocations") or []]),
                r.get("jobUrl"), "career site", r.get("publishedAt"), r.get("descriptionPlain") or "") for r in d["jobs"]]


def lever(name, ref, ctx):
    d = get_json(f"https://api.lever.co/v0/postings/{ref}?mode=json")
    return [job(r.get("text"), name, " / ".join((r.get("categories") or {}).get("allLocations") or [(r.get("categories") or {}).get("location") or ""]),
                r.get("hostedUrl"), "career site", "", r.get("descriptionPlain") or "") for r in d]


def personio(name, ref, ctx):
    import xml.etree.ElementTree as ET
    status, body, _ = http(f"https://{ref}.jobs.personio.de/xml")
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    return [job(p.findtext("name"), name, p.findtext("office") or "", f"https://{ref}.jobs.personio.de/job/{p.findtext('id')}",
                "career site", p.findtext("createdAt") or "", " ".join(d.findtext("value") or "" for d in p.iter("jobDescription")))
            for p in ET.fromstring(body).iter("position") if p.findtext("name")]


def smartrecruiters(name, ref, ctx):
    out, offset = [], 0
    while offset < 3000:
        d = get_json(f"https://api.smartrecruiters.com/v1/companies/{ref}/postings?limit=100&offset={offset}"
                     + (f"&country={ctx['country'].lower()}" if ctx.get("country") else ""))
        out += [job(r.get("name"), name, ", ".join(filter(None, [(r.get("location") or {}).get("city"), (r.get("location") or {}).get("country")])),
                    f"https://jobs.smartrecruiters.com/{ref}/{r.get('id')}", "career site", r.get("releasedDate")) for r in d.get("content", [])]
        offset += 100
        if offset >= d.get("totalFound", 0):
            break
    return out


def workday(name, ref, ctx):
    """ref: the public site URL, https://<tenant>.wd<N>.myworkdayjobs.com/<site>."""
    u = urllib.parse.urlparse(ref)
    base, site = f"https://{u.netloc}", u.path.strip("/").split("/")[-1]
    out, offset = [], 0
    while offset < 400:
        d = get_json(f"{base}/wday/cxs/{u.netloc.split('.')[0]}/{site}/jobs",
                     {"limit": 20, "offset": offset, "searchText": ctx.get("country_name", ""), "appliedFacets": {}})
        posts = d.get("jobPostings", [])
        out += [job(r.get("title"), name, r.get("locationsText", ""), f"{base}/{site}{r.get('externalPath', '')}", "career site") for r in posts]
        offset += 20
        if not posts or offset >= d.get("total", 0):
            break
    return out


def recruitee(name, ref, ctx):
    d = get_json(f"https://{ref}.recruitee.com/api/offers/")
    return [job(r.get("title"), name, f"{r.get('city') or ''} {r.get('country') or ''}", r.get("careers_url"), "career site",
                r.get("published_at"), f"{r.get('description') or ''} {r.get('requirements') or ''}") for r in d.get("offers", [])]


def workable(name, ref, ctx):
    d = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{ref}?details=true")
    return [job(r.get("title"), name, ", ".join(filter(None, [r.get("city"), r.get("country")])), r.get("url") or r.get("shortlink"),
                "career site", r.get("published_on"), r.get("description") or "") for r in d.get("jobs", [])]


def teamtailor(name, ref, ctx):
    """ref: the career site host, e.g. example.teamtailor.com. Its public feed is RSS with tt:city / tt:country."""
    import email.utils
    status, body, _ = http(f"https://{ref}/jobs.rss")
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    tag = lambda item, t: html.unescape((re.search(rf"<{t}[^>]*>(.*?)</{t}>", item, re.S) or [None, ""])[1]).replace("<![CDATA[", "").replace("]]>", "").strip()
    out = []
    for item in re.findall(r"<item>(.*?)</item>", body, re.S):
        when = tag(item, "pubDate")
        posted = email.utils.parsedate_to_datetime(when).date().isoformat() if when else ""
        out.append(job(tag(item, "title"), name, ", ".join(filter(None, [tag(item, "tt:city"), tag(item, "tt:country")])),
                       tag(item, "link"), "career site", posted, tag(item, "description")))
    return out


def breezy(name, ref, ctx):
    d = get_json(f"https://{ref}.breezy.hr/json")
    return [job(r.get("name"), name, (r.get("location") or {}).get("name", ""), r.get("url"), "career site", r.get("published_date") or "")
            for r in d]


def bamboohr(name, ref, ctx):
    d = get_json(f"https://{ref}.bamboohr.com/careers/list")
    return [job(r.get("jobOpeningName"), name, ", ".join(filter(None, [(r.get("location") or {}).get("city"), (r.get("location") or {}).get("country")])),
                f"https://{ref}.bamboohr.com/careers/{r.get('id')}", "career site") for r in d.get("result", [])]


def successfactors(name, ref, ctx):
    """SAP SuccessFactors career site (BMW, VW, ZF, Bosch, Fraunhofer…): RMK html tiles, else the CSB JSON API.
    Ported from the original pipeline (career-ops successfactors.mjs); two sweeps sorted by date catch ties."""
    out, seen, start = [], set(), 0
    cc = (ctx.get("country") or "").upper()
    while start < 1000:
        status, body, _ = http(f"{ref}/tile-search-results/?q=" + (f"&optionsFacetsDD_country={cc}" if cc else "") + f"&startrow={start}",
                               headers={"accept": "text/html"})
        blocks = re.findall(r'<li class="job-tile job-id-\d+.*?</li>', body, re.S) if status == 200 else []
        ids = [re.search(r"job-id-(\d+)", b).group(1) for b in blocks]
        if not ids or set(ids) <= seen:
            break
        for jid, b in zip(ids, blocks):
            seen.add(jid)
            t = re.search(r'class="jobTitle-link[^"]*"[^>]*>(.*?)</a>', b, re.S)
            u = re.search(r'data-url="([^"]+)"', b)
            c = re.search(r'-section-city-value">(.*?)</div>', b, re.S)
            if t and u:
                path = html.unescape(u.group(1))
                city = html.unescape(c.group(1).strip()) if c else urllib.parse.unquote(path.split("/job/")[-1]).split("-")[0]
                out.append(job(html.unescape(t.group(1).strip()), name, city, ref + path, "career site"))
        start += len(ids)
    if seen:
        return out
    for locale in ("de_DE", "en_US"):
        total = 0
        for sweep in range(2):
            got = 0
            for page in range(60):
                status, body, _ = http(f"{ref}/services/recruiting/v1/jobs", data={"keywords": "", "locale": locale, "location": "",
                                       "pageNumber": page, "sortBy": "date"}, headers={"content-type": "application/json", "accept": "application/json"},
                                       timeout=45)
                d = json.loads(body) if status == 200 and body else {}
                rows, total = d.get("jobSearchResult") or [], d.get("totalJobs", total)
                for r in (x["response"] for x in rows):
                    if r.get("id") in seen:
                        continue
                    seen.add(r.get("id"))
                    got += 1
                    loc = re.sub(r"<br\s*/?>", " / ", " / ".join(r.get("jobLocationShort") or []))
                    out.append(job(r.get("unifiedStandardTitle") or r.get("jobTitle") or "", name, loc,
                                   f"{ref}/job/{html.unescape(r.get('unifiedUrlTitle') or r.get('urlTitle') or '')}/{r['id']}-{locale}",
                                   "career site", r.get("unifiedStandardStart", "")))
                if len(rows) < 10 or (page + 1) * 10 >= total:
                    break
            if not got:
                break
    return out


def join_com(name, ref, ctx):
    """join.com company page: the postings sit in its page data (first page, ~20: fine for the small firms on join)."""
    status, body, _ = http(f"https://join.com/companies/{ref}")
    i = body.find('"jobs":{"items":')
    if status != 200 or i < 0:
        raise RuntimeError(f"HTTP {status}" if status != 200 else "no job list in the page")
    items = json.JSONDecoder().raw_decode(body, i + len('"jobs":{"items":'))[0]
    return [job(r.get("title"), name, " ".join(filter(None, [(r.get("city") or {}).get("cityName"), (r.get("city") or {}).get("countryName")])),
                f"https://join.com/companies/{ref}/{r.get('idParam')}", "career site", r.get("createdAt")) for r in items]


def page(name, ref, ctx):
    """A plain career page whose links are job titles (check with source-test first): every link that reads like one.
    A ref "<url>#re:<regex>" keeps every link whose href matches the regex instead (pages full of navigation, e.g.
    universities, where many postings share a title such as "Wissenschaftliche*r Mitarbeiter*in")."""
    url, _, want = ref.partition("#re:")
    status, body, final = http(url)
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    out = {}
    for href, text in re.findall(r'<a\b[^>]*?href="([^"#][^"]*)"[^>]*>(.*?)</a>', body, re.S | re.I):
        title = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text)).split())
        link = urllib.parse.urljoin(final, html.unescape(href))
        if want:
            if not re.search(want, link):
                continue
            # A card-style link (FAU, stellenwerk): the first block is the title; a second block "Employer · Place" is
            # the employer and the location.
            blocks = [x for x in (" ".join(html.unescape(re.sub(r"<[^>]+>", " ", b)).split())
                                  for b in re.split(r"</?(?:div|p|h\d|li|br)\b[^>]*>", text, flags=re.I)) if x]
            head = blocks[0] if blocks else title
            if not 5 <= len(head) <= 200:
                continue
            who, where = (blocks[1].split("·", 1) if len(blocks) > 1 and "·" in blocks[1] and len(blocks[1]) < 200 else (name, ""))
            out[link] = job(head, who.strip(), where.strip(), link, "career site")
        elif 15 <= len(title) <= 200 and re.search(r"(?i)(m/w/d|w/m/d|\(all genders\)|engineer|manager|developer|scientist|mitarbeiter|"
                                    r"ingenieur|entwickler|nurse|assistant|specialist|analyst|research|praktik|werkstud|intern)", title):
            out[title] = job(title, name, "", link, "career site")
    return list(out.values())


def softgarden(name, ref, ctx):
    """softgarden's job widget, {ref}.softgarden.io (ported from career-ops providers/softgarden.mjs): a plain page that
    lists every posting, no paging."""
    url = f"https://{ref}.softgarden.io/de/widgets/jobs"
    status, body, _ = http(url)
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    text = lambda s: " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())
    out = []
    for block in body.split('<div class="matchElement" id="job_id_')[1:]:
        a = re.search(r'<a href="([^"]*/job/[^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not a or not text(a[2]):
            continue
        cities = dict.fromkeys(text(c) for c in re.findall(r'class="location-view-item"[^>]*>(.*?)</span>', block, re.S))
        # no date: the widget shows the day it was loaded for every posting
        out.append(job(text(a[2]), name, " / ".join(c for c in cities if c), urllib.parse.urljoin(url, html.unescape(a[1])),
                       "career site"))
    return out


SCANNERS = {"greenhouse": greenhouse, "ashby": ashby, "lever": lever, "personio": personio, "smartrecruiters": smartrecruiters,
            "workday": workday, "recruitee": recruitee, "workable": workable, "teamtailor": teamtailor, "breezy": breezy,
            "bamboohr": bamboohr, "successfactors": successfactors, "join": join_com, "softgarden": softgarden, "page": page}

# The hiring system a link reveals -> (scanner, ref).
ATS = [(r"jobs\.ashbyhq\.com/([^/?#]+)", "ashby"), (r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/([^/?#]+)", "greenhouse"),
       (r"jobs\.lever\.co/([^/?#]+)", "lever"), (r"//([\w-]+)\.jobs\.personio\.(?:de|com)", "personio"),
       (r"jobs\.smartrecruiters\.com/([^/?#]+)", "smartrecruiters"), (r"//([\w-]+)\.recruitee\.com", "recruitee"),
       (r"apply\.workable\.com/([^/?#]+)", "workable"), (r"//([\w-]+\.teamtailor\.com)", "teamtailor"),
       (r"//([\w-]+)\.breezy\.hr", "breezy"), (r"join\.com/companies/([^/?#]+)", "join"), (r"//([\w-]+)\.bamboohr\.com", "bamboohr"),
       (r"//([\w-]+)\.softgarden\.io", "softgarden"),
       (r"//([\w-]+\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)", "workday")]
NOT_SLUG = {"embed", "jobs", "o", "l", "api", "v1", "careers", "www", "app", "static"}


def ats_ref(url):
    for pattern, kind in ATS:
        m = re.search(pattern, url or "")
        if m and m.group(m.lastindex) not in NOT_SLUG:
            return kind, f"https://{m.group(1)}/{m.group(2)}" if kind == "workday" else m.group(1)
    return None


def find_ats(url: str):
    """'Add a company': the hiring system behind any careers page, from its link or from links inside the page."""
    found = ats_ref(url)
    if found:
        return found
    status, body, final = http(url)
    if re.search(r"rmkcdn\.successfactors|job-tile", body or ""):  # SAP SuccessFactors career site (RMK)
        u = urllib.parse.urlparse(final or url)
        return "successfactors", f"{u.scheme}://{u.netloc}"
    for href in re.findall(r'(?:href|src)="([^"]+)"', body or ""):
        found = ats_ref(html.unescape(href))
        if found:
            return found
    return ats_ref(final)


def posted_ok(posted: str, days: int) -> bool:
    try:
        return date.fromisoformat(posted[:10]) >= date.fromordinal(date.today().toordinal() - days)
    except ValueError:
        return True  # no date: keep, the dedupe drops repeats
