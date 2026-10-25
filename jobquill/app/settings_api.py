"""Routes for settings (saved as you go, Ask JobQuill), the designer (live preview, saved looks), uploads (photo,
signature), alerts, export and deleting everything."""
import copy
import csv
import io
import json
import shutil
import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from jobquill.app.apply_api import guard
from jobquill.core import ai, alerts, mail, prompts, render, scheduler, secrets
from jobquill.core import profile as pf
from jobquill.core.data import db, home
from jobquill.core.profile import Model

router = APIRouter(prefix="/api")
IMAGES = {"png", "jpg", "jpeg"}
MERGED = {"email", "weights"}  # preferences that merge one level deep
NOT_ASKABLE = {"email"}  # and setup_*: changed only on their own pages. Secrets are never in preferences.
# What the person looks for: a change re-judges the stored postings; these also start a search with the new words.
JUDGED = {"job_types", "side_types", "titles", "keywords", "targets", "places", "radius_km", "remote", "exclude_words", "avoid_companies",
          "prefer_companies", "weights", "thesis_level", "mandatory_internship"}
SEARCHED = {"job_types", "side_types", "titles", "keywords", "targets", "thesis_level", "mandatory_internship"}
_refresh = None
WRITING = {"doc_language", "design", "review_depth", "instructions"}  # what the writing box (Profile › Writing) may change


def refresh_later(search_too: bool, wait: float = 20):
    """Settings save field by field: wait until the person stops changing things, then one re-judge (and one search)."""
    global _refresh
    from jobquill.core import search
    if _refresh:
        _refresh.cancel()
        search_too = search_too or _refresh.args[0]
    _refresh = threading.Timer(wait, search.refresh, args=(search_too,))
    _refresh.daemon = True
    _refresh.start()


class Change(Model):
    key: str
    value_json: str


class AnswerChange(Model):
    question: str
    answer: str


class AskAnswer(Model):
    """What the Ask box proposes: settings, CV/letter instructions, application details and a request to change CV facts."""
    reply: str
    changes: list[Change]
    cv_rules: str = ""
    letter_rules: str = ""
    answers: list[AnswerChange] = []
    cv_request: str = ""


