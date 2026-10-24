"""Fit estimate for a posting at no AI cost, from the person's own titles, search words and CV.

Generalised from the original pipeline's assess(): any profession, any country. The score is explainable:
`why` lists what matched and what stands in the way.
"""
import math
import re
from datetime import date
from functools import lru_cache

from careerpilot.core import countries

SENIOR = r"\b(senior|sr|lead|principal|staff|head|chief|director|expert|leitung|leiter|teamleiter)\b"
ENTRY = r"\b(junior|graduate|absolvent|berufseinst\w*|einsteiger|entry|trainee|young professional)\b"
YEARS = r"(?<!\d)(\d{1,2})\s?\+?(?:\s*(?:-|–|bis|to)\s*\d{1,2})?\s*(?:years|jahre|ans|años|anni|jaar)"
# Worldwide job-type words (title, lower case). Packs add local ones (e.g. Werkstudent).
TYPES = {"internship": ["intern", "internship", "stage", "stagiaire", "prácticas", "tirocinio", "stagiair"],
         "student": ["working student", "student job", "student assistant", "student research", "part-time student"],
         "apprenticeship": ["apprentice", "apprenticeship", "alternance"],
         "thesis": ["thesis", "mémoire", "tesis"],
         "phd": ["phd", "doctoral", "doctorate", "doktorand", "promotion", "postdoc", "research associate", "research assistant",
                 "research scientist", "research engineer", "researcher", "wissenschaftliche", "wissenschaftlicher", "akademische mitarbeiter"],
         "graduate": ["graduate programme", "graduate program", "trainee"]}
# Words put in front of the person's titles and keywords to search for a job type (packs add local ones, e.g. Werkstudent).
SEARCH_WORDS = {"student": ["working student"], "internship": ["internship"], "thesis": ["thesis"],
                "apprenticeship": ["apprenticeship"], "graduate": ["graduate programme"]}
FULL = {"full_time", "part_time"}  # a research or graduate job also counts for these
RESEARCH_ORG = (r"universit|hochschule|(tu|th|rwth|kit|tum|lmu|eth|epfl)|institute of technology|fraunhofer|dlr|max.planck|"
                r"mpi|helmholtz|dfki|leibniz|forschungszentrum|research (centre|center|institute)|inria|cnrs")
LEVEL_OK = re.compile(r"\b(b2|c1|c2|native|mother|fluent|full professional|bilingual|muttersprach|fließend|courant|nativo)", re.I)
DEADLINE_WORDS = (r"(?:deadline|closing date|apply by|applications? (?:close|by)|bewerbungsfrist|bewerbungsschluss|einsendeschluss|"
                  r"bewerbung\w* (?:\w+ ){0,5}?bis(?: zum| spätestens)?|date limite|fecha l[ií]mite|scadenza|sluitingsdatum)")


def norm(text):
    return " " + re.sub(r"[^\w+#]+", " ", str(text or "").lower()) + " "


def loose_hit(words: str, title: str) -> bool:
    """Every word of a search word in a (norm'd) title, word endings aside: Softwareentwickler ~ Softwareentwicklung,
    Engineer ~ Engineering, Developer ~ Development. Every word counts; words under 7 letters must match whole."""
    ws = words.split()
    return bool(ws) and all(f" {w} " in title if len(w) < 7 else w[:max(6, len(w) - 3)] in title for w in ws)


@lru_cache
def language_codes() -> dict:
    """'german' / 'deutsch' / 'de' -> 'de' (English and native names, from Babel's CLDR data)."""
    from babel import Locale
    out = {}
    for code, name in Locale("en").languages.items():
        if len(code) != 2:
            continue
        out[name.lower()] = out[code] = code
        try:
            out[Locale.parse(code).get_display_name(code).lower()] = code
        except Exception:
            pass
    return out


@lru_cache
def cities():
    """city name (and alternate names) -> (lat, lon, country), the most populous place per name. GeoNames data (CC-BY)."""
    import geonamescache
    out = {}
    for c in geonamescache.GeonamesCache().get_cities().values():
        for name in [c["name"], *(c.get("alternatenames") or [])]:
            key = name.lower()
            if key and (key not in out or c["population"] > out[key][3]):
                out[key] = (c["latitude"], c["longitude"], c["countrycode"], c["population"])
    return out


