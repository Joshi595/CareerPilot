import json
from datetime import datetime, timezone

import pytest

from jobquill.core import mail
from jobquill.core import profile as pf
from jobquill.core.data import db

TRIAGE = mail.triage  # the real one (the home fixture swaps it out: no test calls an AI unless it fakes the AI itself)

def test_classify_replies():
    assert mail.classify("Unfortunately we have decided to move forward with other candidates.") == "rejected"
    assert mail.classify("Leider müssen wir Ihnen eine Absage erteilen.") == "rejected"
    assert mail.classify("We would like to invite you to an interview next week.") == "interview"
    assert mail.classify("Wir laden Sie zu einem Vorstellungsgespräch ein.") == "interview"
    assert mail.classify("Thank you for your application. We have received your application.") == "acknowledgement"
    # a maybe is not news: "if you are selected we will invite you to an interview"
    assert mail.classify("Thank you for applying. If you are shortlisted, we will invite you to an interview.") == "acknowledgement"
    assert mail.classify("Please complete the online assessment by Friday.") == "assessment"


def test_classify_next_steps_that_were_missed():
    """Real replies the first version misread: an interview that wasn't one, a rejection read as a receipt, unfinished
    applications read as receipts."""
    assert mail.classify("Wir freuen uns über Ihre Bewerbung. Wir möchten gerne mit Ihnen einen Termin für ein "
                         "telefonisches Vorgespräch vereinbaren. Buchen Sie sich gerne direkt einen Termin.") == "interview"
    assert mail.classify("Obwohl uns Ihre Qualifikationen gefallen haben, liegen uns andere Bewerbungen vor, die den "
                         "Anforderungen noch stärker entsprechen.") == "rejected"
    assert mail.classify("Just a reminder about your draft job application. We invite you to complete and submit your job "
                         "application. We thank you for your interest in joining our company.") == "action"
    assert mail.classify("Bitte schließen Sie Ihre Bewerbung ab, damit wir sie bearbeiten können.") == "action"
    assert mail.classify("You have submitted your application, but it has not been confirmed yet. Please confirm your application.") == "action"
    assert mail.classify("Leider müssen wir Ihnen absagen. Gerne laden wir Sie zu einem Vorstellungsgespräch ein.") == "unclear"
    assert mail.classify("Thank you for your application, we would like to invite you to an interview.") == "interview"
    # ... and receipts that only sounded like a next step
    assert mail.classify("Thank you for taking the time to complete your application for our Working Student role.") is None
    assert mail.classify("We noticed that you did not verify your email address yet. In order to complete your application, "
                         "please click on the link in the email we sent you.") == "action"
    assert mail.classify("Ihre Unterlagen sind bei uns eingegangen. Im Anschluss teilen wir Ihnen mit, wie es weitergeht – sei es "
                         "mit einer Entscheidung oder einer Einladung zu einem Gespräch.") == "acknowledgement"


def test_a_polite_condition_never_hides_an_invite():
    """Only "if you are selected/shortlisted" makes an invite hypothetical; "if you are available" or "falls Sie Interesse
    haben" is a real invitation, and a booking link always is."""
    assert mail.classify("If you are available, we would like to invite you to an interview on 28.10.") == "interview"
    assert mail.classify("Falls Sie Interesse haben, laden wir Sie gerne zu einem Vorstellungsgespräch ein.") == "interview"
    assert mail.classify("If you are still interested, please book a slot here: calendly.com/acme/30min") == "interview"
    assert mail.classify("Hinweis: Bitte bringen Sie zum Vorstellungsgespräch Ihren Ausweis mit.") == "interview"
    # still hypothetical: only if chosen
    assert mail.classify("Sollten Sie in die engere Wahl kommen, laden wir Sie zu einem Gespräch ein.") is None
    assert mail.classify("Should your profile match, we will invite you to an interview.") is None


def rows_():
    return [{"id": 1, "company": "Nordwest Brandschutz Service Kramer GbR", "role": "Werkstudent KI / LLM", "status": "applied", "data": "{}"},
            {"id": 2, "company": "ZFR", "role": "PhD Position - Improving Motion Simulation Experience using Reinforcement Learning",
             "status": "applied", "data": "{}"},
            {"id": 3, "company": "ZFR", "role": "Student Multi-Agent Deep Reinforcement Learning für UAVs", "status": "applied", "data": "{}"},
            {"id": 4, "company": "ZFR", "role": "Student für die Erstellung einer Reinforcement Learning Umgebung", "status": "applied", "data": "{}"},
            {"id": 5, "company": "Taxpro Augsburg GmbH & Co. KG Steuerberatungsgesellschaft", "role": "Werkstudent KI-Agenten", "status": "applied", "data": "{}"},
            {"id": 6, "company": "Nordinstitut NIA", "role": "Studentische Hilfskraft – KI und Simulation für die industrielle Fertigung",
             "status": "applied", "data": "{}"},
            {"id": 7, "company": "Nordinstitut NIE", "role": "Students: Computer Vision in Semiconductor Metrology", "status": "pending", "data": "{}"}]


def mail_(frm, subject, body="", thread=""):
    return {"frm": frm, "subject": subject, "thread": thread, "head": f"{frm} {subject}".lower(),
            "text": f"{frm} {subject} {body}".lower(), "date": "2026-10-19"}


