import json

import pytest

from careerpilot.core import score, search, sources
from careerpilot.countries.de import sources as de

PREFS = {"titles": ["Staff Nurse"], "keywords": {"en": ["Registered Nurse"], "de": ["Pflegefachkraft"]}, "job_types": ["full_time"],
         "places": ["Leeds"], "radius_km": 30, "remote": "onsite"}
PROFILE = {"skills": {"Clinical": ["Patient assessment", "NEWS2"]}, "languages": [{"name": "English", "level": "Native"}],
           "experience": [{"start": "2021-06", "end": "present",
                           "bullets": [{"tags": ["patient-care"]}, {"tags": ["patient-care", "news2"]}]}]}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CAREERPILOT_HOME", str(tmp_path))
    return tmp_path


def test_ats_ref_and_liveness():
    assert sources.ats_ref("https://boards.greenhouse.io/acme/jobs/1") == ("greenhouse", "acme")
    assert sources.ats_ref("https://acme.wd3.myworkdayjobs.com/en-US/External/job/x") == ("workday", "https://acme.wd3.myworkdayjobs.com/External")
    assert sources.ats_ref("https://apply.workable.com/acme/j/123") == ("workable", "acme")
    assert sources.classify(404, "")[0] == "expired"
    assert sources.classify(200, "<p>This position has been filled.</p>" + "x " * 200)[0] == "expired"
    assert sources.classify(200, "<p>Apply until the role has been filled.</p> apply now " + "x " * 200)[0] == "active"
    assert sources.clean_title("Krankenpfleger (m/w/d)") == "Krankenpfleger"


def test_matcher():
    m = score.Matcher(PREFS, PROFILE)
    assert 5 < m.years < 30 and m.speaks == {"en"}
    good, off = m.assess({"title": "Staff Nurse - Acute Medicine", "company": "NHS", "location": "Leeds, UK", "country": "GB"},
                         "We need a staff nurse for patient assessment and NEWS2 observations. Closing date: 15/11/2099. " * 5)
    assert not off and good["score"] > 20 and good["deadline"] == "2099-11-15" and good["language"] == "en"
    _, off = m.assess({"title": "Software Engineer", "company": "X", "location": "London"}, "Python " * 50)
    assert off == "junk"
    intern, off = m.assess({"title": "Praktikum Pflege", "company": "Klinik", "location": "Berlin", "country": "DE"}, "")
    assert intern["type"] == "internship" and off == "type"
    de_post, _ = m.assess({"title": "Pflegefachkraft", "company": "Klinik", "location": "München", "country": "DE"},
                          "Wir suchen eine Pflegefachkraft mit sehr guten Deutschkenntnissen für unsere Station und das Team. " * 4)
    assert "posting in de" in de_post["why"] and "far away" in de_post["why"]


def test_store_dedupes_across_boards(home):
    m = score.Matcher(PREFS, PROFILE)
    a = sources.job("Staff Nurse (Band 6)", "Example NHS Trust", "Leeds", "https://a/1", "indeed", desc="We are hiring a staff nurse for our acute ward. You will care for patients and work with the team. " * 6)
    b = sources.job("Staff Nurse - Band 6", "Example NHS Trust Leeds", "Leeds", "https://b/2", "linkedin")
    c = sources.job("Staff Nurse (Band 6)", "Other Hospital", "Leeds", "https://c/3", "indeed", desc="We are hiring a staff nurse for our acute ward. You will care for patients and work with the team. " * 6)
    assert search.store([a, b, c], m) == (2, 0)
    assert search.store([a], m) == (0, 0)


def test_stepstone_rows():
    page = 'x"items":[{"label":"filter"}] y "items":[{"title":"Nurse","datePosted":"2026-10-20T08:00","url":"/a"}] z'
    assert de.stepstone_rows(page)[0]["title"] == "Nurse"


def test_experience_years_overlap():
    p = {"experience": [{"start": "2020-01", "end": "2021-12"}, {"start": "2021-06", "end": "2022-12"}]}
    assert score.experience_years(p) == 3.0


def test_store_keeps_junk_and_expires(home):
    from datetime import date, timedelta
    from careerpilot.core.data import db
    m, gone = score.Matcher(PREFS, PROFILE), (date.today() - timedelta(days=5)).strftime("%d/%m/%Y")
    junk = sources.job("Software Engineer", "X", "London", "https://x/1", "indeed", desc="Python " * 80)
    old = sources.job("Staff Nurse", "NHS", "Leeds", "https://x/2", "indeed",
                      desc=("We need a staff nurse for patient assessment and NEWS2. Closing date: " + gone + ". ") * 5)
    assert search.store([junk, old], m) == (0, 1)  # expired counts as neither
    with db() as con:
        got = {r["url"]: (r["status"], r["why"][:6]) for r in con.execute("select url, status, why from jobs")}
    assert got["https://x/1"] == ("filtered", "[junk]") and got["https://x/2"][0] == "expired"


