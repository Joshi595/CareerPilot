"""Apply to one posting: tailored CV + letter (one tailor run, one reviewer run), review cards,
the person's decisions, PDFs with checks, archive. Ported from the original pipeline's apply.py.

Work files live in <data>/work/<job key>/ until the application is archived in <data>/applications/.
"""
import json
import os
import re
import shutil
import threading
from datetime import date
from pathlib import Path

from ruamel.yaml import YAML

from rapidfuzz import fuzz

from jobquill.core import ai, check, posting, prompts, render, sources
from jobquill.core import profile as pf
from jobquill.core.data import db, home
from jobquill.core.profile import Field, Model

MAX_PROJECTS = 3
ENTRY_SECTIONS = ("experience", "projects", "volunteering", "courses")


# ---------------------------------------------------------------- what the AI answers
class Job(Model):
    """What the tailor run reads off the posting; stored as fit.json for the later steps."""
    company: str
    role: str
    employment_type: str
    ref_id: str
    deadline: str
    country: str
    language: str
    blockers: list[str]
    keywords: list[str]
    contact_name: str


class BulletEdit(Model):
    id: str
    new: str
    reason: str
    keywords: list[str]
    flag: bool


class Order(Model):
    key: str
    ids: list[str]


class SkillEdit(Model):
    category: str
    items: str


class Letter(Model):
    subject: str
    salutation: str
    recipient: list[str]
    paragraphs: list[str]
    sources: list[list[str]] = Field(description="per paragraph, the fact-sheet ids it is built on ([] when none)")


class Tailor(Model):
    job: Job
    summary: str
    summary_reason: str
    bullets: list[BulletEdit]
    orders: list[Order]
    skills: list[SkillEdit]
    letter: Letter


class Edit(Model):
    id: str
    new: str
    reason: str
    note: str = ""
    flag: bool = False


class Review(Model):
    edits: list[Edit]
    missed: list[str]
    stretches: list[str]


class Revision(Model):
    edits: list[Edit]


class Change(Revision):
    language: str = Field(description="ISO 639-1 code of the language the person wants the documents in: the current one "
                                      "unless they ask for another")


class Lessons(Model):
    markdown: str


# ---------------------------------------------------------------- work folder
def work(key: str):
    """The work folder, created if missing (for writing). workdir(): the same path, never created (for reading)."""
    path = workdir(key)
    path.mkdir(parents=True, exist_ok=True)
    return path


def workdir(key: str) -> Path:
    """The work folder without creating it (for reading). A key is letters, digits, _ and - only: never a path ("..")."""
    if not re.fullmatch(r"[\w-]+", key or ""):
        raise ValueError(f"not a job key: {key[:40]!r}")
    return home() / "work" / key


def current() -> Path:
    """The fixed upload folder, "Current application" in the JobQuill folder (it comes with the repo; its contents
    are git-ignored): everything the person uploads when applying. Their own documents (certificates, transcripts,
    references…, added once during setup) stay; the CV and letter of the application open right now are swapped in and
    out. A test home keeps its own; installed as a package (no repo), Documents/JobQuill holds it."""
    if os.environ.get("JOBQUILL_HOME"):
        return home() / "Current application"
    repo = Path(__file__).resolve().parents[2]
    if (repo / "pyproject.toml").exists():
        return repo / "Current application"
    # ponytail: ~/Documents, not the Windows known-folder API; a Documents folder moved to OneDrive gets a local one.
    return Path.home() / "Documents" / "JobQuill" / "Current application"


def placed() -> list[str]:
    """The files the app put in the upload folder: only these are ever replaced or removed there."""
    p = home() / "current.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def show_files(folder: Path) -> str:
    """These PDFs (a work or archive folder) become the current application's; the previous application's go.
    Best effort: a PDF open in another program is locked on Windows; then a short message comes back, nothing breaks."""
    out = current()
    try:
        out.mkdir(parents=True, exist_ok=True)
        for name in placed():
            (out / name).unlink(missing_ok=True)
        names = [pdf.name for pdf in folder.glob("*.pdf")]
        for name in names:
            shutil.copy2(folder / name, out / name)
        (home() / "current.json").write_text(json.dumps(names), encoding="utf-8")
        return ""
    except OSError as e:
        return f"Couldn't update the Current application folder ({e.strerror}). Close the PDF if it is open, then try again."


def clear_current(folder: Path):
    """Sent: its CV and letter leave the upload folder (if they are still the ones there); the person's documents stay."""
    names = sorted(p.name for p in folder.glob("*.pdf"))
    if names and names == sorted(placed()):
        for name in names:
            try:
                (current() / name).unlink(missing_ok=True)
            except OSError:
                pass  # open in a PDF viewer: it stays; the next application replaces it
        (home() / "current.json").write_text("[]", encoding="utf-8")