def test_link_by_sender_name_number_and_role():
    rows = rows_()
    nbs = mail_("NBS | Karriere <karriere@nbs-kramer.example>", "Ihre Bewerbung als Werkstudent KI", thread="t1")
    assert mail.link(nbs, rows)[0]["id"] == 1  # the address names the company, the mail doesn't
    zfr = mail_("Recruiting ZFR <system@successfactors.example>", "Deine Bewerbung beim ZFR (5706) - Student-/in Multi-Agent "
                "Deep Reinforcement Learning für UAV's (w/m/d)")
    assert mail.link(zfr, rows)[0]["id"] == 3
    vague = mail_("Recruiting ZFR <system@successfactors.example>", "Deine Bewerbung beim ZFR (6202)")
    assert mail.link(vague, rows)[0] is None and len(mail.link(vague, rows)[1]) == 3  # several: never a guess
    assert mail.link(mail_("Julia <karriere@taxpro-aug.example>", "Deine Bewerbung bei Taxpro Augsburg!"), rows)[0]["id"] == 5
    ipa = mail_("Nordinstitut <recruiting@nordinstitut.example>", "Ihre Bewerbung als - Studentische Hilfskraft (m/w/d) - KI und "
                "Simulation für die industrielle Fertigung")
    assert mail.link(ipa, rows)[0]["id"] == 6
    assert mail.link(mail_("Newsletter <news@shop.example>", "Our autumn sale"), rows) == (None, [])
    rows += [{"id": 8, "company": "OVX SCHMIDT GmbH", "role": "Werkstudent ADAS", "status": "applied", "data": "{}"},
             {"id": 9, "company": "BrightVenture Management GmbH", "role": "Werkstudent IT / AI", "status": "applied", "data": "{}"},
             {"id": 10, "company": "Vektor Engineering GmbH", "role": "Machine Learning Engineer Automatisiertes Fahren (m/w/d)",
              "status": "applied", "data": "{}"}]
    assert mail.link(mail_("OVX Human Resources <mail@globalhr.ovx.example>", "Ihre Bewerbung"), rows)[0]["id"] == 8
    sgp = rows + [{"id": 12, "company": "TKP Schneider Lindner & Partner", "role": "Werkstudent Generative KI", "status": "applied", "data": "{}"},
                  {"id": 13, "company": "Lindner GmbH & Co. KG", "role": "Werkstudent Anlagenbau", "status": "applied", "data": "{}"},
                  {"id": 14, "company": "Drive Ai GmbH", "role": "AI/ML Engineer Autonomous Systems", "status": "applied", "data": "{}"}]
    assert mail.link(mail_("Ihre Bewerbung <noreply@karriere.tkp.example>", "Ihre Bewerbung bei TKP: Werkstudent Generative KI",
                           "TKP Schneider Lindner & Partner"), sgp)[0]["id"] == 12
    assert mail.link(mail_("Stepstone <info@email.stepstone.example>", "Alex, your application for AI/ML Engineer (m/f/d) "
                           "Autonomous Systems has been sent"), sgp)[0]["id"] == 14
    benz = [{"id": 15, "company": "Autowerk AG", "role": "Werkstudent ADAS EE-Inbetriebnahme Fahrwerk", "status": "applied", "data": "{}"},
            {"id": 16, "company": "Autowerk AG", "role": "Werkstudent Supply Chain und Künstliche Intelligenz", "status": "applied", "data": "{}"},
            {"id": 17, "company": "Autowerk Group AG", "role": "Werkstudent IT Forensik", "status": "applied", "data": "{}"}]
    adas = mail_("Recruiting Team <noreply@recruiting.autowerk.example>", "Werkstudent*in ADAS EE-Inbetriebnahme Fahrwerk-AWK0004938",
                 "Ihre Bewerbung ist eingegangen. Autowerk Group AG, Stuttgart")  # the footer names the parent company
    assert mail.link(adas, benz)[0]["id"] == 15
    # one Gmail thread for all of a person's mails from that system: the mail's own words still decide
    benz[1]["data"] = json.dumps({"threads": ["all"]})
    assert mail.link({**adas, "thread": "all"}, benz)[0]["id"] == 15
    assert mail.REF.findall("fahrwerk-awk00048x1 und awk0004899") == ["awk00048x1", "awk0004899"]
    lack = rows + [{"id": 11, "company": "Lack und Karosserie Weber GmbH", "role": "Werkstudent", "status": "applied", "data": "{}"}]
    assert mail.link(mail_("Slack <no-reply@slack.example>", "Your application to join a workspace"), lack) == (None, [])
    assert mail.link(mail_("noreply@join.example", "Wir haben deine Bewerbung für die Position bei BRIGHTVENTURE erhalten"), rows)[0]["id"] == 9
    assert mail.link(mail_("Stepstone <info@email.stepstone.example>", "Alex, your application for Machine Learning Engineer "
                           "Automatisiertes Fahren has been sent"), rows)[0]["id"] == 10
    # learned: the conversation and the job number bring the next mails to the same application
    from jobquill.core.data import db
    with db() as con:
        con.executemany("insert into applications(id, company, role, status, data) values (?,?,?,?,?)",
                        [(r["id"], r["company"], r["role"], r["status"], r["data"]) for r in rows])
        mail.record(con, rows[2], zfr, "acknowledgement")
        mail.record(con, rows[0], nbs, "acknowledgement")
    assert mail.link(mail_("x <x@successfactors.example>", "Status (5706)"), rows)[0]["id"] == 3
    assert mail.link(mail_("Someone <someone@gmail.example>", "Re: Termin", thread="t1"), rows)[0]["id"] == 1


