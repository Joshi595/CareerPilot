"""Interview practice: likely questions per application (or general ones from the profile), typed answers, AI feedback.
Questions are built from what was actually sent (CV, letter, form answers), so the person's answers stay consistent.

For an interview: a brief (the role, what they want most, gaps and how to answer them, questions to ask, STAR stories
built only from real facts), what was sent (no AI), a drill on every line sent ("defend your CV"), the time and the
meeting link from the invite mail."""
import json
import re
from datetime import datetime

from careerpilot.core import ai, apply, prompts, sources
from careerpilot.core import profile as pf
from careerpilot.core.data import db, home
from careerpilot.core.profile import Field, Model


class Question(Model):
    question: str
    kind: str
    why: str
    facts: str


class Questions(Model):
    questions: list[Question]


class Gap(Model):
    gap: str
    answer: str = Field(description="how to answer it honestly, from the person's facts")


class Story(Model):
    requirement: str = Field(description="what the job asks that this story proves")
    title: str
    situation: str
    task: str
    action: str
    result: str
    facts: list[str] = Field(description="the fact-sheet ids the story is built on, e.g. b3 or e1")


class Brief(Model):
    role: str = Field(description="the job in at most 3 lines")
    wants: list[str] = Field(description="the 5 things they want most")
    gaps: list[Gap]
    ask: list[str] = Field(description="5 good questions to ask them")
    stories: list[Story] = Field(description="4-6 STAR stories")


class Feedback(Model):
    score: int = Field(ge=1, le=5)
    strengths: list[str]
    improve: list[str]
    better: str


def folder(app_id: int | None):
    if not app_id:
        path = home() / "practice"
    else:
        with db() as con:
            row = con.execute("select folder from applications where id=?", (app_id,)).fetchone()
        if not row or not row["folder"]:
            raise ValueError("unknown application")
        path = home() / "applications" / row["folder"]
    path.mkdir(parents=True, exist_ok=True)
    return path


def questions(app_id: int | None) -> list[dict]:
    path = folder(app_id) / "questions.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def generate(app_id: int | None) -> list[dict]:
    base = folder(app_id)
    read = lambda name: (base / name).read_text(encoding="utf-8") if (base / name).exists() else ""
    if app_id:
        sent = "\n".join(filter(None, [read("fit.json")[:3000], "CV changes: " + read("accepted.json")[:3000],
                                       "Letter: " + read("letter.json")[:3000], read("form_answers.md")[:2000]]))
        posting = read("posting.md")[:6000]
    else:
        prefs = pf.Preferences.model_validate(pf.read("preferences"))
        sent, posting = f"No specific job: general interview for {', '.join(prefs.titles[:4])}.", ""
    out = ai.answer(prompts.build("interview", application=sent, facts=apply.factsheet(pf.profile().model_dump())[:8000],
                                  posting=apply.untrusted(posting)), Questions, apply.agent(), timeout=600)
    items = [q.model_dump() for q in out.questions]
    (base / "questions.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return items


def _read(app_id: int, name: str, default):
    p = folder(app_id) / name
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
    except ValueError:
        return default


def sent(app_id: int) -> dict:
    """What went to them, read from the archive (no AI): the CV lines that were changed for this job, the letter."""
    acc, letter = _read(app_id, "accepted.json", {}), _read(app_id, "letter.json", {})
    return {"cv": [v["text"] for k, v in acc.items() if isinstance(v, dict) and v.get("text") and not k.startswith("order_")],
            "letter": [p for p in letter.get("paragraphs", []) if p]}


def _context(app_id: int) -> dict:
    s, fit = sent(app_id), _read(app_id, "fit.json", {})
    posting = (folder(app_id) / "posting.md").read_text(encoding="utf-8") if (folder(app_id) / "posting.md").exists() else ""
    return {"sent": "CV lines:\n" + "\n".join(f"- {x}" for x in s["cv"]) + "\n\nLetter:\n" + "\n\n".join(s["letter"]),
            "blockers": "; ".join(fit.get("blockers") or []) or "(none found)", "posting": apply.untrusted(posting[:8000]),
            "facts": apply.factsheet(pf.profile().model_dump())[:8000]}


def brief(app_id: int, refresh: bool = False) -> dict:
    """One AI run, kept as brief.json in the application folder (Refresh makes a new one). A STAR story must name the
    facts it is built on, and only the person's real ones: any other story is dropped."""
    path = folder(app_id) / "brief.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    out = ai.answer(prompts.build("brief", **_context(app_id)), Brief, apply.agent(), timeout=600).model_dump()
    p = pf.profile().model_dump()
    ids = set(apply.bullets(p)) | set(apply.entries(p))
    out["stories"] = [s for s in out["stories"] if s["facts"] and all(f.strip("[] ") in ids for f in s["facts"])]
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def defend(app_id: int, refresh: bool = False) -> list[dict]:
    """Defend your CV: a probing question for every line and paragraph that was sent, kept as defend.json."""
    path = folder(app_id) / "defend.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    out = ai.answer(prompts.build("defend", **_context(app_id)), Questions, apply.agent(), timeout=600)
    items = [{**q.model_dump(), "kind": "defend"} for q in out.questions]
    path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return items


MEETING = re.compile(r"https?://(?:[\w-]+\.)?(?:teams\.microsoft\.com/l/meetup|zoom\.us/j/|meet\.google\.com/|webex\.com/|"
                     r"calendly\.com/|outlook\.office\.com/book)[^\s\"'<>)]*", re.I)


def prep(app_id: int) -> dict:
    """What the prep page shows besides the questions: the brief and the drill when made, what was sent, the interview
    time, and the meeting link from the invite mail (web links only)."""
    with db() as con:
        row = con.execute("select status, data from applications where id=?", (app_id,)).fetchone()
        bodies = [r[0] or "" for r in con.execute("select body from mail_items where app_id=? order by date desc", (app_id,))]
    link = next((sources.web(m[0]) for b in bodies for m in [MEETING.search(b)] if m), "")
    base = folder(app_id)
    return {"status": row["status"] if row else "", "interview_at": json.loads((row and row["data"]) or "{}").get("interview_at", ""),
            "link": link, "sent": sent(app_id),
            "brief": json.loads((base / "brief.json").read_text(encoding="utf-8")) if (base / "brief.json").exists() else None,
            "defend": json.loads((base / "defend.json").read_text(encoding="utf-8")) if (base / "defend.json").exists() else []}


def answers(app_id: int | None) -> dict:
    path = folder(app_id) / "practice.jsonl"
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            out[row["question"]] = row  # the newest answer per question
    return out


def save_answer(app_id: int | None, question: str, answer: str, feedback: dict | None = None):
    with (folder(app_id) / "practice.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"question": question, "answer": answer, "feedback": feedback,
                            "date": datetime.now().isoformat(timespec="minutes")}, ensure_ascii=False) + "\n")


def feedback(app_id: int | None, question: str, why: str, answer: str) -> dict:
    out = ai.answer(prompts.build("feedback", question=question, why=why, answer=apply.untrusted(answer),
                                  facts=apply.factsheet(pf.profile().model_dump())[:8000]), Feedback, apply.agent()).model_dump()
    save_answer(app_id, question, answer, out)
    return out
