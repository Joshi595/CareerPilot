"""Alerts to the person's phone or chat through Apprise (Telegram, WhatsApp, Discord, Slack, ntfy, email, Signal…), and
optionally by email to their own address. The destination links are secrets (they hold tokens), so they live in the
password store, not in a file.

What only the person can act on (an interview, a test, an offer, "action needed") is never lost: push() writes it to
an outbox first, flush() sends what hasn't arrived yet (every hourly run, and at app start), and after two failed tries
it also goes out by email. It shows on this computer's own notifications at once as well."""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta

from jobquill.core import mail, secrets, sources
from jobquill.core import profile as pf
from jobquill.core.data import db

EVENTS = ["matches", "reply", "action", "assessment", "interview", "offer", "rejection", "digest", "sources"]
IMPORTANT = ("interview", "assessment", "offer", "action")  # always on: a missed one can cost the job
REPEATS = ("interview", "assessment", "offer")  # said again even when the conversation was pushed today (a new time)
KEEP_TRYING = timedelta(days=3)
KINDS = [(r"^ntfys?://", "Phone (ntfy)"), (r"^tgram://", "Telegram"), (r"hooks\.slack\.com|^slack://", "Slack"),
         (r"discord(app)?\.com/api/webhooks|^discord://", "Discord"), (r"webhook\.office\.com|^msteams://|logic\.azure\.com", "Teams")]


def urls() -> list[str]:
    return json.loads(secrets.get("alert_urls") or "[]")


def set_urls(items: list[str]):
    secrets.put("alert_urls", json.dumps([u.strip() for u in items if u.strip()]))


def channels() -> list[dict]:
    """The connected links, masked: enough to tell them apart, never the token."""
    out = []
    for i, u in enumerate(urls()):
        kind = next((k for rx, k in KINDS if re.search(rx, u)), u.split(":", 1)[0])
        head = u.split("://", 1)[0] + "://"
        out.append({"i": i, "kind": kind, "label": head + u[len(head):len(head) + 4] + "…" + u[-3:]})
    return out


def telegram(token: str) -> str:
    """tgram://<token>/<chat id>: the chat is the one that last wrote to the bot (the person, told to do so first)."""
    token = token.strip()
    if not re.fullmatch(r"\d+:[\w-]{20,}", token):
        raise ValueError("That doesn't look like a bot token. It looks like 123456789:AAH… and comes from @BotFather.")
    try:
        updates = sources.get_json(f"https://api.telegram.org/bot{token}/getUpdates").get("result", [])
    except RuntimeError as e:
        raise ValueError(f"Telegram didn't accept that token ({e}). Copy it again from @BotFather.")
    chats = [c for u in updates for c in [(u.get("message") or u.get("channel_post") or {}).get("chat", {}).get("id")] if c]
    if not chats:
        raise ValueError("Send your bot any message first (open it in Telegram and say hi), then press Connect again.")
    return f"tgram://{token}/{chats[-1]}"


def send(event: str, title: str, body: str, force: bool = False, only: list[str] | None = None) -> bool:
    """True when at least one destination took it. Only events the person switched on (the important ones always),
    unless forced (test). only: just these links (a channel's Test button), without the email copy."""
    prefs = pf.Preferences.model_validate(pf.read("preferences"))
    if event not in prefs.alert_events and event not in IMPORTANT and not force:
        return False
    targets, ok = urls() if only is None else only, False
    if targets:
        import apprise
        for u in targets:  # one at a time: Apprise says False when any link failed, though the others delivered it
            a = apprise.Apprise()
            a.add(u)
            ok = bool(a.notify(title=title, body=body)) or ok
    if only is None and prefs.alert_email and mail.configured():
        try:
            mail.send(mail.settings()["address"], title, body)
            ok = True
        except Exception:
            pass  # an alert must never break the step that raised it
    return ok


