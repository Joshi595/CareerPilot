"""Setup wizard and profile: import a CV, edit the profile, preferences, form answers and writing style."""
import json
import shutil
from pathlib import Path

from babel import Locale
from fastapi import APIRouter, HTTPException, Request

from careerpilot.core import ai, alerts, apply, cvimport, mail, prompts, render, secrets
from careerpilot.core import profile as pf

router = APIRouter(prefix="/api")
EXAMPLES = Path(__file__).parent.parent / "examples"
NOT_COUNTRIES = {"EU", "EZ", "UN", "QO", "XA", "XB", "ZZ"}
SIGN_IN = {"claude": ["claude"], "codex": ["codex", "login"], "antigravity": ["agy"]}


def agent():
    return ai.chosen(pf.read("preferences").get("agent", ""))


async def run_ai(prompt, model, timeout=600):
    """The AI call runs in a worker thread so the app stays responsive."""
    from starlette.concurrency import run_in_threadpool
    try:
        return (await run_in_threadpool(ai.answer, prompt, model, agent(), timeout)).model_dump()
    except ai.AIError as e:
        raise HTTPException(502, str(e))


@router.get("/state")
def state():
    prefs = pf.Preferences.model_validate(pf.read("preferences")).model_dump()
    countries = {k: v for k, v in Locale("en").territories.items() if len(k) == 2 and k.isalpha() and k not in NOT_COUNTRIES}
    return {"profile": pf.profile().model_dump(), "preferences": prefs, "answers": pf.read("answers"),
            "style": pf.read("style"), "lessons": pf.read("lessons"), "letter": pf.read("letter"), "cv_rules": pf.read("cv_rules"), "default_cv_rules": prompts.load("default_cv_rules"), "default_style": prompts.load("default_style"), "agents": ai.installed(),
            "countries": dict(sorted(countries.items(), key=lambda kv: kv[1])),
            "examples": sorted(p.name for p in EXAMPLES.iterdir() if p.is_dir()), "features": features()}


def features() -> dict:
    out = {}
    return out


@router.put("/data/{kind}")
async def save(kind: str, request: Request):
    if kind not in {*pf.FILES, *pf.TEXTS}:
        raise HTTPException(404)
    body = await request.json()
    try:
        pf.write(kind, body["text"] if kind in pf.TEXTS else body)
    except ValueError as e:  # pydantic validation
        raise HTTPException(422, str(e))
    if kind in ("profile", "preferences"):  # what the thumbnails show (preferences: the target country)
        render.warm_thumbs()
    return {"ok": True}


@router.get("/answers/suggested")
def suggested_answers():
    """Answers that follow from the CV and preferences, for questions not answered yet. Shown, never saved by themselves."""
    prefs = pf.Preferences.model_validate(pf.read("preferences")).model_dump()
    return pf.suggested_answers(pf.read("profile"), prefs, pf.read("answers"))


@router.post("/instructions")
async def add_instruction(request: Request):
    """One line added to the CV instructions (cv_rules) or the letter instructions (style); in the letter instructions
    above the banned-phrases list."""
    b = await request.json()
    kind, line = b.get("kind"), " ".join(str(b.get("line") or "").split())
    if kind not in ("cv_rules", "style") or not line:
        raise HTTPException(422, "Write the instruction first.")
    text = pf.read(kind) or (prompts.load("default_cv_rules") if kind == "cv_rules" else prompts.load("default_style"))
    banned = pf.banned_section(text)
    pf.write(kind, text[:len(text) - len(banned)].rstrip() + f"\n- {line}\n" + (f"\n{banned}" if banned else ""))
    return {"ok": True}


@router.patch("/answers")
async def patch_answers(request: Request):
    """{question: answer} merged into the saved form answers; an empty answer removes the question. Merged, not
    replaced, so an answer saved meanwhile stays."""
    answers = pf.read("answers") or {}
    for k, v in (await request.json()).items():
        if str(v).strip():
            answers[k] = v
        else:
            answers.pop(k, None)
    pf.write("answers", answers)
    return {"ok": True}