def test_board_reason_link_and_expired_migration(home):
    from careerpilot.app import jobs_api
    from careerpilot.core.data import db
    rows = [("a", "filtered", "[weak] low · ⚠ far away", {}), ("b", "filtered", "[type] internship", {}),
            ("c", "filtered", "[excluded] hidden", {}), ("d", "filtered", "[junk] x", {}), ("e", "filtered", "imported", {}),
            ("f", "new", "good match", {"site_url": "https://acme.com/jobs/1"}), ("g", "filtered", "[expired] late", {})]
    with db() as con:
        con.executemany("insert into jobs(key, title, url, status, why, data) values (?, 't', ?, ?, ?, ?)",
                        [(k, f"https://board/{k}", s, w, json.dumps(d)) for k, s, w, d in rows])
        con.execute("pragma user_version=2")
    db().close()  # re-runs the [expired] migration
    got = {r["key"]: r for r in jobs_api.board("all")}
    assert [got[k]["reason"] for k in "abcdef"] == ["weak", "type", "excluded", "offtopic", "offtopic", ""]
    assert got["a"]["why"] == "low · ⚠ far away" and got["e"]["why"] == "imported"
    assert got["f"]["link"] == "https://acme.com/jobs/1" and got["a"]["link"] == "https://board/a"
    assert got["g"]["status"] == "expired" and "g" not in {r["key"] for r in jobs_api.board("filtered")}


def test_kept_rows_are_not_refiltered(home, monkeypatch):
    from fastapi.testclient import TestClient
    from careerpilot.app import server
    from careerpilot.core.data import db
    with db() as con:
        con.executemany("insert into jobs(key, title, company, location, url, status, first_seen, data) values (?, 'Software Engineer',"
                        " 'X', 'London', ?, 'filtered', '2020-01-01', '{}')", [("k", "https://x/k"), ("n", "https://x/n")])
    c = TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})
    c.put("/api/jobs/k/status", json={"status": "new"})
    with db() as con:
        con.execute("update jobs set status='new' where key='n'")  # same row, not moved by the person
    page = "<html><body><p>" + "We build Python services for our customers and need a software engineer. " * 12 + "</p></body></html>"
    monkeypatch.setattr(sources, "http", lambda url, timeout=20: (200, page, url))
    search.enrich(score.Matcher(PREFS, PROFILE))
    with db() as con:
        got = {r["key"]: (r["status"], json.loads(r["data"]).get("kept")) for r in con.execute("select key, status, data from jobs")}
    assert got == {"k": ("new", True), "n": ("filtered", None)}


ATS_URLS = {"https://boards.greenhouse.io/acme/jobs/1": ("greenhouse", "acme"),
            "https://job-boards.eu.greenhouse.io/acme2/jobs/2": ("greenhouse", "acme2"),
            "https://jobs.lever.co/acme/abc-123": ("lever", "acme"), "https://jobs.ashbyhq.com/acme/9f": ("ashby", "acme"),
            "https://acme.jobs.personio.de/job/12": ("personio", "acme"), "https://jobs.smartrecruiters.com/Acme/7444": ("smartrecruiters", "Acme"),
            "https://acme.recruitee.com/o/engineer": ("recruitee", "acme"), "https://apply.workable.com/acme/j/123": ("workable", "acme"),
            "https://acme.teamtailor.com/jobs/1-x": ("teamtailor", "acme.teamtailor.com"), "https://acme.breezy.hr/p/1": ("breezy", "acme"),
            "https://join.com/companies/acme/123-x": ("join", "acme"), "https://acme.bamboohr.com/careers/5": ("bamboohr", "acme"),
            "https://acme.wd3.myworkdayjobs.com/en-US/External/job/x": ("workday", "https://acme.wd3.myworkdayjobs.com/External")}


def test_sites_learn_every_ats_form(home, monkeypatch):
    from careerpilot.core import sites
    from careerpilot.core.data import db
    monkeypatch.setattr(sources, "http", lambda *a, **k: pytest.fail("a posting link must not be fetched"))
    for url, want in ATS_URLS.items():
        assert sites.learn(url, "Acme", "posting") == want
    assert sites.learn("https://www.linkedin.com/jobs/view/1", "Acme", "posting") is None
    with db() as con:
        got = {(r["kind"], r["ref"]) for r in con.execute("select * from sites")}
    assert got == set(ATS_URLS.values())