def test_when_reads_the_appointment():
    import email.message
    assert mail.when({"text": "we confirm the interview on 28.10.2099 at 10:00 (europe/berlin)"}) == "2099-10-28T10:00"
    assert mail.when({"text": "am montag, 26. oktober 2099 um 14:30 uhr"}) == "2099-10-26T14:30"
    assert mail.when({"text": "ihre bewerbung vom 19.10.2020 um 09:11"}) == ""  # past dates are history, not appointments
    msg = email.message.EmailMessage()
    msg.set_content("See you")
    msg.add_attachment("BEGIN:VEVENT\nDTSTART;TZID=W. Europe Standard Time:20991028T100000\nEND:VEVENT\n",
                       subtype="calendar", filename="invite.ics")
    assert mail.when({"msg": msg, "text": ""}) == "2099-10-28T10:00"


def test_when_reads_english_and_numeric_times():
    """Without a time there is no reminder: every common way of writing one is read."""
    assert mail.when({"text": "your interview is on 26 october 2099, 14:00 (cet)"}) == "2099-10-26T14:00"
    assert mail.when({"text": "please join us on october 26, 2099 at 2:00 pm via teams"}) == "2099-10-26T14:00"
    assert mail.when({"text": "monday, october 26th 2099 at 9:30 am"}) == "2099-10-26T09:30"
    assert mail.when({"text": "date: 26/10/2099 14:00"}) == "2099-10-26T14:00"
    assert mail.when({"text": "start: 2099-10-26 14:00"}) == "2099-10-26T14:00"
    assert mail.when({"text": "noon slot: 26 october 2099 at 12:15 pm, or 12:00 am is not possible"}) == "2099-10-26T12:15"


def test_no_reply_is_dropped(home, monkeypatch):
    """The safety rule: every mail about an application is linked or lands in Needs you; next steps push at once, and a
    linked interview invite needs you too (reply, book a slot), not only a new status."""
    sent = []
    monkeypatch.setattr("jobquill.core.alerts.push", lambda event, title, body, **k: sent.append((event, title)) or True)
    with db() as con:
        for r in rows_():
            con.execute("insert into applications(id, company, role, status, data) values (?,?,?,?,?)",
                        (r["id"], r["company"], r["role"], r["status"], r["data"]))
    mails = [
        mail_("NBS | Karriere <karriere@nbs-kramer.example>", "Ihre Bewerbung als Werkstudent KI",
              "Wir möchten gerne mit Ihnen einen Termin für ein telefonisches Vorgespräch vereinbaren."),
        mail_("Unknown Corp <jobs@unknowncorp.example>", "Your application", "Unfortunately we decided not to proceed."),
        mail_("Nordinstitut <recruiting@nordinstitut.example>", "Bitte schließen Sie Ihre Bewerbung ab", "Bitte schließen Sie Ihre Bewerbung ab."),
        mail_("Recruiting ZFR <system@successfactors.example>", "Deine Bewerbung beim ZFR (6202)", "Leider liegen uns andere Bewerbungen vor."),
        mail_("Shop <news@shop.example>", "Autumn sale", "50% off"),
    ]
    for i, m in enumerate(mails):
        m.update(id=f"<{i}@x>", snippet=m["text"][:300], msg=None)
    monkeypatch.setattr(mail, "fetch", lambda since: mails)
    monkeypatch.setattr(mail, "settings", lambda: {"address": "me@example.org", "provider": "custom", "imap": "imap.example.org",
                                                   "smtp": "", "port": 0})
    r = mail.check_replies()
    with db() as con:
        status = dict(con.execute("select id, status from applications").fetchall())
        items = {i["subject"]: i for i in con.execute("select * from mail_items")}
    assert status[1] == "interview" and ("interview", "JobQuill: Interview · Nordwest Brandschutz Service Kramer GbR") in sent
    assert items["Ihre Bewerbung als Werkstudent KI"]["done"] == 0 and items["Ihre Bewerbung als Werkstudent KI"]["app_id"] == 1
    assert items["Your application"]["app_id"] is None and items["Your application"]["done"] == 0  # kept for the person
    assert items["Bitte schließen Sie Ihre Bewerbung ab"]["signal"] == "action" and any(e == "action" for e, _ in sent)
    zfr = items["Deine Bewerbung beim ZFR (6202)"]
    assert [c["id"] for c in json.loads(zfr["candidates"])] == [2, 3, 4] and status[2] == "applied" and zfr["done"] == 0
    assert "Autumn sale" not in items and len(r["to_check"]) == 4


