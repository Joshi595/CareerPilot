"""Settings: the Ask box validator, email provider detection and self-test, alert channels."""
import json

import keyring
import pytest
from fastapi.testclient import TestClient

from jobquill.core import ai, alerts, mail, secrets, sources
from jobquill.core import profile as pf
from jobquill.core.data import db


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A tmp home with an in-memory keyring: no test touches a real password store."""
    monkeypatch.setenv("JOBQUILL_HOME", str(tmp_path))
    store = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, n: store.get((s, n)))
    monkeypatch.setattr(keyring, "set_password", lambda s, n, v: store.__setitem__((s, n), v))
    monkeypatch.setattr(keyring, "delete_password", lambda s, n: store.pop((s, n), None))
    return tmp_path


def client():
    from jobquill.app import server
    return TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})


def test_csv_export_never_runs_formulas(home):
    """Company and role come from postings: a cell starting with = + - @ would run as a formula in Excel."""
    with db() as con:
        con.execute("insert into applications(company, role, date_applied, status) values (?, ?, '2026-10-21', 'applied')",
                    ('=HYPERLINK("https://x.example")', "-Nurse"))
    text = client().get("/api/export/applications.csv").text
    assert "'=HYPERLINK" in text and "'-Nurse" in text


def test_older_preferences_file_still_loads(home):
    """A setting a later version removed (fit_threshold) must not stop an older preferences file from loading."""
    (pf.folder() / pf.FILES["preferences"]).write_text("fit_threshold: 3\nstart: soon\n", encoding="utf-8")
    assert pf.Preferences.model_validate(pf.read("preferences")).start == "soon"
    assert client().get("/api/setup/ready").json()["missing"][:2] == ["basics", "targets"]


def test_ask_rejects_denied_keys_and_bad_values_and_returns_a_diff(home, monkeypatch):
    from jobquill.app import settings_api
    pf.write("preferences", {"places": ["Berlin"], "job_types": ["full_time", "internship"], "email": {"address": "a@b.de"}})
    seen = {}
    answer = settings_api.AskAnswer(reply="I'll add Munich and drop internships.", changes=[
        {"key": "places", "value_json": '["Berlin", "Munich"]'},
        {"key": "job_types", "value_json": '["full_time"]'},
        {"key": "weights", "value_json": '{"skills": 2}'},
        {"key": "email", "value_json": '{"address": "evil@x.com"}'},
        {"key": "setup_done", "value_json": "false"},
        {"key": "nonsense", "value_json": "1"},
        {"key": "radius_km", "value_json": '"far"'},
        {"key": "theme", "value_json": "not json"}])
    monkeypatch.setattr(ai, "chosen", lambda *a, **k: None)
    monkeypatch.setattr(ai, "answer", lambda prompt, model, agent, timeout=600: seen.setdefault("prompt", prompt) and answer)
    r = client().post("/api/settings/ask", json={"text": "Also search in Munich, no internships"})
    assert r.status_code == 200
    out = r.json()
    assert {c["key"]: c["new"] for c in out["changes"]} == {
        "places": ["Berlin", "Munich"], "job_types": ["full_time"],
        "weights": {"skills": 2.0, "location": 1.0, "seniority": 1.0, "language": 1.0}}
    assert next(c for c in out["changes"] if c["key"] == "places")["old"] == ["Berlin"]
    assert {s["key"] for s in out["skipped"]} == {"email", "setup_done", "nonsense", "radius_km", "theme"}
    assert pf.read("preferences")["places"] == ["Berlin"]  # nothing saved until Apply
    assert "a@b.de" not in seen["prompt"] and "setup_done" not in seen["prompt"]
    assert "Also search in Munich" in seen["prompt"]
    assert client().post("/api/settings/ask", json={"text": " "}).status_code == 422


def test_email_provider_from_the_address():
    assert mail.detect("ana@gmail.com")["provider"] == "gmail" and mail.detect("ana@GMX.de")["help"].startswith("https://")
    assert mail.detect("ana@hotmail.de")["provider"] == "outlook"
    d = mail.detect("ana@example-firm.de")
    assert (d["provider"], d["imap"], d["smtp"], d["port"]) == ("custom", "imap.example-firm.de", "smtp.example-firm.de", 465)
    assert set(mail.HELP) <= set(mail.PROVIDERS) and set(mail.DOMAINS.values()) <= set(mail.PROVIDERS)


def test_mail_test_reads_and_sends_itself_a_mail(home, monkeypatch):
    pf.write("preferences", {"email": {"address": "ana@gmail.com", "provider": "gmail"}})
    secrets.put("email_password", "abcd efgh ijkl mnop")
    calls = []

    class Imap:
        def __init__(self, host, ssl_context=None, timeout=0): calls.append(("imap", host))
        def login(self, user, pw): calls.append(("imap login", user, pw))
        def logout(self): pass

    class Smtp:
        def __init__(self, host, port, timeout=0, context=None): calls.append(("smtp", host, port))
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def login(self, user, pw): calls.append(("smtp login", user, pw))
        def send_message(self, msg): calls.append(("sent", msg["To"], msg["Subject"]))

    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", Imap)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", Smtp)
    assert "ana@gmail.com" in mail.test()
    assert ("imap login", "ana@gmail.com", "abcdefghijklmnop") in calls and ("smtp", "smtp.gmail.com", 465) in calls
    assert ("sent", "ana@gmail.com", "JobQuill is connected") in calls
    assert mail.SKIP.search("ana@gmail.com jobquill is connected")  # its own mails are never read as replies


def test_email_alert_channel(home, monkeypatch):
    sent = []
    monkeypatch.setattr(mail, "configured", lambda: True)
    monkeypatch.setattr(mail, "settings", lambda: {"address": "ana@gmail.com"})
    monkeypatch.setattr(mail, "send", lambda to, subject, body: sent.append((to, subject)))
    pf.write("preferences", {"alert_events": ["interview"], "alert_email": True})
    assert alerts.send("interview", "JobQuill: Acme", "interview") is True and sent == [("ana@gmail.com", "JobQuill: Acme")]
    assert alerts.send("matches", "x", "y") is False and len(sent) == 1  # an event that's off
    assert alerts.send("test", "t", "b", True, only=[]) is False and len(sent) == 1  # a channel's own test: no email copy
    pf.write("preferences", {"alert_events": ["interview"], "alert_email": False})
    assert alerts.send("interview", "x", "y") is False and len(sent) == 1


def test_alert_channels_are_masked_added_and_removed(home):
    c = client()
    assert c.post("/api/alerts/channels", json={"url": "nonsense"}).status_code == 422
    r = c.post("/api/alerts/channels", json={"url": "ntfy://jobquill-q7x2m9k4pz"})
    assert r.status_code == 200 and r.json()["channels"][0]["kind"] == "Phone (ntfy)"
    got = c.get("/api/alerts").json()
    assert "jobquill-q7x2m9k4pz" not in json.dumps(got) and got["channels"][0]["label"].startswith("ntfy://jobq")
    assert c.delete("/api/alerts/channels/0").json()["channels"] == [] and alerts.urls() == []


def test_telegram_finds_the_chat(monkeypatch):
    token = "123456789:AAHfakeTokenForTests_abcdefghij"
    calls = []
    monkeypatch.setattr(sources, "get_json", lambda url: calls.append(url) or {"result": [
        {"message": {"chat": {"id": 111}}}, {"message": {"chat": {"id": 4242}, "text": "hi"}}]})
    assert alerts.telegram(token) == f"tgram://{token}/4242" and calls == [f"https://api.telegram.org/bot{token}/getUpdates"]
    monkeypatch.setattr(sources, "get_json", lambda url: {"result": []})
    with pytest.raises(ValueError, match="message first"):
        alerts.telegram(token)
    with pytest.raises(ValueError, match="bot token"):
        alerts.telegram("not a token")
    monkeypatch.setattr(sources, "get_json", lambda url: (_ for _ in ()).throw(RuntimeError("HTTP 401")))
    with pytest.raises(ValueError, match="didn't accept"):
        alerts.telegram(token)




def test_patch_and_ask_dedupe_lists(home, monkeypatch):
    from jobquill.app import settings_api
    c = client()
    assert c.patch("/api/preferences", json={"places": ["Munich", "munich ", "Berlin", "Munich"],
                                             "keywords": {"de": ["KI", "ki", "Daten"]}}).status_code == 200
    p = pf.read("preferences")
    assert p["places"] == ["Munich", "Berlin"] and p["keywords"] == {"de": ["KI", "Daten"]}
    out = settings_api.check_changes(settings_api.AskAnswer(reply="", changes=[
        {"key": "places", "value_json": '["Munich", "Berlin", "Munich", "Hamburg"]'}]), pf.Preferences.model_validate(p).model_dump(), {"places"})
    assert out["changes"][0]["new"] == ["Munich", "Berlin", "Hamburg"]


def test_a_connected_channel_isnt_added_twice(home):
    c = client()
    assert "existing" not in c.post("/api/alerts/channels", json={"url": "ntfy://jobquill-q7x2m9k4pz"}).json()
    r = c.post("/api/alerts/channels", json={"url": "ntfy://jobquill-q7x2m9k4pz/"}).json()
    assert r["existing"] is True and len(r["channels"]) == 1 and alerts.urls() == ["ntfy://jobquill-q7x2m9k4pz"]


def test_setup_step_is_kept(home):
    c = client()
    assert c.patch("/api/preferences", json={"setup_step": 4}).status_code == 200
    assert c.get("/api/state").json()["preferences"]["setup_step"] == 4


def test_extract_text_from_a_letter(home):
    c = client()
    r = c.post("/api/extract-text?name=letter.txt", content="Dear team, I built the scheduler.".encode())
    assert r.json() == {"text": "Dear team, I built the scheduler."}
    assert c.post("/api/extract-text?name=scan.pdf", content=b"not a pdf").status_code == 422


def test_openings_come_from_the_cv(home, monkeypatch):
    from jobquill.app import setup_api
    pf.write("profile", {"basics": {"name": "Ana", "headline": "Nurse"},
                         "experience": [{"id": "e1", "title": "Nurse", "org": "Charité", "bullets": [{"id": "b1", "text": "Cared for 12 patients"}]}]})
    seen = {}
    fake = setup_api.Openings(openings=[{"style": s, "text": f"{s} opening"} for s in ("Direct", "Story", "Warm")])
    monkeypatch.setattr(ai, "chosen", lambda *a, **k: None)
    monkeypatch.setattr(ai, "answer", lambda prompt, model, agent, timeout=600: seen.setdefault("prompt", prompt) and fake)
    r = client().post("/api/suggest/openings", json={"tone": "friendly", "length": "short"})
    assert [o["style"] for o in r.json()["openings"]] == ["Direct", "Story", "Warm"]
    assert "Cared for 12 patients" in seen["prompt"] and "friendly" in seen["prompt"] and "<untrusted>" in seen["prompt"]


def test_setup_missing_fresh_and_configured(home, monkeypatch):
    from jobquill.core import prompts
    fresh, later, prefs = ["details", "style", "design", "email", "alerts", "search", "documents"], ["style"], {}
    c = client()
    assert c.get("/api/setup/missing").json()["missing"] == fresh
    pf.write("preferences", {"design": "modern", "search_time": "09:00", "setup_hidden": ["email"], "setup_done": True, **prefs})
    pf.write("style", prompts.load("default_style"))  # the default style isn't their own yet
    pf.write("answers", {"Notice period": "1 month"})
    alerts.set_urls(["ntfy://jobquill-q7x2m9k4pz"])
    assert c.post("/api/upload-folder/files?name=..%2FBachelor.pdf", content=b"%PDF").json()["documents"] == ["Bachelor.pdf"]
    assert c.post("/api/upload-folder/files?name=run.exe", content=b"MZ").status_code == 422
    assert c.get("/api/setup/missing").json()["missing"] == later
    pf.write("style", "# My style")
    pf.write("cv_rules", "- Lead with robotics")
    assert c.get("/api/setup/missing").json()["missing"] == []


def test_ask_splits_a_mixed_request_and_saves_nothing(home, monkeypatch):
    """One sentence -> settings, CV instructions, letter instructions (banned list kept), application details and a CV
    request handed to the merge; nothing is written before Apply."""
    from jobquill.app import settings_api, setup_api
    pf.write("preferences", {"places": ["Berlin"]})
    pf.write("cv_rules", "# CV instructions\n- Lead with robotics")
    pf.write("style", "# Letter\n- Formal\n\n## Banned phrases\n- passionate\n")
    pf.write("answers", {"Driving licence": "no"})
    answer = settings_api.AskAnswer(
        reply="Done.", changes=[{"key": "start", "value_json": '"2027-01-01"'}],
        cv_rules="# CV instructions\n- Lead with robotics\n- Keep the thesis line",
        letter_rules="# Letter\n- Less formal", answers=[{"question": "Driving licence", "answer": "yes"}],
        cv_request="Add a skill: Docker")
    monkeypatch.setattr(ai, "chosen", lambda *a, **k: None)
    monkeypatch.setattr(ai, "answer", lambda prompt, model, agent, timeout=600: answer)
    merged = []

    async def merge(kind, info, profile):
        merged.append(info)
        return {"profile": {}, "changes": ["Added a skill: Docker"]}
    monkeypatch.setattr(setup_api, "merge", merge)
    r = client().post("/api/settings/ask", json={"text": "Start 1 Jan, keep thesis line, less formal, licence yes, add Docker"}).json()
    assert [c["key"] for c in r["changes"]] == ["start"]
    assert r["cv_rules"]["new"].endswith("- Keep the thesis line")
    assert "## Banned phrases" in r["letter_rules"]["new"] and "- passionate" in r["letter_rules"]["new"]
    assert r["answers"] == [{"question": "Driving licence", "old": "no", "new": "yes"}]
    assert merged == ["Add a skill: Docker"] and r["cv"]["changes"] == ["Added a skill: Docker"]
    assert pf.read("cv_rules").endswith("robotics") and pf.read("answers") == {"Driving licence": "no"}  # nothing saved yet
    answer.cv_request, merged[:] = "", []
    assert client().post("/api/settings/ask", json={"text": "x"}).json()["cv"] is None and merged == []


def test_applied_jobs_never_show_or_get_a_second_application(home):
    """The same posting, or the same job on another site (same company, near-identical title), counts as applied:
    the Board hides it and no draft starts. Saved-for-later applications don't count."""
    from jobquill.core import apply, posting
    from jobquill.core.data import db
    k1 = posting.save({"url": "https://a.example/1", "title": "ML Engineer (m/w/d)", "company": "Acme GmbH", "description": "d"})
    k2 = posting.save({"url": "https://b.example/2", "title": "ML Engineer", "company": "ACME", "description": "d"})
    k3 = posting.save({"url": "https://c.example/3", "title": "Data Analyst", "company": "Acme GmbH", "description": "d"})
    k4 = posting.save({"url": "https://d.example/4", "title": "Robotics Engineer", "company": "Beta AG", "description": "d"})
    k5 = posting.save({"url": "https://e.example/5", "title": "ML Innovation Engineer", "company": "Acme", "description": "d"})
    with db() as con:
        con.execute("insert into applications(job_key, company, role, date_applied, status) values (?, 'Acme GmbH', 'ML Engineer', '2026-10-01', 'applied')", (k1,))
        con.execute("insert into applications(job_key, company, role, date_applied, status) values ('x', 'Beta AG', 'Robotics Engineer', '2026-10-02', 'pending')")
        con.execute("update jobs set status='applied' where key=?", (k1,))
    keys = [r["key"] for r in client().get("/api/board?show=open").json()]
    assert k2 not in keys and k3 in keys and k4 in keys and k5 in keys  # twin hidden; other roles and saved-for-later stay
    assert client().get(f"/api/apply/{k2}").json()["applied"]["date_applied"] == "2026-10-01"
    try:
        apply.draft(k2)
        raise AssertionError("no second application")
    except ai.AIError as e:
        assert "already applied" in str(e)