def push(event: str, title: str, body: str, key: str = "") -> bool:
    """The reliable way: written to the outbox, then sent (flush). key names the conversation: the same key within 24
    hours is pushed once (reminder mails), except interviews, tests and offers. The important ones also show on this
    computer at once."""
    prefs = pf.Preferences.model_validate(pf.read("preferences"))
    if event not in prefs.alert_events and event not in IMPORTANT:
        return False
    with db() as con:
        since = (datetime.now() - timedelta(hours=24)).isoformat(" ", "seconds")
        if key and event not in REPEATS and con.execute("select 1 from alerts_out where key=? and at >= ?", (key, since)).fetchone():
            return True
        con.execute("insert into alerts_out(at, event, title, body, key) values (?,?,?,?,?)",
                    (datetime.now().isoformat(" ", "seconds"), event, title, body, key))
    if event in IMPORTANT and prefs.desktop_alerts:
        desktop(title, body)
    return flush()


def flush() -> bool:
    """Send what hasn't arrived yet (from the last 3 days). After two failed tries, an important alert also goes to the
    person's own email address. Without any channel there is nothing to send to: the desktop notification was it.
    True when nothing is left waiting."""
    prefs = pf.Preferences.model_validate(pf.read("preferences"))
    since = (datetime.now() - KEEP_TRYING).isoformat(" ", "seconds")
    with db() as con:
        rows = [dict(r) for r in con.execute("select * from alerts_out where sent_at is null and at >= ? order by id", (since,))]
    left = False
    for r in rows:
        if not urls() and not (prefs.alert_email and mail.configured()):
            ok, error = True, "no channel connected"
        else:
            try:
                ok, error = send(r["event"], r["title"], r["body"]), ""
            except Exception as e:  # a broken link or the network: try again next time
                ok, error = False, str(e)[:200]
        fallback = r["fallback"]
        if not ok and r["tries"] + 1 >= 2 and r["event"] in IMPORTANT and not fallback and mail.configured():
            try:
                mail.send(mail.settings()["address"], r["title"], r["body"])
                fallback = 1
            except Exception:
                pass
        with db() as con:
            con.execute("update alerts_out set tries=tries+1, sent_at=?, error=?, fallback=? where id=?",
                        (datetime.now().isoformat(" ", "seconds") if ok else None, error, fallback, r["id"]))
        left = left or not ok
    return not left


def failing() -> str:
    """When an important alert hasn't reached the person's channels: the time of the oldest one still being tried, else ''."""
    since = (datetime.now() - KEEP_TRYING).isoformat(" ", "seconds")
    with db() as con:
        r = con.execute("select min(at) from alerts_out where sent_at is null and at >= ? and event in (?,?,?,?)",
                        (since, *IMPORTANT)).fetchone()
    return r[0] or ""


TOAST = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
         "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
         "$t = $x.GetElementsByTagName('text');"
         "$t.Item(0).AppendChild($x.CreateTextNode($env:CP_TITLE)) > $null; $t.Item(1).AppendChild($x.CreateTextNode($env:CP_BODY)) > $null;"
         "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
         "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show("
         "[Windows.UI.Notifications.ToastNotification]::new($x))")


def desktop(title: str, body: str):
    """This computer's own notification, with the system's own tools. Mail subjects are other people's text: they go in
    as arguments or environment values, never into script text, and without control characters (a NUL can't go in one)."""
    title, body = (re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", x) for x in (title, body))
    try:
        if sys.platform == "win32":
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", TOAST], capture_output=True, timeout=30,
                           env={**os.environ, "CP_TITLE": title, "CP_BODY": body}, creationflags=subprocess.CREATE_NO_WINDOW)
        elif sys.platform == "darwin":
            subprocess.run(["osascript", "-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
                            "-e", "end run", title, body], capture_output=True, timeout=30)
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", "--", title, body], capture_output=True, timeout=30)
    except Exception:
        pass  # a notification is a bonus on top of the outbox: nothing it does may stop the mail check