def test_receipts_are_filed_by_themselves_and_logged(home, monkeypatch):
    """A receipt never asks anything: linked or not, it is filed (auto=1) with a line saying what was done."""
    monkeypatch.setattr("jobquill.core.alerts.push", lambda *a, **k: True)
    with db() as con:
        for r in rows_():
            con.execute("insert into applications(id, company, role, status, data) values (?,?,?,?,?)",
                        (r["id"], r["company"], r["role"], r["status"], r["data"]))
    mails = [mail_("Recruiting ZFR <system@successfactors.example>", "Deine Bewerbung beim ZFR (6202)",
                   "Vielen Dank für Ihre Bewerbung. Ihre Unterlagen sind eingegangen."),
             mail_("Taxpro <karriere@taxpro-aug.example>", "Deine Bewerbung bei Taxpro Augsburg!", "Wir haben deine Bewerbung erhalten.")]
    for i, m in enumerate(mails):
        m.update(id=f"<r{i}@x>", snippet=m["text"][:300], msg=None)
    monkeypatch.setattr(mail, "fetch", lambda since: mails)
    monkeypatch.setattr(mail, "settings", lambda: {"address": "me@example.org", "provider": "custom", "imap": "imap.example.org",
                                                   "smtp": "", "port": 0})
    assert mail.check_replies()["to_check"] == []
    with db() as con:
        items = {i["subject"]: dict(i) for i in con.execute("select * from mail_items")}
    assert all(i["done"] == 1 and i["auto"] == 1 for i in items.values())
    assert items["Deine Bewerbung bei Taxpro Augsburg!"]["app_id"] == 5 and "Taxpro" in items["Deine Bewerbung bei Taxpro Augsburg!"]["note"]


def test_one_company_many_applications_the_job_number_decides(home):
    """One university, three applications: the role words are the same, the position ID in the mail's body is in one posting only (the postal
    code, in all of them, decides nothing)."""
    rows = []
    for i, (role, ref) in enumerate([("Research Assistant/Associate (f/m/d)", "V000077301"),
                                     ("Research Assistant/Associate (f/m/d)", "V000077412"),
                                     ("Research Assistant (f/m/d) Robotics", "V000076555")], 1):
        folder = home / "applications" / f"app{i}"
        folder.mkdir(parents=True)
        (folder / "posting.md").write_text(f"# {role}\nPosition ID: {ref}\n10115 Berlin\n", encoding="utf-8")
        rows.append({"id": i, "company": "Nordlicht University", "role": role, "status": "applied", "folder": f"app{i}",
                     "date_applied": "2026-10-0" + str(i), "data": "{}"})
    m = mail_("Nordlicht Jobs <noreply@nordlicht-uni.example>", "Your application for the position Research Assistant/Associate (f/m/d)",
              "Your application as: Research Assistant/Associate (f/m/d) Position ID: V000077301 University institution: "
              "Lehrstuhl X, 10115 Berlin")
    assert mail.link(m, rows)[0]["id"] == 1
    why = {c["id"]: c["why"] for c in mail.reasons(m, rows)}
    assert "V000077301" in why[1].upper() and "V000077301" not in why[2].upper()


def test_a_receipt_goes_to_the_application_sent_just_before_it(home):
    rows = [{"id": 1, "company": "Skyward Drones GmbH", "role": "Working Student - Automation, Procurement (m/f/d)",
             "status": "applied", "date_applied": "2026-10-17", "data": "{}"},
            {"id": 2, "company": "Skyward Drones GmbH", "role": "Working Student - Automation, Engineering (m/f/d)",
             "status": "applied", "date_applied": "2026-10-01", "data": "{}"}]
    m = {**mail_("Skyward <jobs@recruitee-inbox.example>", "Working Student - Automation - Confirmation of your application",
                 "Thank you for your interest in working for Skyward Drones! Your application was received."), "date": "2026-10-18"}
    assert mail.link(m, rows, "acknowledgement")[0]["id"] == 1
    assert mail.link(m, rows, "rejected")[0] is None  # a rejection is never matched by date: the person says


def test_ai_reads_what_the_rules_cant(home, monkeypatch):
    """A linked human reply with no known phrase: the AI sorts it (summary, signal, time). Without AI it still lands in
    Needs you as "Read this" (a person wrote it), and a no-reply notice without a signal is filed."""
    monkeypatch.setattr("jobquill.core.alerts.push", lambda *a, **k: True)
    with db() as con:
        con.execute("insert into applications(id, company, role, status, data) values (1, 'Acme Robotics', 'Data Engineer', 'applied', '{}')")
    human = {**mail_("Jane Doe <jane@acme-robotics.example>", "Your application at Acme Robotics",
                     "Hi, could we have a quick chat next week? Tuesday 3pm would suit me."), "id": "<h@x>", "snippet": "", "msg": None}
    notice = {**mail_("Acme Robotics <no-reply@acme-robotics.example>", "Your application at Acme Robotics: data privacy",
                      "We store your data for six months."), "id": "<n@x>", "snippet": "", "msg": None}
    monkeypatch.setattr(mail, "fetch", lambda since: [human, notice])
    monkeypatch.setattr(mail, "settings", lambda: {"address": "me@example.org", "provider": "custom", "imap": "x", "smtp": "", "port": 0})
    monkeypatch.setattr(mail, "triage", lambda m, cands: {"signal": "interview", "app_id": 1, "when": "2099-10-27T15:00",
                                                          "summary": "Jane asks for a short call on Tuesday at 3 pm."}
                        if "jane" in m["frm"] else None)
    mail.check_replies()
    with db() as con:
        items = {i["msg_id"]: dict(i) for i in con.execute("select * from mail_items")}
        app = dict(con.execute("select * from applications where id=1").fetchone())
    assert items["<h@x>"]["signal"] == "interview" and items["<h@x>"]["done"] == 0 and "short call" in items["<h@x>"]["summary"]
    assert json.loads(app["data"])["interview_at"] == "2099-10-27T15:00" and app["status"] == "interview"
    assert items["<n@x>"]["done"] == 1
    mail._save({"seen": [], "last": ""})
    monkeypatch.setattr(mail, "triage", lambda m, cands: None)  # no AI
    with db() as con:
        con.execute("delete from mail_items")
    mail.check_replies()
    with db() as con:
        assert con.execute("select signal, done from mail_items where msg_id='<h@x>'").fetchone()[:] == ("unclear", 0)


