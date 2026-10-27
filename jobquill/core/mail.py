"""Email: read replies to open applications (IMAP), send applications and follow-ups (SMTP), and fetch account
verification links. Any provider that allows an app password; the password lives in the
computer's password store. Reply sorting is ported from the original pipeline's inbox.py (EN + DE phrases)."""
import email
import email.message
import email.policy
import email.utils
import html
import imaplib
import json
import mimetypes
import re
import smtplib
import ssl
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jobquill.core import secrets
from jobquill.core import profile as pf
from jobquill.core.data import db, home

PROVIDERS = {  # imap host, smtp host, smtp port (465 = SSL, 587 = STARTTLS)
    "gmail": ("imap.gmail.com", "smtp.gmail.com", 465), "outlook": ("outlook.office365.com", "smtp.office365.com", 587),
    "yahoo": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com", 465), "icloud": ("imap.mail.me.com", "smtp.mail.me.com", 587),
    "gmx": ("imap.gmx.net", "mail.gmx.net", 465), "web.de": ("imap.web.de", "smtp.web.de", 587),
    "zoho": ("imap.zoho.eu", "smtp.zoho.eu", 465), "fastmail": ("imap.fastmail.com", "smtp.fastmail.com", 465)}
DOMAINS = {  # address domain -> provider
    "gmail.com": "gmail", "googlemail.com": "gmail", "outlook.com": "outlook", "outlook.de": "outlook", "hotmail.com": "outlook",
    "hotmail.de": "outlook", "live.com": "outlook", "live.de": "outlook", "msn.com": "outlook", "yahoo.com": "yahoo",
    "yahoo.de": "yahoo", "ymail.com": "yahoo", "icloud.com": "icloud", "me.com": "icloud", "mac.com": "icloud", "gmx.de": "gmx",
    "gmx.net": "gmx", "gmx.at": "gmx", "gmx.ch": "gmx", "web.de": "web.de", "zoho.com": "zoho", "zohomail.eu": "zoho",
    "fastmail.com": "fastmail", "fastmail.fm": "fastmail"}
HELP = {  # provider -> how to get an app password (or switch on IMAP)
    "gmail": "https://myaccount.google.com/apppasswords",
    "outlook": "https://support.microsoft.com/en-us/account-billing/how-to-get-and-use-app-passwords-5896ed9b-4263-e681-128a-a6f2979a7944",
    "yahoo": "https://help.yahoo.com/kb/SLN15241.html", "icloud": "https://support.apple.com/en-us/102654",
    "gmx": "https://hilfe.gmx.net/pop-imap/einschalten.html", "web.de": "https://hilfe.web.de/pop-imap/einschalten.html",
    "zoho": "https://www.zoho.com/mail/help/adminconsole/two-factor-authentication.html",
    "fastmail": "https://www.fastmail.help/hc/en-us/articles/360058752854-App-passwords"}
OPEN = ["applied", "assessment", "interview", "offer"]
SKIP = re.compile(r"alert|jobnotification|jobs-listings|newsletter|jobagent|marketing|digest|recommend|new jobs similar|"
                  r"\bhiring\b(?![ -]team)|\bapply (?:now )?to\b|registration|registrierung|password|passwort|kennwort|verif|verfiz|"
                  r"benutzerkonto|zugangsdaten|career portal|karriereportal|candidate account|user account|ihr zugang|"
                  r"career opportunities|job opportunities|jobquill", re.I)  # own alerts and test mails
SIGNALS = [  # first hit wins; specific phrases only: receipts say "unfortunately" / "interview" too
    ("rejected", r"other candidates|other applicants|not been selected|not selected|decided not to|decided against|"
                 r"move forward with other|chosen to move forward|pursue other|position has been filled|regret to inform|"
                 r"(?:not (?:be )?able|unable) to (?:offer you|shortlist you|consider your|proceed with your)|not (?:be )?moving forward|"
                 r"absagen|eine absage|anderweitig|nicht (?:weiter |mehr )?berücksichtig|anderen? (?:bewerber|kandidat)|"
                 r"nicht weiter verfolg|nicht in die engere|für eine?n? andere|nicht für (?:sie|dich) entschieden|"
                 r"andere bewerbungen vor|(?:stärker|besser) (?:entsprechen|passen)|nicht berücksichtigen|keine zusage|"
                 r"unsuccessful|not to proceed|will not be (?:moving|proceeding)|"
                 r"(?:won't|will not|cannot|can't|unable to|not (?:be )?able to) (?:be able to )?invite you|nicht (?:\w+ ){0,6}einladen|"
                 r"nicht (?:\w+ ){0,5}fortfahren|keine stelle (?:\w+ ){0,4}anbieten|(?:leider|bereits|inzwischen|schon) besetzt"),
    ("offer", r"job offer(?!s)|offer letter|offer of employment|offer you (?:the|a) (?:position|role|job)|vertragsangebot|"
              r"arbeitsvertrag|ihnen die stelle anbieten|anbieten zu können"),
    ("interview", r"invit(?:e|ing) you (?:to|for) (?:an? |the )?(?:\w+ )?(?:interview|call|conversation|meeting|chat)|"
                  r"interview invitation(?!s)|invitation (?:to|for) (?:an? |the )?(?:\w+ )?interview|"
                  r"schedule (?:an?|the|your) (?:\w+ )?(?:interview|call)|availability for (?:an? |the )?(?:\w+ )?(?:interview|call)|"
                  r"book a (?:time|slot)|calendly|vorstellungsgespräch|kennenlerngespräch|telefoninterview|"
                  r"einladung (?:zu[mr]?|für) |(?:zu|zum) (?:einem )?\w*(?:gespräch|interview)|"
                  r"vorgespräch|telefonat\b|kennenlern-?(?:termin|call)|termin (?:buchen|vereinbaren)|terminvorschl|einen termin|"
                  r"interview confirmation|confirm(?:ation of)? (?:the|your) interview|phone (?:screen|call|interview)|"
                  r"video (?:call|interview)|outlook\.office\.com/book|teams\.microsoft\.com/l/meetup|zoom\.us/j/|meet\.google\.com/"),
    ("assessment", r"online assessment|assessment (?:link|center|centre)|coding (?:challenge|test|task)|online[- ]test|"
                   r"hackerrank|codility|take-home|testgorilla|complete (?:the|an|a) (?:\w+ )?(?:test|assessment)"),
    # something only the person can do: an unfinished or unconfirmed application, a request for documents (portal account
    # mails, "confirm your e-mail address", are told apart below by ACCOUNT: one action for the company)
    ("action", r"(?:in order to|please|still need to) complete your (?:job )?application|complete and submit your (?:job )?application|continue (?:to apply|your application)|draft (?:job )?application|"
               r"schließen sie ihre bewerbung ab|bewerbung (?:noch )?nicht abgeschlossen|confirm your application|"
               r"bestätigen sie (?:bitte )?ihre bewerbung|aktion erforderlich|action required|unterlagen nachreichen|"
               r"fehlende unterlagen|missing documents|send us your (?:cv|resume|documents)|persönlichkeitstest|personality test|"
               r"fragebogen|questionnaire"),
    ("acknowledgement", r"received your application|application (?:has been|was) (?:successfully )?(?:received|submitted|sent)|"
                        r"successfully submitted|eingangsbestätigung|eingegangen|eingang (?:ihrer|deiner) bewerbung|"
                        r"bewerbung erhalten|reviewing your (?:documents|application)|now processing|weitergeleitet"),
    ("thanks", r"thank(?:s| you) for (?:applying|your application|your interest)|dank für (?:ihre|deine) bewerbung|"
               r"dank für (?:ihr|dein) interesse"),
]
# imaplib and smtplib check no certificate by default: anyone on the same network could pose as the mail server and
# read the app password. This context checks the server's certificate and name.
TLS = ssl.create_default_context()
# A sentence that only says what might happen is no news: an either/or ("sei es mit einer Entscheidung oder einer
# Einladung zu einem Gespräch"), or a condition on being chosen ("if you are shortlisted, we will invite you"). A polite
# condition ("if you are available", "falls Sie Interesse haben") is a real invitation, and a booking link always is.
EITHER = re.compile(r"\b(?:whether|either|sei es|entweder)\b")
IF = re.compile(r"\b(?:if|in case|should|once|falls|sofern|sollten?|im falle|wenn)\b")
CHOSEN = re.compile(r"shortlist|short-list|select|success|chosen|suitab|qualif|\bmatch|next (?:stage|round)|engere|ausgewählt|"
                    r"auswahl|in ?frage|berücksichtig|passend|überzeug|geeignet|eignung|nächste runde")
