"""The person's own files: master CV, preferences, form answers, writing style, lessons.

The master CV is the single source of truth: every line in a tailored CV or letter must trace back to it.
YAML is saved with ruamel.yaml so comments the person (or an earlier save) wrote stay in place.
"""
import io
import re
from datetime import date
from pathlib import Path

from babel import Locale

from pydantic import BaseModel, ConfigDict, Field
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from careerpilot.core.data import home
from careerpilot.core.score import experience_years


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)  # YAML reads 2017 as a number


class Basics(Model):
    name: str = ""
    headline: str = ""
    email: str = ""
    phone: str = ""
    location: str = Field("", description="City, Country")
    address: str = ""
    dob: str = Field("", description="Date of birth, only shown where the job's country expects it")
    nationality: str = ""
    links: dict[str, str] = Field(default_factory=dict, description="e.g. linkedin, github, portfolio")
    photo: str = Field("", description="File name in the profile folder")


class Bullet(Model):
    id: str
    text: str = Field(description="One achievement or duty. **bold** allowed. Facts only, as the person wrote them.")
    tags: list[str] = Field(default_factory=list, description="Short lowercase skill/topic tags")


class Entry(Model):
    """A job, project, course, volunteering role or custom-section item."""
    id: str
    title: str = Field("", description="Role or project title")
    org: str = Field("", description="Company, organisation or school")
    unit: str = ""
    location: str = ""
    start: str = Field("", description="YYYY-MM or YYYY")
    end: str = Field("", description="YYYY-MM, YYYY or 'present'")
    link: str = ""
    bullets: list[Bullet] = Field(default_factory=list)


class Education(Model):
    id: str
    degree: str = ""
    school: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    details: str = Field("", description="Grade, thesis, key courses")
    only_for: list[str] = Field(default_factory=list, description="Shown only on CVs for these job types (e.g. student, internship); empty = always")


class Item(Model):
    """Certification, award or publication: one line each."""
    id: str
    name: str
    issuer: str = ""
    date: str = ""
    link: str = ""


class Language(Model):
    name: str
    level: str = Field("", description="CEFR level (A1-C2) or Native")


class Section(Model):
    title: str
    entries: list[Entry] = Field(default_factory=list)


class Profile(Model):
    basics: Basics = Field(default_factory=Basics)
    summaries: dict[str, str] = Field(default_factory=dict, description="Short profile texts, keyed by target role")
    experience: list[Entry] = Field(default_factory=list)
    projects: list[Entry] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    skills: dict[str, list[str]] = Field(default_factory=dict, description="Category -> skills")
    languages: list[Language] = Field(default_factory=list)
    certifications: list[Item] = Field(default_factory=list)
    awards: list[Item] = Field(default_factory=list)
    publications: list[Item] = Field(default_factory=list)
    volunteering: list[Entry] = Field(default_factory=list)
    courses: list[Entry] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    custom: list[Section] = Field(default_factory=list, description="Any other section, with its own title")


class Country(Model):
    code: str = Field(description="ISO 3166 alpha-2, e.g. DE")
    permit: str = Field("", description="Work permit status for this country, in the person's words")
    needs_sponsorship: bool = False


