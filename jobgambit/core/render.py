"""CV and cover letter PDFs (and PNG previews) with Typst. No LaTeX, no system fonts needed.

Python decides what the document says: which sections, in which order, titles in the document's language,
formatted dates. The Typst engine (templates/engine) only draws it, in the chosen design.
"""
import contextlib
import hashlib
import json
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from functools import lru_cache
from pathlib import Path

import typst
from babel.dates import format_date
from ruamel.yaml import YAML

from jobgambit.core import countries
from jobgambit.core.data import home

TPL = Path(__file__).parent.parent / "templates"

STYLE = {  # every design starts from these and overrides some
    "layout": "single", "side": "left", "header": "left", "font": "Libertinus Serif", "heading_font": "Libertinus Serif",
    "size": 10.5, "leading": 0.55, "margin_x": 1.7, "margin_y": 1.4, "accent": "#1F3A5F", "muted": "#555555",
    "side_fill": "#F1F3F6", "section_style": "rule", "bullet": "•", "date_pos": "right", "group": True,
    "skills_style": "lines", "languages_style": "text", "photo": "circle", "paper": "a4",
}
ORDER = ["summary", "experience", "projects", "education", "skills", "languages", "certifications", "awards",
         "publications", "volunteering", "courses", "custom", "interests"]
KINDS = {"experience": "entries", "projects": "entries", "volunteering": "entries", "courses": "entries",
         "education": "education", "certifications": "items", "awards": "items", "publications": "items",
         "skills": "skills", "languages": "languages", "interests": "tags", "summary": "text"}
CEFR = {"a1": 1, "a2": 2, "b1": 3, "b2": 4, "c1": 5, "c2": 5, "native": 5, "mother": 5, "muttersprache": 5, "fluent": 5,
        "full professional": 5, "professional": 4, "limited": 2, "elementary": 1, "basic": 1, "grundkenntnisse": 1}


@lru_cache
def designs() -> dict[str, dict]:
    return {p.stem: YAML(typ="safe").load(p) for p in sorted((TPL / "designs").glob("*.yaml"))}


@lru_cache
def labels(lang: str) -> dict:
    en = YAML(typ="safe").load(TPL / "labels" / "en.yaml")
    path = TPL / "labels" / f"{lang}.yaml"
    return {**en, **(YAML(typ="safe").load(path) if path.exists() else {})}


def style_for(design: str, country: str = "", overrides: dict | None = None) -> dict:
    d = designs().get(design) or designs()["classic"]
    return {**STYLE, "paper": countries.conventions(country)["paper"], **d.get("style", {}), **(overrides or {})}


def _month(value: str, present: str) -> str:
    value = str(value or "")
    if value.lower() == "present":
        return present
    y, _, m = value.partition("-")
    return f"{m}/{y}" if m else y


def span(item: dict, present: str) -> str:
    a, b = _month(item.get("start"), present), _month(item.get("end"), present)
    return f"{a} – {b}" if a and b else a or b