@router.post("/import")
async def import_file(request: Request, name: str):
    """Raw file body. ZIP = LinkedIn data export, JSON = JSON Resume, anything else = a CV file for the AI."""
    data = await request.body()
    low = name.lower()
    if low.endswith(".zip"):
        draft = cvimport.from_linkedin(data)
    elif low.endswith(".json"):
        draft = cvimport.from_json_resume(data)
    else:
        text = cvimport.cv_text(data, name)
        if len(text.split()) < 30:
            raise HTTPException(422, "No readable text in this file (it may be a scan). "
                                     "Try the Word version, or paste the text of your CV instead.")
        return {"profile": await run_ai(prompts.build("import_cv", cv=text), pf.Profile)}
    return {"profile": pf.Profile.model_validate(draft).model_dump()}


@router.post("/import-text")
async def import_text(request: Request):
    text = (await request.json()).get("text", "")
    if len(text.split()) < 30:
        raise HTTPException(422, "That is too short for a CV. Paste the whole text.")
    return {"profile": await run_ai(prompts.build("import_cv", cv=text), pf.Profile)}


class Gap(pf.Model):
    id: str
    question: str
    about: str


class Gaps(pf.Model):
    questions: list[Gap]


class Merged(pf.Model):
    profile: pf.Profile
    changes: list[str]


@router.get("/profile/asked")
def asked():
    """Skills postings asked for most that the CV lacks (no AI)."""
    from careerpilot.core import check
    return check.asked_missing()


@router.post("/profile/gaps")
async def gaps(request: Request):
    """Up to 5 optional questions whose answers would make the CV stronger."""
    body = await request.json()
    profile = body.get("profile") or pf.profile().model_dump()
    return await run_ai(prompts.build("gaps", profile=json.dumps(profile, ensure_ascii=False)[:20000]), Gaps)


async def merge(kind: str, info: str, profile: dict | None) -> dict:
    old = profile or pf.profile().model_dump()
    out = await run_ai(prompts.build("merge", profile=json.dumps(old, ensure_ascii=False), kind=kind, info=info), Merged, 900)
    new = pf.keep_existing(old, out["profile"])
    return {"profile": pf.Profile.model_validate(new).model_dump(), "changes": out["changes"]}


@router.post("/profile/merge")
async def merge_text(request: Request):
    """New information in the person's words (answers to the gap questions, or "what's new") -> an updated draft, not saved."""
    b = await request.json()
    if not (b.get("text") or "").strip():
        raise HTTPException(422, "Nothing to add.")
    return await merge(b.get("kind", "what's new"), b["text"], b.get("profile"))


@router.post("/profile/merge-file")
async def merge_file(request: Request, name: str):
    """A newer CV file -> an updated draft of the existing profile (nothing already there is lost)."""
    text = cvimport.cv_text(await request.body(), name)
    if len(text.split()) < 30:
        raise HTTPException(422, "No readable text in this file. Try the Word version, or paste the text.")
    return await merge("a newer CV", text, None)


@router.post("/example/{name}")
def load_example(name: str):
    src = EXAMPLES / Path(name).name
    if not src.is_dir():
        raise HTTPException(404)
    if (pf.folder() / pf.FILES["profile"]).exists():  # an example is for trying the app, never over someone's own CV
        raise HTTPException(409, "You already have a CV here. An example would replace it, so it wasn't loaded.")
    for f in src.iterdir():
        shutil.copy(f, pf.folder() / f.name)
    render.warm_thumbs()
    return {"ok": True}


