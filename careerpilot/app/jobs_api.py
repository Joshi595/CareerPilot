"""Routes for the search: run it, watch it, rank with AI, career sites, the daily schedule."""
import json
import re
import threading
from collections import Counter

from fastapi import APIRouter, HTTPException, Request

from careerpilot.app import settings_api
from careerpilot.app.apply_api import guard
from careerpilot.core import profile as pf
from careerpilot.core import search, sites, sources
from careerpilot.core.data import db

router = APIRouter(prefix="/api")


@router.post("/search/run")
def run_search():
    if search.STATUS["running"]:
        return {"started": False}
    threading.Thread(target=search.run, daemon=True).start()
    return {"started": True}


@router.get("/search/status")
def search_status():
    return {**search.STATUS, "health": search.health(), "dropped": search.load_state().get("dropped", [])}


@router.get("/search/coverage")
def search_coverage():
    """How often each search word comes round per board, and whether the look-back covers that (Settings › Search)."""
    return search.coverage(pf.Preferences.model_validate(pf.read("preferences")).model_dump(), search.load_state())


@router.post("/search/rank")
async def rank():
    return {"ranked": await guard(search.rank)}


OPEN = ("new", "shortlisted", "applying", "paused")  # the Board's statuses; f"in {OPEN}" is the SQL list (2+ items)


@router.get("/board")
def board(show: str = "open"):
    """Postings for the board. show: open (new, starred, in progress) | filtered | all.
    Each row gets link (the company's own posting when known), and reason for filtered ones."""
    where = {"open": f"status in {OPEN}", "filtered": "status='filtered'", "all": "1=1"}.get(show, "1=1")
    with db() as con:
        rows = [dict(r) for r in con.execute(
            f"select key, title, company, location, country, url, source, posted, first_seen, deadline, type, status, score, why, note, data"
            f" from jobs where {where} order by coalesce(score, 0) desc, first_seen desc")]
        applied = Counter(r[0].lower() for r in con.execute("select company from applications"))
    if show == "open":  # a job already applied to (also the same job found on another site) never shows again
        from careerpilot.core import apply
        index = apply.applied_index()
        rows = [r for r in rows if not apply.applied_to(r, index)]
    for r in rows:
        r["applied_before"] = applied[(r["company"] or "").lower()]
        data = json.loads(r.pop("data") or "{}")
        r["site_url"], r["applicants"] = data.get("site_url", ""), data.get("applicants")
        r["link"] = r["site_url"] or r["url"]
        tag, r["why"] = reason(r["why"])
        r["reason"] = tag if r["status"] == "filtered" else ""
    return rows


def reason(why):
    """'[weak] text' -> ('weak', 'text'). Untagged or [junk] -> off-topic."""
    m = re.match(r"\[(\w+)\]\s*", why or "")
    return (m[1] if m and m[1] in ("weak", "type", "excluded") else "offtopic"), (why or "")[m.end() if m else 0:]


@router.get("/sources")
def all_sources():
    """Every source that can run for the person's countries, and which ones are off."""
    from careerpilot.core import profile as pf2
    prefs = pf2.Preferences.model_validate(pf2.read("preferences"))
    names = set(sources.REMOTE_BOARDS)
    for t in prefs.targets:
        names |= set(sources.global_boards(t.code)) | set(search.pack_sources(t.code)[0])
    return {"sources": sorted(names), "off": prefs.sources_off}


@router.get("/sites")
def list_sites():
    """Every career site CareerPilot knows, plus a summary: how many are on, and how each was found."""
    sites.seed()
    with db() as con:
        rows = [dict(r) for r in con.execute("select * from sites order by lower(company), kind")]
    by = {}
    for r in rows:
        by[r["origin"]] = by.get(r["origin"], 0) + 1
    return {"rows": rows, "summary": {"total": len(rows), "on": sum(not r["off"] for r in rows), "origins": by,
                                      "jobs": sum(r["last_count"] or 0 for r in rows if not r["off"])}}


@router.post("/sites")
async def add_site(request: Request):
    b = await request.json()
    url, name = b.get("url", "").strip(), b.get("name", "").strip()
    found = await guard(sites.learn, url, name, "you")
    if not found:
        raise HTTPException(422, "Couldn't recognise the hiring system behind that page. Supported: "
                                 + ", ".join(k.title() for k in sorted(sites.scanners()) if k != "page")
                                 + ", or a careers page that lists its jobs as links.")
    if not name:
        with db() as con:
            con.execute("update sites set company=? where kind=? and ref=? and coalesce(company, '')=''", (found[1], *found))
    return {"kind": found[0], "ref": found[1]}


@router.put("/sites")
async def switch_site(request: Request):
    b = await request.json()
    with db() as con:
        con.execute("update sites set off=? where kind=? and ref=?", (int(bool(b.get("off"))), b["kind"], b["ref"]))
    return {"ok": True}


@router.put("/schedule")
async def schedule(request: Request):
    at = (await request.json()).get("time", "")
    settings_api.schedule(at)
    prefs = pf.read("preferences")
    prefs["search_time"] = at
    pf.write("preferences", prefs)
    return {"ok": True}
