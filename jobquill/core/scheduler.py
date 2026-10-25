"""Daily search and an hourly email check even when the app is closed: Windows scheduled tasks, macOS launchd jobs or
Linux cron lines, running `jobquill-app search` / `jobquill-app mail` (no window). A JOBQUILL_HOME gets its
own tasks, named with its id.

The email check stands on its own: it runs whenever email is connected, whether or not there is a daily search (an
interview mail mustn't wait for the person to open the app). ensure() puts back a task that is missing or runs an old
command; the app calls it at start and after every scheduled run."""
import html
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from jobquill.core.data import instance


def _suffix(sep: str) -> str:
    return sep + instance() if instance() else ""


JOBS = {"search": "daily search", "mail": "email check"}


def name(job="search") -> str:
    return f"JobQuill {JOBS[job]}" + _suffix(" ")


def _plist(job="search") -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"app.jobquill.{job}{_suffix('-')}.plist"


def _marker(job="search") -> str:
    return "# jobquill" + (" mail" if job == "mail" else "") + _suffix(" ")


def command(job="search") -> list[str]:
    """The windowless launcher of this very install (next to the running Python), so no console window opens each hour
    and another copy on the PATH is never the one that runs."""
    here = Path(sys.executable).parent
    app = next((p for p in (here / "jobquill-app.exe", here / "jobquill-app") if p.is_file()), None)
    exe = str(app) if app else shutil.which("jobquill-app") or shutil.which("jobquill")
    pythonw = here / "pythonw.exe"
    cmd = [exe, job] if exe else [str(pythonw if pythonw.is_file() else sys.executable), "-m", "jobquill.app.server", job]
    home = os.environ.get("JOBQUILL_HOME")
    return cmd + [str(Path(home).resolve())] if home else cmd  # `search <home>` sets JOBQUILL_HOME for the run


def line(job="search") -> str:
    """The command as one comparable line (no quotes)."""
    return " ".join(command(job))


def enable(at: str = "10:00"):
    """The daily search at HH:MM, and the email check every hour (it does nothing until email is set up)."""
    _enable("search", at)
    _enable("mail", at)


def ensure(search_time: str | None = None, mail_on: bool | None = None):
    """The tasks match what is wanted: the daily search when it has a time, the hourly email check whenever email is
    connected. A missing task, or one running another command (an older install, python.exe), is made again."""
    if search_time is None or mail_on is None:
        from jobquill.core import mail
        from jobquill.core import profile as pf
        search_time = pf.read("preferences").get("search_time", "") if search_time is None else search_time
        mail_on = mail.configured() if mail_on is None else mail_on
    want, have = {"search": bool(search_time), "mail": bool(mail_on)}, current()
    for job, on in want.items():
        if on and have.get(job) != line(job):
            _enable(job, search_time or "09:00")
        elif not on and have.get(job):
            _disable(job)


def current() -> dict[str, str]:
    """{job: the command line its task runs} for the tasks that exist."""
    out = {}
    for job in JOBS:
        try:
            if sys.platform == "win32":
                r = subprocess.run(["schtasks", "/Query", "/TN", name(job), "/XML"], capture_output=True, text=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                if r.returncode == 0:
                    out[job] = from_xml(r.stdout)
            elif sys.platform == "darwin":
                if _plist(job).is_file():
                    out[job] = " ".join(html.unescape(a) for a in re.findall(r"<string>(.*?)</string>", _plist(job).read_text())[1:])
            else:
                hit = next((l for l in _crontab_lines() if l.endswith(_marker(job))), None)
                if hit:
                    out[job] = " ".join(hit[:-len(_marker(job))].split()[5:]).replace('"', "")
        except OSError:
            pass
    return out


def from_xml(xml: str) -> str:
    """A Windows task's command line, from `schtasks /Query /XML`."""
    get = lambda tag: html.unescape((re.search(rf"<{tag}>(.*?)</{tag}>", xml, re.S) or [None, ""])[1])
    return " ".join(x for x in (get("Command"), get("Arguments")) if x).replace('"', "")


def _enable(job, at):
    hour, minute = (int(x) for x in at.split(":"))
    cmd = command(job)
    if sys.platform == "win32":
        every = ["/SC", "DAILY", "/ST", f"{hour:02d}:{minute:02d}"] if job == "search" else ["/SC", "HOURLY", "/MO", "1"]
        subprocess.run(["schtasks", "/Create", "/F", "/TN", name(job), *every, "/TR", subprocess.list2cmdline(cmd)],
                       check=True, capture_output=True)
        # schtasks makes a task that never starts on battery and skips a start missed while asleep: a laptop then misses
        # interview mails for hours. These settings aren't reachable through schtasks, only PowerShell.
        subprocess.run(["powershell", "-NoProfile", "-Command", "Set-ScheduledTask -TaskName '" + name(job) + "' -Settings "
                        "(New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries)"],
                       capture_output=True)
    elif sys.platform == "darwin":
        args = "".join(f"<string>{html.escape(a)}</string>" for a in cmd)
        when = (f"<key>StartCalendarInterval</key><dict><key>Hour</key><integer>{hour}</integer><key>Minute</key>"
                f"<integer>{minute}</integer></dict>" if job == "search" else "<key>StartInterval</key><integer>3600</integer>")
        plist = _plist(job)
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>Label</key><string>{plist.stem}</string>
<key>ProgramArguments</key><array>{args}</array>
{when}
</dict></plist>""", encoding="utf-8")
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        subprocess.run(["launchctl", "load", str(plist)], check=True, capture_output=True)
    else:
        entry = f"{minute if job == 'search' else 0} {hour if job == 'search' else '*'} * * * {subprocess.list2cmdline(cmd)}  {_marker(job)}"
        _crontab([l for l in _crontab_lines() if not l.endswith(_marker(job))] + [entry])


def _disable(job):
    if sys.platform == "win32":
        subprocess.run(["schtasks", "/Delete", "/F", "/TN", name(job)], capture_output=True)
    elif sys.platform == "darwin":
        subprocess.run(["launchctl", "unload", str(_plist(job))], capture_output=True)
        _plist(job).unlink(missing_ok=True)
    else:
        _crontab([l for l in _crontab_lines() if not l.endswith(_marker(job))])


def disable(job: str | None = None):
    """One task, or both (Delete everything)."""
    for j in [job] if job else JOBS:
        _disable(j)


def _crontab_lines():
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout.splitlines() if r.returncode == 0 else []


def _crontab(lines):
    subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)
