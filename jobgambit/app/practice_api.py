"""Routes for interview practice and the problem report."""
import json
import platform
import sys
from importlib import metadata
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from jobgambit.app.apply_api import guard
from jobgambit.core import ai, practice, search, secrets
from jobgambit.core import profile as pf
from jobgambit.core.data import db, home

router = APIRouter(prefix="/api")


def app_id(value: str) -> int | None:
    if value in ("", "general", "0"):
        return None
    if not value.isdigit():
        raise HTTPException(404)
    return int(value)


@router.get("/practice/{which}")
def get_practice(which: str):
    a = app_id(which)
    try:
        return {"questions": practice.questions(a), "answers": practice.answers(a), "talk": practice.talk(a),
                **({"prep": practice.prep(a)} if a else {})}
    except ValueError:
        raise HTTPException(404)


@router.post("/practice/{which}/generate")
async def generate(which: str):
    return {"questions": await guard(practice.generate, app_id(which))}


@router.post("/practice/{which}/brief")
async def make_brief(which: str):
    """The interview brief (one AI run; made again on Refresh)."""
    a = app_id(which)
    if not a:
        raise HTTPException(404)
    return {"brief": await guard(practice.brief, a, True)}


@router.post("/practice/{which}/defend")
async def make_defend(which: str):
    a = app_id(which)
    if not a:
        raise HTTPException(404)
    return {"defend": await guard(practice.defend, a, True)}


@router.post("/practice/{which}/answer")
async def answer(which: str, request: Request):
    b = await request.json()
    if b.get("feedback"):
        return await guard(practice.feedback, app_id(which), b["question"], b.get("why", ""), b["answer"])
    practice.save_answer(app_id(which), b["question"], b["answer"])
    return {"ok": True}


@router.post("/practice/{which}/explain")
async def explain(which: str, request: Request):
    """The person's own question about a practice question, answered in plain words."""
    b = await request.json()
    return await guard(practice.explain, app_id(which), b["question"], b["ask"])


@router.get("/problem-report")
def problem_report():
    """What helps fix a problem, with personal data and secrets removed. The person reads it before sharing."""
    with db() as con:
        counts = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in ("jobs", "applications", "needs")}
    log = []
    for f in sorted((home()).glob("*.log"))[-3:]:
        log += f.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
    text = "\n".join([
        "## JobGambit problem report",
        f"- JobGambit {metadata.version('jobgambit')}, Python {sys.version.split()[0]}, {platform.platform()}",
        f"- AI installed: {', '.join(k for k, v in ai.installed().items() if v) or 'none'}",
        f"- Records: {json.dumps(counts)}",
        "- Search sources: " + "; ".join(f"{k}: {v.get('error') or v.get('count')}" for k, v in search.health().items()),
        "", "### Recent log", *log[-60:], "", "### What happened (write it here)", ""])
    b = pf.read("profile").get("basics") or {}  # who you are stays out too, not only the passwords
    for private in sorted({str(Path.home()), *(str(b.get(k) or "").strip() for k in ("name", "email", "phone"))}, key=len, reverse=True):
        if len(private) >= 4:
            text = text.replace(private, "•••")
    return {"text": secrets.redact(text)}