def cv_inputs(cv: dict, design: str = "classic", lang: str = "en", country: str = "", show_photo=None, settings=None) -> dict:
    """cv: a profile dump, optionally with "summary" (the text to show) and "hidden" (section keys left out).
    settings: designer choices {order, sidebar, titles{key: text}, hidden[], max_items{key: n}, max_bullets, show_photo,
    show_personal, style{...}}."""
    settings = settings or {}
    style = style_for(design, country, settings.get("style"))
    show_photo = settings.get("show_photo", show_photo)
    meta = designs().get(design) or designs()["classic"]
    L = labels(lang)
    conv = countries.conventions(country)
    b = cv.get("basics", {})
    contact = [x for x in (b.get("email"), b.get("phone"), b.get("location")) if x]
    contact += [v for v in (b.get("links") or {}).values() if v]
    personal = []
    personal_ok = settings.get("show_personal", conv["dob"] != "avoid")
    if b.get("dob") and personal_ok:
        personal.append(f"{L['dob']}: {b['dob']}")
    if b.get("nationality") and personal_ok:
        personal.append(f"{L['nationality']}: {b['nationality']}")
    want_photo = conv["photo"] != "avoid" if show_photo is None else show_photo
    photo = b.get("photo") if want_photo and b.get("photo") else None

    hidden = set(cv.get("hidden", [])) | set(settings.get("hidden", []))
    titles = settings.get("titles", {})
    sidebar = settings.get("sidebar", meta.get("sidebar", []))
    sections = []
    for key in settings.get("order") or meta.get("order") or ORDER:
        if key in hidden:
            continue
        if key == "custom":
            for c in cv.get("custom", []):
                if c.get("entries"):
                    sections.append({"key": "custom", "title": c["title"], "kind": "entries", "group": False, "place": "main",
                                     "items": [_entry(e, L["present"]) for e in c["entries"]]})
            continue
        if key == "summary":
            text = cv.get("summary") or next(iter((cv.get("summaries") or {}).values()), "")
            items = text
        elif key == "skills":
            items = [{"cat": k, "items": v} for k, v in (cv.get("skills") or {}).items() if v]
        elif key == "languages":
            items = [{"name": l["name"], "level": l.get("level", ""),
                      "dots": next((n for k, n in CEFR.items() if k in l.get("level", "").lower()), 3)} for l in cv.get("languages", [])]
        elif KINDS.get(key) == "entries":
            items = [_entry(e, L["present"]) for e in cv.get(key, [])]
        elif key == "education":
            items = [{**{k: e.get(k, "") for k in ("degree", "school", "location", "details")}, "dates": span(e, L["present"])}
                     for e in cv.get("education", []) if not e.get("only_for") or cv.get("job_kind") in e["only_for"]]
        elif KINDS.get(key) == "items":
            items = [{**{k: i.get(k, "") for k in ("name", "issuer", "link")}, "date": _month(i.get("date"), L["present"])}
                     for i in cv.get(key, [])]
        else:
            items = cv.get(key, [])
        cap = (settings.get("max_items") or {}).get(key)
        if cap and isinstance(items, list):
            items = items[:cap]
        if settings.get("max_bullets") and KINDS.get(key) == "entries":
            items = [{**e, "bullets": e["bullets"][:settings["max_bullets"]]} for e in items]
        if items:
            sections.append({"key": key, "title": titles.get(key) or L.get(key, key.title()), "kind": KINDS.get(key, "tags"),
                             "items": items, "group": bool(style.get("group")) and key == "experience",
                             "place": "side" if key in sidebar else "main"})
    return {"lang": lang, "labels": L, "style": style,
            "header": {"name": b.get("name", ""), "headline": b.get("headline", ""), "contact": contact,
                       "personal": personal, "photo": photo},
            "sections": sections}


def _entry(e: dict, present: str) -> dict:
    return {"title": e.get("title", ""), "org": e.get("org", ""), "unit": e.get("unit", ""), "location": e.get("location", ""),
            "link": e.get("link", ""), "dates": span(e, present), "bullets": [x["text"] for x in e.get("bullets", []) if x.get("text")]}


