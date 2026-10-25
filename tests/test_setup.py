import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from jobquill.core import cvimport
from jobquill.core import profile as pf


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBQUILL_HOME", str(tmp_path))
    return tmp_path


def test_save_keeps_comments_and_validates(home):
    (pf.folder() / "master_cv.yaml").write_text(
        "basics:\n  name: Ana  # from passport\nexperience:\n  - id: e1  # first job\n    title: Nurse\n    bullets: []\n",
        encoding="utf-8")
    p = pf.read("profile")
    p["experience"][0]["title"] = "Senior Nurse"
    pf.write("profile", p)
    text = (pf.folder() / "master_cv.yaml").read_text(encoding="utf-8")
    assert "# from passport" in text and "# first job" in text and "Senior Nurse" in text
    assert pf.profile().experience[0].title == "Senior Nurse"
    with pytest.raises(ValueError):
        pf.write("profile", {"basics": {"name": "x"}, "nonsense": 1})


def test_parallel_reads_dont_mix_files(home):
    """The app serves requests in parallel; a shared YAML reader once returned preferences as the profile."""
    from concurrent.futures import ThreadPoolExecutor
    pf.write("profile", {"basics": {"name": "Ana"}})
    pf.write("preferences", {"home_country": "ES"})
    kinds = ["profile", "preferences"] * 40
    with ThreadPoolExecutor(16) as ex:
        out = list(ex.map(pf.read, kinds))
    assert all(("basics" in o) == (k == "profile") for k, o in zip(kinds, out))


def test_linkedin_export():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Profile.csv", "First Name,Last Name,Headline,Summary,Geo Location\nAna,Lopez,Nurse,Caring nurse,\"Madrid, Spain\"\n")
        z.writestr("Positions.csv", "Company Name,Title,Description,Location,Started On,Finished On\n"
                                    "Hospital X,Staff Nurse,\"Cared for 8 patients\nMentored students\",Madrid,Oct 2020,\n")
        z.writestr("Skills.csv", "Name\nTriage\nIV therapy\n")
    p = pf.Profile.model_validate(cvimport.from_linkedin(buf.getvalue()))
    e = p.experience[0]
    assert p.basics.name == "Ana Lopez" and e.start == "2020-10" and e.end == "present"
    assert [b.text for b in e.bullets] == ["Cared for 8 patients", "Mentored students"]
    assert p.skills == {"Skills": ["Triage", "IV therapy"]}


def test_json_resume_round_trip():
    src = {"basics": {"name": "Bo", "email": "b@x.org", "profiles": [{"network": "GitHub", "url": "github.com/bo"}]},
           "work": [{"name": "Acme", "position": "Dev", "startDate": "2021-02-01", "highlights": ["Built **X**"]}],
           "education": [{"institution": "TU", "startDate": 2016, "endDate": 2020}],  # bare years as numbers
           "skills": [{"name": "Web", "keywords": ["JS"]}]}
    p = pf.Profile.model_validate(cvimport.from_json_resume(json.dumps(src).encode()))
    assert p.experience[0].start == "2021-02" and p.basics.links == {"github": "github.com/bo"} and p.education[0].end == "2020"
    back = cvimport.to_json_resume(p.model_dump())
    assert back["work"][0]["highlights"] == ["Built X"] and back["skills"] == [{"name": "Web", "keywords": ["JS"]}]


def test_setup_api(home):
    from jobquill.app import server
    c = TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})
    st = c.get("/api/state").json()
    assert st["preferences"]["setup_done"] is False and st["countries"]["DE"] == "Germany"
    assert "alex-engineer-de" in st["examples"]
    assert c.post("/api/example/alex-engineer-de").status_code == 200
    st = c.get("/api/state").json()
    assert st["profile"]["basics"]["name"] == "Alex Example" and st["preferences"]["setup_done"] is True
    assert c.put("/api/data/style", json={"text": "# Style"}).status_code == 200
    assert c.put("/api/data/preferences", json={"bogus": 1}).status_code == 422
    assert c.get("/api/export/json-resume").json()["basics"]["name"] == "Alex Example"
    r = c.post("/api/import?name=cv.txt", content=b"too short")
    assert r.status_code == 422
    assert c.get("/api/backup").content[:2] == b"PK"
    assert c.get("/tabs/profile.html").status_code == 200


def test_ai_update_never_drops_existing_content():
    old = {"basics": {"name": "Ana", "email": "a@x.org", "links": {"github": "gh/ana"}},
           "experience": [{"id": "e1", "title": "Nurse", "bullets": [{"id": "e1_b1", "text": "Cared for 8 patients"}]},
                          {"id": "e2", "title": "Carer", "bullets": []}],
           "skills": {"Clinical": ["Triage", "IV"]}, "languages": [{"name": "English", "level": "Native"}]}
    new = {"basics": {"name": "Ana", "email": ""},
           "experience": [{"id": "e1", "title": "Senior Nurse", "bullets": [{"id": "e1_b2", "text": "Leads a team of 6"}]}],
           "skills": {"Clinical": ["Triage"], "Software": ["Epic"]}, "languages": []}
    out = pf.keep_existing(old, new)
    assert [e["id"] for e in out["experience"]] == ["e1", "e2"] and out["experience"][0]["title"] == "Senior Nurse"
    assert [b["id"] for b in out["experience"][0]["bullets"]] == ["e1_b2", "e1_b1"]
    assert out["skills"] == {"Clinical": ["Triage", "IV"], "Software": ["Epic"]}
    assert out["languages"] == [{"name": "English", "level": "Native"}]
    assert out["basics"]["email"] == "a@x.org" and out["basics"]["links"] == {"github": "gh/ana"}