def test_sites_successfactors_and_own_page(home, monkeypatch):
    from careerpilot.core import sites
    jobs = "".join(f'<a href="/jobs/{i}">{t} (m/w/d)</a>' for i, t in enumerate(["Data Engineer", "Software Engineer", "Test Engineer"]))
    pages = {"https://jobs.acme.com/job/Munich-Data-Engineer/123": '<script src="https://rmkcdn.successfactors.com/x.js"></script>',
             "https://acme.example/careers/jobs/42": "<p>Apply</p>", "https://acme.example/careers/jobs/": jobs,
             "https://jobboard.example/search/": jobs}
    monkeypatch.setattr(sources, "http", lambda url, *a, **k: (200, pages.get(url, ""), url))
    assert sites.learn("https://jobs.acme.com/job/Munich-Data-Engineer/123", "Acme", "applying") == ("successfactors", "https://jobs.acme.com")
    assert sites.learn("https://acme.example/careers/jobs/42", "Acme", "applying", "Data Engineer") == ("page", "https://acme.example/careers/jobs/")
    assert sites.learn("https://acme.example/careers/jobs/42", "Acme", "applying", "Head Chef") is None  # no title match
    assert sites.learn("https://acme.example/careers/jobs/42", "Acme", "posting") is None  # weak origin: no fetch
    assert sites.learn("https://jobboard.example/search/1", "Acme", "apply link", "Data Engineer") is None  # not Acme's domain


def test_sites_probe_needs_a_title_match(home, monkeypatch):
    from careerpilot.core import sites
    from careerpilot.core.data import db
    with db() as con:
        con.executemany("insert into jobs(key, title, company, url, source, status, score) values (?,?,?,?, 'indeed', 'new', 50)",
                        [("a", "Data Engineer (m/w/d)", "Acme GmbH", "https://x/a"), ("b", "Staff Nurse", "Beta Care AG", "https://x/b")])
    calls = []

    def fake(kind):
        def scan(name, ref, ctx):
            calls.append((kind, ref))
            if kind == "personio" and ref in ("acme", "betacare"):
                return [sources.job("Senior Data Engineer" if ref == "acme" else "Accountant", name, "", f"https://{ref}/1", "career site")]
            raise RuntimeError("HTTP 404")
        return scan
    for kind in sites.PROBE:
        monkeypatch.setitem(sources.SCANNERS, kind, fake(kind))
    assert sites.probe() == 1
    with db() as con:
        assert [tuple(r) for r in con.execute("select kind, ref, company, origin from sites")] == [("personio", "acme", "Acme GmbH", "name check")]
    assert ("lever", "beta-care") in calls and set(search.load_state()["probed"]) == {"acme gmbh", "beta care ag"}
    calls.clear()
    assert sites.probe() == 0 and not calls  # tried in the last 30 days


def test_sites_pause_after_3_fails_retry_after_7_days(home):
    from datetime import date, timedelta
    from careerpilot.core import sites
    from careerpilot.core.data import db
    sites.add("greenhouse", "acme", "Acme", "posting")
    sites.add("lever", "beta", "Beta", "posting")
    for _ in range(3):
        sites.record("greenhouse", "acme", 0, "HTTP 500")
    sites.record("lever", "beta", 4)
    assert sites.active() == []  # both scanned today
    ago = lambda n: (date.today() - timedelta(days=n)).isoformat()
    with db() as con:
        con.execute("update sites set last_scan=?", (ago(3),))
    assert [s["ref"] for s in sites.active()] == ["beta"]  # acme paused
    with db() as con:
        con.execute("update sites set last_scan=? where ref='acme'", (ago(7),))
    assert {s["ref"] for s in sites.active()} == {"acme", "beta"}  # retried after a week
    with db() as con:
        con.execute("update sites set off=1 where ref='beta'")
    sites.add("lever", "Beta", "Beta", "you")  # learned again: stays off
    assert [s["ref"] for s in sites.active()] == ["acme"]


def test_site_url_same_key_earlier_in_run_and_twin(home):
    from careerpilot.core.data import db
    m = score.Matcher(PREFS, PROFILE)
    site = lambda title, company, n: sources.job(title, company, "Leeds", f"https://acme.career/{n}", "career site")
    search.store([sources.job("Staff Nurse", "Example NHS Trust GmbH", "Leeds", "https://www.linkedin.com/jobs/view/1", "linkedin")], m)
    search.store([sources.job("Ward Nurse", "Example NHS Trust", "Leeds", "https://www.indeed.com/2", "indeed"),
                  site("Ward Nurse (m/w/d)", "Example NHS Trust", 2),  # same key, earlier in this run
                  site("Staff Nurse", "Example NHS Trust", 1),  # same key, already stored
                  sources.job("Night Nurse", "Leeds Teaching Hospitals NHS", "Leeds", "https://www.indeed.com/3", "indeed"),
                  site("Night Nurse", "Leeds Teaching Hospitals", 3)], m)  # twin: other key, near-identical company
    with db() as con:
        rows = {r["url"]: dict(r) for r in con.execute("select * from jobs")}
    site_url = lambda u: json.loads(rows[u]["data"] or "{}").get("site_url")
    assert site_url("https://www.linkedin.com/jobs/view/1") == "https://acme.career/1"
    assert site_url("https://www.indeed.com/2") == "https://acme.career/2"
    assert site_url("https://www.indeed.com/3") == "https://acme.career/3"
    assert len(rows) == 3  # the career-site copies aren't stored twice