BOOKING = re.compile(r"calendly|outlook\.office\.com/book|teams\.microsoft\.com/l/meetup|zoom\.us/j/|meet\.google\.com/|"
                     r"book a (?:time|slot)|termin (?:buchen|vereinbaren)")


def iffy(sentence: str) -> bool:
    return not BOOKING.search(sentence) and bool(EITHER.search(sentence) or IF.search(sentence) and CHOSEN.search(sentence))


def settings() -> dict:
    s = pf.read("preferences").get("email") or {}
    return {"address": s.get("address", ""), "provider": s.get("provider", "gmail"), "imap": s.get("imap", ""),
            "smtp": s.get("smtp", ""), "port": s.get("port", 0)}


def detect(address: str) -> dict:
    """Provider and servers from the address. Unknown domains get the usual imap./smtp.<domain> guess, to be checked."""
    domain = address.rpartition("@")[2].strip().lower()
    provider = DOMAINS.get(domain)
    if provider:
        return {"provider": provider, "imap": "", "smtp": "", "port": 0, "help": HELP.get(provider, "")}
    return {"provider": "custom", "imap": f"imap.{domain}" if domain else "", "smtp": f"smtp.{domain}" if domain else "", "port": 465, "help": ""}


def hosts():
    s = settings()
    imap, smtp, port = PROVIDERS.get(s["provider"], ("", "", 465))
    return s["imap"] or imap, s["smtp"] or smtp, int(s["port"] or port)


def configured() -> bool:
    return bool(settings()["address"] and secrets.get("email_password"))


def _imap():
    imap_host, _, _ = hosts()
    m = imaplib.IMAP4_SSL(imap_host, ssl_context=TLS, timeout=60)
    m.login(settings()["address"], secrets.get("email_password").replace(" ", ""))
    return m


def test() -> str:
    """Reads the inbox once, then sends a short mail to the address itself, so both directions are proven."""
    m = _imap()
    m.logout()
    address = settings()["address"]
    send(address, "JobQuill is connected", "This test shows JobQuill can read replies and send email from this address. Nothing else to do.")
    return f"Connected. JobQuill can read your inbox, and a test email went to {address}."


def _smtp(host, port):
    if port == 465:
        return smtplib.SMTP_SSL(host, port, timeout=60, context=TLS)
    s = smtplib.SMTP(host, port, timeout=60)
    s.starttls(context=TLS)
    return s


STRONG = ("offer", "interview", "assessment", "action")  # a next step for the person
IMPORTANT = STRONG + ("unclear",)  # worth an alert at once


def classify(text):
    """First signal, 'unclear' (a next step next to a receipt or a rejection: the person reads it) or None."""
    text = " ".join(x for x in re.split(r"(?<=[.!?])\s+", text.lower()) if not iffy(x))
    hits = [name for name, rx in SIGNALS if re.search(rx, text)]
    if any(h in STRONG for h in hits) and any(h in ("rejected", "acknowledgement") for h in hits):  # "thanks" opens invites too
        return "unclear"
    return {"thanks": "acknowledgement"}.get(hits[0], hits[0]) if hits else None


LEGAL = re.compile(r"\(.*?\)|&|\b(?:gmbh|ggmbh|mbh|ag|se|kg|kgaa|gbr|ug|ohg|e\.\s?v|inc|ltd|llc|plc|co|corp|university|"
                   r"steuerberatungsgesellschaft|nhs trust)\b\.?", re.I)