@router.post("/suggest/targets")
async def suggest_targets(request: Request):
    body = await request.json()
    p = pf.profile()
    summary = json.dumps({"headline": p.basics.headline, "roles": [e.title for e in p.experience],
                          "education": [e.degree for e in p.education], "skills": p.skills,
                          "languages": [l.model_dump() for l in p.languages]}, ensure_ascii=False)
    return await run_ai(prompts.build("targets", profile=summary, countries=", ".join(body.get("countries", [])),
                                job_types=", ".join(body.get("job_types", [])), wishes=body.get("wishes", "")),
                  pf.Targets)


@router.post("/suggest/style")
async def suggest_style(request: Request):
    body = await request.json()
    return await run_ai(prompts.build("style", answers=json.dumps(body.get("answers", {}), ensure_ascii=False),
                                sample=body.get("sample", "")), pf.Style)


class Opening(pf.Model):
    style: str
    text: str


class Openings(pf.Model):
    openings: list[Opening]


@router.post("/suggest/openings")
async def suggest_openings(request: Request):
    """Three cover-letter openings in clearly different styles, built from the CV, so a person with no letters of their
    own can pick the voice they like."""
    body = await request.json()
    p = pf.profile()
    facts = json.dumps({"headline": p.basics.headline, "summaries": p.summaries,
                        "experience": [{"title": e.title, "org": e.org, "lines": [b.text for b in e.bullets[:3]]} for e in p.experience[:4]],
                        "education": [e.degree for e in p.education], "skills": p.skills}, ensure_ascii=False)[:12000]
    return await run_ai(prompts.build("openings", profile=facts, tone=body.get("tone", ""), length=body.get("length", "")), Openings)


@router.post("/extract-text")
async def extract_text(request: Request, name: str):
    """A letter the person wrote (PDF, Word, text) -> its text, as a writing sample."""
    try:
        text = cvimport.cv_text(await request.body(), name)
    except Exception:  # not a PDF/DOCX after all, or a damaged file
        text = ""
    if not text.strip():
        raise HTTPException(422, "No readable text in this file. Try the Word version, or paste the text.")
    return {"text": text}


@router.get("/setup/missing")
def setup_missing():
    """What's still not set up, from the actual state (so it works for installs that finished setup long ago),
    minus what the person dismissed. In the order setup asks."""
    prefs = pf.Preferences.model_validate(pf.read("preferences")).model_dump()
    style = pf.read("style").strip()
    done = {"details": bool(pf.read("answers")),
            "style": bool(style) and style != prompts.load("default_style").strip() and bool(pf.read("cv_rules").strip()),
            "design": prefs["design"] != "classic",
            "email": mail.configured(),
            "alerts": bool(alerts.urls()) or (prefs["alert_email"] and mail.configured()),
            "search": bool(prefs["search_time"]),
            "documents": bool(apply.own_documents())}
    return {"missing": [k for k, ok in done.items() if not ok and k not in prefs["setup_hidden"]]}


@router.get("/setup/ready")
def setup_ready():
    """The minimum to apply that's still missing (the Apply page lists it first; also for installs set up long ago)."""
    b, prefs = pf.profile().basics, pf.Preferences.model_validate(pf.read("preferences"))
    done = {"basics": bool(b.name and b.email), "targets": bool(prefs.targets), "start": bool(prefs.start.strip()),
            "permit": all(c.permit.strip() for c in prefs.targets), "ai": any(ai.installed().values()),
            "writing": bool(pf.read("cv_rules").strip() and pf.read("style").strip()),
            "documents": bool(apply.own_documents()) or "documents" in prefs.setup_hidden}  # or "I'll add them later"
    return {"missing": [k for k, ok in done.items() if not ok]}


@router.get("/export/json-resume")
def export_json_resume():
    return cvimport.to_json_resume(pf.profile().model_dump())


@router.post("/ai/sign-in/{name}")
def sign_in(name: str):
    a = ai.AGENTS.get(name)
    if not a or not a.path():
        raise HTTPException(404, "not installed")
    from careerpilot.core.system import open_terminal
    open_terminal([a.path(), *SIGN_IN[name][1:]])
    return {"ok": True}