def test_sites_seed_runs_once_and_backfills(home):
    from careerpilot.core import profile as pf
    from careerpilot.core import sites
    from careerpilot.core.data import db
    pf.write("companies", [{"name": "Mercedes-Benz", "kind": "beesite", "ref": "https://mb.app.beesite.de"}])
    with db() as con:
        con.executemany("insert into jobs(key, title, company, url, why, data) values (?, 't', ?, ?, ?, ?)",
                        [("a", "Acme", "https://boards.greenhouse.io/acme/jobs/1", "good", "{}"),
                         ("b", "Junk Co", "https://jobs.lever.co/junkco/1", "[junk] off-topic", "{}"),
                         ("c", "Beta", "https://www.arbeitsagentur.de/x", "ok", json.dumps({"apply_url": "https://beta.jobs.personio.de/job/1"}))])
        con.execute("insert into applications(company, data) values ('Gamma', ?)", (json.dumps({"url": "https://jobs.ashbyhq.com/gamma/1"}),))
    sites.seed()
    with db() as con:
        got = {(r["kind"], r["ref"], r["origin"]) for r in con.execute("select * from sites")}
        assert got == {("beesite", "https://mb.app.beesite.de", "you"), ("greenhouse", "acme", "posting"),
                       ("personio", "beta", "posting"), ("ashby", "gamma", "posting")}
        con.execute("delete from sites")
    sites.seed()
    with db() as con:
        assert not con.execute("select count(*) from sites").fetchone()[0]


def test_page_with_link_pattern_keeps_postings_sharing_a_title(monkeypatch):
    """A "<url>#<regex>" page ref keeps every link whose href matches, also when titles repeat; navigation is dropped."""
    from careerpilot.core import sources
    body = ('<a href="/jobs/1">Wissenschaftliche*r Mitarbeiter*in (m/w/d)</a><a href="/jobs/2">Wissenschaftliche*r Mitarbeiter*in (m/w/d)</a>'
            '<a href="/forschung">Wissenschaftliche Weiterbildung und Forschung</a>')
    monkeypatch.setattr(sources, "http", lambda url: (200, body, "https://uni.example/list"))
    got = sources.page("Uni", r"https://uni.example/list#re:/jobs/\d+", {})
    assert [g["url"] for g in got] == ["https://uni.example/jobs/1", "https://uni.example/jobs/2"]


def test_page_card_links_and_plain_fragments(monkeypatch):
    """Card links give title, employer and place; a normal #fragment in a career-page link is not a filter."""
    from careerpilot.core import sources
    card = ('<a href="/berlin/data-engineer-261021-1"><div><p>Data Engineer (m/w/d)</p></div>'
            '<p>Acme GmbH<span>·</span>Berlin, Deutschland</p><span>vor 2 Stunden</span></a>')
    monkeypatch.setattr(sources, "http", lambda url: (200, card + '<a href="/jobs/9">Software Engineer (m/w/d)</a>', "https://x.example/b/"))
    got = sources.page("Board", r"https://x.example/b/#re:/berlin/[a-z0-9-]+$", {})
    assert [(g["title"], g["company"], g["location"]) for g in got] == [("Data Engineer", "Acme GmbH", "Berlin, Deutschland")]
    plain = sources.page("Acme", "https://x.example/careers#open-positions", {})
    assert "Software Engineer" in [g["title"] for g in plain]


def test_ledger_counts_error_answers_as_failed(tmp_path):
    from careerpilot.core import ai
    ai.record("Tailor", "k", ai.Claude(), '{"is_error": true, "result": "overloaded", "modelUsage": {}}', 1.0)
    ai.record("Review", "k", ai.Claude(), '{"modelUsage": {"m": {"inputTokens": 5, "outputTokens": 2, "costUSD": 0.01}}}', 1.0)
    rows = ai.calls("k")
    assert [(r["step"], r["error"], r["input"]) for r in rows] == [("Tailor", "overloaded", 0), ("Review", "", 5)]


def test_review_runs_on_the_check_model():
    """CHECK_STEPS names answer models by class: renaming Review must not quietly move it to the expensive model."""
    from careerpilot.core import ai, apply
    assert apply.Review.__name__ in ai.CHECK_STEPS
    c = ai.Claude()
    c.level = "standard"
    assert "sonnet" in c.command({}, "Review") and "sonnet" not in c.command({}, "Tailor")