def dedupe(v):
    """Lists (also those inside a dict, like keywords) without repeats, in their order; text compared ignoring case."""
    if isinstance(v, dict):
        return {k: dedupe(x) for k, x in v.items()}
    if not isinstance(v, list):
        return v
    seen, out = set(), []
    for x in v:
        key = x.strip().casefold() if isinstance(x, str) else json.dumps(x, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def merged(prefs: dict, body: dict) -> dict:
    """prefs with body's keys set. The keys in MERGED merge one level deep, so fields not sent survive (e.g. one
    set by the server); other values (keywords too) are replaced whole."""
    out = copy.deepcopy(prefs)
    for k, v in body.items():
        v = dedupe(v)
        out[k] = {**out[k], **v} if k in MERGED and isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def add_alert(url: str) -> dict:
    """A channel that's already connected isn't added twice."""
    have = [u.rstrip("/") for u in alerts.urls()]
    if url.rstrip("/") in have:
        return {"channels": alerts.channels(), "existing": True}
    alerts.set_urls(alerts.urls() + [url])
    return {"channels": alerts.channels()}


def schedule(at: str):
    """The daily search at HH:MM, or none when empty. The email check is never switched off with it."""
    try:
        scheduler.disable("search")  # made again at the new time
        scheduler.ensure(search_time=at)
    except Exception as e:
        raise HTTPException(500, f"Couldn't set up the daily search: {e}")


@router.patch("/preferences")
async def patch_preferences(request: Request):
    """{key: value} merged into the stored preferences (see merged()), validated, then saved."""
    body = await request.json()
    prefs = pf.Preferences.model_validate(pf.read("preferences")).model_dump()
    old_time, old = prefs["search_time"], copy.deepcopy(prefs)
    prefs = merged(prefs, body)
    try:
        pf.Preferences.model_validate(prefs)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if prefs["search_time"] != old_time:
        schedule(prefs["search_time"])
    changed = {k for k in body if k in old and old[k] != prefs.get(k)}
    pf.write("preferences", prefs)
    if prefs["setup_done"] and changed & JUDGED:
        refresh_later(bool(changed & SEARCHED))
    if "targets" in body:  # the first target country decides what the thumbnails show
        render.warm_thumbs()
    return {"ok": True}


@router.post("/settings/ask")
async def ask(request: Request):
    """A sentence -> proposed changes to settings, CV/letter instructions, application details and CV facts (the last
    through the same merge as Profile's "What's new"). Nothing is saved here: the page shows them and saves on Apply."""
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(422, "Type what you'd like to change.")
    writing = body.get("areas") == "writing"  # the box under the CV and letter instructions: those and the writing settings only
    prefs = pf.Preferences.model_validate(pf.read("preferences")).model_dump()
    allowed = {k for k in prefs if k not in NOT_ASKABLE and not k.startswith("setup_") and (not writing or k in WRITING)}
    fields = {k: v for k, v in pf.Preferences.model_json_schema()["properties"].items() if k in allowed}
    cur = body.get("current") or {}  # the editors' text, saved or not
    rules = (cur.get("cv_rules") if writing else None) or pf.read("cv_rules") or prompts.load("default_cv_rules")
    style = (cur.get("style") if writing else None) or pf.read("style") or prompts.load("default_style")
    answers = {} if writing else pf.read("answers") or {}
    prompt = prompts.build("ask", request=text, current=json.dumps({k: prefs[k] for k in sorted(allowed)}, ensure_ascii=False),
                           fields=json.dumps(fields, ensure_ascii=False), cv_rules=rules, letter_rules=style,
                           answers=json.dumps(answers, ensure_ascii=False),
                           areas="the settings listed, the CV instructions and the letter instructions only (answers [] and "
                                 "cv_request \"\")" if writing else "any of them")
    out = await guard(ai.answer, prompt, AskAnswer, ai.chosen(prefs["agent"]))
    if writing:
        out.answers, out.cv_request = [], ""
    res = check_changes(out, prefs, allowed)
    res["cv_rules"] = text_change(rules, out.cv_rules)
    res["letter_rules"] = text_change(style, keep_banned(style, out.letter_rules))
    res["answers"] = [{"question": a.question.strip(), "old": answers.get(a.question.strip(), ""), "new": a.answer}
                      for a in out.answers if a.question.strip() and a.answer != answers.get(a.question.strip(), "")]
    res["cv"] = None
    if out.cv_request.strip():  # CV facts: the existing merge (keeps every entry and id, never invents) -> a draft, not saved
        from jobquill.app.setup_api import merge
        res["cv"] = await merge("a request from the person, in their own words", out.cv_request, None)
    return res


def text_change(old: str, new: str):
    return {"old": old, "new": new} if new.strip() and new.strip() != old.strip() else None


def keep_banned(old: str, new: str) -> str:
    """The banned-phrases list is checked automatically: a rewrite that dropped it gets the old one back."""
    banned = pf.banned_section(old)
    return new.rstrip() + "\n\n" + banned if new.strip() and banned and not pf.banned_section(new) else new


def check_changes(out: AskAnswer, prefs: dict, allowed: set) -> dict:
    """Only allowed keys whose new value still validates; the rest is listed as left out, with why."""
    new, skipped = prefs, []
    for c in out.changes:
        if c.key not in allowed:
            skipped.append({"key": c.key, "why": "not something the Ask box may change"})
            continue
        try:
            new = pf.Preferences.model_validate(merged(new, {c.key: json.loads(c.value_json)})).model_dump()
        except ValueError:  # bad JSON or a value of the wrong kind
            skipped.append({"key": c.key, "why": "not a valid value"})
    return {"reply": out.reply, "changes": [{"key": k, "old": prefs[k], "new": new[k]} for k in prefs if new[k] != prefs[k]],
            "skipped": skipped}


@router.post("/designs/preview")
async def preview(request: Request):
    """The person's own CV in a design with the designer's settings: every page as SVG, plus the page limit."""
    b = await request.json()
    design, settings = b.get("design", "classic"), b.get("settings") or {}
    country = render.target_country()
    pages = await guard(render.cv_pages, pf.profile().model_dump(), design, b.get("lang", "en"), country, settings=settings)
    return {"pages": [p.decode() for p in pages], "limit": render.page_limit(design, settings, country)}


@router.post("/designs/presets")
async def save_preset(request: Request):
    b = await request.json()
    name = (b.get("name") or "").strip()
    if not name or b.get("design") not in render.designs():
        raise HTTPException(422, "A look needs a name and a base design.")
    render.save_preset(name, b["design"], b.get("settings") or {})
    if b.get("default"):
        prefs = pf.read("preferences")
        prefs["design"] = name
        pf.write("preferences", prefs)
    return {"ok": True}


@router.delete("/designs/presets/{name}")
def delete_preset(name: str):
    items = render.presets()
    items.pop(name, None)
    pf.write("designs", items)
    return {"ok": True}


@router.post("/upload")
async def upload(request: Request, kind: str, name: str):
    """A photo (shown on CVs where the job's country expects one) or a signature image for letters."""
    ext = name.rsplit(".", 1)[-1].lower()
    if kind not in ("photo", "signature") or ext not in IMAGES:
        raise HTTPException(422, "Please upload a PNG or JPG image.")
    data = await request.body()
    if len(data) > 8_000_000:
        raise HTTPException(422, "That image is too large (max 8 MB).")
    for old in pf.folder().glob(f"{kind}.*"):
        old.unlink()
    (pf.folder() / f"{kind}.{ext}").write_bytes(data)
    if kind == "photo":
        p = pf.read("profile") or {"basics": {}}
        p.setdefault("basics", {})["photo"] = f"photo.{ext}"
        pf.write("profile", p)
        render.warm_thumbs()
    return {"file": f"{kind}.{ext}"}


@router.get("/alerts")
def get_alerts():
    """Connected channels, masked (the links hold tokens and stay in the password store), the events, the email address."""
    return {"channels": alerts.channels(), "events": alerts.EVENTS, "always": list(alerts.IMPORTANT),
            "email": mail.settings()["address"] if mail.configured() else ""}


@router.post("/alerts/channels")
async def add_channel(request: Request):
    url = ((await request.json()).get("url") or "").strip()
    import apprise
    if not apprise.Apprise().add(url):
        raise HTTPException(422, "JobQuill doesn't recognise that link. Each service's format is on github.com/caronc/apprise/wiki.")
    return add_alert(url)


@router.post("/alerts/telegram")
async def add_telegram(request: Request):
    """The bot token -> the chat that last wrote to the bot -> a Telegram channel."""
    try:
        url = await guard(alerts.telegram, (await request.json()).get("token") or "")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return add_alert(url)


@router.delete("/alerts/channels/{i}")
def remove_channel(i: int):
    items = alerts.urls()
    if 0 <= i < len(items):
        items.pop(i)
        alerts.set_urls(items)
    return {"channels": alerts.channels()}


@router.post("/alerts/test")
async def test_alert(request: Request):
    """{i: n}: one channel; {i: "email"}: the email copy; {}: everything that's on."""
    i = (await request.json()).get("i")
    title, body = "JobQuill test", "Alerts work. You'll hear from JobQuill here."
    if i == "email":
        if not mail.configured():
            raise HTTPException(422, "Connect your email first (Settings → Email).")
        try:
            await guard(mail.send, mail.settings()["address"], title, body)
        except Exception as e:
            raise HTTPException(422, f"The email didn't go out: {e}")
        return {"ok": True}
    items = alerts.urls()
    only = [items[i]] if isinstance(i, int) and 0 <= i < len(items) else None
    if not await guard(alerts.send, "test", title, body, True, only):
        raise HTTPException(422, "No alert went out. Check the link (each service's format is on apprise's wiki).")
    return {"ok": True}


@router.get("/ai-usage")
def ai_usage(days: int = 30):
    """The AI ledger summed up per step (days=0: all time), plus the average per application."""
    since = f"-{days} day" if days else "-100 year"
    with db() as con:
        steps = [dict(r) for r in con.execute(
            "select step, count(*) calls, sum(error != '') failed, sum(input) input, sum(cached) cached, sum(output) output,"
            " sum(cost_usd) cost_usd, avg(seconds) seconds from ai_calls where at >= datetime('now', ?)"
            " group by step order by sum(input) + sum(output) desc", (since,))]
        app = dict(con.execute(  # applications actually made (not drafts left half-way)
            "select count(distinct job_key) n, sum(input) input, sum(cached) cached, sum(output) output, sum(cost_usd) cost_usd"
            " from ai_calls where job_key in (select job_key from applications) and at >= datetime('now', ?)", (since,)).fetchone())
    return {"steps": steps, "apps": app}


@router.get("/export/applications.csv")
def export_csv():
    with db() as con:
        rows = [dict(r) for r in con.execute("select date_applied, company, role, status, method, follow_up, note, folder from applications"
                                             " order by date_applied desc")]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["date_applied", "company", "role", "status", "method", "follow_up", "note", "folder"])
    w.writeheader()
    # Company and role come from postings: a cell starting with = + - @ would run as a formula in Excel
    w.writerows({k: "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v for k, v in r.items()}
                for r in rows)
    return Response("﻿" + buf.getvalue(), media_type="text/csv",  # BOM: Excel opens it with the right characters
                    headers={"Content-Disposition": 'attachment; filename="my-applications.csv"'})


@router.post("/playbooks/open")
def open_playbooks():
    """A folder for the person's own copies of the AI instructions; the shipped ones are copied in as a starting point."""
    from jobquill.core.system import open_path
    own = home() / "playbooks"
    originals = own / "_originals (copy a file up one level to change it)"
    originals.mkdir(parents=True, exist_ok=True)
    for f in prompts.SHIPPED.glob("*.md"):
        shutil.copy(f, originals / f.name)
    open_path(own)
    return {"ok": True}


@router.post("/delete-everything")
async def delete_everything(request: Request):
    if (await request.json()).get("confirm") != "DELETE":
        raise HTTPException(422, "Type DELETE to confirm.")
    try:
        scheduler.disable()
    except Exception:
        pass
    secrets.delete_all()
    from jobquill.core import apply
    for name in apply.placed():  # only the CV and letter the app put there: the person's own documents are theirs
        try:
            (apply.current() / name).unlink(missing_ok=True)
        except OSError:
            pass
    root = home()
    for item in root.iterdir():
        try:
            shutil.rmtree(item) if item.is_dir() else item.unlink()
        except OSError:
            pass  # e.g. the open database file; it's emptied on the next start
    return {"ok": True}
