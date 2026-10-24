"""The local app: one web server on 127.0.0.1, opened in the person's browser."""
import json
import os
import secrets
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from careerpilot.app import apply_api, jobs_api, mail_api, practice_api, settings_api, setup_api
from careerpilot.app.jobs_api import OPEN
from careerpilot.core import ai, data, system

HERE = Path(__file__).parent
TOKEN = secrets.token_urlsafe(24)  # new every start; only our own page knows it
STAMP = format(int(time.time()), "x")  # new every start: script and style links change, so no browser keeps an old copy
IDLE_EXIT = 600  # seconds without a page open before the app stops itself
SERVER: uvicorn.Server | None = None
last_ping = 0.0  # 0 until a tab has opened: the app never stops before the person got to see it

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])  # blocks DNS rebinding
for name in ("i18n", "static"):
    app.mount(f"/{name}", StaticFiles(directory=HERE / name), name=name)
app.mount("/tabs", StaticFiles(directory=HERE / "pages" / "tabs"), name="tabs")
app.mount("/fonts", StaticFiles(directory=HERE.parent / "templates" / "fonts"), name="fonts")
app.include_router(setup_api.router)
app.include_router(apply_api.router)
app.include_router(jobs_api.router)
app.include_router(settings_api.router)
app.include_router(mail_api.router)
app.include_router(practice_api.router)


@app.middleware("http")
async def require_token(request: Request, call_next):
    # Any website open in the same browser could call our API; only our page has the token.
    # Links and images (PDFs, previews) can't send headers, so a GET may carry it as ?t=.
    sent = request.headers.get("x-token") or (request.query_params.get("t") if request.method == "GET" else None)
    # /api/ping only says "a tab is open" (keeps the app alive); it changes nothing, so it needs no token
    if request.url.path.startswith("/api/") and request.url.path != "/api/ping" and not secrets.compare_digest(sent or "", TOKEN):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await call_next(request)


# Our pages run only our own scripts (no inline ones, no javascript: links: a link from a posting can't run code here)
# and can't be shown inside another website's frame (where a click could be steered).
CSP = ("default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")  # unsafe-eval: Alpine's expressions


@app.middleware("http")
async def safety_headers(request: Request, call_next):
    r = await call_next(request)
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "no-referrer"  # links opened from the app never carry its address (or a ?t= key)
    # the browser asks each time (a cheap "not modified" when nothing changed): after an update it would otherwise keep
    # showing the old page for hours
    r.headers.setdefault("Cache-Control", "no-cache")
    if r.headers.get("content-type", "").startswith("text/html"):  # pages only: PDFs open in the browser's own viewer
        r.headers["Content-Security-Policy"] = CSP
        r.headers["X-Frame-Options"] = "DENY"
    return r


def _private_write(path: Path, text: str):
    """A file only this user may read (0600 where that exists; the data folder itself is private on Windows)."""
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        f.write(text)