# words that don't tell one employer from another (alone, in a company name or a sender address)
GENERIC = set("""deutsche deutschland germany europe international group gruppe holding stiftung institut institute universität
hochschule gesellschaft technische service services solutions consulting digital engineering systems software technologies
technology media management partner partners recruit recruiter recruiting talent tech european global karriere career
careers jobs mail noreply reply bewerbung team
""".split())
# a job or application number, whole: (5706), V000077301, ABC00048X1
REF = re.compile(r"(?<![\w.:/])(?!(?:19|20)\d\d\b)[a-z]{0,3}\d{4,}[a-z0-9]*(?![\d.:])")
# ... named as one: "Kennziffer: 5706", "Job-ID 12345", "Ref. 2026-117"
LABELED = re.compile(r"(?:\bid|kennziffer|\bref(?:erenz|erence)?|job-?\s?nr|stellen-?\s?(?:nr|nummer)|position|requisition|\breq|"
                     r"ausschreibungs?-?\s?(?:nr|nummer)|job number|vacancy)\b\W{0,4}([a-z]{0,3}\d{4,}[a-z0-9]*)")
ABOUT = re.compile(r"bewerbung|bewerber|application|applying|applied|candida|interview|gespräch|vorstellung|recruit|hiring[ -]team")


def company_key(name):
    """The name without legal form, brackets or "&": "Taxpro Augsburg GmbH & Co. KG" → "Taxpro Augsburg"."""
    return " ".join(LEGAL.sub(" ", name or "").split())


def _words(s):
    return [w for w in re.findall(r"[a-zäöüß0-9]+", (s or "").lower()) if len(w) >= 4 and w not in GENERIC]


def _role_words(s):
    """A role's words: "Engineering" or "Software" tell two roles apart, though they tell no employers apart."""
    return {w for w in re.findall(r"[a-zäöüß0-9]+", (s or "").lower()) if len(w) >= 4}


def _data(r):
    return json.loads(r.get("data") or "{}")


def _by_name(m, rows):
    """Applications the mail names, strongest first: the company in the sender or subject; the role title in the subject
    (a job board's receipt: "your application for Machine Learning Engineer …"); the company anywhere in the mail (a
    footer can name a parent company, a board's receipt other companies' jobs: their roles then decide); the most
    distinctive company words in the sender (karriere@nbs-kramer.de is Nordwest Brandschutz … Kramer)."""
    word = lambda w, where: re.search(rf"(?<![\w.-]){re.escape(w.lower())}(?![\w-])", where)
    key = lambda r: company_key(r["company"]).lower()
    text, head = (company_key(x) for x in (m["text"], m["head"]))  # the mail without "&", legal forms, brackets: like the keys
    full = [r for r in rows if key(r) and word(key(r), text)]
    if any(word(key(r), head) for r in full):
        return [r for r in full if word(key(r), head)]
    role = lambda r: " ".join(re.sub(r"\(.*?\)", " ", r["role"] or "").lower().split())
    subject = " ".join(re.sub(r"\(.*?\)", " ", m["subject"].lower()).split())
    named = [r for r in rows if len(role(r)) >= 15 and role(r) in subject]
    if named:
        return [r for r in named if len(role(r)) == max(len(role(x)) for x in named)]  # the fullest title named
    if full:
        return full
    frm = m["frm"].lower()
    labels = [x for x in re.split(r"[@.\-_+]", email.utils.parseaddr(frm)[1]) if len(x) >= 4]
    # a short name in capitals (OVX, ZFR) counts in the sender; a long name word (BrightVenture) in the subject too
    names = lambda r: {*_words(company_key(r["company"])), *map(str.lower, re.findall(r"\b[A-Z]{3}\b", company_key(r["company"])))}
    # an address part is the word or starts with it (schneiderlindner, nova for NovaForward); "slack" isn't "Lack"
    starts = lambda w, x: w == x or min(len(w), len(x)) >= 5 and (x.startswith(w) or w.startswith(x))
    count = [sum(1 for w in names(r) if word(w, frm) or len(w) >= 6 and word(w, m["head"]) or any(starts(w, x) for x in labels))
             for r in rows]
    best = max(count, default=0)
    return [r for r, n in zip(rows, count) if best and n == best]


def _narrow(m, hits):
    """One of several applications at a company, by how much of its role the mail names; a close call stays open."""
    if len(hits) == 1:
        return hits[0], []
    text = set(re.findall(r"[a-zäöüß0-9]+", f"{m['subject']} {m['text'][:2000]}".lower()))
    share = lambda r: len(_role_words(r["role"]) & text) / max(len(_role_words(r["role"])), 1)
    ranked = sorted(hits, key=share, reverse=True)
    if share(ranked[0]) >= .6 and share(ranked[0]) - share(ranked[1]) >= .2:
        return ranked[0], []
    return None, ranked


def posting_text(r: dict) -> str:
    """The posting an application was written for: its archived copy, else the stored description."""
    for p in (home() / "applications" / r["folder"] / "posting.md" if r.get("folder") else None,
              home() / "jobs" / "desc" / f"{r['job_key']}.txt" if r.get("job_key") else None):
        if p and p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")
    return ""


def _ids(text: str) -> set:
    """The job numbers in a mail's or a posting's text: ones with letters (V000077301, ABC0004938) or named as one. A bare
    number there is as likely the postcode or a phone number of a contact block."""
    return {r for r in REF.findall(text) if re.search(r"[a-z]", r)} | set(LABELED.findall(text))


def _refs_of(r: dict) -> set:
    """Job and reference numbers known for an application: learned from mails, in its link, in its posting."""
    return {*_data(r).get("refs", []), *REF.findall((_data(r).get("url") or "").lower()), *_ids(posting_text(r).lower())}


def _by_posting(m, cands):
    """Several applications at one company: the ones whose posting has a number the mail has too (a position ID in its
    body). A number every one of them has tells nothing."""
    refs = _ids(m["text"][:4000])
    known = [_refs_of(r) for r in cands]
    telling = refs - set.intersection(*known) if refs and known else set()
    hits = [r for r, k in zip(cands, known) if telling & k]
    return hits if 0 < len(hits) < len(cands) else cands


def _by_date(m, cands):
    """A receipt comes right after the application: the one candidate sent 0–3 days before the mail."""
    try:
        d = date.fromisoformat(m["date"])
        near = [r for r in cands if r.get("date_applied") and 0 <= (d - date.fromisoformat(r["date_applied"][:10])).days <= 3]
    except ValueError:
        return cands
    return near if len(near) == 1 else cands