def place(text):
    """First known city in a location string: (lat, lon, country) or None."""
    for part in re.split(r"[,/;|()]+| - ", str(text or "")):
        hit = cities().get(part.strip().lower())
        if hit:
            return hit
    return None


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def posting_language(text):
    if len(text) < 200:
        return ""
    from langdetect import DetectorFactory, detect_langs
    DetectorFactory.seed = 0
    try:
        best = detect_langs(text[:3000])[0]
    except Exception:
        return ""
    return best.lang if best.prob >= 0.9 else ""  # unsure is "", never a penalty


def deadline(text):
    import dateparser
    m = re.search(DEADLINE_WORDS + r"\W{0,3}(.{6,40})", text, re.I)
    if not m:
        return ""
    found = re.search(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\.? \w+ \d{4}|\w+ \d{1,2},? \d{4}", m.group(1))
    when = dateparser.parse(found.group(0), settings={"PREFER_DAY_OF_MONTH": "first", "DATE_ORDER": "DMY"}) if found else None
    return when.date().isoformat() if when and when.year >= date.today().year - 1 else ""


class Matcher:
    """Built once per search from the person's preferences and profile."""

    def __init__(self, prefs: dict, profile: dict):
        self.titles = {t.lower() for t in prefs.get("titles", []) if t.strip()}
        self.keywords = {k.lower() for words in prefs.get("keywords", {}).values() for k in words if k.strip()}
        tags = [t.replace("-", " ").lower() for sec in ("experience", "projects", "volunteering", "courses")
                for e in profile.get(sec, []) for b in e.get("bullets", []) for t in b.get("tags", [])]
        skills = {s.lower() for items in profile.get("skills", {}).values() for s in items if 2 < len(s) < 40}
        weights = {t: min(5, tags.count(t)) for t in set(tags) if len(t) > 2}
        self.skills = {**{s: 2 for s in skills}, **weights}
        self.job_types = set(prefs.get("job_types") or ["full_time"])
        self.countries = [t["code"] for t in prefs.get("targets") or [] if t.get("code")]  # a posting with no country: theirs
        codes = language_codes()
        self.speaks = {codes.get(l["name"].lower()) for l in profile.get("languages", []) if LEVEL_OK.search(l.get("level", "") or "")} - {None}
        self.speaks_any = {codes.get(l["name"].lower()) for l in profile.get("languages", [])} - {None}
        self.years = experience_years(profile)
        self.places = [p for p in (place(x) for x in prefs.get("places", [])) if p]
        self.radius = prefs.get("radius_km") or 50
        self.remote = prefs.get("remote", "any")
        self.prefer = [c.lower() for c in prefs.get("prefer_companies") or []]
        self.avoid = [c.lower() for c in prefs.get("avoid_companies") or []]
        self.exclude = [w.lower() for w in prefs.get("exclude_words") or []]
        self.w = {"skills": 1.0, "location": 1.0, "seniority": 1.0, "language": 1.0, **(prefs.get("weights") or {})}

    def kind(self, title, country="", company=""):
        t = norm(title)
        rules = {k: list(v) for k, v in TYPES.items()}
        for cc in [country] if country else getattr(self, "countries", []):
            for k, words in (countries.pack(cc).get("job_types") or {}).items():
                rules.setdefault(k, []).extend(words)
        found = next((k for k, words in rules.items() if any(f" {w} " in t or (len(w) > 6 and w in t) for w in words)), "full_time")
        if found == "full_time" and re.search(RESEARCH_ORG, company or "", re.I):  # universities and research institutes
            return "phd"
        return found

    def assess(self, j: dict, desc: str = "") -> tuple[dict, str | bool]:
        title, text = norm(j["title"]), norm(desc)
        t_hits = sorted(t for t in self.titles | self.keywords if f" {t} " in title or (len(t) > 5 and t in title)
                        or loose_hit(norm(t), title))
        s_title = sorted(s for s in self.skills if f" {s} " in title)
        s_desc = sorted((s for s in self.skills if f" {s} " in text and s not in s_title), key=lambda s: -self.skills[s])
        score = min(50, 20 * len(t_hits) + 6 * len(s_title)) + self.w["skills"] * min(30, 2 * sum(self.skills[s] for s in s_desc)) + \
            (10 if re.search(ENTRY, title) and self.years < 3 else 0)
        why, warn = [], []
        if t_hits or s_title or s_desc:
            why.append("matches: " + ", ".join(list(dict.fromkeys(t_hits + s_title + s_desc))[:6]))
        kind = self.kind(j["title"], j.get("country", ""), j.get("company", ""))
        word = lambda w, text: re.search(rf"(?<![a-zäöüß]){re.escape(w)}(?![a-zäöüß])", text.lower())  # "MAN" is not "Germany"
        preferred = any(word(c, j.get("company", "")) for c in self.prefer)
        if preferred:
            score += 10
            why.append("preferred company")
        if re.search(SENIOR, title) and self.years < 5:
            score -= 15 * self.w["seniority"]
            warn.append("senior role")
        asked = max((int(m[1]) for m in re.finditer(YEARS, text) if int(m[1]) <= 15
                     and re.search(r"experience|erfahrung|expérience|experiencia", text[max(0, m.start() - 60):m.end() + 60])), default=0)
        if asked > self.years + 1:
            score -= (20 if asked - self.years >= 3 else 10) * self.w["seniority"]
            warn.append(f"asks {asked}+ years")
        lang = posting_language(desc)
        if lang and self.speaks_any and lang not in self.speaks and lang not in self.speaks_any:
            score -= 25 * self.w["language"]
            warn.append(f"posting in {lang}")
        for language, pattern in (countries.pack(j.get("country", "")).get("language_required") or {}).items():
            code = language_codes().get(language.lower())
            if code not in self.speaks and re.search(pattern, text):
                score -= 20 * self.w["language"]
                warn.append(f"{language} required")
        far = ""
        if self.places and self.remote != "remote":
            here = place(j.get("location", ""))
            if here and min(km(here, p) for p in self.places) > self.radius and "remote" not in title + norm(j.get("location")):
                score -= 10 * self.w["location"]
                far = "far away"
        when = deadline(desc)
        fields = {"score": max(0, min(100, round(score))), "type": kind, "language": lang, "deadline": when,
                  "why": " · ".join(why + [f"⚠ {w}" for w in warn + ([far] if far else [])])}
        if when and when < date.today().isoformat():
            return fields, "expired"
        if any(word(c, j.get("company", "")) for c in self.avoid) or any(word(w, j["title"]) for w in self.exclude):
            return fields, "excluded"
        if kind not in self.job_types and not (kind in ("graduate", "phd") and self.job_types & FULL):  # research jobs show under Jobs if not picked
            return fields, "type"
        if not (t_hits or s_title) and len(s_desc) < 3:
            return fields, "junk"
        return fields, "weak" if score < 15 else False


def experience_years(profile) -> float:
    """Years of work experience from the profile's dated jobs (overlaps counted once)."""
    def month(v, end=False):
        v = str(v or "")
        if v.lower() == "present":
            return date.today().year * 12 + date.today().month
        m = re.match(r"(\d{4})(?:-(\d{2}))?", v)
        return int(m[1]) * 12 + (int(m[2]) if m[2] else (12 if end else 1)) if m else None
    spans = sorted((month(e.get("start")), month(e.get("end"), True) + 1) for e in profile.get("experience", [])
                   if month(e.get("start")) and month(e.get("end"), True))
    total, cur = 0, None
    for a, b in spans:
        if cur and a <= cur[1]:
            cur = (cur[0], max(cur[1], b))
        else:
            total += (cur[1] - cur[0]) if cur else 0
            cur = (a, b)
    total += (cur[1] - cur[0]) if cur else 0
    return total / 12
