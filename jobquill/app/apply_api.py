"""Routes for: adding a posting, the apply steps, my applications, files (PDFs, previews) and CV designs."""
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from jobquill.core import ai, apply, posting, render, sources
from jobquill.core import profile as pf
from jobquill.core.data import db, home

router = APIRouter(prefix="/api")
STATUSES = ["pending", "applied", "assessment", "interview", "offer", "rejected", "withdrawn", "no_response", "ghosted"]


async def guard(fn, *args, **kw):
    """Runs a (slow) step off the event loop; AI problems become a readable message for the page."""
    from starlette.concurrency import run_in_threadpool
    try:
        return await run_in_threadpool(fn, *args, **kw)
    except ai.UsageLimit as e:
        raise HTTPException(429, f"Your AI plan's usage limit is reached. Try again after it resets. ({e})")
    except ai.AIError as e:
        raise HTTPException(502, str(e))


@router.post("/jobs/intake")
async def intake(request: Request):
    body = await request.json()
    url, text = (body.get("url") or "").strip(), (body.get("text") or "").strip()
    if url and not sources.web(url):
        raise HTTPException(422, "That isn't a web link. Copy the link that starts with https:// from your browser.")
    job = {"url": url, "source": "pasted"}
    if url and not text:
        try:
            job.update(posting.read_url(url), source="link")
        except Exception as e:  # network, 403, timeouts: the person can still paste the text
            raise HTTPException(422, f"Couldn't open that page ({e.__class__.__name__}). Copy the posting text and paste it instead.")
    if text:
        job["description"] = text
    if len(job.get("description", "").split()) < 60:
        raise HTTPException(422, "Couldn't read the posting from that page (it may need a login or JavaScript). "
                                 "Copy the whole posting text and paste it instead.")
    with db() as con:
        known = bool(url) and con.execute("select 1 from jobs where url=?", (url,)).fetchone()
    key = posting.save(job)
    if url and not known:  # the search never found it: counted in the search summary (recall)
        with db() as con:
            con.execute("update jobs set data=json_set(coalesce(data, '{}'), '$.missed', ?) where key=?", (date.today().isoformat(), key))
    return {"key": key}


@router.get("/jobs")
def jobs(status: str = ""):
    with db() as con:
        rows = con.execute("select key, title, company, location, country, url, source, posted, deadline, type, status, score, why"
                           " from jobs where (?='' or status=?) order by coalesce(score, 0) desc, first_seen desc limit 500",
                           (status, status)).fetchall()
    return [dict(r) for r in rows]


KEPT = {"new", "shortlisted"}  # statuses a re-score never undoes
JOB_STATUSES = {"new", "shortlisted", "skipped", "applying", "paused", "applied", "expired", "filtered"}  # what the Board sets


@router.put("/jobs/{key}/status")
async def job_status(key: str, request: Request):
    status = (await request.json())["status"]
    if status not in JOB_STATUSES:
        raise HTTPException(422, "unknown status")
    with db() as con:
        con.execute("update jobs set status=? where key=?", (status, key))
        if status in KEPT:  # the person's choice sticks: a re-score never hides it again
            con.execute("update jobs set data=json_set(coalesce(data, '{}'), '$.kept', json('true')) where key=?", (key,))
    return {"ok": True}


@router.post("/jobs/{key}/applicants")
async def job_applicants(key: str):
    """How many applied (LinkedIn postings only): asked when the Apply page opens, kept for a day."""
    job = posting.get(key)
    if not job:
        raise HTTPException(404)
    data = json.loads(job.get("data") or "{}")
    known = data.get("applicants")
    if known and known.get("at") == date.today().isoformat():
        return {"applicants": known}
    if "linkedin" in pf.Preferences.model_validate(pf.read("preferences")).sources_off:
        return {"applicants": known}
    from starlette.concurrency import run_in_threadpool
    got = await run_in_threadpool(sources.applicants, job.get("url") or "")
    if got:
        with db() as con:
            con.execute("update jobs set data=json_set(coalesce(data, '{}'), '$.applicants', json(?)) where key=?", (json.dumps(got), key))
    return {"applicants": got or known}


@router.put("/jobs/{key}/note")
async def job_note(key: str, request: Request):
    with db() as con:
        con.execute("update jobs set note=? where key=?", ((await request.json()).get("note", "").strip(), key))
    return {"ok": True}


@router.get("/apply/{key}")
def apply_state(key: str):
    job = posting.get(key)
    if not job:
        raise HTTPException(404)
    return {"job": job, **apply.state(key), "applied": apply.applied_to(job)}


@router.post("/apply/{key}/current")
def make_current(key: str):
    """The person opened this application: its CV and letter go into the Current application folder."""
    if not posting.get(key) or not any(apply.workdir(key).glob("*.pdf")):
        raise HTTPException(404)
    return {"warning": apply.show_files(apply.workdir(key))}