def link(m, rows, signal=None):
    """(the application a mail is about, []) or (None, the applications it could be about). In order: a job number seen
    before (or in the posting link), what the mail names, the conversation it belongs to. Among one company's
    applications a close call is settled by a number in the mail that only one of their postings has, then, for a
    receipt only (it changes no status), by the one application sent 0–3 days before. The conversation comes last:
    some recruiting systems put all of a person's mails into one Gmail thread, so it only decides for a mail that names
    no application ("Re: Termin") or names several, one of them in that thread."""
    refs = set(REF.findall(m["subject"].lower()))
    by_ref = [r for r in rows if refs & ({*_data(r).get("refs", [])} | set(REF.findall((_data(r).get("url") or "").lower())))]
    if by_ref:
        return _narrow(m, by_ref)
    named = _by_name(m, rows) if ABOUT.search(m["text"]) else []  # "Welcome to pCloud" names no application
    row, cands = _narrow(m, named) if named else (None, [])
    if row:
        return row, []
    if cands:
        cands = _by_posting(m, cands)
        if signal == "acknowledgement":
            cands = _by_date(m, cands)
        if len(cands) == 1:
            return cands[0], []
    thread = [r for r in (cands or rows) if m.get("thread") and m["thread"] in _data(r).get("threads", [])]
    return _narrow(m, thread) if thread else (None, cands)


def reasons(m, cands) -> list[dict]:
    """[{id, why}] for the applications a mail could be about, so the person can tell them apart: a number only that
    posting shares with the mail, how much of the role the mail names, when it was sent."""
    refs = _ids(m["text"][:4000])
    shared = [refs & _refs_of(r) for r in cands]
    everywhere = set.intersection(*shared) if shared else set()
    words = set(re.findall(r"[a-zäöüß0-9]+", f"{m['subject']} {m['text'][:2000]}".lower()))
    out = []
    for r, same in zip(cands, shared):
        why = []
        if same - everywhere:
            why.append("same job number " + max(same - everywhere, key=len).upper())
        role = _role_words(r["role"])
        if role:
            why.append(f"{round(100 * len(role & words) / len(role))}% of the role's words")
        try:
            days = (date.fromisoformat(m["date"]) - date.fromisoformat((r.get("date_applied") or "")[:10])).days
            why.append("applied after this mail" if days < 0 else "applied the same day" if days == 0 else
                       "applied the day before" if days == 1 else f"applied {days} days before")
        except ValueError:
            pass
        out.append({"id": r["id"], "why": " · ".join(why)})
    return out


def _body(msg):
    try:
        part = msg.get_body(("plain", "html"))
        body = part.get_content() if part else ""
        if part and part.get_content_subtype() == "html":
            body = re.sub(r"(?s)<(style|script).*?</\1>|<[^>]+>", " ", body)
        return html.unescape(body)
    except (LookupError, ValueError):
        return ""


def gmail() -> bool:
    return hosts()[0] == "imap.gmail.com"


def gmail_link(address: str, company: str, since: str, thread: str = "") -> str:
    """Opens that conversation in Gmail, or (no thread yet) a search for the company's mails since the date.
    ?authuser= keeps the #part; /u/<email>/ drops it and lands on the inbox."""
    q = f'"{company_key(company)}" after:{(since or "").replace("-", "/")} -from:me'
    return (f"https://mail.google.com/mail/?authuser={urllib.parse.quote(address)}#"
            + (f"all/{thread}" if thread else "search/" + urllib.parse.quote(q, safe="")))


def _folders(m):
    """Where replies can be: all mail (Gmail's labels and filters can keep them out of the inbox) or, without such a
    folder, the inbox; plus spam. Found by their standard flags, so a German "Alle Nachrichten" works too."""
    found = {}
    for line in m.list()[1] or []:
        hit = re.match(rb'\(([^)]*)\) (?:"[^"]*"|NIL) (.+)$', line or b"")
        for flag in (rb"\All", rb"\Junk"):
            if hit and flag in hit[1].split():
                found[flag] = hit[2].decode()
    return [found.get(rb"\All", "INBOX")] + ([found[rb"\Junk"]] if rb"\Junk" in found else [])


CAP = 1500  # mails per folder and check: an hourly check sees a day or two, one after two weeks away a few hundred


def _get(m, uid, thrid: bool) -> dict:
    """One mail as the checks read it."""
    head, raw = m.uid("FETCH", uid, "(X-GM-THRID BODY.PEEK[])" if thrid else "(BODY.PEEK[])")[1][0]
    thread = re.search(rb"X-GM-THRID (\d+)", head)
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    frm, subject, body = str(msg["From"] or ""), " ".join(str(msg["Subject"] or "").split()), " ".join(_body(msg).split())
    sent = getattr(msg["Date"], "datetime", None)
    return {"id": str(msg["Message-ID"] or uid.decode()), "frm": frm, "subject": subject,
            "thread": format(int(thread[1]), "x") if thread else "",
            "date": sent.date().isoformat() if sent else date.today().isoformat(), "msg": msg, "snippet": body[:300],
            "body": body[:4000], "head": f"{frm} {subject}".lower(), "text": f"{frm} {subject} {body}".lower()}


def fetch(since: date):
    thrid = gmail()  # Gmail's conversation id, for the "Open in Gmail" link
    m = _imap()
    out = []
    try:
        for folder in _folders(m):
            m.select(folder, readonly=True)
            query = f'SINCE {since.strftime("%d-%b-%Y")} NOT FROM "{settings()["address"]}"'  # not the person's own mails
            uids = (m.uid("SEARCH", None, query)[1][0] or b"").split()
            out += [_get(m, uid, thrid) for uid in uids[-CAP:]]
        return out
    finally:
        m.logout()


def fetch_ids(ids: list[str]) -> list[dict]:
    """The mails with these Message-IDs (Needs you › Check these again). The ids come from other people's mails: only
    what can't break the IMAP command is kept of them."""
    thrid, m, out = gmail(), _imap(), []
    try:
        for folder in _folders(m):
            m.select(folder, readonly=True)
            for msg_id in ids:
                clean = re.sub(r'[^\w.@<>+=$%&!#*/?^`{|}~-]', "", msg_id)
                if clean:
                    out += [_get(m, uid, thrid) for uid in (m.uid("SEARCH", None, f'HEADER Message-ID "{clean}"')[1][0] or b"").split()]
        return out
    finally:
        m.logout()