def test_browser_only_sites_connect_like_chrome(monkeypatch):
    """StepStone stalls any non-browser connection: its pages go through curl_cffi as Chrome, everything else stays urllib."""
    import curl_cffi.requests
    from careerpilot.core import sources
    calls = []
    monkeypatch.setattr(curl_cffi.requests, "request", lambda method, url, **kw: calls.append((method, url, kw["impersonate"])) or
                        type("R", (), {"status_code": 200, "text": "ok", "url": url})())
    assert sources.http("https://www.stepstone.de/stellenangebote--X--1-inline.html")[:2] == (200, "ok")
    assert calls == [("GET", "https://www.stepstone.de/stellenangebote--X--1-inline.html", "chrome")]
    assert sources.BROWSER_ONLY.match("https://stepstone.at/x") and not sources.BROWSER_ONLY.match("https://jobs.example/stepstone.de")


def test_only_web_links_and_only_the_internet(monkeypatch):
    """Links come from other people's pages: a javascript: link is never stored; file: links, this computer and the
    local network are never fetched."""
    import socket
    from careerpilot.core import sources
    assert sources.job("Nurse", "Acme", "", "javascript:alert(1)", "career site")["url"] == ""
    assert sources.job("Nurse", "Acme", "", "https://acme.example/jobs/1", "career site")["url"] == "https://acme.example/jobs/1"
    ips = {"acme.example": "93.184.215.14", "router.example": "192.168.1.1"}
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [(0, 0, 0, "", (ips.get(host, host), 0))])
    assert sources.public("https://acme.example/jobs/1")
    for url in ("file:///C:/Windows/win.ini", "http://127.0.0.1:8790/api/quit", "http://10.0.0.1/", "https://router.example/",
                "http://[::1]/"):
        assert not sources.public(url), url
    assert sources.http("file:///C:/Windows/win.ini") == (0, "", "file:///C:/Windows/win.ini")


def test_queries_follow_the_job_types():
    """Each chosen job type searches with its own words in front of the person's; every chosen type counts the same."""
    base, st = {"titles": ["Data Analyst"], "keywords": {"de": ["Datenanalyse"]}}, {"last_q": {}}
    q = lambda **p: search.queries({**base, **p}, "DE", "stepstone", st)
    student = q(job_types=["student"])
    assert {k for _, k in student} == {"student"} and "Werkstudent Data Analyst" in dict(student)
    assert "working student Datenanalyse" in dict(student) and "Data Analyst" not in dict(student)  # no plain searches
    thesis = dict(q(job_types=["thesis"], thesis_level="master"))
    assert "Masterarbeit Data Analyst" in thesis and "master thesis Data Analyst" in thesis
    assert not any("Bachelor" in w for w in thesis)
    both = q(job_types=["full_time", "internship"])
    assert both[:2] == [("Data Analyst", ""), ("Praktikum Data Analyst", "internship")] and ("Datenanalyse", "") in both
    assert not any("Pflichtpraktikum" in w for w, _ in both) and any("Pflichtpraktikum" in w for w, _ in
                                                                     q(job_types=["internship"], mandatory_internship=True))
    assert dict(q(job_types=["student"], titles=["Werkstudent Data"], keywords={}))  == {"Werkstudent Data": "student"}  # already typed
    aa = search.queries({**base, "job_types": ["student", "internship"]}, "DE", "arbeitsagentur", st)  # it filters types itself
    assert aa == [("Data Analyst", "student+internship"), ("Datenanalyse", "student+internship")]
    many = q(job_types=["full_time", "student", "internship", "thesis", "apprenticeship"], titles=[f"T{i}" for i in range(9)], keywords={})
    per = [sum(k == g for _, k in many) for g in ("", "student", "internship", "thesis", "apprenticeship")]
    assert len(many) == 18 and max(per) - min(per) <= 1  # three boards' worth, shared equally (not the first type first)
    two = q(job_types=["full_time", "student"], titles=[f"T{i}" for i in range(9)], keywords={})
    assert len(two) == 12 and sum(k == "student" for _, k in two) == 6
    nine = {"titles": [f"T{i}" for i in range(9)], "keywords": {}}
    lean = q(job_types=["full_time", "student"], side_types=["full_time"], **nine)
    assert len(lean) == 12 and sum(k == "student" for _, k in lean) == 8  # "Also" gets every other turn: half as many
    assert q(job_types=["full_time", "student"], side_types=["full_time", "student"], **nine) == two  # all "Also" = equal
    # a word typed for a job type that wasn't chosen is never searched (it would only fill Less relevant)
    assert dict(q(job_types=["full_time", "student"], titles=["Praktikum Robotik", "Robotik"], keywords={})).get("Praktikum Robotik") is None