def test_applications_include_form_answers(home):
    from jobquill.app import server
    from jobquill.core.data import db
    (home / "applications" / "2026-10-01_Acme_Dev").mkdir(parents=True)
    (home / "applications" / "2026-10-01_Acme_Dev" / "form_answers.md").write_text("# Form answers\n- Salary: 60000\n" + "x" * 5000,
                                                                                    encoding="utf-8")
    with db() as con:
        con.execute("insert into applications(company, role, status, folder, data) values ('Acme', 'Dev', 'applied', '2026-10-01_Acme_Dev', '{}')")
        con.execute("insert into applications(company, role, status, folder, data) values ('Beta', 'QA', 'pending', null, '{}')")
    c = TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})
    rows = {r["company"]: r for r in c.get("/api/applications").json()["rows"]}
    assert rows["Acme"]["form_answers"].startswith("# Form answers\n- Salary: 60000") and len(rows["Acme"]["form_answers"]) == 4096
    assert rows["Beta"]["form_answers"] == "" and rows["Beta"]["files"] == []


def client():
    from jobquill.app import server
    return TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})


def test_suggested_answers_for_the_example_person_and_saved_answers_win(home):
    c = client()
    assert c.post("/api/example/alex-engineer-de").status_code == 200
    s = c.get("/api/answers/suggested").json()
    assert s["Highest degree"] == "B.Sc. Computer Science"
    assert int(s["Years of experience"]) >= 6
    assert (s["Salary expectation"], s["Earliest start date"], s["Notice period"]) == ("65000-75000 EUR", "in 3 months", "3 months")
    assert s["Authorised to work in Germany?"] == "Yes" and s["Need visa sponsorship for Germany?"] == "No"
    assert s["English level"] == "C1" and s["German level"] == "Native"
    assert "Nationality" not in s and "Current university" not in s  # empty in the CV / no studies in progress: nothing guessed
    pf.write("answers", {"Notice period": "", "Old question": "x"})
    assert c.patch("/api/answers", json={"Highest degree": "Bachelor", "Old question": " "}).status_code == 200
    assert pf.read("answers") == {"Notice period": "", "Highest degree": "Bachelor"}  # merged; an empty answer removes
    s = c.get("/api/answers/suggested").json()
    assert "Highest degree" not in s and s["Notice period"] == "3 months"  # a saved answer wins, an empty one doesn't count


def test_suggested_answers_studies_in_progress():
    p = {"education": [{"degree": "M.Sc. Robotics", "school": "TU Example", "start": "2025", "end": "2099-09"},
                       {"degree": "B.Eng. Mechatronics", "school": "HS Example", "end": "2024"}]}
    prefs = {"targets": [{"code": "DE", "permit": "", "needs_sponsorship": True}, {"code": "AT", "permit": "Not sure"}]}
    s = pf.suggested_answers(p, prefs)
    assert s["Highest degree"] == "B.Eng. Mechatronics"  # the finished one
    assert (s["Current university"], s["Current degree programme"], s["Expected graduation"]) == ("TU Example", "M.Sc. Robotics", "2099-09")
    assert s["Authorised to work in Germany?"] == "No" and s["Need visa sponsorship for Germany?"] == "Yes"
    assert not any("Austria" in k for k in s)


def test_patch_preferences_merges_and_keeps_server_fields(home, monkeypatch):
    from jobquill.app import settings_api
    calls = []
    monkeypatch.setattr(settings_api.scheduler, "disable", lambda job=None: calls.append(f"off {job}"))
    monkeypatch.setattr(settings_api.scheduler, "ensure", lambda search_time=None, mail_on=None: calls.append(search_time))
    pf.write("preferences", {"home_country": "DE", "salary": "60k",
                             "weights": {"skills": 2.0, "location": 0.5, "seniority": 1.0, "language": 1.0}})
    c = client()
    assert c.patch("/api/preferences", json={"weights": {"skills": 1.5}, "notice": "1 month"}).status_code == 200
    p = pf.read("preferences")
    assert p["weights"] == {"skills": 1.5, "location": 0.5, "seniority": 1.0, "language": 1.0}  # the rest survives
    assert (p["home_country"], p["salary"], p["notice"]) == ("DE", "60k", "1 month") and calls == []
    # a new time: the search task is made again at it (the email check is left as it is)
    assert c.patch("/api/preferences", json={"search_time": "08:30"}).status_code == 200 and calls == ["off search", "08:30"]
    assert c.patch("/api/preferences", json={"search_time": "08:30"}).status_code == 200 and calls == ["off search", "08:30"]  # unchanged
    assert c.patch("/api/preferences", json={"radius_km": "far"}).status_code == 422
    assert pf.read("preferences")["radius_km"] == 50