MONTHS = ["jan", "feb", "m[aä]r", "apr", "ma[iy]", "jun", "jul", "aug", "sep", "o[ck]t", "nov", "de[cz]"]
MON = "(" + "|".join(MONTHS) + r")[a-zä]*\.?"
TIME = r"\D{1,30}?\b(\d{1,2})[:.](\d{2})(?!\d)(?:\s*([ap])\.?\s?m\b)?"  # "um 14:30", "at 2:00 pm"
DATES = [  # a date written one of these ways, then its time: (pattern, the groups holding year, month, day)
    (r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})", (3, 2, 1)),  # 28.10.2026
    (r"\b(\d{1,2})/(\d{1,2})/(\d{4})", (3, 2, 1)),  # 26/10/2026 (10/26/2026 when the middle can't be a month)
    (r"\b(\d{4})-(\d{2})-(\d{2})", (1, 2, 3)),  # 2026-10-26
    (rf"\b(\d{{1,2}})\.?(?:st|nd|rd|th)? {MON},? (\d{{4}})", (3, 2, 1)),  # 26. Oktober 2026, 26 October 2026
    (rf"\b{MON} (\d{{1,2}})(?:st|nd|rd|th)?,? (\d{{4}})", (3, 1, 2)),  # October 26, 2026
]


def _times(text: str):
    """(position, (year, month, day, hour, minute)) for every date with a time in the text."""
    for n, (pattern, (yi, mi, di)) in enumerate(DATES):
        for hit in re.finditer(pattern + TIME, text):
            g = hit.groups()
            month, day = g[mi - 1], int(g[di - 1])
            month = int(month) if month.isdigit() else next(i + 1 for i, x in enumerate(MONTHS) if re.match(x, month))
            if n == 1 and month > 12 >= day:
                day, month = month, day
            hour, ampm = int(g[-3]), g[-1]
            if ampm:
                hour = hour % 12 + (12 if ampm == "p" else 0)
            yield hit.start(), (int(g[yi - 1]), month, day, hour, int(g[-2]))


def when(m) -> str:
    """The appointment a mail sets, as local "YYYY-MM-DDTHH:MM": a calendar invite's start, else the first date with a
    time in the text that is still ahead ("28.10.2026 at 10:00", "26. Oktober 2026 um 14:00 Uhr", "October 26, 2026 at
    2:00 PM", "26/10/2026 14:00", "2026-10-26 14:00")."""
    for part in m["msg"].walk() if m.get("msg") else []:
        if part.get_content_type() in ("text/calendar", "application/ics"):
            ics = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
            hit = re.search(r"^DTSTART[^:\n]*:(\d{8}T\d{4})\d*(Z?)", ics, re.M)
            if hit:
                t = datetime.strptime(hit[1], "%Y%m%dT%H%M")
                if hit[2]:  # UTC: to this computer's time
                    t = t.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
                return t.isoformat(timespec="minutes")
    now = datetime.now()
    for _, (y, mo, d, h, mi) in sorted(_times(m["text"])):
        try:
            t = datetime(y, mo, d, h, mi)
        except ValueError:
            continue
        if t > now:
            return t.isoformat(timespec="minutes")
    return ""


def _state() -> dict:
    p = home() / "mail_state.json"
    return {"seen": [], "last": "", **(json.loads(p.read_text(encoding="utf-8")) if p.exists() else {})}


def _save(st: dict):
    (home() / "mail_state.json").write_text(json.dumps(st), encoding="utf-8")


def record(con, row: dict, m: dict, signal: str, status: str | None = None, at: str = ""):
    """Adds the mail to the application's replies and learns its conversation and job numbers, so the next mail about it
    links at once. row is updated in place."""
    data = _data(row)
    replies = data.setdefault("replies", [])
    if not any(x.get("date") == m["date"] and x.get("subject") == m["subject"] for x in replies):  # read again: once
        was = {"was": row["status"]} if status and status != row["status"] else {}  # what "Wrong? Link it" puts back
        replies.append({"date": m["date"], "from": m["frm"], "subject": m["subject"], "signal": signal or "", **was})
    if m.get("thread"):
        data["thread"] = m["thread"]  # the latest conversation: My applications links to it
        data["threads"] = sorted({*data.get("threads", []), m["thread"]})
    data["refs"] = sorted({*data.get("refs", []), *REF.findall(m["subject"].lower())})
    if at:
        data["interview_at"] = at
    row["data"], row["status"] = json.dumps(data), status or row["status"]
    con.execute("update applications set status=?, data=? where id=?", (row["status"], row["data"], row["id"]))


def unrecord(con, row: dict, m: dict):
    """A mail that was linked to the wrong application leaves it: its reply, conversation, job numbers and time go, and
    the status it set goes back (unless the person changed it since)."""
    data = _data(row)
    mine = [x for x in data.get("replies", []) if x.get("date") == m["date"] and x.get("subject") == m["subject"]]
    data["replies"] = [x for x in data.get("replies", []) if x not in mine]
    if m.get("thread") in data.get("threads", []):
        data["threads"] = [t for t in data["threads"] if t != m["thread"]]
        data["thread"] = data["threads"][-1] if data["threads"] else ""
    data["refs"] = sorted(set(data.get("refs", [])) - set(REF.findall(m["subject"].lower())))
    if m.get("at") and data.get("interview_at") == m["at"]:
        data.pop("interview_at")
    was = next((x["was"] for x in mine if "was" in x and x.get("signal") == row["status"]), None)
    row["data"], row["status"] = json.dumps(data), was or row["status"]
    con.execute("update applications set status=?, data=? where id=?", (row["status"], row["data"], row["id"]))


STEPS = ["pending", "applied", "assessment", "interview", "offer"]  # a reply only moves an application forward
CLOSED = ["rejected", "withdrawn", "no_response", "ghosted"]