def checked(monkeypatch, mails, rows=()):
    """check_replies over these mails and applications; the mail_items by msg_id."""
    with db() as con:
        for r in rows:
            con.execute("insert into applications(id, company, role, status, data) values (?,?,?,?,?)",
                        (r["id"], r["company"], r["role"], r["status"], r.get("data", "{}")))
    for i, m in enumerate(mails):
        m.setdefault("id", f"<c{i}@x>"), m.setdefault("snippet", ""), m.setdefault("msg", None)
    monkeypatch.setattr(mail, "fetch", lambda since: mails)
    monkeypatch.setattr(mail, "settings", lambda: {"address": "me@example.org", "provider": "custom", "imap": "x", "smtp": "", "port": 0})
    mail.check_replies()
    with db() as con:
        return {i["msg_id"]: dict(i) for i in con.execute("select * from mail_items")}


def test_a_persons_reply_is_never_filed_as_nothing_to_do(home, monkeypatch):
    """A recruiter's "thanks for your application, could you send your availability?" is no receipt, and a person's
    mail about an application JobQuill can't place is still the person's to read (no AI here)."""
    monkeypatch.setattr("jobquill.core.alerts.push", lambda *a, **k: True)
    acme = [{"id": 1, "company": "Acme Robotics", "role": "Data Engineer", "status": "applied"}]
    items = checked(monkeypatch, [
        {**mail_("Jane Doe <jane.doe@acme-robotics.example>", "Your application at Acme Robotics",
                 "Thanks for your application. Could you send me your availability for next week?"), "id": "<jane@x>"},
        {**mail_("Max Muster <max@nordwind-labs.example>", "Ihre Bewerbung", "Hallo, können wir nächste Woche kurz telefonieren?"),
         "id": "<max@x>"},
        {**mail_("Acme Robotics <jobs@acme-robotics.example>", "Your application at Acme Robotics",
                 "Thank you for your application. We have received your application."), "id": "<ats@x>"}], acme)
    assert items["<jane@x>"]["done"] == 0 and items["<jane@x>"]["signal"] == "unclear"
    assert items["<max@x>"]["done"] == 0 and items["<max@x>"]["signal"] == "unclear"
    assert items["<ats@x>"]["done"] == 1  # the system's receipt is still filed by itself


def test_one_bad_mail_never_stops_the_check(home, monkeypatch):
    """A mail that breaks something (a NUL in its subject) is still shown, the mails after it are read, and the next
    check doesn't push the ones before it again."""
    pushed = []

    def push(event, title, body, **k):
        if "\x00" in title + body:
            raise ValueError("embedded null character")
        pushed.append(title)
        return True
    monkeypatch.setattr("jobquill.core.alerts.push", push)
    rows = [{"id": 1, "company": "Acme Robotics", "role": "Data Engineer", "status": "applied"},
            {"id": 2, "company": "Nordwind Labs", "role": "Data Analyst", "status": "applied"}]
    mails = [mail_("Jane <jane@acme-robotics.example>", "Invitation to an interview at Acme Robotics",
                   "We would like to invite you to an interview."),
             mail_("HR <hr@nordwind-labs.example>", "Nordwind Labs: your application\x00", "We would like to invite you to an interview."),
             mail_("HR <hr@nordwind-labs.example>", "Nordwind Labs: your application, again", "Unfortunately we decided not to proceed.")]
    items = checked(monkeypatch, mails, rows)
    bad = next(i for i in items.values() if "\x00" in i["subject"])
    assert len(items) == 3 and bad["done"] == 0  # still in Needs you
    with db() as con:
        assert con.execute("select status from applications where id=2").fetchone()[0] == "rejected"
    first = list(pushed)
    mail.check_replies()
    assert pushed == first and len(mail._state()["seen"]) == 3


