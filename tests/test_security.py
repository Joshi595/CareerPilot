"""Hardening from the 0.12 review: only known statuses, example profiles never overwrite a CV, the problem report hides
who you are, practice ids are numbers, a StepStone answer from a private address is dropped."""
from pathlib import Path

import keyring
import pytest
from fastapi.testclient import TestClient

from jobquill.core import profile as pf
from jobquill.core.data import db


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBQUILL_HOME", str(tmp_path))
    store = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, n: store.get((s, n)))
    monkeypatch.setattr(keyring, "set_password", lambda s, n, v: store.__setitem__((s, n), v))
    monkeypatch.setattr(keyring, "delete_password", lambda s, n: store.pop((s, n), None))
    return tmp_path


def client():
    from jobquill.app import server
    return TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})


def test_job_status_takes_known_statuses_only(home):
    with db() as con:
        con.execute("insert into jobs(key, title, status) values ('k1', 'Nurse', 'new')")
    c = client()
    assert c.put("/api/jobs/k1/status", json={"status": "<b>made up</b>"}).status_code == 422
    assert c.put("/api/jobs/k1/status", json={"status": "shortlisted"}).status_code == 200
    with db() as con:
        assert con.execute("select status from jobs where key='k1'").fetchone()[0] == "shortlisted"


def test_an_example_profile_never_overwrites_a_cv(home):
    c = client()
    assert c.post("/api/example/alex-engineer-de").status_code == 200  # a fresh install may load one
    assert c.post("/api/example/sam-nurse-uk").status_code == 409  # there is a CV now: it stays
    assert pf.profile().basics.name == "Alex Example"


def test_problem_report_hides_who_you_are(home):
    pf.write("profile", {"basics": {"name": "Ada Lovelace", "email": "ada@example.org", "phone": "+44 7700 900123"}})
    (home / "app.log").write_text(f"Ada Lovelace <ada@example.org> +44 7700 900123 opened {Path.home()}\\x\n", encoding="utf-8")
    text = client().get("/api/problem-report").json()["text"]
    for private in ("Ada Lovelace", "ada@example.org", "+44 7700 900123", str(Path.home())):
        assert private not in text


def test_practice_ids_are_numbers(home):
    c = client()
    assert c.get("/api/practice/abc").status_code == 404
    assert c.post("/api/practice/abc/generate").status_code == 404
    assert c.post("/api/practice/abc/answer", json={"question": "q", "answer": "a"}).status_code == 404


def test_a_folder_made_private_is_this_users_alone(tmp_path):
    """Current application holds passports and permits; a repo on D:\\ lets every account on the computer change it."""
    import os
    import subprocess
    import sys
    from jobquill.core import system
    repo = tmp_path / "repo"
    repo.mkdir()
    if sys.platform == "win32":  # like D:\ : every signed-in account may change what's below (S-1-5-11 = Authenticated Users)
        subprocess.run(["icacls", str(repo), "/grant", "*S-1-5-11:(OI)(CI)M"], check=True, capture_output=True)
    folder = repo / "Current application"
    folder.mkdir()
    acl = lambda: subprocess.run(["icacls", str(folder)], capture_output=True, text=True).stdout.replace(str(folder), "")
    if sys.platform == "win32":
        assert "Authenticated Users" in acl()  # inherited before
    system.private(folder)
    if sys.platform == "win32":
        assert os.environ["USERNAME"].lower() in acl().lower()
        assert not any(who in acl() for who in ("Authenticated Users", "BUILTIN\\Users", "Everyone"))
    else:
        assert folder.stat().st_mode & 0o777 == 0o700


def test_update_installs_the_newest_release_never_main(monkeypatch):
    """An update installs a released version (a tag), so one bad push to main never reaches anyone. No tag readable:
    nothing is installed."""
    import sys
    from jobquill.core import sources, system
    ran = []
    monkeypatch.setattr(system.shutil, "which", lambda name: sys.executable)  # "uv" is there
    monkeypatch.setattr(system.subprocess, "run", lambda cmd, **k: ran.append(cmd) or type("R", (), {"stdout": "ok", "stderr": ""})())
    monkeypatch.setattr(sources, "get_json", lambda url, **k: [{"name": "v0.9.0"}, {"name": "v0.12.0"}, {"name": "v0.10.1"}, {"name": "nightly"}])
    system.update()
    assert ran[-1][-1] == "jobquill @ https://github.com/Joshi595/JobQuill/archive/refs/tags/v0.12.0.zip"
    ran.clear()

    def offline(url, **k):
        raise RuntimeError("HTTP 404")
    monkeypatch.setattr(sources, "get_json", offline)
    assert "Nothing was changed" in system.update() and not ran


def test_documents_with_secret_shaped_text_are_blocked(home):
    """An AI with tools (Codex, Antigravity) tricked by a posting could copy a key from this computer into a CV line:
    the PDFs are where it would leave, so the checks stop them there."""
    from jobquill.core import check
    plain = "Built a shipment tracking service in Python, see linkedin.com/in/alexandra-example-engineer-berlin."
    assert not check.check(plain)["fails"]
    head = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"  # split: the release's own secret scan reads this file too
    for leak in ("Token ghp_" + "a1B2c3D4e5" * 4, head + " b3BlbnNzaC1rZXk",
                 "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl me@pc",
                 "Notes: Q2hhbmdlIHRoaXMgdG8geW91ciBvd24gc2VjcmV0IGtleSBub3c9"):
        assert any("password, key or token" in f for f in check.check(plain + " " + leak)["fails"]), leak


def test_ai_answer_runs_start_in_an_empty_folder(monkeypatch, tmp_path):
    """An answer run never starts inside the app's or the person's folders, so relative paths find nothing."""
    import os
    import subprocess
    from jobquill.core import ai
    seen = {}

    def fake_run(args, **kw):
        seen["cwd"] = kw.get("cwd")
        return subprocess.CompletedProcess(args, 0, stdout='{"structured_output": {"ok": true}}', stderr="")
    monkeypatch.setattr(ai.subprocess, "run", fake_run)
    monkeypatch.setenv("JOBQUILL_HOME", str(tmp_path))
    agent = ai.Antigravity()
    monkeypatch.setattr(agent, "path", lambda: "agy")
    assert agent.run("p", {"type": "object"}) == {"ok": True}
    assert seen["cwd"] and seen["cwd"] != os.getcwd() and not os.path.exists(seen["cwd"])  # gone afterwards, was empty


def test_a_stepstone_answer_from_a_private_address_is_dropped(monkeypatch):
    from curl_cffi import requests as browser
    from jobquill.core import sources

    class Answer:
        status_code, text, url = 200, "the app's own data", "http://127.0.0.1:8790/api/health"
    monkeypatch.setattr(browser, "request", lambda *a, **k: Answer())
    status, body, _ = sources.http("https://www.stepstone.de/jobs/nurse")
    assert status == 0 and body == ""


def test_no_password_store_reads_as_nothing_saved(tmp_path, monkeypatch):
    """Linux without a Secret Service (a minimal desktop, a server, CI): reading a secret says "none saved" instead of
    breaking Needs you and every page that asks whether email is connected."""
    import keyring
    from fastapi.testclient import TestClient
    from keyring.errors import NoKeyringError
    monkeypatch.setenv("JOBQUILL_HOME", str(tmp_path))
    monkeypatch.setattr(keyring, "get_password", lambda *a: (_ for _ in ()).throw(NoKeyringError("no backend")))
    from jobquill.app import server
    from jobquill.core import secrets
    assert secrets.get("email_password") == "" and secrets.names() == []
    c = TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})
    assert c.get("/api/mail/items").status_code == 200