def step(row: dict, signal) -> str | None:
    """The status a mail moves its application to: forward, or to rejected. A closed application that comes back is the
    person's call (the mail is open in Needs you)."""
    new = signal if signal in ("rejected", "offer", "interview", "assessment") else None
    if new and (row["status"] in CLOSED or new != "rejected" and STEPS.index(new) <= (STEPS + [row["status"]]).index(row["status"])):
        return None
    return new
SAYS = {"interview": "Interview", "assessment": "Test or assessment", "offer": "Offer", "action": "Action needed",
        "unclear": "Please read this reply", "rejected": "Rejection", "acknowledgement": "Receipt"}


def who(frm: str) -> str:
    name, address = email.utils.parseaddr(frm)
    return name or address


# A mail that isn't anyone's to answer: a mail without a known phrase from such an address is filed, not shown
NOREPLY = re.compile(r"no-?reply|do-?not-?reply|donotreply|mailer-daemon|postmaster|bounce|newsletter", re.I)
# A team's or a recruiting system's mailbox (karriere@, jobs@, system@successfactors…): its receipts are filed by themselves
MAILBOX = re.compile(r"(?:karriere|career|careers|jobs?|recruit\w*|hr|humanresources|bewerbung\w*|talent\w*|system|info|team|"
                     r"hiring|people|personal|application\w*|notification\w*|mail|office|kontakt|contact|service|support|"
                     r"ats|workday|personio|greenhouse|lever|smartrecruiters|stellen\w*)[._+-]?\d*@")
# Portal accounts: one action for the company ("confirm your e-mail address"), never "which application?"
ACCOUNT = re.compile(r"registr|e-?mail-?adresse (?:zu )?bestätig|confirm your (?:e-?mail|account|registration)|"
                     r"verify your (?:e-?mail|account|identity)|activate your account|konto aktivier|benutzerkonto|"
                     r"candidate account|zugangsdaten", re.I)
TRIAGE_MAX = 10  # AI reads per check: a busy inbox after two weeks away doesn't use up the plan


class Triage(pf.Model):
    signal: str = pf.Field(description="interview | assessment | offer | action | rejected | acknowledgement | other")
    app_id: int = pf.Field(description="the id of the application the mail is about, from the list; 0 when none fits or unsure")
    when: str = pf.Field(description='the appointment it sets, as "YYYY-MM-DDTHH:MM" (the mail\'s own time), or ""')
    summary: str = pf.Field(description="what the mail says or asks the person, in at most 20 words")


def triage(m, cands: list[dict]) -> dict | None:
    """One AI read (economy level) of a mail the rules can't sort for sure: what it is, which application, when, in one
    line. The mail is untrusted data. None when the AI is switched off for mail, missing or fails: the rules decide."""
    from jobquill.core import ai, prompts
    from jobquill.core.apply import untrusted
    prefs = pf.Preferences.model_validate(pf.read("preferences"))
    if not prefs.ai_mail:
        return None
    apps = "\n".join(f"{r['id']} | {r['company']} | {r['role']} | applied {r.get('date_applied') or '?'} | job numbers: "
                     f"{', '.join(sorted(_refs_of(r))[:6]) or '-'}" for r in cands) or "(none known)"
    text = f"From: {m['frm']}\nDate: {m['date']}\nSubject: {m['subject']}\n\n{m.get('body') or m['text']}"[:5000]
    try:
        out = ai.answer(prompts.build("triage_mail", applications=apps, mail=untrusted(text), today=date.today().isoformat()),
                        Triage, ai.chosen(prefs.agent, "economy"), timeout=180).model_dump()
    except Exception:  # its own errors, a missing command, a broken answer: the rules decide
        return None
    out["signal"] = out["signal"] if out["signal"] in ("interview", "assessment", "offer", "action", "rejected", "acknowledgement") else None
    out["app_id"] = out["app_id"] if out["app_id"] in {r["id"] for r in cands} else 0
    try:
        out["when"] = _local(datetime.fromisoformat(out["when"])).isoformat(timespec="minutes") if out["when"] else ""
    except ValueError:
        out["when"] = ""
    return out


def _local(t: datetime) -> datetime:
    """This computer's time without an offset: how every interview time is kept and compared."""
    return t.astimezone().replace(tzinfo=None) if t.tzinfo else t


def _note(row, signal, new, company):
    """What JobQuill did with a mail it settled by itself (Needs you › Filed by itself)."""
    said = SAYS.get(signal) or "Reply"
    if row:
        return f"{said} → {row['company']} · {row['role']}" + (f": status set to {new}" if new else "")
    return f"{said} from {company}: not sure which application, nothing to do"


def check_replies() -> dict:
    """Mails since the last check. Each one about an application is logged in mail_items: settled by itself (filed:
    receipts, clear rejections and status changes) or open in Needs you (an interview, test, offer, something to do, a
    reply nobody could read for sure, or a rejection whose application is unclear). Nothing is dropped silently; every
    open one with a next step is pushed at once. The AI reads what the rules can't (TRIAGE_MAX per check)."""
    st = _state()
    since = date.fromisoformat(st["last"]) - timedelta(days=1) if st["last"] else date.today() - timedelta(days=14)
    with db() as con:
        rows = [dict(r) for r in con.execute("select * from applications")]
    address = settings()["address"] if gmail() else ""
    out = {"changed": [], "to_check": [], "budget": TRIAGE_MAX}
    for m in fetch(since):
        if m["id"] in st["seen"]:
            continue
        st["seen"].append(m["id"])
        try:
            _one(m, rows, address, out)
        except Exception as e:  # one mail that breaks something never stops the others, and is still shown
            with db() as con:
                con.execute("insert or ignore into mail_items(msg_id, date, frm, subject, thread, signal, snippet, candidates, body,"
                            " summary) values (?,?,?,?,?,?,?,?,?,?)",
                            (m["id"], m["date"], m["frm"], m["subject"], m.get("thread", ""), "unclear", m.get("snippet", ""), "[]",
                             (m.get("body") or "")[:4000], f"JobQuill couldn't read this mail ({e.__class__.__name__}): please read it"))
            out["to_check"].append(m["subject"])
        _save(st)  # as it goes: a crash later never sends these again
    st["seen"] = st["seen"][-2000:]
    st["last"] = date.today().isoformat()
    st["ok_at"] = datetime.now().isoformat(timespec="minutes")
    _save(st)
    return {"changed": out["changed"], "unclear": out["to_check"], "to_check": out["to_check"]}