def test_an_ai_time_with_a_timezone_still_reminds(home, monkeypatch):
    from jobquill.core import ai
    monkeypatch.setattr("jobquill.core.alerts.push", lambda *a, **k: True)
    monkeypatch.setattr(mail, "triage", TRIAGE)
    monkeypatch.setattr(ai, "chosen", lambda *a, **k: None)  # as if an AI app were installed (CI has none)
    monkeypatch.setattr(ai, "answer", lambda *a, **k: mail.Triage(signal="interview", app_id=1, when="2099-10-27T15:00+01:00",
                                                                  summary="A call on Tuesday."))
    items = checked(monkeypatch, [mail_("Jane <jane@acme-robotics.example>", "Your application at Acme Robotics", "Can we talk?")],
                    [{"id": 1, "company": "Acme Robotics", "role": "Data Engineer", "status": "applied"}])
    at = next(iter(items.values()))["at"]
    assert len(at) == 16 and "+" not in at  # local time, no offset: every comparison and reminder works with it
    pushed = []
    monkeypatch.setattr("jobquill.core.alerts.push", lambda event, title, body, **k: pushed.append(title) or True)
    with db() as con:
        con.execute("insert into applications(id, company, role, status, data) values (2, 'Old', 'X', 'interview', ?)",
                    (json.dumps({"interview_at": "2099-10-28T14:00+00:00"}),))  # stored by an earlier version
        con.execute("insert into applications(id, company, role, status, data) values (3, 'Beta', 'Y', 'interview', ?)",
                    (json.dumps({"interview_at": "2099-10-28T14:00"}),))
    frozen(monkeypatch, "2099-10-27T18:00")
    mail.reminders()
    assert "JobQuill: interview tomorrow at 14:00 · Beta" in pushed


def test_ai_triage_never_breaks_the_check(home, monkeypatch):
    """Any failure of the AI (not only its own errors: a missing command, a broken answer) leaves the rules to decide."""
    from jobquill.core import ai
    monkeypatch.setattr(ai, "chosen", lambda *a, **k: None)  # as if an AI app were installed (CI has none)
    monkeypatch.setattr(ai, "answer", lambda *a, **k: (_ for _ in ()).throw(OSError("the AI command is gone")))
    assert TRIAGE(mail_("Jane <jane@acme.example>", "Hi", "Can we talk?"), []) is None
    monkeypatch.setattr(ai, "answer", lambda *a, **k: mail.Triage(signal="interview", app_id=0, when="2099-10-27T15:00Z", summary="x"))
    assert TRIAGE(mail_("Jane <jane@acme.example>", "Hi", "Can we talk?"), [])["when"] == \
        datetime(2099, 10, 27, 15, tzinfo=timezone.utc).astimezone().replace(tzinfo=None).isoformat(timespec="minutes")


def test_a_postcode_or_phone_number_is_no_job_number(home):
    """Two close roles at one company: a postcode or phone number that only one posting's contact block has decides
    nothing (a rejection would go to the wrong application); a position ID does."""
    rows = []
    for i, (role, extra) in enumerate([("Data Scientist (m/w/d) Forecasting", "Kontakt: 80331 München, Tel. 089 1234567"),
                                       ("Data Scientist (m/w/d) Pricing", "")], 1):
        (home / "applications" / f"a{i}").mkdir(parents=True)
        (home / "applications" / f"a{i}" / "posting.md").write_text(f"# {role}\nWe are hiring.\n{extra}\n", encoding="utf-8")
        rows.append({"id": i, "company": "Nordwind Labs GmbH", "role": role, "status": "applied", "folder": f"a{i}", "data": "{}"})
    m = mail_("Nordwind Labs <hr@nordwind-labs.example>", "Ihre Bewerbung als Data Scientist",
              "Leider müssen wir Ihnen absagen. Nordwind Labs GmbH, 80331 München, Tel. 089 1234567")
    assert mail.link(m, rows, "rejected")[0] is None
    assert all("same job number" not in c["why"] for c in mail.reasons(m, rows))


def test_check_these_again_moves_the_status_too(home, monkeypatch):
    with db() as con:
        con.execute("insert into applications(id, company, role, status, data) values (1, 'Acme', 'Nurse', 'applied', ?)",
                    (json.dumps({"refs": ["77123"]}),))
        con.execute("insert into mail_items(msg_id, date, frm, subject, signal, snippet, candidates) values "
                    "('<r@x>', '2026-10-19', 'HR <hr@other.example>', 'Ihre Bewerbung (77123)', 'rejected', '', '[]')")
    m = {**mail_("HR <hr@other.example>", "Ihre Bewerbung (77123)", "Leider müssen wir absagen."), "id": "<r@x>", "msg": None}
    monkeypatch.setattr(mail, "fetch_ids", lambda ids: [m])
    assert mail.recheck() == 1
    with db() as con:
        assert con.execute("select status from applications where id=1").fetchone()[0] == "rejected"


def test_wrong_link_is_undone_and_the_right_one_gets_the_status(home):
    """Filed by itself › Wrong? Link it: the wrong application loses the reply, conversation, number, time and the
    status the mail gave it; the right one gets them."""
    from fastapi.testclient import TestClient
    from jobquill.app import server
    c = TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})
    with db() as con:
        for i in (1, 2):
            con.execute("insert into applications(id, company, role, status, data) values (?, 'Acme', ?, 'applied', '{}')", (i, f"Role {i}"))
        wrong = dict(con.execute("select * from applications where id=1").fetchone())
        m = {"date": "2026-10-19", "frm": "HR <hr@acme.example>", "subject": "Ihre Bewerbung (77123)", "thread": "th1"}
        mail.record(con, wrong, m, "rejected", "rejected")
        con.execute("insert into mail_items(msg_id, date, frm, subject, thread, signal, snippet, app_id, candidates, done, auto, note) "
                    "values ('<w@x>', '2026-10-19', 'HR <hr@acme.example>', 'Ihre Bewerbung (77123)', 'th1', 'rejected', '', 1, '[]', 1, 1, 'x')")
        item = con.execute("select id from mail_items").fetchone()[0]
    assert c.post(f"/api/mail/items/{item}", json={"app_id": 2}).json() == {"ok": True}
    with db() as con:
        rows = {r["id"]: dict(r) for r in con.execute("select * from applications")}
    old, new = json.loads(rows[1]["data"]), json.loads(rows[2]["data"])
    assert rows[1]["status"] == "applied" and not old["replies"] and not old.get("threads") and not old.get("refs")
    assert rows[2]["status"] == "rejected" and new["replies"][0]["subject"] == "Ihre Bewerbung (77123)" and new["threads"] == ["th1"]


