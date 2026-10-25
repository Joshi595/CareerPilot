"""Turn what the person already has into a draft profile: a CV file, a LinkedIn data export or a JSON Resume.

LinkedIn and JSON Resume map field by field (no AI). A CV file becomes text here; the AI structures it.
"""
import csv
import io
import json
import re
import zipfile
from datetime import datetime

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def cv_text(data: bytes, filename: str) -> str:
    """PDF (pypdf) / DOCX (its XML, stdlib) / TXT -> plain text. Empty string when nothing readable comes out (e.g. a scan)."""
    low = filename.lower()
    if low.endswith((".txt", ".md")):
        return data.decode("utf-8", "replace")
    if low.endswith(".docx"):
        import xml.etree.ElementTree as ET
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
        return "\n".join("".join(t.text or "" for t in p.iter(f"{ns}t")) for p in root.iter(f"{ns}p")).strip()
    from pypdf import PdfReader
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages).strip()


def _month(value: str) -> str:
    """LinkedIn 'Oct 2022' / '2022' / ISO '2022-10-01' -> '2022-10' / '2022'."""
    value = str(value or "").strip()  # a bare year may come as a number
    if not value:
        return ""
    if re.match(r"\d{4}-\d{2}", value):
        return value[:7]
    m = re.match(r"([A-Za-z]{3})\w* (\d{4})", value)
    return f"{m[2]}-{MONTHS.get(m[1].lower(), 1):02d}" if m else value[:4]


def _bullets(text: str, prefix: str) -> list[dict]:
    lines = [l.strip(" -•*\t") for l in re.split(r"\n+", text or "") if l.strip(" -•*\t")]
    return [{"id": f"{prefix}_b{i}", "text": line, "tags": []} for i, line in enumerate(lines, 1)]


def from_linkedin(data: bytes) -> dict:
    """LinkedIn: Settings > Data privacy > Get a copy of your data. The ZIP holds one CSV per section."""
    z = zipfile.ZipFile(io.BytesIO(data))
    names = {n.rsplit("/", 1)[-1].lower(): n for n in z.namelist()}

    def rows(name):
        if name.lower() not in names:
            return []
        text = z.read(names[name.lower()]).decode("utf-8-sig")
        if text.startswith("Notes:"):  # some exports start with a notes paragraph before the header
            text = text.split("\n\n", 1)[-1]
        return list(csv.DictReader(io.StringIO(text)))

    me = (rows("Profile.csv") or [{}])[0]
    email = next((r.get("Email Address", "") for r in rows("Email Addresses.csv") if r.get("Primary") == "Yes"), "")
    phone = next((r.get("Number", "") for r in rows("PhoneNumbers.csv")), "")
    p = {"basics": {"name": f"{me.get('First Name', '')} {me.get('Last Name', '')}".strip(),
                    "headline": me.get("Headline", ""), "location": me.get("Geo Location", ""),
                    "email": email, "phone": phone, "links": {}},
         "summaries": {"main": me["Summary"]} if me.get("Summary") else {}}
    p["experience"] = [{"id": f"exp{i}", "title": r.get("Title", ""), "org": r.get("Company Name", ""),
                        "location": r.get("Location", ""), "start": _month(r.get("Started On")),
                        "end": _month(r.get("Finished On")) or "present", "bullets": _bullets(r.get("Description"), f"exp{i}")}
                       for i, r in enumerate(rows("Positions.csv"), 1)]
    p["education"] = [{"id": f"edu{i}", "degree": r.get("Degree Name", ""), "school": r.get("School Name", ""),
                       "start": _month(r.get("Start Date")), "end": _month(r.get("End Date")),
                       "details": " ".join(filter(None, [r.get("Notes"), r.get("Activities")]))}
                      for i, r in enumerate(rows("Education.csv"), 1)]
    p["projects"] = [{"id": f"proj{i}", "title": r.get("Title", ""), "link": r.get("Url", ""),
                      "start": _month(r.get("Started On")), "end": _month(r.get("Finished On")),
                      "bullets": _bullets(r.get("Description"), f"proj{i}")}
                     for i, r in enumerate(rows("Projects.csv"), 1)]
    p["certifications"] = [{"id": f"cert{i}", "name": r.get("Name", ""), "issuer": r.get("Authority", ""),
                            "date": _month(r.get("Started On")), "link": r.get("Url", "")}
                           for i, r in enumerate(rows("Certifications.csv"), 1)]
    skills = [r.get("Name", "") for r in rows("Skills.csv") if r.get("Name")]
    p["skills"] = {"Skills": skills} if skills else {}
    p["languages"] = [{"name": r.get("Name", ""), "level": r.get("Proficiency", "")} for r in rows("Languages.csv")]
    return p