def test_a_type_marked_also_pairs_its_words_with_the_first_titles_only():
    """112 of 288 searches were "graduate programme <every word>" for a type marked Also. Its type words now go with the
    first 5 titles only; a main type keeps every word (working student counts as much as full time)."""
    p = {"titles": [f"T{i}" for i in range(9)], "keywords": {"en": [f"K{i}" for i in range(6)]},
         "job_types": ["full_time", "student", "graduate"], "side_types": ["graduate"]}
    every = search.queries(p, "DE", "indeed", {"last_q": {}}, every=True)
    grad = [w for w, k in every if k == "graduate"]
    assert grad and all(any(w.endswith(f" T{i}") for i in range(5)) for w in grad)
    assert any(w.endswith(" K5") for w, k in every if k == "student")


def test_every_word_is_searched_within_what_the_board_looks_back_on():
    """LinkedIn came round to a word every ~32 days but looked back 7: now the look-back covers the gap (30 days at
    most); a long word list gets more searches per run on boards that allow it; LinkedIn keeps its pace."""
    from datetime import date, timedelta
    ago = (date.today() - timedelta(days=21)).isoformat()
    st = {"last_q": {"linkedin:DE": {"ml": ago}}}
    assert search.days_back(st, "linkedin", "DE", "ml") == 22 and search.days_back(st, "linkedin", "DE", "new") == 14
    big = {"titles": [f"T{i}" for i in range(400)], "keywords": {}, "job_types": ["full_time"], "targets": [{"code": "DE"}]}
    assert len(search.queries(big, "DE", "indeed", {"last_q": {}})) == 14  # 400 words in a 30-day window
    assert len(search.queries(big, "DE", "linkedin", {"last_q": {}})) == 3
    cov = search.coverage(big, {"last_q": {}})["DE"]
    assert cov["linkedin"]["cycle"] == 134 and cov["linkedin"]["gap"] and not cov["indeed"]["gap"]


def test_several_places_search_the_whole_country_and_the_radius_is_used(monkeypatch):
    many = search.board_ctx({"places": ["Munich", "Berlin"], "radius_km": 50, "remote": "any"}, "DE")
    assert many["location"] == "Germany" and many["place"] == ""  # not Munich only: the distance score judges
    one = search.board_ctx({"places": ["Munich"], "radius_km": 80, "remote": "any"}, "DE")
    assert one["location"] == "Munich" and one["distance"] == 50  # 80 km, in the miles JobSpy wants
    seen = {}
    monkeypatch.setattr("jobspy.scrape_jobs", lambda **kw: seen.update(kw) or type("D", (), {"to_dict": lambda self, o: []})())
    sources.jobspy("indeed", "nurse", {**one, "days": 7})
    assert seen["distance"] == 50


def _filtered(key, title, today=True):
    from datetime import date, timedelta
    from careerpilot.core.data import db
    with db() as con:
        con.execute("insert into jobs(key, title, company, location, country, url, source, status, why, first_seen, data) "
                    "values (?,?,?,?,?,?,?,?,?,?,?)", (key, title, "Klinikum Nord", "Leeds", "GB", f"https://example.org/{key}", "career site",
                                                      "filtered", "[junk] ", (date.today() - timedelta(days=0 if today else 30)).isoformat(), "{}"))


def test_applicant_numbers_from_linkedins_public_page(monkeypatch):
    """LinkedIn shows how many applied, without a login: "39 applicants", "Over 200 applicants", "Be among the first 25
    applicants". Read gently: LinkedIn links only, and a 429 stops it for the rest of the run."""
    cap = lambda text: f'<figcaption class="num-applicants__caption topcard__flavor--metadata">\n  {text}\n</figcaption>'
    assert sources.parse_applicants(cap("39 applicants")) == {"n": 39, "text": "39 applicants"}
    assert sources.parse_applicants(cap("Over 200 applicants")) == {"n": 200, "text": "Over 200 applicants"}
    assert sources.parse_applicants(cap("Be among the first 25 applicants")) == {"n": 0, "text": "Be among the first 25 applicants"}
    assert sources.parse_applicants("<p>no number here</p>") is None
    asked = []
    answers = iter([(200, cap("39 applicants"), ""), (429, "", ""), (200, cap("5 applicants"), "")])
    monkeypatch.setattr(sources, "http", lambda url, timeout=20: asked.append(url) or next(answers))
    monkeypatch.setattr(sources, "BLOCKED", {})
    assert sources.applicants("https://www.stepstone.de/stellenangebote--x-123") is None and asked == []  # LinkedIn only
    got = sources.applicants("https://de.linkedin.com/jobs/view/data-engineer-at-acme-4471387546")
    assert got["n"] == 39 and asked[0].endswith("/jobs-guest/jobs/api/jobPosting/4471387546")
    assert sources.applicants("https://www.linkedin.com/jobs/view/4483153231") is None  # 429
    assert sources.applicants("https://www.linkedin.com/jobs/view/4483153232") is None and len(asked) == 2  # blocked: no request