def _instance() -> dict:
    try:
        return json.loads((data.home() / "instance.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def launch_code() -> str:
    """What the page needs (?k=) before it hands out the key. Anything on this computer can reach 127.0.0.1, other user
    accounts too; only this user can read the data folder the code is kept in. Kept across restarts, so an open tab
    reloads fine after an update; Delete everything removes it (a new one next time)."""
    inst = _instance()
    if not inst.get("code"):
        inst["code"] = secrets.token_urlsafe(24)
        _private_write(data.home() / "instance.json", json.dumps(inst))
    return inst["code"]


def write_opener(port: int) -> Path:
    """open.html: a page that forwards to the app with the code. The browser is given this file, so the code never
    shows up in a command line other accounts could list."""
    url = f"http://127.0.0.1:{port}/?k={launch_code()}"
    _private_write(data.home() / "instance.json", json.dumps({**_instance(), "port": port}))
    page = data.home() / "open.html"
    _private_write(page, f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={url}">'
                         f'<title>CareerPilot</title><a href="{url}">Open CareerPilot</a>\n')
    return page


def opener(port: int) -> str:
    """What the browser is given: open.html (the code never in a command line), or on Linux the address itself: snap
    browsers (Ubuntu's Firefox) can't open files in hidden folders such as ~/.local/share."""
    page = write_opener(port)
    return f"http://127.0.0.1:{port}/?k={launch_code()}" if sys.platform.startswith("linux") else page.as_uri()


def proof(nonce: str) -> str:
    """Shows a caller that this is the CareerPilot of this data folder without giving the launch code away."""
    import hashlib
    import hmac
    return hmac.new(launch_code().encode(), nonce.encode(), hashlib.sha256).hexdigest()


def alive(port: int) -> bool:
    """This data folder's CareerPilot answers on this port (another one, of another account or another folder, may have
    the port now). It is asked to prove it knows the code; the code itself is never sent to whatever listens there."""
    import urllib.request
    nonce = secrets.token_hex(16)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/ping?n={nonce}", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=3) as r:
            return secrets.compare_digest(str(json.loads(r.read()).get("proof", "")), proof(nonce))
    except (OSError, ValueError):
        return False


def running_port() -> int | None:
    """The port of this data folder's CareerPilot when it is already running, else None."""
    port = _instance().get("port")
    return port if isinstance(port, int) and alive(port) else None


ELSEWHERE = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>CareerPilot</title><body style="font-family:'
             'system-ui,sans-serif;max-width:36rem;margin:15vh auto;padding:0 1rem;line-height:1.5"><h1>CareerPilot is running</h1>'
             "<p>Open it with its <b>Start CareerPilot</b> icon. This address alone doesn't open it: that keeps your data "
             "away from anyone else using this computer.</p></body></html>")


@app.get("/", response_class=HTMLResponse)
def index(k: str = ""):
    if not secrets.compare_digest(k.encode(), launch_code().encode()):
        return HTMLResponse(ELSEWHERE, status_code=403)
    page = (HERE / "pages" / "index.html").read_text(encoding="utf-8").replace("{{TOKEN}}", TOKEN)
    return page.replace('.js"></script>', f'.js?v={STAMP}"></script>').replace('href="/static/app.css"', f'href="/static/app.css?v={STAMP}"')


@app.get("/api/health")
def health():
    data.db().close()
    return {"data_folder": str(data.home()), "agents": ai.installed()}


@app.get("/api/counts")
def counts():
    """Numbers for the sidebar. Needs you: the open mails (to act on, or to say which application)."""
    sql = {"board": f"jobs where status in {OPEN}", "less": "jobs where status='filtered'", "applications": "applications",
           "needs": "mail_items where done=0"}
    with data.db() as con:
        out = {k: con.execute(f"select count(*) from {v}").fetchone()[0] for k, v in sql.items()}
    return out


class Ping(BaseModel):
    ok: bool
    model_config = {"extra": "forbid"}


@app.post("/api/ai/test/{name}")
def ai_test(name: str):
    if name not in ai.AGENTS:
        raise HTTPException(404)
    try:
        ai.answer("Reply with ok set to true.", Ping, ai.AGENTS[name], timeout=180)
        return {"ok": True}
    except ai.AIError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/ping")
def ping(n: str = ""):
    """A tab is open. With a nonce (a second start asking "is this mine?"): proof that this server knows the launch code."""
    global last_ping
    last_ping = time.time()
    return {"ok": True, **({"proof": proof(n[:64])} if n else {})}


@app.post("/api/quit")
def quit_app():
    if SERVER:
        SERVER.should_exit = True
    return {"ok": True}


@app.post("/api/open-folder")
def open_folder():
    system.open_path(data.home())
    return {"ok": True}


@app.get("/api/backup")
def backup():
    return Response(system.backup(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="careerpilot-backup.zip"'})


@app.post("/api/update")
def update():
    return {"message": system.update()}


def upkeep(after: str = ""):
    """At app start and after every scheduled run: alerts that didn't arrive go out again, the scheduled tasks are put
    back if missing, and a stale email check runs now (after the daily search it also says so: the computer is on, so
    the hourly task should have run)."""
    from careerpilot.core import alerts, mail, scheduler
    for step in (alerts.flush, scheduler.ensure, lambda: mail.watchdog(loud=after == "search")):
        try:
            step()
        except Exception:
            pass  # upkeep never stops the app or a run


def watchdog():
    """Stop when no page has pinged for a while: closing the browser tab ends the app."""
    while SERVER and not SERVER.should_exit:
        time.sleep(15)
        if last_ping and time.time() - last_ping > IDLE_EXIT:
            SERVER.should_exit = True


def free_port(start=8790):
    for port in range(start, start + 50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)):
                return port
    raise SystemExit("No free port between 8790 and 8839")


def source_test(country: str = "", query: str = "nurse"):
    """Run every source for one country once and print what came back. Nothing is stored."""
    from babel import Locale
    from careerpilot.core import search, sources
    if not country:
        raise SystemExit('usage: careerpilot source-test <country code> "<search words>"')
    cc = country.upper()
    name = Locale("en").territories.get(cc, cc)
    ctx = {"country": cc, "country_name": name.lower(), "location": name, "days": 14, "remote": "any", "place": "", "radius": 50}
    boards, _, _ = search.pack_sources(cc)
    for bname, fn in {**sources.global_boards(cc), **boards}.items():
        try:
            rows = fn(query, ctx)
            print(f"{bname:16} {len(rows):4}  " + " | ".join(r["title"][:40] for r in rows[:3]))
        except Exception as e:
            print(f"{bname:16} FAILED {type(e).__name__}: {str(e)[:120]}")


def main():
    global SERVER
    if sys.stdout is None:  # started without a console (pythonw, the desktop shortcut): uvicorn's logging needs streams
        sys.stdout = sys.stderr = open(os.devnull, "w")
    if sys.argv[1:2] in (["search"], ["mail"]):  # the schedules: daily search, hourly email check, without opening the app
        if sys.argv[2:3]:  # a CAREERPILOT_HOME's own task passes its folder (scheduler.command)
            os.environ["CAREERPILOT_HOME"] = sys.argv[2]
        from careerpilot.core import mail, search
        print(search.run() if sys.argv[1] == "search" else mail.hourly(), mail.sweep())
        upkeep(after=sys.argv[1])
        return
    if sys.argv[1:2] == ["source-test"]:  # for country-pack authors: careerpilot source-test <country code> "<search words>"
        source_test(*sys.argv[2:4])
        return
    show = not os.environ.get("CAREERPILOT_NO_BROWSER")
    port = running_port()
    if port:  # already running for this data folder: open it, never a second server on the same data
        if show:
            webbrowser.open(opener(port))
        return
    port = free_port()
    page = opener(port)
    from careerpilot.core import apply, render
    apply.current().mkdir(parents=True, exist_ok=True)
    system.private(apply.current())  # passports and permits: this user's alone (a repo folder on D:\ is everyone's)
    if sys.platform != "win32":
        system.private(data.home())
    render.warm_thumbs()
    threading.Thread(target=upkeep, daemon=True).start()
    SERVER = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=watchdog, daemon=True).start()
    if show:
        threading.Timer(1.0, webbrowser.open, [page]).start()
    SERVER.run()


if __name__ == "__main__":
    main()