def from_json_resume(data: bytes) -> dict:
    """jsonresume.org schema -> profile."""
    r = json.loads(data)
    b = r.get("basics", {})
    loc = b.get("location", {})
    links = {(x.get("network") or "link").lower(): x.get("url", "") for x in b.get("profiles", [])}
    if b.get("url"):
        links["website"] = b["url"]

    def entries(items, prefix, title, org):
        return [{"id": f"{prefix}{i}", "title": x.get(title, ""), "org": x.get(org, ""), "location": x.get("location", ""),
                 "start": _month(x.get("startDate")), "end": _month(x.get("endDate")) or "present", "link": x.get("url", ""),
                 "bullets": ([{"id": f"{prefix}{i}_s", "text": x["summary"], "tags": []}] if x.get("summary") else [])
                 + [{"id": f"{prefix}{i}_b{j}", "text": h, "tags": []} for j, h in enumerate(x.get("highlights", []), 1)]}
                for i, x in enumerate(items, 1)]

    return {
        "basics": {"name": b.get("name", ""), "headline": b.get("label", ""), "email": b.get("email", ""),
                   "phone": b.get("phone", ""), "links": links,
                   "location": ", ".join(filter(None, [loc.get("city"), loc.get("countryCode")]))},
        "summaries": {"main": b["summary"]} if b.get("summary") else {},
        "experience": entries(r.get("work", []), "exp", "position", "name"),
        "volunteering": entries(r.get("volunteer", []), "vol", "position", "organization"),
        "projects": entries(r.get("projects", []), "proj", "name", "entity"),
        "education": [{"id": f"edu{i}", "degree": " ".join(filter(None, [e.get("studyType"), e.get("area")])),
                       "school": e.get("institution", ""), "start": _month(e.get("startDate")),
                       "end": _month(e.get("endDate")), "details": e.get("score", "")}
                      for i, e in enumerate(r.get("education", []), 1)],
        "skills": {s.get("name", "Skills"): s.get("keywords", []) for s in r.get("skills", [])},
        "languages": [{"name": l.get("language", ""), "level": l.get("fluency", "")} for l in r.get("languages", [])],
        "certifications": [{"id": f"cert{i}", "name": c.get("name", ""), "issuer": c.get("issuer", ""),
                            "date": _month(c.get("date")), "link": c.get("url", "")}
                           for i, c in enumerate(r.get("certificates", []), 1)],
        "awards": [{"id": f"award{i}", "name": a.get("title", ""), "issuer": a.get("awarder", ""), "date": _month(a.get("date"))}
                   for i, a in enumerate(r.get("awards", []), 1)],
        "publications": [{"id": f"pub{i}", "name": x.get("name", ""), "issuer": x.get("publisher", ""),
                          "date": _month(x.get("releaseDate")), "link": x.get("url", "")}
                         for i, x in enumerate(r.get("publications", []), 1)],
        "interests": [i.get("name", "") for i in r.get("interests", [])],
    }


def to_json_resume(p: dict) -> dict:
    """Profile -> jsonresume.org schema, for use in other tools."""
    b = p.get("basics", {})

    def work(items, org_key, title_key):
        return [{org_key: e.get("org", ""), title_key: e.get("title", ""), "location": e.get("location", ""),
                 "startDate": e.get("start", ""), "endDate": "" if e.get("end") == "present" else e.get("end", ""),
                 "url": e.get("link", ""), "highlights": [re.sub(r"\*\*", "", x["text"]) for x in e.get("bullets", [])]}
                for e in items]

    return {
        "basics": {"name": b.get("name", ""), "label": b.get("headline", ""), "email": b.get("email", ""),
                   "phone": b.get("phone", ""), "summary": next(iter(p.get("summaries", {}).values()), ""),
                   "location": {"city": b.get("location", "")},
                   "profiles": [{"network": k, "url": v} for k, v in b.get("links", {}).items()]},
        "work": work(p.get("experience", []), "name", "position"),
        "volunteer": work(p.get("volunteering", []), "organization", "position"),
        "projects": [{"name": e.get("title", ""), "url": e.get("link", ""),
                      "highlights": [x["text"] for x in e.get("bullets", [])]} for e in p.get("projects", [])],
        "education": [{"institution": e.get("school", ""), "studyType": e.get("degree", ""),
                       "startDate": e.get("start", ""), "endDate": e.get("end", ""), "score": e.get("details", "")}
                      for e in p.get("education", [])],
        "skills": [{"name": k, "keywords": v} for k, v in p.get("skills", {}).items()],
        "languages": [{"language": l["name"], "fluency": l.get("level", "")} for l in p.get("languages", [])],
        "certificates": [{"name": c["name"], "issuer": c.get("issuer", ""), "date": c.get("date", "")}
                         for c in p.get("certifications", [])],
        "meta": {"generator": "JobQuill", "lastModified": datetime.now().isoformat(timespec="seconds")},
    }
