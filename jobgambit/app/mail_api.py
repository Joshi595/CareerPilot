"""Routes for email: connect, check replies, follow-ups."""
import json
import re
import threading
from datetime import date

from fastapi import APIRouter, HTTPException, Request

from jobgambit.app.apply_api import guard
from jobgambit.core import ai, apply, mail, prompts, secrets
from jobgambit.core import profile as pf
from jobgambit.core.data import db, home
from jobgambit.core.profile import Model

router = APIRouter(prefix="/api")


@router.get("/email")
def get_email():
    return {**mail.settings(), "password_saved": bool(secrets.get("email_password")), "providers": list(mail.PROVIDERS), "help": mail.HELP}


@router.get("/email/detect")
def detect_email(address: str):
    return mail.detect(address)


@router.put("/email")
async def put_email(request: Request):
    b = await request.json()
    prefs = pf.read("preferences")
    prefs["email"] = {k: b.get(k, "") for k in ("address", "provider", "imap", "smtp", "port")}
    pf.write("preferences", prefs)
    if b.get("password"):
        secrets.put("email_password", b["password"])
    from jobgambit.core import scheduler
    try:
        scheduler.ensure()  # connected: the hourly check starts now, with or without a daily search
    except Exception:
        pass  # Settings › Email's test and the app start try again
    return {"ok": True}


@router.post("/email/test")
async def test_email():
    try:
        return {"result": await guard(mail.test)}
    except Exception as e:
        raise HTTPException(422, f"Couldn't connect: {e}. Check the address, the servers and the app password.")


@router.post("/email/check")
async def check_email():
    if not mail.configured():
        raise HTTPException(422, "Connect your email in Settings first.")
    return await guard(mail.check_replies)


@router.get("/mail/items")
def mail_items():
    """Needs you: the open mails (group act: something to do; link: which application?), the ones settled without the
    person in the last 14 days (filed), the interviews coming up, and whether the email check and the alerts work."""
    from datetime import datetime, timedelta
    from jobgambit.core import alerts
    since = (date.today() - timedelta(days=14)).isoformat()
    with db() as con:
        rows = [dict(r) for r in con.execute("select * from mail_items where done=0 or (auto=1 and date >= ?) "
                                             "order by date desc, id desc", (since,))]
        apps = {r["id"]: dict(r) for r in con.execute("select id, company, role, date_applied, status, folder, data from applications")}
    address = mail.settings()["address"] if mail.configured() and mail.gmail() else ""
    items, filed = [], []
    for i in rows:
        i["candidates"] = [c if isinstance(c, dict) else {"id": c, "why": ""} for c in json.loads(i["candidates"] or "[]")]
        i["candidates"] = [{**c, **{k: apps[c["id"]][k] for k in ("company", "role", "date_applied")}} for c in i["candidates"]
                           if c["id"] in apps]
        companies = {mail.company_key(c["company"]).lower() for c in i["candidates"]}
        i["company"] = (apps[i["app_id"]]["company"] if i["app_id"] in apps else
                        i["candidates"][0]["company"] if len(companies) == 1 else mail.who(i["frm"]))
        i["role"] = apps[i["app_id"]]["role"] if i["app_id"] in apps else ""
        i["account"] = i["signal"] == "action" and bool(mail.ACCOUNT.search(f"{i['subject']} {i['snippet'] or ''} {i['body'] or ''}"))
        i["group"] = "filed" if i["done"] else "act" if i["signal"] in mail.IMPORTANT else "link"
        i["mail"] = mail.gmail_link(address, i["company"], i["date"], i["thread"]) if address else ""
        (filed if i["done"] else items).append(i)
    now = datetime.now()
    coming = [{"id": a["id"], "company": a["company"], "role": a["role"], "status": a["status"], "at": at}
              for a in apps.values() for at in [json.loads(a["data"] or "{}").get("interview_at", "")]
              if at and (now - timedelta(hours=2)).isoformat() <= at <= (now + timedelta(days=14)).isoformat()]
    st = mail._state()
    return {"items": items, "filed": filed, "coming_up": sorted(coming, key=lambda a: a["at"]), "ok_at": st.get("ok_at", ""),
            "configured": mail.configured(), "alerts_failing": alerts.failing() if alerts.urls() else ""}