def own_documents() -> list[str]:
    """What the person keeps in the upload folder besides the current CV and letter."""
    out, mine = current(), set(placed())
    return sorted(p.name + ("/" if p.is_dir() else "") for p in out.iterdir()
                  if p.name not in mine and p.name != "README.md") if out.is_dir() else []  # README: comes with the repo


def load(key, name, default=None):
    p = workdir(key) / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def dump(key, name, data):
    (work(key) / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def state(key) -> dict:
    s = load(key, "state", {"stage": "new"})
    if s["stage"] == "drafting" and key not in RUNNING:  # the app stopped mid-draft: start again
        s["stage"] = "new"
    s["fit"] = load(key, "fit")
    s["proposal"] = load(key, "proposal")
    s["files"] = sorted(p.name for p in workdir(key).glob("*.pdf"))
    s["usage"] = ai.calls(key)
    s["choices"] = load(key, "choices")  # the review cards' choices so far: leaving the page keeps them
    s["folder"] = str(current()) if s["files"] else ""
    s["documents"] = own_documents()
    return s


def set_stage(key, stage, **extra):
    s = load(key, "state", {})
    s.update(stage=stage, **extra)
    dump(key, "state", s)


# ---------------------------------------------------------------- facts the AI may use
def entries(p: dict) -> dict:
    out = {e["id"]: e for sec in ENTRY_SECTIONS for e in p.get(sec, [])}
    out.update({e["id"]: e for c in p.get("custom", []) for e in c.get("entries", [])})
    return out


def bullets(p: dict) -> dict:
    return {b["id"]: b["text"] for e in entries(p).values() for b in e.get("bullets", [])}


def factsheet(p: dict) -> str:
    out = []
    for sec in (*ENTRY_SECTIONS, "custom"):
        items = p.get(sec, []) if sec != "custom" else [e for c in p.get("custom", []) for e in c.get("entries", [])]
        for e in items:
            out.append(f"## [{e['id']}] {e.get('title', '')} | {e.get('org', '')} | {e.get('start', '')}–{e.get('end', '')} ({sec})")
            out += [f"- [{b['id']}] {b['text']}" for b in e.get("bullets", [])]
    out.append("## Education")
    out += [f"- {e.get('degree')}, {e.get('school')}, {e.get('start')}–{e.get('end')}. {e.get('details', '')}" for e in p.get("education", [])]
    out.append("## Skills")
    out += [f"- {k}: {', '.join(v)}" for k, v in p.get("skills", {}).items()]
    out.append("## Languages\n- " + "; ".join(f"{l['name']}: {l.get('level', '')}" for l in p.get("languages", [])))
    for sec in ("certifications", "awards", "publications"):
        if p.get(sec):
            out.append(f"## {sec.title()}")
            out += [f"- {i['name']}, {i.get('issuer', '')} {i.get('date', '')}" for i in p[sec]]
    if p.get("summaries"):
        out.append("## Profile texts\n" + "\n".join(f"- {v}" for v in p["summaries"].values()))
    return "\n".join(out)


def variants_text(limit=3) -> str:
    path = pf.folder() / "variants.yaml"
    lib = YAML(typ="safe").load(path) if path.exists() else {}
    return "\n".join(f"[{bid}] " + " | ".join(v["text"] for v in vs[-limit:]) for bid, vs in (lib or {}).items()) or "(none yet)"


def style_text() -> str:
    style = (pf.read("style") or prompts.load("default_style")).split("## Banned phrases")[0].strip()
    own = pf.read("letter").strip()  # Profile › Cover letter
    return style + ("\n\n## The person's own letter\nFollow its structure and voice; take facts only from the fact sheet.\n\n" + own if own else "")


def cv_rules() -> str:
    return pf.read("cv_rules").strip() or prompts.load("default_cv_rules").strip()  # Profile › Writing › CV instructions


def prefs() -> pf.Preferences:
    return pf.Preferences.model_validate(pf.read("preferences"))


def agent():
    return ai.chosen(prefs().agent)


def untrusted(text: str) -> str:
    """A posting or mail can't close (or reopen) its own fence, not even with a tag built from what one removal leaves."""
    while True:
        cleaned = re.sub(r"<\s*/?\s*untrusted\s*>", "", text, flags=re.I)
        if cleaned == text:
            return text
        text = cleaned


# ---------------------------------------------------------------- already applied
def _company(name) -> str:
    return sources.slug(re.sub(sources.LEGAL, "", name or "", flags=re.I), 60)


def applied_index():
    """Sent applications (not the ones saved for later), by job key and by company, for applied_to()."""
    with db() as con:
        rows = [dict(r) for r in con.execute("select id, job_key, company, role, date_applied, status from applications"
                                             " where status != 'pending'")]
    by_company = {}
    for r in rows:
        by_company.setdefault(_company(r["company"]), []).append(r)
    return {r["job_key"]: r for r in rows if r["job_key"]}, by_company


def applied_to(job: dict, index=None) -> dict | None:
    """The application already sent for this job: the same posting, or the same job found on another site (same
    company, near-identical title). None when there is none."""
    by_key, by_company = index or applied_index()
    if job.get("key") in by_key:
        return by_key[job["key"]]
    words = lambda t: " ".join(re.findall(r"\w+", sources.clean_title(t or "").lower()))
    title = words(job.get("title"))
    for a in by_company.get(_company(job.get("company")), []) if title else []:
        if fuzz.token_sort_ratio(title, words(a["role"])) >= 95:  # punctuation and word order differ; an extra word is another job
            return a
    return None


# ---------------------------------------------------------------- steps
RESUMABLE = ("new", "shortlisted", "filtered", "paused")  # f"in {RESUMABLE}" is the SQL list; statuses an application in progress moves to 'applying' from
RUNNING, LOCK = set(), threading.Lock()  # applications being drafted right now: never two runs (and two bills) for one


def availability(pr: pf.Preferences) -> str:
    """What setup step 1 asked students (hours, studies, internship, thesis), for the tailor run."""
    grad = str((pf.read("answers") or {}).get("Expected graduation") or "").strip()
    parts = [pr.hours and f"{pr.hours} hours per week during the semester", grad and f"studies until {grad}",
             pr.mandatory_internship and "the internship must be a mandatory one (Pflichtpraktikum) in their studies",
             pr.internship_months and f"an internship of up to {pr.internship_months} months",
             pr.thesis_level and f"{pr.thesis_level} thesis"]
    return "; ".join(p for p in parts if p) or "not stated"


def discard(key: str):
    """Throw an application in progress away (Start over / Discard draft): its work folder, its uploads, its open
    questions; the job goes back to the Board as new. The AI ledger keeps its rows: that money was spent."""
    with LOCK:
        if key in RUNNING:
            raise ai.AIError("This application is being written right now. Wait until it's ready, then start over.")
    clear_current(workdir(key))
    shutil.rmtree(workdir(key), ignore_errors=True)
    shutil.rmtree(home() / "uploads" / key, ignore_errors=True)
    with db() as con:
        con.execute("delete from needs where job_key=?", (key,))
        con.execute("update jobs set status='new' where key=? and status in ('applying', 'paused')", (key,))


def draft(key: str, depth: str = "", stop_on_blocker: bool = False, note: str = "", language: str = "") -> dict:
    """Tailor run (which also reads the job's details off the posting) + reviewer run -> proposal cards (CV and letter).
    stop_on_blocker: a blocker found by the tailor run ends here, before the reviewer run.
    note: the person's wish for this draft only (Start over, the Apply page's AI box); language: overrides doc_language."""
    done = applied_to({**(posting.get(key) or {}), "key": key})
    if done:
        raise ai.AIError(f"You already applied to this job on {done['date_applied']}. It's in My applications.")
    with LOCK:
        if key in RUNNING:
            raise ai.AIError("This application is already being written. It shows here as soon as it's ready.")
        RUNNING.add(key)
    resume = load(key, "state", {}).get("stage") == "blocked" and load(key, "tailor")  # wanted anyway: its tailor run is paid for
    try:
        set_stage(key, "drafting")
        return _draft(key, depth, stop_on_blocker, resume, note.strip(), language)
    except BaseException:
        if load(key, "state", {}).get("stage") == "drafting":
            set_stage(key, "blocked" if resume else "new")
        raise
    finally:
        RUNNING.discard(key)


def _draft(key: str, depth: str, stop_on_blocker: bool, resume=None, note: str = "", language: str = "") -> dict:
    job = posting.get(key)
    with db() as con:  # Board › In progress, until it is archived
        con.execute(f"update jobs set status='applying' where key=? and status in {RESUMABLE}", (key,))
    p, pr = pf.profile().model_dump(), prefs()
    want = language or pr.doc_language
    permits = "; ".join(f"{c.code}: {c.permit or 'unknown'}" + (" (needs sponsorship)" if c.needs_sponsorship else "") for c in pr.targets)
    style = style_text()
    paras = "4" if "short" in style.lower()[:400] else "5"
    t = resume or ai.answer(prompts.build(
        "tailor", factsheet=factsheet(p), style=style, lessons=pf.read("lessons") or "(none yet)", variants=variants_text(),
        instructions=pr.instructions or "(none)", cv_rules=cv_rules(),
        language="the language the posting is written in" if want == "match" else want,
        languages=", ".join(f"{l['name']} {l.get('level', '')}" for l in p.get("languages", [])),
        posting=untrusted(job["description"]), max_projects=str(MAX_PROJECTS), paragraphs=paras,
        start=pr.start or "to be agreed", permits=permits or "not stated", availability=availability(pr),
        note=note or "(nothing)"), Tailor, agent(), 900, key).model_dump()
    dump(key, "tailor", t)
    fit = {**t["job"], "score": job.get("score")}
    dump(key, "fit", fit)
    with db() as con:
        con.execute("update jobs set company=coalesce(nullif(company, ''), ?), title=coalesce(nullif(title, ''), ?),"
                    " deadline=coalesce(nullif(deadline, ''), ?), country=coalesce(nullif(country, ''), ?) where key=?",
                    (fit["company"], fit["role"], fit["deadline"], fit["country"], key))
    if stop_on_blocker and fit["blockers"]:
        set_stage(key, "blocked")
        return {"stage": "blocked", "blockers": fit["blockers"]}
    lang = fit["language"] or ("en" if want == "match" else want)  # what the tailor wrote in (a note may ask another)
    items = cards(t, p, {})
    letter = t["letter"]
    draft_text = "\n".join(f"{i['id']}: {i['old']} → {i['new']}" for i in items) + "\n\n" + \
                 "\n".join(f"p{n}: {x}" for n, x in enumerate(letter["paragraphs"], 1))
    rev = ai.answer(prompts.build("review", factsheet=factsheet(p), style=style, cv_rules=cv_rules(), draft=draft_text,
                                  posting=untrusted(job["description"])), Review, agent(), 900, key).model_dump()
    dump(key, "review", rev)
    merge_review(items, letter, rev["edits"], bullets(p))
    flag_scope(items, bullets(p))
    proposal = {"items": items, "letter": [dict(id=f"p{n}", section=f"Paragraph {n}", old="", new=x, keywords=[], flag=False,
                                                reason=based_on(p, (letter.get("sources") or [])[n - 1:n]))
                                           for n, x in enumerate(letter["paragraphs"], 1)],
                "letter_meta": {k: letter[k] for k in ("subject", "salutation", "recipient", "paragraphs")},
                "keywords": fit.get("keywords", []), "missed": rev["missed"], "stretches": rev["stretches"],
                "context": context_text(p, items), "language": lang}
    proposal = apply_depth(proposal, depth or pr.review_depth)
    dump(key, "proposal", proposal)
    (work(key) / "choices.json").unlink(missing_ok=True)
    set_stage(key, "review", language=lang)
    return proposal


def based_on(p: dict, src: list) -> str:
    """A letter paragraph's sources, as the person knows them: "Based on: <line> · <job>" (ids not in the CV are left out)."""
    ents, bl = entries(p), bullets(p)
    names = [label(bl[i], 5) if i in bl else ents[i].get("title") or ents[i].get("org", "") for i in (src[0] if src else [])
             if i in bl or i in ents]
    return "Based on: " + " · ".join(names) if names else ""


def label(text, n=6):
    return " ".join(re.sub(r"\*\*", "", text).split()[:n]) + "…"


def cards(t: dict, p: dict, accepted: dict) -> list[dict]:
    ents, bl = entries(p), bullets(p)
    items = []
    if t.get("summary"):
        old = accepted.get("summary", {}).get("text") or next(iter(p.get("summaries", {}).values()), "")
        items.append(dict(id="summary", section="Profile", old=old, new=t["summary"], reason=t.get("summary_reason", ""), keywords=[], flag=False))
    for o in t.get("orders", []):
        key, new = o["key"], o["ids"]
        if key == "skills":
            old, show, name = list(p.get("skills", {})), (lambda x: x), "Skills · category order"
        elif key == "projects":
            old, show, name = [x["id"] for x in p.get("projects", [])], (lambda x: ents[x]["title"]), f"Projects · the {MAX_PROJECTS} shown, in order"
        elif key in ents:
            old, show, name = [b["id"] for b in ents[key]["bullets"]], (lambda x: label(bl[x])), f"{ents[key].get('title')} · line order"
        else:
            continue
        new = [i for i in new if i in old]
        fmt = lambda seq: "  ".join(f"{n}. {show(i)}" for n, i in enumerate(seq, 1))
        left = [i for i in old if i not in new[:MAX_PROJECTS]] if key == "projects" else []
        items.append(dict(id=f"order_{key}", section=name, old=fmt(old), order=new, keywords=[], flag=False,
                          new=fmt(new[:MAX_PROJECTS] if key == "projects" else new) + (("   Left out: " + ", ".join(map(show, left))) if left else ""),
                          reason="Order only. To change it, use the comment box."))
    for e in t.get("bullets", []):
        if e["id"] not in bl:
            continue
        owner = next(x for x in ents.values() if any(b["id"] == e["id"] for b in x.get("bullets", [])))
        items.append(dict(id=e["id"], section=f"{owner.get('org') or owner.get('title')} · {'remove' if not e['new'] else 'line'}",
                          old=accepted.get(e["id"], {}).get("text") or bl[e["id"]], new=e["new"], reason=e.get("reason", ""),
                          keywords=e.get("keywords", []), flag=bool(e.get("flag"))))
    for s in t.get("skills", []):
        if s["category"] in p.get("skills", {}):
            items.append(dict(id=f"skills_{s['category']}", section=f"Skills · {s['category']}",
                              old=f"{s['category']}: {', '.join(p['skills'][s['category']])}", new=f"{s['category']}: {s['items']}",
                              reason="", keywords=[], flag=False))
    return items


# Opening-verb scope tiers: 1 took part, 2 did the work, 3 owned it (EN + DE).
SCOPE = {**dict.fromkeys("contributed helped supported assisted unterstützte half wirkte".split(), 1),
         **dict.fromkeys("built implemented designed developed created entwickelte implementierte baute konzipierte erstellte".split(), 2),
         **dict.fromkeys("led owned architected drove spearheaded headed managed leitete verantwortete führte".split(), 3)}


def scope(text):
    w = re.findall(r"[^\W\d_]+", text.lower())[:1]
    return SCOPE.get(w[0], 0) if w else 0


def flag_scope(items, bl):
    """A line that opens with a bigger role than its master text ("Led" for "Built") is flagged."""
    for i in items:
        o, n = (scope(bl[i["id"]]), scope(i["new"])) if i["id"] in bl and i["new"] else (0, 0)
        if n > o and (n == 3 or o == 1) and not i.get("scope"):
            i.update(flag=True, scope=True, reason=f"Bigger role than your profile says ({bl[i['id']].split()[0].strip('*')}). {i['reason']}".strip())


def reviewed(e) -> str:
    """The reviewer's note as the person reads it (its internal category, e.g. 'keyword', stays out)."""
    note = (e.get("note") or "").strip().rstrip(".")
    return f"Reviewer: {note}." if note else "Sharpened by the reviewer."


def merge_review(items, letter, edits, bl):
    by = {i["id"]: i for i in items}
    for e in edits:
        if re.fullmatch(r"p\d+", e["id"]) and int(e["id"][1:]) <= len(letter["paragraphs"]):
            letter["paragraphs"][int(e["id"][1:]) - 1] = e["new"]
        elif e["id"] in by and "order" not in by[e["id"]]:
            by[e["id"]].update(new=e["new"], flag=by[e["id"]]["flag"] or e.get("flag", False),
                               reason=" ".join(filter(None, [reviewed(e), by[e["id"]]["reason"]])))
        elif e["id"] in bl:
            items.append(dict(id=e["id"], section="Added by the reviewer", old=bl[e["id"]], new=e["new"], keywords=[],
                              flag=e.get("flag", False), reason=reviewed(e)))


def context_text(p, items):
    touched = {i["id"] for i in items}
    ctx = [t for k, t in bullets(p).items() if k not in touched]
    ctx += [f"{e.get('title', '')} {e.get('org', '')}" for e in entries(p).values()]
    ctx += [f"{e.get('degree')} {e.get('school')} {e.get('details', '')}" for e in p.get("education", [])]
    ctx += [f"{k}: {', '.join(v)}" for k, v in p.get("skills", {}).items() if f"skills_{k}" not in touched]
    return " ".join(ctx)


def auto_decisions(items):
    """Every card accepted, except flagged ones: those keep the profile's wording (only the person can confirm them)."""
    return [dict(id=i["id"], decision="reject" if i.get("flag") else "accept", text=i["old"] if i.get("flag") else i["new"], comment="")
            for i in items]


def apply_depth(proposal, depth):
    """'big': small unflagged line edits are pre-accepted and hidden; 'none': nothing is shown."""
    if depth == "all":
        return proposal
    hide = [i for i in proposal["items"] if depth == "none" or not (i.get("flag") or i["id"] == "summary" or not i["new"])]
    proposal["auto"] = auto_decisions(hide)
    proposal["items"] = [i for i in proposal["items"] if i not in hide]
    if depth == "none":
        proposal["auto"] += auto_decisions(proposal["letter"])
        proposal["letter"] = []
    return proposal


def decide(key: str, decisions: list[dict]) -> dict:
    """Store the person's decisions. Cards marked revise go to a revise run and come back as new cards."""
    prop = load(key, "proposal")
    (work(key) / "choices.json").unlink(missing_ok=True)
    with db() as con:  # a paused application that moves on is in progress again
        con.execute(f"update jobs set status='applying' where key=? and status in {RESUMABLE}", (key,))
    decisions = decisions + prop.get("auto", [])
    rounds = load(key, "rounds", [])
    rounds.append(decisions)
    dump(key, "rounds", rounds)
    acc = load(key, "accepted", {})
    meta = {i["id"]: i for i in prop["items"] + prop["letter"]}
    letter = load(key, "letter") or dict(prop["letter_meta"])  # the full draft, also when no paragraph is shown for review
    paras = {f"p{n}": x for n, x in enumerate(letter["paragraphs"], 1)}
    revise = []
    for d in decisions:
        if d["decision"] == "revise":
            revise.append(d)
        elif d["id"] in paras:
            paras[d["id"]] = "" if d["decision"] == "reject" else d["text"]
        elif d["decision"] in ("accept", "edit"):
            acc[d["id"]] = {"text": d["text"], **({"order": meta[d["id"]]["order"]} if "order" in meta.get(d["id"], {}) else {})}
        elif d["decision"] == "reject":
            acc.pop(d["id"], None)
    letter["paragraphs"] = [paras[k] for k in sorted(paras, key=lambda k: int(k[1:]))]  # "" = rejected, kept so p-ids stay stable
    dump(key, "accepted", acc)
    dump(key, "letter", letter)
    if not revise:
        return build(key)
    job, p = posting.get(key), pf.profile().model_dump()
    todo = "\n".join(f"{r['id']}: {meta[r['id']]['new']} | comment: {r.get('comment') or '(none)'}" for r in revise if r["id"] in meta)
    rev = ai.answer(prompts.build("revise", factsheet=factsheet(p), style=style_text(), cv_rules=cv_rules(), cards=todo,
                                  posting=untrusted(job["description"]), language=prop["language"]), Revision, agent(), key=key).model_dump()
    fixed = {e["id"]: e for e in rev["edits"]}
    prop["items"] = [{**meta[r["id"]], "new": fixed.get(r["id"], {}).get("new", meta[r["id"]]["new"]),
                      "reason": "revised: " + fixed.get(r["id"], {}).get("reason", "")} for r in revise if r["id"] in meta and not r["id"] in paras]
    prop["letter"] = [{**meta[r["id"]], "new": fixed.get(r["id"], {}).get("new", meta[r["id"]]["new"])} for r in revise if r["id"] in paras]
    flag_scope(prop["items"], bullets(p))
    prop["auto"] = []
    prop.pop("final", None)  # a Change-text comment comes back as a normal card: accept or reject the rewrite
    dump(key, "proposal", prop)
    set_stage(key, "review")
    return {"stage": "review", "proposal": prop}


def edit_cards(key: str) -> dict:
    """Late changes from the Documents step: every line the PDFs show now, as cards to edit. Edits rebuild without AI
    (decide -> build); a comment sends only that card to the revise run."""
    final = final_cards(key)
    dump(key, "proposal", final)
    (work(key) / "choices.json").unlink(missing_ok=True)
    set_stage(key, "review")
    return final


def change(key: str, request: str) -> dict:
    """The Documents step's AI box: one run turns the person's request (empty: fix what the checks found) into edits of
    the lines the PDFs show; they are applied and the PDFs rebuilt right away. Change text shows the result line by line."""
    final, s, job, p = final_cards(key), load(key, "state", {}), posting.get(key), pf.profile().model_dump()
    lines = final["items"] + final["letter"]
    problems = [f for r in (s.get("checks") or {}).values() for f in r.get("fails", [])]
    rev = ai.answer(prompts.build(
        "change", factsheet=factsheet(p), style=style_text(), cv_rules=cv_rules(),
        cards="\n".join(f"{c['id']} ({c['section']}): {c['new']}" for c in lines), problems="\n".join(problems) or "(none)",
        request=request.strip() or "Fix the problems the checks found.", posting=untrusted(job["description"]),
        language=final["language"]), Change, agent(), key=key).model_dump()
    if rev["language"] and rev["language"].lower() != final["language"]:  # subject, salutation, every line: a new draft
        return {"restart": rev["language"].lower()}
    by = {c["id"]: c for c in lines}
    edits = [e for e in rev["edits"] if e["id"] in by]
    dump(key, "proposal", final)
    r = decide(key, [{"id": e["id"], "decision": "edit", "text": e["new"]} for e in edits])
    return {**r, "changes": [e["reason"] for e in edits],  # what changed, line by line: the page shows each as a word diff
            "diffs": [{"id": e["id"], "section": by[e["id"]]["section"], "old": by[e["id"]]["new"], "new": e["new"], "reason": e["reason"]}
                      for e in edits]}


def final_cards(key: str) -> dict:
    s, fit, job, prop = load(key, "state", {}), load(key, "fit"), posting.get(key), load(key, "proposal")
    p, acc = pf.profile().model_dump(), load(key, "accepted", {})
    from jobquill.core.score import Matcher
    kind = job.get("type") or Matcher({}, {}).kind(f"{job.get('title', '')} {fit.get('employment_type', '')} {fit.get('role', '')}", fit.get("country", ""))
    cv = cv_data(p, acc, kind)
    card = lambda i, sec, text: dict(id=i, section=sec, old="", new=text, reason="", keywords=[], flag=False)
    items = [card("summary", "Summary", cv.get("summary") or next(iter(p.get("summaries", {}).values()), ""))]
    for sec in ENTRY_SECTIONS:
        items += [card(b["id"], " · ".join(filter(None, [e.get("org"), e.get("title")])), b["text"]) for e in cv.get(sec, []) for b in e["bullets"]]
    items += [card(b["id"], " · ".join(filter(None, [c.get("title"), e.get("title")])), b["text"]) for c in cv["custom"] for e in c["entries"] for b in e["bullets"]]
    items += [card(f"skills_{k}", "Skills", f"{k}: {', '.join(v)}") for k, v in cv["skills"].items()]
    letter = load(key, "letter") or {}
    final = {"items": items, "letter": [card(f"p{n}", f"Paragraph {n}", x) for n, x in enumerate(letter.get("paragraphs", []), 1) if x],
             "letter_meta": letter, "keywords": fit.get("keywords", []), "missed": [], "stretches": [], "final": True, "auto": [],
             "context": "", "language": s.get("language", prop.get("language", "en") if prop else "en")}
    return final


# ---------------------------------------------------------------- build
def cv_data(p: dict, acc: dict, kind: str = "") -> dict:
    text = lambda b: acc[b["id"]]["text"] if b["id"] in acc else b["text"]

    def entry(e):
        bl = [dict(id=b["id"], text=text(b)) for b in e.get("bullets", []) if text(b)]
        want = acc.get(f"order_{e['id']}", {}).get("order")
        if want:
            bl.sort(key=lambda b: want.index(b["id"]) if b["id"] in want else len(want))
        return {**{k: v for k, v in e.items() if k != "bullets"}, "bullets": bl}

    cv = {**p, **{sec: [entry(e) for e in p.get(sec, [])] for sec in ENTRY_SECTIONS}}
    cv["custom"] = [{**c, "entries": [entry(e) for e in c.get("entries", [])]} for c in p.get("custom", [])]
    want = acc.get("order_projects", {}).get("order")
    if want:
        cv["projects"].sort(key=lambda x: want.index(x["id"]) if x["id"] in want else len(want))
    cv["projects"] = cv["projects"][:MAX_PROJECTS]
    skills = {k: ([acc[f"skills_{k}"]["text"].split(": ", 1)[-1]] if f"skills_{k}" in acc else v) for k, v in p.get("skills", {}).items()}
    want = acc.get("order_skills", {}).get("order")
    if want:
        skills = {k: skills[k] for k in want + [k for k in skills if k not in want]}
    cv["skills"] = skills
    cv["education"] = [e for e in p.get("education", []) if not e.get("only_for") or kind in e["only_for"]]
    if "summary" in acc:
        cv["summary"] = acc["summary"]["text"]
    return cv


def filename(name: str, kind: str, company: str) -> str:
    clean = lambda s, n=40: re.sub(r"[^\w]+", "_", s, flags=re.UNICODE).strip("_")[:n].strip("_")  # Windows paths max 260
    return f"{clean(name, 30)}_{kind}_{clean(company, 24)}.pdf"


def build(key: str, design: str = "") -> dict:
    """PDFs from the accepted state, then the checks. Over the page limit: the last project is left out once."""
    s, fit, job = load(key, "state", {}), load(key, "fit"), posting.get(key)
    p = pf.profile().model_dump()
    look = design or s.get("design") or prefs().design  # a saved look (preset) or a design id
    design, settings = render.resolve(look)
    lang, country = s.get("language", "en"), fit.get("country", "")
    pages = render.page_limit(design, settings, country)
    from jobquill.core.score import Matcher
    kind = job.get("type") or Matcher({}, {}).kind(f"{job.get('title', '')} {fit.get('employment_type', '')} {fit.get('role', '')}", country)
    cv = cv_data(p, load(key, "accepted", {}), kind)
    for old in work(key).glob("*.pdf"):
        old.unlink()
    name = p["basics"].get("name") or "CV"
    cv_path = work(key) / filename(name, "CV", fit["company"])
    pdf = render.cv_pdf(cv, design, lang, country, settings=settings)
    from io import BytesIO
    from pypdf import PdfReader
    if len(PdfReader(BytesIO(pdf)).pages) > pages and len(cv["projects"]) > 1:
        cv["projects"] = cv["projects"][:-1]
        pdf = render.cv_pdf(cv, design, lang, country, settings=settings)
    cv_path.write_bytes(pdf)
    letter = load(key, "letter") or {}
    letter_path = work(key) / filename(name, "Cover_Letter", fit["company"])
    letter_path.write_bytes(render.letter_pdf(letter, p["basics"], design, lang, country, settings=settings))
    checks = {"cv": check.check("", job["description"], fit.get("keywords", []), pdf=cv_path, max_pages=pages),
              "letter": check.check("", job["description"], pdf=letter_path, max_pages=1)}
    set_stage(key, "ready", design=look, checks=checks)
    return {"stage": "ready", "files": [cv_path.name, letter_path.name], "checks": checks}


# ---------------------------------------------------------------- finish
def add_variants(acc, bl, source):
    path = pf.folder() / "variants.yaml"
    lib = YAML(typ="safe").load(path) if path.exists() else {}
    lib = lib or {}
    for bid, v in acc.items():
        if bid in bl and v["text"] and v["text"] != bl[bid] and v["text"] not in [x["text"] for x in lib.get(bid, [])]:
            lib.setdefault(bid, []).append({"text": v["text"], "source": source, "date": date.today().isoformat()})
    out = YAML()
    out.width = 4096
    with path.open("w", encoding="utf-8") as f:
        f.write("# Wordings you approved in past applications (reused in new ones).\n")
        out.dump(lib, f)


def finish(key: str, status: str = "applied", method: str = "", note: str = "", when: str = "", clear: bool = True) -> dict:
    """Archives the application. clear: a sent one's CV and letter leave the upload folder (off for runs that never
    put them there)."""
    fit, job = load(key, "fit"), posting.get(key)
    fit = {**fit, "company": job.get("company") or fit["company"], "role": job.get("title") or fit["role"]}  # as the Board shows it
    p = pf.profile().model_dump()
    when = when or date.today().isoformat()
    slug = lambda x, n: re.sub(r"[^\w]+", "-", x, flags=re.UNICODE).strip("-")[:n].strip("-")  # Windows paths max 260
    folder = home() / "applications" / f"{when}_{slug(fit['company'], 28)}_{slug(fit['role'], 32)}"
    if folder.exists():
        folder = folder.with_name(folder.name + "_" + key[:4])
    learn(key)
    if clear and status != "pending":
        clear_current(work(key))
    shutil.copytree(work(key), folder)
    (folder / "posting.md").write_text(f"# {fit['role']} at {fit['company']}\n- URL: {job.get('url') or 'pasted'}\n\n{job['description']}\n",
                                       encoding="utf-8")
    acc = load(key, "accepted", {})
    add_variants(acc, bullets(p), f"{fit['company']} – {fit['role']}")
    with db() as con:
        cur = con.execute("insert into applications(job_key, company, role, date_applied, status, method, folder, note, data)"
                          " values (?,?,?,?,?,?,?,?,?)",
                          (key, fit["company"], fit["role"], when, status, method, folder.name, note,
                           json.dumps({"score": fit["score"], "country": fit["country"], "url": job.get("url", ""),
                                       # how many had applied when this one went (LinkedIn postings), for the drawer
                                       "applicants_when_applied": json.loads(job.get("data") or "{}").get("applicants")})))
        con.execute("update jobs set status='applied' where key=?", (key,))
    shutil.rmtree(work(key), ignore_errors=True)
    return {"id": cur.lastrowid, "folder": folder.name}


def learn(key: str):
    """Corrections the person made (edits, rejections, revise comments) become lessons for the next application."""
    rounds = load(key, "rounds", [])
    prop = load(key, "proposal") or {}
    meta = {i["id"]: i for i in prop.get("items", []) + prop.get("letter", [])}
    corr = [f"{d['id']}: proposed {meta.get(d['id'], {}).get('new', '')!r} → {d['decision']} {d.get('text', '')!r} {d.get('comment', '')}"
            for r in rounds for d in r if d["decision"] in ("edit", "reject", "revise") and (d.get("comment") or d["decision"] == "edit")]
    if not corr:
        return
    try:
        out = ai.answer(prompts.build("lessons", lessons=pf.read("lessons") or "(none)", corrections="\n".join(corr)), Lessons, agent(), key=key)
        pf.write("lessons", out.markdown)
    except ai.AIError:
        pass  # lessons are a bonus; never block archiving
