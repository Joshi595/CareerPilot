"""Germany pack: national boards and German career portals (ported from the original pipeline's jobs.py)."""
import datetime
import email.utils
import html
import json
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

from jobquill.core.sources import get_json, http, job, web

AA = {"X-API-Key": "jobboerse-jobsuche", "accept": "application/json"}  # the public client key arbeitsagentur.de itself uses


# Offer types (angebotsart) per job type: 1 = regular jobs, 34 = internships, trainee and working-student jobs, 4 = Ausbildung.
OFFERS = {"student": (34,), "internship": (34,), "graduate": (34,), "apprenticeship": (4,), "thesis": (1, 34)}
# Job types a board filters by itself: searched with the person's plain words (a "Werkstudent" in front only narrows it).
TYPED = {"arbeitsagentur": {"student", "internship", "graduate", "apprenticeship"}}


def arbeitsagentur(query, ctx):
    """Federal employment agency (v6), in the offer types of the job types the query is for (ctx["kind"], "a+b")."""
    out = []
    for offer in sorted({o for k in ctx.get("kind", "").split("+") for o in OFFERS.get(k, (1,))}):
        for pg in range(1, 4):
            # no "wo" (place): it wants German place names; the score judges the distance instead
            q = urllib.parse.urlencode({"was": query, "angebotsart": offer, "veroeffentlichtseit": min(ctx["days"], 100),
                                        "size": 100, "page": pg})
            status, body, _ = http(f"https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs?{q}", headers=AA)
            if status != 200:
                raise RuntimeError(f"HTTP {status}")
            rows = json.loads(body).get("ergebnisliste", [])
            for r in rows:
                ref, title = r.get("referenznummer"), r.get("stellenangebotsTitel")
                if ref and title:
                    loc = ((r.get("stellenlokationen") or [{}])[0].get("adresse") or {}).get("ort", "")
                    out.append({**job(title, r.get("firma", ""), loc, "https://www.arbeitsagentur.de/jobsuche/jobdetail/" + urllib.parse.quote(ref),
                                      "arbeitsagentur", r.get("aktuelleVeroeffentlichungsdatum", ""), country="DE"),
                                **({"apply_url": web(r["externeURL"])} if web(r.get("externeURL")) else {})})
            if len(rows) < 100:
                break
    return out


def stepstone_rows(page):
    for m in re.finditer(r'"items":(?=\[\{)', page):
        try:
            rows = json.JSONDecoder().raw_decode(page, m.end())[0]
        except ValueError:
            continue
        if rows[0].get("datePosted"):
            return rows
    return []


def stepstone(query, ctx):
    out, since = [], (datetime.date.today() - datetime.timedelta(days=ctx["days"])).isoformat()
    age = "ag=age_1&" if ctx["days"] <= 1 else "ag=age_7&" if ctx["days"] <= 7 else ""
    for pg in range(1, 6):
        status, body, _ = http(f"https://www.stepstone.de/jobs/{urllib.parse.quote(query.lower().replace(' ', '-'))}/in-deutschland?{age}page={pg}")
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        rows = stepstone_rows(body)
        out += [job(r.get("title"), r.get("companyName") or "", r.get("location") or "", r.get("url"), "stepstone", r["datePosted"][:10], country="DE")
                for r in rows if r.get("title") and r.get("url") and r["datePosted"][:10] >= since]
        if len(rows) < 25:
            break
        time.sleep(0.5)
    return out


def arbeitnow(query, ctx):
    """Arbeitnow's DACH feed, newest first. It ignores search terms; the score filters."""
    out = []
    for p in range(1, 4):
        try:
            d = get_json(f"https://www.arbeitnow.com/api/job-board-api?page={p}")
        except RuntimeError:
            if p == 1:
                raise
            break
        out += [job(r["title"], r.get("company_name") or "", r.get("location") or "", r.get("url"), "arbeitnow",
                    datetime.date.fromtimestamp(r["created_at"]).isoformat() if r.get("created_at") else "", r.get("description") or "", "DE")
                for r in d.get("data", [])]
        if not (d.get("links") or {}).get("next"):
            break
    return out


def bund_rss(query, ctx):
    """service.bund.de RSS: the newest public-sector postings (it ignores search terms)."""
    status, body, _ = http("https://www.service.bund.de/Content/Globals/Functions/RSSFeed/RSSGenerator_Stellen.xml")
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    field = lambda label, d: html.unescape(re.sub(r"<[^>]+>", "", (re.search(rf"{label}:\s*(.*?)<br", d, re.S) or [""] * 2)[1])).strip()
    return [job(i.findtext("title"), field("Arbeitgeber", d), field("Ort", d), i.findtext("guid") or i.findtext("link"), "service.bund.de",
                email.utils.parsedate_to_datetime(i.findtext("pubDate")).date().isoformat() if i.findtext("pubDate") else "", country="DE")
            for i in ET.fromstring(body).iter("item") for d in [i.findtext("description") or ""]]


def beesite(name, ref, ctx):
    """milch & zucker search backend (e.g. Mercedes-Benz). ref: https://<tenant>.app.beesite.de ; country 329 = Germany."""
    out, first = [], 1
    while first < 3000:
        q = {"LanguageCode": "EN", "SearchParameters": {"FirstItem": first, "CountItem": 100, "Sort": [{"Criterion": "PublicationStartDate", "Direction": "DESC"}],
             "MatchedObjectDescriptor": ["PositionID", "PositionTitle", "PositionURI", "PositionLocation.CityName", "PublicationStartDate"]},
             "SearchCriteria": [{"CriterionName": "PositionLocation.Country", "CriterionValue": [329]}]}
        items = get_json(f"{ref.rstrip('/')}/search?data={urllib.parse.quote(json.dumps(q))}").get("SearchResult", {}).get("SearchResultItems") or []
        for d in (i.get("MatchedObjectDescriptor") or {} for i in items):
            out.append(job(html.unescape(re.sub(r"<[^>]+>", " ", d.get("PositionTitle") or "")), name,
                           " / ".join(c.get("CityName", "") for c in d.get("PositionLocation") or []), d.get("PositionURI"), "career site",
                           d.get("PublicationStartDate"), country="DE"))
        first += 100
        if len(items) < 100:
            break
    return out


def karriere_nrw(name, ref, ctx):
    """Public-sector portal of North Rhine-Westphalia (universities): one call returns every posting."""
    return [job(r.get("title"), r.get("authority") or name, r.get("location") or "", f"https://karriere.nrw/stellenausschreibung/{r.get('uuid')}",
                "karriere.nrw", r.get("published"), country="DE")
            for r in get_json(ref)["items"] if r.get("jobtype") == "joboffer"]


BOARDS = {"arbeitsagentur": arbeitsagentur, "stepstone": stepstone, "arbeitnow": arbeitnow, "service.bund.de": bund_rss}
SCANNERS = {"beesite": beesite, "karriere_nrw": karriere_nrw}
FEEDS = {"arbeitnow", "service.bund.de"}  # ignore the query: called once per run, not per search term