def test_instruction_added_while_applying(home):
    pf.write("style", "# Letter\n- Formal\n\n## Banned phrases\n- passionate\n")
    c = client()
    assert c.post("/api/instructions", json={"kind": "style", "line": "Mention my start date early"}).status_code == 200
    assert pf.read("style") == "# Letter\n- Formal\n- Mention my start date early\n\n## Banned phrases\n- passionate\n"
    assert c.post("/api/instructions", json={"kind": "cv_rules", "line": "Keep the thesis line"}).status_code == 200
    assert pf.read("cv_rules").rstrip().endswith("- Keep the thesis line") and "## Ground rules" in pf.read("cv_rules")  # defaults first
    assert c.post("/api/instructions", json={"kind": "profile", "line": "x"}).status_code == 422




def test_writing_box_builds_on_the_unsaved_editors_and_touches_writing_only(home, monkeypatch):
    """Profile › Writing's AI box: the editors' unsaved text goes in, only writing settings and the two instructions come
    out (a language wish becomes doc_language); application details and CV facts are never touched from there."""
    from jobquill.app import settings_api
    pf.write("preferences", {"places": ["Berlin"]})
    seen = {}
    answer = settings_api.AskAnswer(reply="English from now on.", changes=[{"key": "doc_language", "value_json": '"en"'},
                                                                         {"key": "places", "value_json": '["Munich"]'}],
                                    cv_rules="- typed, not saved\n- Projects first", letter_rules="",
                                    answers=[{"question": "Driving licence", "answer": "yes"}], cv_request="Add Docker")
    monkeypatch.setattr(ai, "chosen", lambda *a, **k: None)
    monkeypatch.setattr(ai, "answer", lambda prompt, model, agent, timeout=600: seen.setdefault("prompt", prompt) and answer)
    r = client().post("/api/settings/ask", json={"text": "in English, projects first", "areas": "writing",
                                                 "current": {"cv_rules": "- typed, not saved", "style": "- my letter rule"}}).json()
    assert "- typed, not saved" in seen["prompt"] and "- my letter rule" in seen["prompt"] and '"places"' not in seen["prompt"]
    assert [c["key"] for c in r["changes"]] == ["doc_language"] and [s["key"] for s in r["skipped"]] == ["places"]
    assert r["cv_rules"]["new"].endswith("Projects first") and r["answers"] == [] and r["cv"] is None


