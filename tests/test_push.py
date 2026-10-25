"""Pushes that always arrive: written first, sent after, tried again until they get through; interview, test, offer and
"action needed" can't be switched off; one push per conversation; a desktop notification on this computer."""
import sys

import apprise
import keyring
import pytest

from jobquill.core import alerts, mail
from jobquill.core import profile as pf

DESKTOP = alerts.desktop  # the real one (conftest swaps it out for every test; this test runs it with a fake subprocess)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBQUILL_HOME", str(tmp_path))
    store = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, n: store.get((s, n)))
    monkeypatch.setattr(keyring, "set_password", lambda s, n, v: store.__setitem__((s, n), v))
    monkeypatch.setattr(keyring, "delete_password", lambda s, n: store.pop((s, n), None))
    monkeypatch.setattr(alerts, "desktop", lambda title, body: None)
    return tmp_path


@pytest.fixture
def phone(home, monkeypatch):
    """One connected channel; .up decides whether it takes the next alert; .got lists what arrived."""
    class Phone:
        up, got = True, []

        def add(self, url):
            return True

        def notify(self, title, body):
            if Phone.up:
                Phone.got.append(title)
            return Phone.up
    monkeypatch.setattr(apprise, "Apprise", Phone)
    alerts.set_urls(["ntfy://jobquill-test"])
    Phone.up, Phone.got = True, []
    return Phone


def test_an_alert_that_didnt_arrive_is_sent_on_the_next_run_once(phone):
    phone.up = False
    alerts.push("interview", "JobQuill: Interview · Acme", "Tue 10:00")
    assert phone.got == []
    phone.up = True
    alerts.flush()
    alerts.flush()
    assert phone.got == ["JobQuill: Interview · Acme"]


def test_important_alerts_cant_be_switched_off(phone):
    prefs = pf.read("preferences")
    prefs["alert_events"] = []
    pf.write("preferences", prefs)
    for event in ("interview", "assessment", "offer", "action"):
        assert alerts.send(event, event, "")
    assert not alerts.send("matches", "matches", "") and not alerts.send("rejection", "rejection", "")


def test_one_push_per_conversation_but_every_interview(phone):
    alerts.push("action", "Confirm your email · Acme", "", key="acme|confirm your email")
    alerts.push("action", "Confirm your email · Acme", "", key="acme|confirm your email")  # the reminder mail an hour later
    alerts.push("interview", "Interview · Acme", "", key="acme|invitation")
    alerts.push("interview", "Interview · Acme", "", key="acme|invitation")  # a changed time: say it again
    assert phone.got == ["Confirm your email · Acme", "Interview · Acme", "Interview · Acme"]


def test_after_two_failed_tries_an_important_alert_also_goes_by_email(phone, monkeypatch):
    sent = []
    monkeypatch.setattr(mail, "configured", lambda: True)
    monkeypatch.setattr(mail, "settings", lambda: {"address": "me@example.org"})
    monkeypatch.setattr(mail, "send", lambda to, subject, body, files=(): sent.append((to, subject)))
    phone.up = False
    alerts.push("offer", "Offer · Acme", "")
    assert alerts.failing()  # Needs you shows it
    alerts.flush()
    alerts.flush()
    assert sent == [("me@example.org", "Offer · Acme")]


def test_one_broken_channel_doesnt_make_the_working_one_repeat(home, monkeypatch):
    """ntfy works, an old Telegram bot doesn't: the alert reached the person once, it isn't sent again every hour."""
    got = []

    class Channels:
        def __init__(self):
            self.urls = []

        def add(self, url):
            self.urls.append(url)
            return True

        def notify(self, title, body):
            got.extend(u for u in self.urls if u.startswith("ntfy"))
            return all(u.startswith("ntfy") for u in self.urls)  # Apprise: False when any of its links failed
    monkeypatch.setattr(apprise, "Apprise", Channels)
    alerts.set_urls(["ntfy://jobquill-test", "tgram://123:expired/1"])
    alerts.push("interview", "Interview · Acme", "")
    alerts.flush()
    alerts.flush()
    assert got == ["ntfy://jobquill-test"] and not alerts.failing()