def test_the_best_new_matches_get_their_applicant_numbers(home, monkeypatch):
    from careerpilot.core.data import db
    with db() as con:
        for i, (score_, url) in enumerate([(80, "https://www.linkedin.com/jobs/view/111"), (75, "https://jobs.example/2"),
                                           (10, "https://www.linkedin.com/jobs/view/333")]):
            con.execute("insert into jobs(key, title, url, status, score, first_seen, data) values (?,?,?,?,?,date('now'),'{}')",
                        (f"k{i}", f"Job {i}", url, "new", score_))
    monkeypatch.setattr(sources, "applicants", lambda url: {"n": 12, "text": "12 applicants"} if "111" in url else None)
    monkeypatch.setattr(search.time, "sleep", lambda s: None)
    assert search.applicant_numbers() == 1
    with db() as con:
        data = {r[0]: json.loads(r[1]) for r in con.execute("select key, data from jobs")}
    assert data["k0"]["applicants"]["n"] == 12 and "applicants" not in data["k2"]  # a weak match isn't looked up


def test_a_board_that_blocks_us_says_so(monkeypatch):
    """JobSpy logs as "jobspy.<site>": a LinkedIn 429 was never recorded, so a blocked board showed as "0 found"."""
    import logging

    def blocked(**kw):
        logging.getLogger("jobspy.linkedin").error("response status code 429 (blocked: use a proxy)")
        return type("D", (), {"to_dict": lambda self, o: []})()
    monkeypatch.setattr("jobspy.scrape_jobs", blocked)
    sources.JOBSPY_ERRORS.clear()
    sources.jobspy("linkedin", "nurse", {"country": "GB", "country_name": "united kingdom", "location": "Leeds", "days": 7})
    assert "429" in " ".join(sources.JOBSPY_ERRORS.get("linkedin", []))


def test_a_posting_judged_on_its_title_alone_is_read_and_judged_again(home, monkeypatch):
    """15,119 postings were filtered on their title alone and never read again. The newest are read now; one whose
    text fits goes to the Board, saying why."""
    from careerpilot.core.data import db
    profile = {**PROFILE, "skills": {"Clinical": ["patient assessment", "NEWS2", "wound care", "medication management", "triage"]}}
    _filtered("k1", "Pflegekraft Station 3")
    _filtered("old", "Pflegekraft Station 9", today=False)  # weeks old: not read again
    text = "Our ward needs patient assessment, NEWS2, wound care, medication management and triage every day. " * 8
    page = '<script type="application/ld+json">{"@type": "JobPosting", "title": "Pflegekraft", "description": "%s"}</script>' % text
    monkeypatch.setattr(sources, "http", lambda url, timeout=20: (200, page + " Apply now", url))
    search.enrich(score.Matcher(PREFS, profile))
    with db() as con:
        got = {r["key"]: dict(r) for r in con.execute("select key, status, why from jobs")}
    assert got["k1"]["status"] == "new" and got["k1"]["why"].startswith("found after reading the posting")
    assert got["old"]["status"] == "filtered"


def test_the_ai_takes_a_second_look_at_titles_filtered_today(home, monkeypatch):
    from careerpilot.core import ai
    from careerpilot.core import profile as pf
    from careerpilot.core.data import db
    _filtered("k_perc", "Perception Engineer (m/w/d)")
    _filtered("k_bake", "Bäcker (m/w/d)")
    asked = []

    def answer(prompt, model, agent, timeout=600, key=""):
        asked.append(prompt)
        return model(picks=[{"key": "k_perc", "why": "camera perception is computer vision work"}, {"key": "nope", "why": "x"}])
    monkeypatch.setattr(ai, "answer", answer)
    monkeypatch.setattr(ai, "chosen", lambda *a, **k: None)
    assert search.second_look() == 1
    with db() as con:
        got = dict(con.execute("select key, status from jobs").fetchall())
        why = con.execute("select why from jobs where key='k_perc'").fetchone()[0]
    assert got == {"k_perc": "new", "k_bake": "filtered"} and why == "AI second look: camera perception is computer vision work"
    assert "<untrusted>" in asked[0] and "Bäcker" in asked[0]
    prefs = pf.read("preferences")
    prefs["ai_second_look"] = False
    pf.write("preferences", prefs)
    _filtered("k_more", "Robotics Engineer")
    assert search.second_look() == 0 and len(asked) == 1  # switched off: no AI at all