@router.post("/apply/{key}/draft")
async def draft(key: str, request: Request):
    """Body (optional): {note, language}: the person's wish for this draft only (Start over, the Apply page's AI box)."""
    b = await request.json() if await request.body() else {}
    return await guard(apply.draft, key, "", False, str(b.get("note", "")), str(b.get("language", "")))


@router.delete("/apply/{key}")
async def discard(key: str):
    """Start over / Discard draft: the application in progress is thrown away; the job is new again."""
    await guard(apply.discard, key)
    return {"ok": True}


@router.post("/apply/{key}/pause")
def pause(key: str):
    """Stop for now: the job leaves Board › In progress; its drafts stay, so opening it again continues."""
    with db() as con:
        con.execute("update jobs set status='paused' where key=? and status='applying'", (key,))
    return {"ok": True}


@router.post("/apply/{key}/decide")
async def decide(key: str, request: Request):
    r = await guard(apply.decide, key, (await request.json())["items"])
    if r.get("stage") == "ready":
        r["warning"] = apply.show_files(apply.work(key))
    return r


@router.post("/apply/{key}/edit")
async def edit(key: str):
    return await guard(apply.edit_cards, key)


@router.post("/apply/{key}/change")
async def change(key: str, request: Request):
    r = await guard(apply.change, key, (await request.json()).get("request", ""))
    if not r.get("restart"):  # another language: the page starts over in it
        r["warning"] = apply.show_files(apply.work(key))
    return r


@router.post("/apply/{key}/build")
async def build(key: str, request: Request):
    r = await guard(apply.build, key, (await request.json()).get("design", ""))
    r["warning"] = apply.show_files(apply.work(key))
    return r


@router.post("/apply/{key}/finish")
async def finish(key: str, request: Request):
    b = await request.json()
    return await guard(apply.finish, key, b.get("status", "applied"), b.get("method", ""), b.get("note", ""), b.get("date", ""))


@router.get("/applications")
def applications():
    with db() as con:
        rows = [dict(r) for r in con.execute("select * from applications order by date_applied desc, id desc")]
    from jobquill.core import mail
    address = mail.settings()["address"] if mail.gmail() else ""  # other providers have no stable link to one mail
    for r in rows:
        folder = home() / "applications" / (r["folder"] or "")
        r["files"] = sorted(p.name for p in folder.glob("*.pdf")) if r["folder"] and folder.is_dir() else []
        answers = folder / "form_answers.md"  # what an application form was told
        r["form_answers"] = answers.read_text(encoding="utf-8", errors="replace")[:4096] if r["folder"] and answers.is_file() else ""
        r["data"] = json.loads(r["data"] or "{}")
        r["mail"] = mail.gmail_link(address, r["company"], r["date_applied"], r["data"].get("thread", "")) if address else ""
        r["usage"] = ai.calls(r["job_key"]) if r["job_key"] else []  # the AI ledger (applications from before it have none)
    return {"rows": rows, "statuses": STATUSES}


@router.get("/applications/{app_id}/posting")
def application_posting(app_id: int):
    """The posting an application was written for (its archived copy, else the stored description): the drawer shows it
    for interviews, tests and offers. Other people's text: the page shows it as plain text."""
    from jobquill.core import mail
    with db() as con:
        row = con.execute("select folder, job_key from applications where id=?", (app_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    # the archive's own header ("# Role at Company", "- URL: …", written by apply.finish) isn't part of the posting;
    # Markdown headings and bold become plain text
    text = re.sub(r"\A# .*\n(?:- URL: .*\n)?\s*", "", mail.posting_text(dict(row)))
    return {"text": re.sub(r"^#{1,6}\s+", "", text, flags=re.M).replace("**", "")}


@router.put("/applications/{app_id}")
async def update_application(app_id: int, request: Request):
    b = await request.json()
    fields = {k: b[k] for k in ("status", "note", "follow_up") if k in b}
    if fields.get("status") and fields["status"] not in STATUSES:
        raise HTTPException(422, "unknown status")
    with db() as con:
        if fields:
            con.execute(f"update applications set {', '.join(f'{k}=?' for k in fields)} where id=?", [*fields.values(), app_id])
        if "interview_at" in b:  # the next interview, "YYYY-MM-DDTHH:MM" or "": the Upcoming list and the reminders read it
            if not re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d)?", b["interview_at"] or ""):
                raise HTTPException(422, "interview_at must be YYYY-MM-DDTHH:MM")
            data = json.loads((con.execute("select data from applications where id=?", (app_id,)).fetchone() or {"data": "{}"})["data"] or "{}")
            data["interview_at"] = b["interview_at"] or ""
            con.execute("update applications set data=? where id=?", (json.dumps(data), app_id))
        row = con.execute("select folder from applications where id=?", (app_id,)).fetchone()
    if row and row["folder"] and fields.get("status") not in (None, "pending"):  # sent (or closed): the current folder is done
        apply.clear_current(home() / "applications" / row["folder"])
    return {"ok": True}


@router.get("/file")
def file(path: str):
    """A PDF or image from the data folder (work or applications). Nothing outside it."""
    root = home().resolve()
    target = (root / path).resolve()
    if root not in target.parents or target.suffix.lower() not in (".pdf", ".png", ".jpg", ".jpeg") or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target)