def test_the_red_line_goes_once_alerts_arrive_again(phone):
    """An alert given up after 3 days doesn't keep saying "your alerts haven't arrived" forever."""
    from jobquill.core.data import db
    with db() as con:
        con.execute("insert into alerts_out(at, event, title, body, key) values ('2020-01-01 09:00:00', 'interview', 'old', '', '')")
    assert alerts.failing() == ""


def test_a_hostile_subject_never_breaks_the_desktop_notification(monkeypatch):
    seen = []

    def run(args, **kw):
        text = " ".join(args) + " ".join((kw.get("env") or {}).values())
        if "\x00" in text:
            raise ValueError("embedded null character")
        seen.append(text)
    monkeypatch.setattr(alerts.subprocess, "run", run)
    monkeypatch.setattr(alerts.shutil, "which", lambda name: "/usr/bin/" + name)
    DESKTOP("Interview\x00 · Acme", "at 10:00\x07")
    assert seen and "Interview · Acme" in seen[0]
    monkeypatch.setattr(alerts.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("anything")))
    DESKTOP("Interview · Acme", "")  # a notification is a bonus: nothing it does stops the check


def test_the_email_check_stands_alone_and_comes_back(home, monkeypatch):
    """Switching the daily search off never stops the email check; a task deleted by hand or still running an old
    command is made again; the check runs the windowless launcher next to this Python, never python.exe."""
    from jobquill.core import scheduler
    tasks = {}
    monkeypatch.setattr(scheduler, "current", lambda: dict(tasks))
    monkeypatch.setattr(scheduler, "_enable", lambda job, at: tasks.__setitem__(job, scheduler.line(job)))
    monkeypatch.setattr(scheduler, "_disable", lambda job: tasks.pop(job, None))
    assert "jobquill-app" in scheduler.line("mail") and "python.exe" not in scheduler.line("mail")
    tasks["search"] = scheduler.line("search")
    scheduler.ensure(search_time="", mail_on=True)
    assert set(tasks) == {"mail"}
    tasks.clear()
    scheduler.ensure(search_time="09:00", mail_on=True)
    assert set(tasks) == {"search", "mail"}
    tasks["mail"] = "C:\\x\\python.exe -m jobquill.app.server mail"
    scheduler.ensure(search_time="09:00", mail_on=True)
    assert tasks["mail"] == scheduler.line("mail")
    scheduler.ensure(search_time="", mail_on=False)
    assert tasks == {}


def test_a_windows_task_is_read_back_from_its_xml():
    from jobquill.core import scheduler
    xml = ("<Task><Actions Context=\"Author\"><Exec><Command>\"C:\\Program Files\\cp\\jobquill-app.exe\"</Command>"
           "<Arguments>mail &amp;</Arguments></Exec></Actions></Task>")
    assert scheduler.from_xml(xml) == "C:\\Program Files\\cp\\jobquill-app.exe mail &"


def test_desktop_notification_never_puts_mail_text_into_a_script(monkeypatch):
    seen = []
    monkeypatch.setattr(alerts.subprocess, "run", lambda args, **kw: seen.append((args, kw.get("env") or {})))
    monkeypatch.setattr(alerts.shutil, "which", lambda name: "/usr/bin/" + name)
    title, body = "Interview'; Remove-Item C:\\ -Recurse; '", "$(rm -rf ~) at 10:00"
    DESKTOP(title, body)
    args, env = seen[0]
    assert title not in " ".join(args) or sys.platform != "win32"
    if sys.platform == "win32":
        assert env["CP_TITLE"] == title and env["CP_BODY"] == body
    else:
        assert args[-2:] == [title, body]  # plain arguments, never shell text