def _compile(template: str, inputs: dict, fmt: str = "pdf", ppi: int = 110, files: tuple = ()) -> bytes | list[bytes]:
    """PDF: bytes; PNG/SVG: one bytes per page.
    Typst may only read files inside its root, so the template and any photo are copied into a temp folder."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        shutil.copy(TPL / "engine" / template, tmp / template)
        for name in files:
            src = home() / "profile" / Path(name).name  # a bare name: a profile can't point outside its folder
            if src.is_file():
                shutil.copy(src, tmp / src.name)
        out = typst.compile(str(tmp / template), root=str(tmp), format=fmt, ppi=ppi,
                            font_paths=[str(TPL / "fonts")], sys_inputs={"data": json.dumps(inputs, ensure_ascii=False)})
    return out if fmt == "pdf" or isinstance(out, list) else [out]


def cv_pdf(cv: dict, design="classic", lang="en", country="", fmt="pdf", show_photo=None, settings=None, ppi=110):
    inputs = cv_inputs(cv, design, lang, country, show_photo, settings)
    photo = inputs["header"]["photo"]
    if photo:
        inputs["header"]["photo"] = Path(photo).name
    return _compile("cv.typ", inputs, fmt, ppi, files=(photo,) if photo else ())


def cv_pages(cv: dict, design="classic", lang="en", country="", fmt="svg", settings=None, ppi=110) -> list[bytes]:
    """Every page as an SVG or PNG."""
    return cv_pdf(cv, design, lang, country, fmt, settings=settings, ppi=ppi)


def page_limit(design: str, settings: dict, country: str) -> int:
    """The look's own limit, else the design's, else what the country expects."""
    return settings.get("pages") or designs().get(design, {}).get("pages") or countries.conventions(country)["cv_pages"]


def letter_pdf(letter: dict, basics: dict, design="classic", lang="en", country="", settings=None) -> bytes:
    """letter: {recipient: [lines], subject, salutation, paragraphs: [..], closing?, place?}."""
    L = labels(lang)
    conv = countries.conventions(country)
    place = letter.get("place") or (basics.get("location") or "").split(",")[0]
    when = format_date(date.today(), format="long", locale=lang)
    sig = (settings or {}).get("signature") or ""
    inputs = {"lang": lang, "style": style_for(design, country, (settings or {}).get("style")),
              "layout": (settings or {}).get("letter_layout") or ("formal" if conv["letter"] == "formal" else "modern"),
              "name": basics.get("name", ""),
              "contact": [x for x in (basics.get("address") or basics.get("location"), basics.get("phone"), basics.get("email")) if x],
              "recipient": letter.get("recipient", []), "place_date": f"{place}, {when}" if place else when,
              "subject": letter.get("subject", ""), "salutation": letter.get("salutation") or L["salutation"],
              "paragraphs": [x for x in letter.get("paragraphs", []) if x.strip()], "closing": letter.get("closing") or L["closing"],
              "signature": Path(sig).name if sig else None}
    from io import BytesIO
    from pypdf import PdfReader
    for k in (1, 0.95, 0.9, 0.85):  # a letter is one page: shrink a little until it fits (9.5 pt at the smallest)
        pdf = _compile("letter.typ", {**inputs, "fit": k}, files=(sig,) if sig else ())
        if len(PdfReader(BytesIO(pdf)).pages) == 1:
            break
    return pdf  # still 2 pages at 0.85: the text is too long, and the one-page check reports it


# ---------------------------------------------------------------- saved looks: a base design + designer settings
def presets() -> dict:
    """{name: {design, settings}} from <data>/profile/designs.yaml."""
    path = home() / "profile" / "designs.yaml"
    return (YAML(typ="safe").load(path) or {}) if path.exists() else {}


def save_preset(name: str, design: str, settings: dict):
    from jobgambit.core import profile as pf
    items = presets()
    items[name] = {"design": design, "settings": settings}
    pf.write("designs", items)


def resolve(name: str) -> tuple[str, dict]:
    """A preset name or a design id -> (design id, settings)."""
    p = presets().get(name)
    if p:
        return p["design"], p.get("settings") or {}
    return (name if name in designs() else "classic"), {}


# ---------------------------------------------------------------- gallery thumbnails: page 1 at 72 ppi, cached on disk
THUMB_PPI = 72
_warming = threading.Lock()


def target_country() -> str:
    from jobgambit.core import profile as pf
    targets = pf.Preferences.model_validate(pf.read("preferences")).targets
    return targets[0].code if targets else ""


def thumb_key(name: str, country: str | None = None) -> str:
    """Changes whenever the thumbnail would look different: profile, photo, country, design file, engine, look settings."""
    from jobgambit.core import profile as pf
    design, settings = resolve(name)
    mtime = lambda p: p.stat().st_mtime if p.exists() else 0  # noqa: E731
    parts = [mtime(pf.folder() / pf.FILES["profile"]), max((mtime(p) for p in pf.folder().glob("photo.*")), default=0),
             target_country() if country is None else country, mtime(TPL / "designs" / f"{design}.yaml"),
             mtime(TPL / "engine" / "cv.typ"), THUMB_PPI, json.dumps(settings, sort_keys=True)]
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:12]


def thumb(name: str) -> Path:
    """Page 1 of the person's own CV in a design or saved look, rendered once per thumb_key."""
    from jobgambit.core import profile as pf
    fid = name if name in designs() else "look-" + hashlib.sha1(name.encode()).hexdigest()[:10]
    country = target_country()
    path = home() / "cache" / f"thumb-{fid}-{thumb_key(name, country)}.png"
    if path.exists():
        return path
    design, settings = resolve(name)
    png = cv_pages(pf.profile().model_dump(), design, "en", country, "png", settings, THUMB_PPI)[0]
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_bytes(png)
    with contextlib.suppress(OSError):  # another thread got there first, or a file is being served
        tmp.replace(path)
        for old in path.parent.glob(f"thumb-{fid}-*.png"):
            if old != path and old.stem.rsplit("-", 1)[0] == f"thumb-{fid}":
                old.unlink()
    tmp.unlink(missing_ok=True)
    return path


def warm_thumbs():
    """Renders missing thumbnails in the background (app start, profile, photo or targets changed)."""
    def run():
        with _warming:
            for old in (home() / "cache").glob("*.png"):  # previews from before thumbnails
                if not old.name.startswith("thumb-"):
                    old.unlink(missing_ok=True)
            with ThreadPoolExecutor(4) as pool:
                for name in [*designs(), *presets()]:
                    pool.submit(thumb, name)  # a failure only means that thumbnail renders on request
    threading.Thread(target=run, daemon=True).start()