class Preferences(Model):
    home_country: str = ""
    targets: list[Country] = Field(default_factory=list)
    app_language: str = "en"
    agent: str = ""
    titles: list[str] = Field(default_factory=list, description="Job titles to search for")
    keywords: dict[str, list[str]] = Field(default_factory=dict, description="Language code -> search words")
    job_types: list[str] = Field(default_factory=lambda: ["full_time"])
    side_types: list[str] = Field(default_factory=list, description="Chosen job types marked \"Also\": searched half as often")
    places: list[str] = Field(default_factory=list)
    radius_km: int = 50
    remote: str = Field("any", description="any | remote | hybrid | onsite")
    salary: str = ""
    start: str = ""
    notice: str = ""
    # Asked in setup step 1 for student job types (with Application details' "Expected graduation"); empty = not stated
    # (never guessed). Used in letters, forms and search.
    hours: str = Field("", description="Hours per week the person can work during the semester (working student)")
    mandatory_internship: bool = Field(False, description="The internship must be a mandatory one (Pflichtpraktikum) in their studies")
    internship_months: str = Field("", description="How many months an internship can last")
    thesis_level: str = Field("", description="bachelor | master, for thesis postings")
    doc_language: str = Field("match", description="'match' = the posting's language, or an ISO 639-1 code")
    review_depth: str = Field("all", description="all | big | none")
    design: str = "classic"
    instructions: str = Field("", description="The person's own instructions, added to every AI run")
    cost_level: str = Field("standard", description="economy | standard | premium")
    search_time: str = Field("", description="HH:MM for the daily search, empty = off")
    ai_ranking: bool = Field(False, description="Let the AI re-rank the day's best postings after a search")
    ai_mail: bool = Field(True, description="Let the AI read application mails the rules can't sort (at most 10 per check)")
    ai_second_look: bool = Field(True, description="Let the AI look again at postings filtered out on their title alone (once per search)")
    weights: dict[str, float] = Field(default_factory=lambda: {"skills": 1.0, "location": 1.0, "seniority": 1.0, "language": 1.0},
                                      description="How much each part of the fit score counts (0 = ignore, 2 = double)")
    alert_events: list[str] = Field(default_factory=lambda: ["matches", "interview", "assessment", "offer", "reply", "digest"])
    alert_email: bool = Field(False, description="Also send alerts by email to the person's own address")
    desktop_alerts: bool = Field(True, description="Interview, test, offer and action-needed alerts also show on this computer")
    theme: str = Field("auto", description="auto | light | dark")
    email: dict = Field(default_factory=dict, description="address, provider (gmail, outlook…) or imap/smtp/port; the password is in the password store")
    follow_up_days: int = 10
    sources_off: list[str] = Field(default_factory=list, description="Job sources the person switched off")
    prefer_companies: list[str] = Field(default_factory=list, description="Companies that get a ranking bonus")
    avoid_companies: list[str] = Field(default_factory=list, description="Companies whose postings are hidden")
    exclude_words: list[str] = Field(default_factory=list, description="Words that hide a posting when its title has them")
    setup_done: bool = False
    setup_step: int = 0
    setup_hidden: list[str] = Field(default_factory=list, description="'Finish setting up' items the person dismissed")


class Targets(Model):
    titles: list[str]
    keywords: dict[str, list[str]]


class Style(Model):
    markdown: str


FILES = {"profile": "master_cv.yaml", "preferences": "preferences.yaml", "answers": "answers.yaml", "companies": "companies.yaml", "designs": "designs.yaml"}
TEXTS = {"style": "style.md", "lessons": "lessons.md", "letter": "letter.md", "cv_rules": "cv_rules.md"}
# style: letter instructions; letter: the person's own cover letter; cv_rules: CV instructions (all in Profile › Writing)



def _yaml() -> YAML:
    """A new instance per call: ruamel.yaml instances are not thread-safe (the app serves requests in parallel)."""
    y = YAML()
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


BANNED = "## Banned phrases"


def banned_section(text: str) -> str:
    """The banned-phrases part of CV or letter instructions (checked automatically, so it always stays), '' if none."""
    i = text.find(BANNED)
    return text[i:] if i >= 0 else ""


def folder() -> Path:
    path = home() / "profile"
    path.mkdir(exist_ok=True)
    return path


def read(kind: str):
    """Plain data of profile / preferences / answers, or the text of style / lessons."""
    if kind in TEXTS:
        path = folder() / TEXTS[kind]
        return path.read_text(encoding="utf-8") if path.exists() else ""
    path = folder() / FILES[kind]
    data = _plain(_yaml().load(path)) if path.exists() else {}
    if kind == "preferences":  # a setting a later version removed (fit_threshold, upload_folder) must not stop an older file loading
        data = {k: v for k, v in data.items() if k in Preferences.model_fields}
    return data


def write(kind: str, value):
    if kind in TEXTS:
        (folder() / TEXTS[kind]).write_text(value, encoding="utf-8")
        return
    model = {"profile": Profile, "preferences": Preferences}.get(kind)
    if model:
        value = model.model_validate(value).model_dump()
    path = folder() / FILES[kind]
    y = _yaml()
    old = y.load(path) if path.exists() else CommentedMap()
    buf = io.StringIO()
    y.dump(_merge(old, value), buf)
    path.write_text(buf.getvalue(), encoding="utf-8")


def profile() -> Profile:
    return Profile.model_validate(read("profile"))


DEGREE_RANK = [(3, r"\bph\.? ?d|doctor|dr\.|promotion"), (2, r"master|\bm\.? ?sc|\bm\.? ?a\b|m\.? ?eng|mba|diplom|magister|staatsexamen"),
               (1, r"bachelor|\bb\.? ?sc|\bb\.? ?a\b|b\.? ?eng|licen")]


def _future(end: str) -> bool:
    """An end that hasn't come yet: 'present', or a YYYY / YYYY-MM after this month."""
    end = str(end or "").strip().lower()
    m = re.match(r"(\d{4})(?:-(\d{2}))?", end)
    return end == "present" or bool(m) and (int(m[1]), int(m[2] or 12)) > (date.today().year, date.today().month)