def person(frm: str) -> bool:
    """A person's own address, not a portal's or a team mailbox's: what they write can ask something of the reader."""
    return not NOREPLY.search(frm) and not MAILBOX.match(email.utils.parseaddr(frm)[1].lower())


def _one(m: dict, rows: list[dict], address: str, out: dict):
    """One mail of check_replies: logged in mail_items, its application updated, pushed when it needs the person."""
    from jobquill.core import alerts
    signal = classify(m["text"])
    row, cands = link(m, rows, signal)
    if SKIP.search(m["head"]) and signal not in IMPORTANT + ("rejected",):  # alerts, newsletters, account housekeeping;
        return  # a rejection never: its job title can hold a skip word ("Dose Verification", "Marketing")
    if not row and not cands and not ABOUT.search(m["head"]) and signal not in IMPORTANT:
        return  # not about an application
    if signal == "acknowledgement" and person(m["frm"]):
        signal = None  # a recruiter's "thanks for your application" can go on with a question: read, not filed
    at, summary, pick = "", "", None
    if out["budget"] and (signal is None or signal in ("interview", "assessment") and not when(m)
                          or not row and cands and (signal in IMPORTANT or signal == "rejected")):
        out["budget"] -= 1
        t = triage(m, [row] if row else cands)
        if t:
            summary, at = t["summary"], t["when"]
            signal = signal or t["signal"]
            pick = next((r for r in cands if r["id"] == t["app_id"]), None)  # a suggestion, never a decision
    if signal is None and not NOREPLY.search(m["frm"]) and (row or cands or ABOUT.search(m["head"])):
        signal = "unclear"  # a person wrote it and nobody could read it for sure: the person reads it
    at = at or (when(m) if signal in ("interview", "assessment", "unclear") else "")
    new = step(row, signal) if row else None
    open_ = signal in IMPORTANT or (not row and signal == "rejected")
    companies = {company_key(r["company"]).lower() for r in cands}
    name = row["company"] if row else cands[0]["company"] if len(companies) == 1 else who(m["frm"])
    ranked = reasons(m, [pick] + [r for r in cands if r is not pick] if pick else cands)
    if pick:
        ranked[0]["why"] = " · ".join(filter(None, ["the AI's pick", ranked[0]["why"]]))
    with db() as con:
        if row:
            record(con, row, m, signal, new, at)
        con.execute("insert or ignore into mail_items(msg_id, date, frm, subject, thread, signal, snippet, app_id, candidates, at,"
                    " body, summary, auto, done, note) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (m["id"], m["date"], m["frm"], m["subject"], m["thread"], signal or "", m["snippet"],
                     row["id"] if row else None, json.dumps(ranked), at, (m.get("body") or "")[:4000], summary,
                     int(not open_), int(not open_), "" if open_ else _note(row, signal, new, name)))
    if signal in IMPORTANT or new == "rejected":
        event = signal if signal in ("interview", "assessment", "offer") else "rejection" if signal == "rejected" else "action"
        alerts.push(event, f"JobQuill: {SAYS[signal]} · {name}",
                    "\n".join(x for x in (m["subject"], summary, row and row["role"], at and f"When: {at.replace('T', ' ')}",
                                          open_ and "Open JobQuill › Needs you.",
                                          address and gmail_link(address, name, m["date"], m["thread"])) if x),
                    key=f"{name}|{m['subject']}".lower())
    if new:
        out["changed"].append(f"{name}: {new}")
    if open_:
        out["to_check"].append(f"{name}: {m['subject']}")


def recheck() -> int:
    """Open mails without an application, and ones filed without one, read again by their Message-ID and linked with
    what JobQuill knows now (Needs you › Check these again). Returns how many found their application."""
    with db() as con:
        items = {r["msg_id"]: dict(r) for r in con.execute("select * from mail_items where app_id is null and (done=0 or auto=1)")}
        rows = [dict(r) for r in con.execute("select * from applications")]
    found = 0
    for m in fetch_ids(list(items)) if items else []:
        it = items.get(m["id"])
        if not it:
            continue
        signal = it["signal"] or None
        row, cands = link(m, rows, signal)
        new = step(row, signal) if row else None
        with db() as con:
            if row:
                found += 1
                record(con, row, m, signal, new, it["at"] or "")
            done = it["done"] or (row is not None and signal not in IMPORTANT)
            con.execute("update mail_items set app_id=?, candidates=?, body=?, done=?, auto=?, note=? where id=?",
                        (row["id"] if row else None, json.dumps(reasons(m, cands)), (m.get("body") or "")[:4000], int(done),
                         int(done), _note(row, signal, new, row["company"] if row else who(m["frm"])) if done else "", it["id"]))
    return found


def reminders():
    """Interview reminders, each once per interview time: the evening before (the first check after 17:00), the morning
    of the day (from 08:00, when more than 3 hours are left) and in the last 3 hours. Through the outbox: one that
    doesn't arrive at once is sent again, so it's marked as sent when queued."""
    from jobquill.core import alerts
    now = datetime.now()
    with db() as con:
        for r in con.execute("select id, company, role, data from applications where data like '%interview_at%'").fetchall():
            data = json.loads(r["data"])
            try:
                t = _local(datetime.fromisoformat(data["interview_at"]))  # one stored with an offset still reminds
            except (KeyError, TypeError, ValueError):
                continue
            sent, hhmm, left = data.setdefault("reminded", {}), t.strftime("%H:%M"), t - now
            for kind, due, title in (
                    ("day", t.date() == now.date() + timedelta(days=1) and now.hour >= 17, f"interview tomorrow at {hhmm}"),
                    ("morning", t.date() == now.date() and now.hour >= 8 and left > timedelta(hours=3), f"interview today at {hhmm}"),
                    ("soon", timedelta(0) < left <= timedelta(hours=3),
                     f"interview at {hhmm} (in {max(1, round(left.total_seconds() / 3600))} hours)")):
                if due and sent.get(kind) != data["interview_at"]:
                    alerts.push("interview", f"JobQuill: {title} · {r['company']}",
                                "\n".join(x for x in (r["role"], t.strftime("%a %d.%m.%Y %H:%M")) if x),
                                key=f"reminder|{r['id']}|{kind}|{data['interview_at']}")
                    sent[kind] = data["interview_at"]
                    con.execute("update applications set data=? where id=?", (json.dumps(data), r["id"]))