def test_linking_an_interview_moves_the_application_forward(home):
    from fastapi.testclient import TestClient
    from jobquill.app import server
    c = TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})
    with db() as con:
        con.execute("insert into applications(id, company, role, status, data) values (9, 'Acme', 'Nurse', 'applied', '{}')")
        con.execute("insert into mail_items(msg_id, date, frm, subject, signal, snippet, candidates) values "
                    "('<i@x>', '2026-10-19', 'HR <hr@acme.example>', 'Einladung', 'interview', '', '[]')")
        item = con.execute("select id from mail_items").fetchone()[0]
    c.post(f"/api/mail/items/{item}", json={"app_id": 9})
    with db() as con:
        assert con.execute("select status from applications where id=9").fetchone()[0] == "interview"


def test_an_interview_that_mentions_registering_is_still_an_interview(home):
    """"Bitte registrieren Sie sich für den Termin" doesn't make an interview a company account mail (which would hide
    its application and its time)."""
    from fastapi.testclient import TestClient
    from jobquill.app import server
    c = TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})
    with db() as con:
        con.execute("insert into mail_items(msg_id, date, frm, subject, signal, snippet, body, candidates) values "
                    "('<v@x>', '2026-10-19', 'HR <hr@acme.example>', 'Einladung zum Vorstellungsgespräch', 'interview', '', "
                    "'Bitte registrieren Sie sich vorab im Videoportal.', '[]')")
    assert c.get("/api/mail/items").json()["items"][0]["account"] is False


def test_an_account_mail_is_one_company_action(home, monkeypatch):
    """"Confirm your e-mail address" from a portal with ten applications: one action for the company, no question
    about which application."""
    from fastapi.testclient import TestClient
    from jobquill.app import server
    monkeypatch.setattr("jobquill.core.alerts.push", lambda *a, **k: True)
    with db() as con:
        for i, role in enumerate(["Werkstudent Fahrwerk", "Werkstudent Marketing"], 1):
            con.execute("insert into applications(id, company, role, status, data) values (?, 'Autowerk AG', ?, 'applied', '{}')", (i, role))
    m = {**mail_("Autowerk <noreply@recruiting.autowerk.example>", "Aktion erforderlich: E-Mailadresse bestätigen",
                 "Hallo, diese Nachricht wird nach einer Bewerbung in unserem Portal versendet. Bitte bestätigen Sie Ihre "
                 "E-Mail-Adresse. Autowerk AG"), "id": "<acc@x>", "snippet": "", "msg": None}
    monkeypatch.setattr(mail, "fetch", lambda since: [m])
    monkeypatch.setattr(mail, "settings", lambda: {"address": "me@example.org", "provider": "custom", "imap": "x", "smtp": "", "port": 0})
    mail.check_replies()
    c = TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})
    item = c.get("/api/mail/items").json()["items"][0]
    assert item["group"] == "act" and item["account"] and item["company"] == "Autowerk AG"


def test_failing_check_alerts_and_keeps_its_place(home, monkeypatch):
    sent = []
    monkeypatch.setattr("jobquill.core.alerts.send", lambda event, title, body, **k: sent.append(title) or True)
    monkeypatch.setattr(mail, "configured", lambda: True)
    monkeypatch.setattr(mail, "fetch", lambda since: (_ for _ in ()).throw(OSError("login refused")))
    mail._save({"seen": [], "last": "2026-10-20"})
    assert "login refused" in mail.hourly()
    mail.hourly()  # within 6 hours: no second alert
    assert sent == ["JobQuill can't read your email"] and mail._state()["last"] == "2026-10-20"