def test_arbeitsagentur_offer_types_and_thesis(monkeypatch):
    asked = []
    monkeypatch.setattr(de, "http", lambda url, **k: asked.append(url) or (200, '{"ergebnisliste": []}', url))
    for kind, offers in [("", ["1"]), ("student", ["34"]), ("apprenticeship", ["4"]), ("thesis", ["1", "34"]),
                         ("student+apprenticeship", ["34", "4"])]:
        asked.clear()
        de.arbeitsagentur("x", {"days": 7, "kind": kind})
        assert sorted(u.split("angebotsart=")[1].split("&")[0] for u in asked) == sorted(offers), kind
    m = score.Matcher({**PREFS, "job_types": ["thesis"]}, PROFILE)
    assert m.assess({"title": "Master Thesis Staff Nurse", "company": "C"}, "")[1] != "type"
    assert score.Matcher(PREFS, PROFILE).assess({"title": "Master Thesis Staff Nurse", "company": "C"}, "")[1] == "type"
    student_only = score.Matcher({**PREFS, "job_types": ["student"]}, PROFILE)  # no research jobs for a student-only search
    assert student_only.assess({"title": "Staff Nurse", "company": "Universität Bonn"}, "")[1] == "type"


def test_rescore_follows_new_job_types(home):
    from careerpilot.core import profile as pf
    from careerpilot.core.data import db
    pf.write("preferences", {**PREFS, "job_types": ["full_time"]})
    m = score.Matcher(PREFS, PROFILE)
    search.store([sources.job("Registered Nurse", "NHS", "Leeds", "https://a/1", "indeed"),
                  sources.job("Working Student Registered Nurse", "NHS", "Leeds", "https://a/2", "indeed"),
                  sources.job("Senior Registered Nurse", "NHS", "Leeds", "https://a/3", "indeed")], m)
    with db() as con:
        con.execute("update jobs set status='shortlisted' where url='https://a/3'")
        before = {r[0]: r[1] for r in con.execute("select url, status from jobs")}
    assert before["https://a/2"] == "filtered" and before["https://a/1"] == "new"
    pf.write("preferences", {**PREFS, "job_types": ["student"]})
    assert search.rescore() == 2
    with db() as con:
        after = {r[0]: r[1] for r in con.execute("select url, status from jobs")}
    assert after == {"https://a/1": "filtered", "https://a/2": "new", "https://a/3": "shortlisted"}  # a star stays


def test_empty_site_is_noticed_and_looked_for_again(home):
    from datetime import date, timedelta
    from careerpilot.core import sites
    from careerpilot.core.data import db
    sites.add("greenhouse", "acme", "Acme", "posting")
    sites.add("lever", "never", "Never", "posting")
    sites.record("greenhouse", "acme", 5)
    for _ in range(sites.EMPTY_AFTER):
        sites.record("greenhouse", "acme", 0)
        sites.record("lever", "never", 0)
    with db() as con:
        rows = {r["ref"]: dict(r) for r in con.execute("select * from sites")}
        con.execute("update sites set last_scan=?", ((date.today() - timedelta(days=2)).isoformat(),))
    assert rows["acme"]["zeros"] == 7 and rows["acme"]["empty_since"] == date.today().isoformat()
    assert rows["never"]["zeros"] == 0 and rows["never"]["empty_since"] is None  # never had jobs: not "moved"
    assert "acme" not in sites.companies() and [s["ref"] for s in sites.active()] == ["never"]  # weekly now
    sites.record("greenhouse", "acme", 3)
    with db() as con:
        assert dict(con.execute("select zeros, empty_since from sites where ref='acme'").fetchone()) == {"zeros": 0, "empty_since": None}


def test_softgarden_widget(monkeypatch):
    page = ('<div class="matchElement" id="job_id_7"><div class="matchValue date">21.10.26</div><div class="matchValue title">'
            '<a href="../../job/7/Werkstudent-Data?l=de">Werkstudent Data (m/w/d)</a></div><div class="matchValue ProjectGeoLocationCity">'
            '<span class="location-view-item">Augsburg</span></div></div>')
    monkeypatch.setattr(sources, "http", lambda url, **k: (200, page, url))
    r = sources.softgarden("RENK", "renk", {})
    assert r[0]["url"] == "https://renk.softgarden.io/job/7/Werkstudent-Data?l=de" and r[0]["location"] == "Augsburg"
    assert sources.ats_ref("https://renk.softgarden.io/de/widgets/jobs") == ("softgarden", "renk")


def test_loose_title_match_and_type_without_country():
    assert score.loose_hit("softwareentwickler", score.norm("Werkstudent Softwareentwicklung Backend"))
    assert not score.loose_hit("backend engineer", score.norm("Werkstudent Backoffice")) and not score.loose_hit("aws", score.norm("Laws"))
    m = score.Matcher({"targets": [{"code": "DE"}], "job_types": ["student"], "titles": ["Softwareentwickler"]}, {})
    assert m.kind("Werkstudierende im Bereich NLP", "", "Fraunhofer") == "student"  # no country on the posting: theirs
    assert m.assess({"title": "Werkstudent Softwareentwicklung", "company": "C", "country": "DE"}, "")[1] is False