def digest():
    """The morning summary (the first check from 08:00): mail to check, interviews this week, yesterday's rejections and
    receipts. Nothing to say, nothing sent."""
    from jobquill.core import alerts
    st, now = _state(), datetime.now()
    if now.hour < 8 or st.get("digest") == now.date().isoformat():
        return
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    with db() as con:
        items = con.execute("select * from mail_items where done=0 order by date").fetchall()
        apps = [dict(r) for r in con.execute("select company, role, data from applications")]
    lines = [f"Needs you: {len(items)} open"] + [f"• {i['date']} {who(i['frm'])}: {i['subject']}" for i in items[:15]] if items else []
    soon = sorted(((d["interview_at"], a) for a in apps for d in [_data(a)]
                   if now.isoformat() <= d.get("interview_at", "") <= (now + timedelta(days=7)).isoformat()), key=lambda x: x[0])
    lines += ["Interviews this week:"] + [f"• {t.replace('T', ' ')} {a['company']} · {a['role']}" for t, a in soon] if soon else []
    said = [(a["company"], x.get("signal")) for a in apps for x in _data(a).get("replies", []) if x.get("date") == yesterday]
    lines += [f"Rejections yesterday: {', '.join(c for c, s in said if s == 'rejected')}"] if any(s == "rejected" for _, s in said) else []
    lines += [f"Receipts yesterday: {sum(s == 'acknowledgement' for _, s in said)}"] if any(s == "acknowledgement" for _, s in said) else []
    if not lines or alerts.send("digest", "JobQuill: this morning", "\n".join(lines)):
        st["digest"] = now.date().isoformat()
        _save(st)


def watchdog(loud: bool = False):
    """Email is connected but the check last worked more than 3 hours ago: the task is put back and the check runs now.
    loud (the daily search: the computer is on, so the hourly task should have run) also tells the person; at app start
    it only catches up (the computer was probably off)."""
    from jobquill.core import alerts, scheduler
    ok = _state().get("ok_at", "")
    if not configured() or (ok and datetime.fromisoformat(ok) > datetime.now() - timedelta(hours=3)):
        return
    if loud:
        alerts.push("action", "JobQuill's email check isn't running",
                    f"Replies to your applications weren't read since {ok.replace('T', ' ') or 'it was set up'}. It's set up "
                    "again now; if this comes back, check Settings › Email.", key="watchdog")
    scheduler.ensure()
    hourly()


def hourly():
    """The scheduled run. Alerts that didn't arrive go first. A check that fails says so (every 6 hours at most): a
    silent stop would hide interview mails."""
    from jobquill.core import alerts
    alerts.flush()
    result = ""
    if configured():
        try:
            st = _state()
            if not st.get("rechecked"):  # once, after the 0.12 update: the mails asked about read with the new linking
                st["rechecked"] = True
                _save(st)
                recheck()
            result = check_replies()
        except Exception as e:
            st = _state()
            if not st.get("broken_at") or datetime.fromisoformat(st["broken_at"]) < datetime.now() - timedelta(hours=6):
                alerts.send("reply", "JobQuill can't read your email",
                            f"{e}\nReplies to your applications aren't being checked until this works again (Settings › Email).", force=True)
                st["broken_at"] = datetime.now().isoformat(timespec="minutes")
                _save(st)
            result = f"email check failed: {e}"
    reminders()
    digest()
    return result


STALE_DAYS = 60  # the original pipeline's sweep: an open application with no news for 60 days


def sweep() -> list[str]:
    """Applications with no reply, follow-up or status news for STALE_DAYS move to no_response. Returns the companies."""
    cutoff, today, moved = (date.today() - timedelta(days=STALE_DAYS)).isoformat(), date.today().isoformat(), []
    with db() as con:
        for r in con.execute("select * from applications where status in ('applied', 'interview')").fetchall():
            seen = [r["date_applied"] or ""] + [x.get("date", "") for x in json.loads(r["data"] or "{}").get("replies", [])]
            seen += [r["follow_up"]] if r["follow_up"] and r["follow_up"] <= today else []  # a sent follow-up sets it to that day
            if max(seen) and max(seen) < cutoff:
                con.execute("update applications set status='no_response' where id=?", (r["id"],))
                moved.append(r["company"])
    return moved


def verification_links(domain: str, minutes: int = 60) -> list[str]:
    """Links pointing at the portal's own domain in mails from the last hour: only those, so a mail can't steer elsewhere."""
    base = ".".join(domain.lower().split(".")[-2:])
    out = []
    for m in fetch(date.today() - timedelta(days=1)):
        when = getattr(m["msg"]["Date"], "datetime", None)
        if when and when.timestamp() < datetime.now().timestamp() - minutes * 60:
            continue
        part = m["msg"].get_body(("html", "plain"))
        urls = {html.unescape(u) for u in re.findall(r"https?://[^\s\"'<>]+", part.get_content() if part else "")}
        out += [u for u in urls if (urllib.parse.urlsplit(u).hostname or "").endswith(base)]
    return out




def send(to: str, subject: str, body: str, files: list[Path] = ()) -> str:
    s = settings()
    msg = email.message.EmailMessage(email.policy.default.clone(max_line_length=998))
    name = pf.read("profile").get("basics", {}).get("name", "")
    msg["From"], msg["To"], msg["Subject"] = email.utils.formataddr((name, s["address"])), to, subject
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg.set_content(body)
    for f in files:
        kind = (mimetypes.guess_type(f.name)[0] or "application/octet-stream").split("/")
        msg.add_attachment(Path(f).read_bytes(), maintype=kind[0], subtype=kind[1], filename=Path(f).name)
    _, smtp_host, port = hosts()
    with _smtp(smtp_host, port) as server:
        server.login(s["address"], secrets.get("email_password").replace(" ", ""))
        server.send_message(msg)
    return f"sent to {to}"