def frozen(monkeypatch, when: str):
    import datetime as dt

    class Now(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime.fromisoformat(when)
    monkeypatch.setattr(mail, "datetime", Now)


def test_interview_reminders_evening_morning_and_soon_each_once(home, monkeypatch):
    pushed = []
    monkeypatch.setattr("jobquill.core.alerts.push", lambda event, title, body, **k: pushed.append((event, title)) or True)
    with db() as con:
        con.execute("insert into applications(id, company, role, status, data) values (1, 'Acme', 'Nurse', 'interview', ?)",
                    (json.dumps({"interview_at": "2099-10-28T14:00"}),))
    for now in ("2099-10-27T16:00", "2099-10-27T18:00", "2099-10-27T19:00", "2099-10-28T08:05", "2099-10-28T09:00",
                "2099-10-28T12:00", "2099-10-28T13:00"):
        frozen(monkeypatch, now)
        mail.reminders()
    assert pushed == [("interview", "JobQuill: interview tomorrow at 14:00 · Acme"),
                      ("interview", "JobQuill: interview today at 14:00 · Acme"),
                      ("interview", "JobQuill: interview at 14:00 (in 2 hours) · Acme")]


def test_a_silent_email_check_is_caught(home, monkeypatch):
    """The daily search finds the hourly check hasn't worked for hours although the computer is on: the person is told,
    the task is put back and the check runs now. At app start it only catches up, quietly (the computer was off)."""
    pushed, ran = [], []
    monkeypatch.setattr(mail, "configured", lambda: True)
    monkeypatch.setattr("jobquill.core.alerts.push", lambda event, title, body, **k: pushed.append(title) or True)
    monkeypatch.setattr(mail, "hourly", lambda: ran.append("check"))
    monkeypatch.setattr("jobquill.core.scheduler.ensure", lambda *a, **k: ran.append("ensure"))
    mail._save({"seen": [], "last": "", "ok_at": "2026-01-01T08:00"})
    mail.watchdog(loud=True)
    assert pushed == ["JobQuill's email check isn't running"] and ran == ["ensure", "check"]
    pushed.clear(), ran.clear()
    mail.watchdog(loud=False)
    assert pushed == [] and ran == ["ensure", "check"]
    pushed.clear(), ran.clear()
    mail._save({"seen": [], "last": "", "ok_at": datetime.now().isoformat(timespec="minutes")})
    mail.watchdog(loud=True)
    assert pushed == [] and ran == []


def test_gmail_link():
    assert mail.gmail_link("me@gmail.com", "Acme GmbH", "2026-10-08", "1a11bcf3") == \
        "https://mail.google.com/mail/?authuser=me%40gmail.com#all/1a11bcf3"
    assert mail.gmail_link("me@gmail.com", "Acme GmbH", "2026-10-08").endswith(
        "#search/%22Acme%22%20after%3A2026%2F10%2F08%20-from%3Ame")


def test_mail_checks_the_servers_certificate(monkeypatch):
    """imaplib and smtplib accept any certificate unless given a checking context: the app password could be read."""
    import imaplib
    import smtplib
    import ssl
    seen = []

    class Server:
        def __init__(self, *a, ssl_context=None, context=None, **k):
            if ssl_context or context:
                seen.append(ssl_context or context)

        def login(self, *a):
            pass

        def starttls(self, context=None):
            seen.append(context)

    for mod, name in ((imaplib, "IMAP4_SSL"), (smtplib, "SMTP_SSL"), (smtplib, "SMTP")):
        monkeypatch.setattr(mod, name, Server)
    monkeypatch.setattr(mail, "settings", lambda: {"address": "a@b.example", "provider": "gmail", "imap": "", "smtp": "", "port": 0})
    monkeypatch.setattr(mail.secrets, "get", lambda name: "pw")
    mail._imap(), mail._smtp("smtp.example", 465), mail._smtp("smtp.example", 587)
    assert len(seen) == 3 and all(c.verify_mode == ssl.CERT_REQUIRED and c.check_hostname for c in seen)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBQUILL_HOME", str(tmp_path))
    monkeypatch.setattr("jobquill.core.alerts.send", lambda *a, **k: False)
    monkeypatch.setattr("jobquill.core.alerts.desktop", lambda *a, **k: None)
    monkeypatch.setattr(mail, "triage", lambda m, cands: None)  # never a real AI call from a test (one is installed here)
    return tmp_path




def test_sweep_moves_silent_applications():
    from datetime import date, timedelta
    old, recent = ((date.today() - timedelta(days=d)).isoformat() for d in (90, 10))
    reply = '{"replies": [{"date": "%s"}]}' % recent
    with db() as con:
        con.executemany("insert into applications(company, date_applied, status, data) values (?,?,?,?)", [
            ("Silent", old, "applied", "{}"), ("Answered", old, "applied", reply),
            ("Fresh", recent, "applied", "{}"), ("Unsent", old, "pending", "{}")])
    assert mail.sweep() == ["Silent"]
    with db() as con:
        assert dict(con.execute("select company, status from applications").fetchall()) == {
            "Silent": "no_response", "Answered": "applied", "Fresh": "applied", "Unsent": "pending"}


def test_person_says_which_application(home):
    """Mail to check › Which application?: the reply joins it with its status, and the next mail in that conversation
    links by itself."""
    from fastapi.testclient import TestClient
    from jobquill.app import server
    c = TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})
    with db() as con:
        con.execute("insert into applications(id, company, role, status, data) values (9, 'Acme', 'Nurse', 'applied', '{}')")
        con.execute("insert into mail_items(msg_id, date, frm, subject, thread, signal, snippet, candidates, at) values "
                    "('<1@x>', '2026-10-19', 'HR <hr@other.example>', 'Ihre Bewerbung (77123)', 'th9', 'interview', '', '[]', '2099-01-05T09:30')")
    item = c.get("/api/mail/items").json()["items"][0]
    assert c.post(f"/api/mail/items/{item['id']}", json={"app_id": 9, "status": "interview"}).json() == {"ok": True}
    assert c.get("/api/mail/items").json()["items"] == []
    with db() as con:
        row = dict(con.execute("select * from applications where id=9").fetchone())
    data = json.loads(row["data"])
    assert row["status"] == "interview" and data["interview_at"] == "2099-01-05T09:30"
    assert data["threads"] == ["th9"] and data["refs"] == ["77123"]
    assert c.put("/api/applications/9", json={"interview_at": "javascript:x"}).status_code == 422