def reveal(folder: Path):
    """Opens the file manager on a folder (the CV selected on Windows, if there is one), to drag files into an upload box."""
    folder.mkdir(parents=True, exist_ok=True)
    cv = next((folder / n for n in apply.placed() if "_CV_" in n and (folder / n).exists()), None)
    if sys.platform == "win32":
        subprocess.Popen(f'explorer /select,"{cv}"' if cv else f'explorer "{folder}"')
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])
    return {"folder": str(folder)}


DOC_TYPES = {".pdf", ".png", ".jpg", ".jpeg", ".docx", ".doc", ".odt", ".txt"}


@router.get("/upload-folder")
def upload_folder():
    """The upload folder and the person's own documents in it (certificates, transcripts, references…)."""
    return {"folder": str(apply.current()), "documents": apply.own_documents()}


@router.post("/upload-folder/files")
async def add_document(request: Request, name: str):
    """One of the person's documents, copied into the upload folder (an existing file of that name is replaced)."""
    clean = Path(name).name.strip()  # a bare file name: never a path out of the folder
    if not clean or Path(clean).suffix.lower() not in DOC_TYPES:
        raise HTTPException(422, f"{name}: only PDF, Word, image or text files")
    if clean in apply.placed():
        raise HTTPException(422, f"{name} has the name of the current application's CV or letter. Rename it first.")
    body = await request.body()
    if len(body) > 25_000_000:
        raise HTTPException(413, f"{name} is larger than 25 MB")
    apply.current().mkdir(parents=True, exist_ok=True)
    (apply.current() / clean).write_bytes(body)
    return {"documents": apply.own_documents()}


@router.post("/upload-folder/open")
def open_upload_folder():
    return reveal(apply.current())


@router.post("/apply/{key}/folder")
def apply_folder(key: str):
    if not posting.get(key):  # a known job key only, never a path
        raise HTTPException(404)
    warning = apply.show_files(apply.work(key))
    return {**reveal(apply.current()), "warning": warning}


@router.put("/apply/{key}/choices")
async def choices(key: str, request: Request):
    """The review cards' choices so far, saved as the person clicks (picked up again after leaving the page)."""
    if not posting.get(key):
        raise HTTPException(404)
    apply.dump(key, "choices", (await request.json())["choices"])
    return {"ok": True}


@router.post("/applications/{app_id}/folder")
def application_folder(app_id: int):
    with db() as con:
        row = con.execute("select folder, status from applications where id=?", (app_id,)).fetchone()
    if not row or not row["folder"]:
        raise HTTPException(404)
    archive = home() / "applications" / row["folder"]
    if row["status"] != "pending":
        return reveal(archive)
    warning = apply.show_files(archive)  # saved for later: it becomes the current application again
    return {**reveal(apply.current()), "warning": warning}


@router.get("/designs")
def designs():
    """Designs and saved looks; keys[name] versions each thumbnail URL, so the browser may cache it for good."""
    current = pf.Preferences.model_validate(pf.read("preferences")).design
    country = render.target_country()
    return {"current": current, "presets": render.presets(),
            "designs": [{"id": k, **{x: v.get(x, "") for x in ("name", "family", "description")}, "style": v.get("style", {}),
                         "order": v.get("order", []), "sidebar": v.get("sidebar", [])} for k, v in render.designs().items()],
            "keys": {n: render.thumb_key(n, country) for n in [*render.designs(), *render.presets()]},
            "defaults": render.STYLE, "sections": render.ORDER}


@router.get("/designs/{name:path}/thumb.png")
def thumb(name: str):
    """The person's own CV, page 1, in a design or saved look. The URL carries the key (?v=), so it never goes stale."""
    if name not in render.designs() and name not in render.presets():
        raise HTTPException(404)
    return FileResponse(render.thumb(name), media_type="image/png",
                        headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.put("/designs/current")
async def set_design(request: Request):
    name = (await request.json())["design"]
    if name not in render.designs() and name not in render.presets():
        raise HTTPException(404)
    prefs = pf.read("preferences")
    prefs["design"] = name
    pf.write("preferences", prefs)
    return {"ok": True}