@router.post("/mail/recheck")
async def recheck_mail():
    """Needs you › Check these again: the open mails without an application, read again with what is known now."""
    if not mail.configured():
        raise HTTPException(422, "Connect your email in Settings first.")
    return {"found": await guard(mail.recheck)}


@router.post("/mail/items/{item_id}")
async def settle_mail_item(item_id: int, request: Request):
    """The person's answer: which application the mail is about (it learns the conversation and job number) and its
    status: the one given, else the step the mail means (an interview moves it forward, a rejection closes it). A mail
    that JobGambit had linked to another application leaves that one first (Wrong? Link it). Without an application:
    not about one. Either way it leaves Needs you."""
    from jobgambit.app.apply_api import STATUSES
    b = await request.json()
    status = b.get("status") or None
    if status and status not in STATUSES:
        raise HTTPException(422, "unknown status")
    with db() as con:
        item = con.execute("select * from mail_items where id=?", (item_id,)).fetchone()
        if not item:
            raise HTTPException(404)
        if b.get("app_id"):
            row = con.execute("select * from applications where id=?", (int(b["app_id"]),)).fetchone()
            if not row:
                raise HTTPException(404)
            row = dict(row)
            if item["app_id"] and item["app_id"] != row["id"]:
                old = con.execute("select * from applications where id=?", (item["app_id"],)).fetchone()
                if old:
                    mail.unrecord(con, dict(old), dict(item))
            status = status or mail.step(row, item["signal"] or None)
            mail.record(con, row, dict(item), item["signal"], status, item["at"] or "")
            if status and status != "pending" and row["folder"]:
                apply.clear_current(home() / "applications" / row["folder"])
        con.execute("update mail_items set done=1, app_id=? where id=?", (b.get("app_id") or item["app_id"], item_id))
    return {"ok": True}


class FollowUp(Model):
    subject: str
    body: str


@router.post("/applications/{app_id}/followup")
async def followup(app_id: int):
    with db() as con:
        row = con.execute("select * from applications where id=?", (app_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    folder = home() / "applications" / (row["folder"] or "")
    letter = json.loads((folder / "letter.json").read_text(encoding="utf-8")) if (folder / "letter.json").exists() else {}
    fit = json.loads((folder / "fit.json").read_text(encoding="utf-8")) if (folder / "fit.json").exists() else {}
    days = (date.today() - date.fromisoformat(row["date_applied"])).days if row["date_applied"] else 0
    p = pf.profile()
    prefs = pf.Preferences.model_validate(pf.read("preferences"))
    prompt = prompts.build("followup", role=row["role"], company=row["company"], applied=row["date_applied"] or "", days=str(days),
                           contact=fit.get("contact_name") or "the hiring team", focus=" ".join(letter.get("paragraphs", [])[:2])[:1200],
                           style=apply.style_text(), language=fit.get("language") or "en", name=p.basics.name)
    out = await guard(ai.answer, prompt, FollowUp, ai.chosen(prefs.agent))
    posting = (folder / "posting.md").read_text(encoding="utf-8") if (folder / "posting.md").exists() else ""
    emails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", posting)
    return {**out.model_dump(), "to": emails[0] if emails else ""}


@router.post("/applications/{app_id}/send")
async def send_followup(app_id: int, request: Request):
    b = await request.json()
    if not mail.configured():
        raise HTTPException(422, "Connect your email in Settings first, or copy the text into your own email.")
    result = await guard(mail.send, b["to"], b["subject"], b["body"])
    with db() as con:
        row = con.execute("select data from applications where id=?", (app_id,)).fetchone()
        data = json.loads(row["data"] or "{}")
        data.setdefault("followups", []).append({"date": date.today().isoformat(), "to": b["to"], "subject": b["subject"]})
        con.execute("update applications set data=?, follow_up=? where id=?", (json.dumps(data), date.today().isoformat(), app_id))
    return {"result": result}