def test_board_notes_applied_count_and_missed_postings(home, monkeypatch):
    from jobquill.core import sites
    monkeypatch.setattr(sites, "learn", lambda *a, **k: None)  # no network
    c = client()
    text = "Acme GmbH is hiring a Robotics Engineer in Munich. " + "You build robot software in Python and C++. " * 12
    key = c.post("/api/jobs/intake", json={"url": "https://acme.example/jobs/1", "text": text}).json()["key"]
    with db() as con:
        con.execute("update jobs set company='Acme GmbH', status='new' where key=?", (key,))
        con.executemany("insert into applications(company, status) values (?, 'applied')", [("Acme GmbH",), ("acme gmbh",)])
        assert json.loads(con.execute("select data from jobs where key=?", (key,)).fetchone()[0])["missed"]
    c.post("/api/jobs/intake", json={"url": "https://acme.example/jobs/1", "text": text})  # known now: not missed again
    assert c.put(f"/api/jobs/{key}/note", json={"note": " ask Anna about the team "}).json() == {"ok": True}
    row = next(r for r in c.get("/api/board?show=all").json() if r["key"] == key)
    assert row["note"] == "ask Anna about the team" and row["applied_before"] == 2


def test_asked_missing_counts_keywords_the_cv_lacks(home):
    from jobquill.core import check
    from jobquill.core.data import home as data_home
    master = pf.folder() / pf.FILES["profile"]
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_text("skills: [Python, ROS 2]", encoding="utf-8")
    for i, kws in enumerate([["Python", "Kubernetes", "Rust"], ["Kubernetes", "ROS 2"]]):
        d = data_home() / "applications" / f"app{i}"
        d.mkdir(parents=True)
        (d / "fit.json").write_text(json.dumps({"keywords": kws}), encoding="utf-8")
    (data_home() / "applications" / "old").mkdir()
    (data_home() / "applications" / "old" / "job.yaml").write_text("keywords: [Rust, Kubernetes]\n", encoding="utf-8")
    assert check.asked_missing() == [("kubernetes", 3), ("rust", 2)]


def test_source_that_finds_nothing_three_times_sends_one_alert(home, monkeypatch):
    from jobquill.core import search
    sent = []
    pf.write("preferences", {**pf.Preferences().model_dump(), "targets": [{"code": "DE"}], "titles": ["Robotics Engineer"], "remote": "onsite"})
    monkeypatch.setattr(search, "pack_sources", lambda cc: ({}, {}, {"emptyfeed"}))
    monkeypatch.setattr(sources, "global_boards", lambda cc: {"emptyfeed": lambda q, ctx: []})
    monkeypatch.setattr(alerts, "send", lambda event, title, body, **k: sent.append((event, title)))
    for name in ("seed", "probe", "learn_all"):
        monkeypatch.setattr(search.sites, name, lambda *a, **k: 0)
    monkeypatch.setattr(search.sites, "active", lambda: [])
    monkeypatch.setattr(search, "enrich", lambda *a, **k: None)
    monkeypatch.setattr(search, "after_search", lambda *a, **k: None)
    for _ in range(4):
        search.run()
    assert search.health()["emptyfeed"]["streak"] == 4
    assert [e for e, _ in sent] == ["sources"] and "emptyfeed" in sent[0][1]  # once, on the third empty run