def suggested_answers(profile: dict, prefs: dict, saved: dict | None = None) -> dict[str, str]:
    """Form answers that follow from the CV and preferences alone, nothing guessed. A saved answer always wins: its
    question is left out. Only the answers the person kept are ever used."""
    out = {}
    edu = profile.get("education") or []
    done = [e for e in edu if e.get("degree") and not _future(e.get("end"))] or [e for e in edu if e.get("degree")]
    if done:
        rank = lambda e: next((r for r, rx in DEGREE_RANK if re.search(rx, e["degree"], re.I)), 0)
        out["Highest degree"] = max(done, key=rank)["degree"]  # max keeps the first of equals: the CV's own order
    years = experience_years(profile)
    if years >= 1:
        out["Years of experience"] = f"{years:.0f}"
    out["Nationality"] = (profile.get("basics") or {}).get("nationality", "")
    out["Salary expectation"], out["Earliest start date"], out["Notice period"] = (prefs.get(k, "") for k in ("salary", "start", "notice"))
    out["Hours per week"], out["Internship length (months)"] = prefs.get("hours", ""), prefs.get("internship_months", "")  # students
    if prefs.get("mandatory_internship"):
        out["Mandatory internship?"] = "Yes"
    names = Locale("en").territories
    for c in prefs.get("targets") or []:
        name, permit = names.get(c["code"], c["code"]), c.get("permit", "")
        if c.get("needs_sponsorship"):
            out[f"Authorised to work in {name}?"], out[f"Need visa sponsorship for {name}?"] = "No", "Yes"
        elif permit and not re.search(r"not sure|unsure|unknown", permit, re.I):
            out[f"Authorised to work in {name}?"], out[f"Need visa sponsorship for {name}?"] = "Yes", "No"
    now = next((e for e in edu if _future(e.get("end"))), None)
    if now:
        out["Current university"], out["Current degree programme"] = now.get("school", ""), now.get("degree", "")
        out["Expected graduation"] = "" if str(now.get("end")).lower() == "present" else str(now["end"])
    for l in profile.get("languages") or []:
        out[f"{l['name']} level"] = l.get("level", "")
    saved = saved or {}
    return {k: str(v) for k, v in out.items() if str(v or "").strip() and not str(saved.get(k) or "").strip()}


def keep_existing(old: dict, new: dict) -> dict:
    """Safety net for AI updates: whatever the old profile had and the update left out comes back (by id, by name,
    by skill). An update may add or change things, never silently delete them."""
    for sec, value in old.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            key = "id" if "id" in value[0] else "name" if "name" in value[0] else "title" if "title" in value[0] else None
            if not key:
                continue
            have = {x.get(key): x for x in new.get(sec, [])}
            for x in value:
                if x.get(key) not in have:
                    new.setdefault(sec, []).append(x)
                elif "bullets" in x:  # lines inside a job / project
                    ids = {b["id"] for b in have[x[key]].get("bullets", [])}
                    have[x[key]].setdefault("bullets", []).extend(b for b in x["bullets"] if b["id"] not in ids)
                elif "entries" in x:  # custom sections
                    keep_existing({"entries": x["entries"]}, have[x[key]])
        elif isinstance(value, dict) and sec in ("skills", "summaries", "links"):
            for k, v in value.items():
                if k not in new.setdefault(sec, {}):
                    new[sec][k] = v
                elif isinstance(v, list):
                    new[sec][k] = new[sec][k] + [s for s in v if s not in new[sec][k]]
        elif isinstance(value, list) and value and sec not in new:
            new[sec] = value
    if isinstance(old.get("basics"), dict):
        b = new.setdefault("basics", {})
        for k, v in old["basics"].items():
            if v and not b.get(k):
                b[k] = v
        keep_existing({"links": old["basics"].get("links") or {}}, b)
    return new


def _merge(old, new):
    """Write new values into the loaded YAML tree so comments on unchanged keys and same-id items survive."""
    if isinstance(old, dict) and isinstance(new, dict):
        for k in [k for k in old if k not in new]:
            del old[k]
        for k, v in new.items():
            old[k] = _merge(old.get(k), v)
        return old
    if isinstance(old, list) and isinstance(new, list):
        by_id = {o.get("id"): o for o in old if isinstance(o, dict) and o.get("id")}
        items = [_merge(by_id[n["id"]], n) if isinstance(n, dict) and n.get("id") in by_id else n for n in new]
        old[:] = items
        return old
    return new


def _plain(node):
    if isinstance(node, dict):
        return {str(k): _plain(v) for k, v in node.items()}
    if isinstance(node, (list, CommentedSeq)):
        return [_plain(v) for v in node]
    return node
