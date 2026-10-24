"""Quality gate for a tailored CV or letter: banned phrases, invented numbers, unbacked reach claims,
keyword coverage, page count, extractable text (ATS) and layout. Ported from the original pipeline's check.py."""
import datetime
import re

from careerpilot.core import profile as pf, prompts

# Spelled-out counts read as digits ("drei Jahre" = "3 years"); one/ein are left out, they are mostly articles.
NUM_WORDS = {w: str(n) for n, pair in enumerate([("two", "zwei"), ("three", "drei"), ("four", "vier"), ("five", "fünf"), ("six", "sechs"),
                                                 ("seven", "sieben"), ("eight", "acht"), ("nine", "neun"), ("ten", "zehn"),
                                                 ("eleven", "elf"), ("twelve", "zwölf")], 2) for w in pair}
# Reach claims name no number, so the number check can't see them: (wording in the document, wording that backs it)
ADOPTION = [(r"\bused daily\b|\btäglich (?:genutzt|im einsatz)", r"\bdaily\b|\btäglich"),
            (r"\b(?:company|org(?:ani[sz]ation)?)[- ]?wide\b|\bacross the (?:\w+ ){0,2}org(?:ani[sz]ations?)?\b|\bunternehmensweit",
             r"\b(?:company|org(?:ani[sz]ation)?)[- ]?wide\b|\bacross the\b[^.;]{0,40}\b(?:org|company|business)|\bunternehmensweit"),
            (r"\badopted by\b|\bübernommen von\b", r"\badopt|\bübernommen"),
            (r"\bstandard across\b|\bzum standard\b", r"\bstandard")]


# Keys and tokens that must never leave the computer (tools/release.py scans every release for these too): private
# keys, Slack/Telegram/GitHub/Anthropic/OpenAI/AWS/Google.
SECRET = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|hooks\.slack\.com/services/T\w+/|xox[abprs]-[\w-]{10,}|"
                    r"\b\d{8,10}:AA[\w-]{33}\b|\bgh[pousr]_[A-Za-z0-9]{36}|github_pat_\w{20,}|\bsk-(?:ant-|proj-)?[\w-]{32,}|"
                    r"\bAKIA[0-9A-Z]{16}\b|\bAIza[\w-]{35}\b")
KEY_HEADS = re.compile(r"-----BEGIN [A-Z ]+-----|\bssh-(?:rsa|ed25519|dss)\b|\becdsa-sha2-nistp\d+\b")
RUN = re.compile(r"(?<![\w/+=.-])[A-Za-z0-9+/_]{32,}={0,2}(?![\w/+=.-])")  # one unbroken token of 32+ characters


def secret_shaped(text: str) -> bool:
    """A key, token or random-looking string (base64 or hex with several digits; a long CamelCase word has few)."""
    return bool(SECRET.search(text) or KEY_HEADS.search(text)) or any(
        sum(c.isdigit() for c in m[0]) >= 4 and any(c.isalpha() for c in m[0]) for m in RUN.finditer(text))


def banned_phrases(style: str = "") -> list[str]:
    text = style or pf.read("style") or prompts.load("default_style")
    section = text.split("## Banned phrases", 1)[-1] if "## Banned phrases" in text else ""
    return [l[2:].strip().lower() for l in section.splitlines() if l.startswith("- ") and l[2:].strip()]


def find_banned(text, phrases):
    low = text.lower()
    return [p for p in phrases if (re.search(rf"\b{re.escape(p)}\b", low) if p[0].isalnum() else p in low)]


def numbers(text):
    """Number claims, normalised: thousands separators dropped, leading zeros dropped, 4-digit years ignored."""
    # thousands separators: 14,000 and German 14.000 -> 14000; a decimal like 0.970 stays
    text = re.sub(r"(?:(?<=[1-9])|(?<=\d0))[,.](?=\d{3}\b)", "", text)
    text = re.sub(r"\b(" + "|".join(NUM_WORDS) + r")\b", lambda m: NUM_WORDS[m[1].lower()], text, flags=re.I)
    out = set()
    for n in re.findall(r"\d+(?:\.\d+)?", text):
        if re.fullmatch(r"(19|20)\d\d", n):
            continue
        out.add(n if "." in n else str(int(n)))
    return out


def tech_tokens(text):
    """Tool-shaped words (PyTorch, ROS2, NEWS2): an inner capital or a letter+digit mix."""
    words = re.findall(r"[A-Za-z][A-Za-z0-9+#]*", text)
    return {w for w in words if len(w) > 1 and (any(c.isupper() for c in w[1:]) or any(c.isdigit() for c in w))}


def fact_check(text, source):
    """(invented numbers -> block, unknown tool-like tokens -> warn)."""
    low = source.lower()
    return sorted(numbers(text) - numbers(source)), sorted(t for t in tech_tokens(text) if t.lower() not in low)


def adoption_claims(text, source):
    return [m[0] for claim, backing in ADOPTION if not re.search(backing, source, re.I) for m in re.finditer(claim, text, re.I)]


def coverage(text, keywords):
    low = " ".join(text.lower().split())
    hit = [k for k in keywords if k.lower() in low]
    return hit, [k for k in keywords if k not in hit]


def layout(pdf):
    """(fails, warns) from text positions: stranded headings or titles at a page end, early page ends,
    a single word alone on the last line of a bullet."""
    from pypdf import PdfReader
    fails, warns = [], []
    pages = PdfReader(pdf).pages
    for i, page in enumerate(pages):
        h, chunks = float(page.mediabox.height), []

        def v(text, cm, tm, font, size):
            if text.strip():
                x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                chunks.append((y, x, size * (tm[2] ** 2 + tm[3] ** 2) ** .5 * abs(cm[3]),
                               "Bold" in str((font or {}).get("/BaseFont", "")), text.strip()))
        page.extract_text(visitor_text=v)
        if not chunks:
            continue
        lines = {}
        for c in chunks:
            key = next((k for k in lines if abs(k - c[0]) < 3), c[0])
            lines.setdefault(key, []).append(c)
        rows = [sorted(lines[k], key=lambda c: c[1]) for k in sorted(lines, reverse=True)]
        body = sorted(c[2] for c in chunks)[len(chunks) // 2]
        left = min(c[1] for c in chunks)
        last = rows[-1]
        end_gap = min(c[0] for c in last) / h
        if i < len(pages) - 1:
            first = last[0]
            if max(c[2] for c in last) > body * 1.1:
                fails.append(f"page {i+1} ends with a section heading: {first[4]!r}")
            elif first[3] and first[1] <= left + 1 and not first[4].endswith(":"):
                fails.append(f"page {i+1} ends with a title separated from its lines: {first[4]!r}")
            if end_gap > 0.25:
                fails.append(f"page {i+1} ends {end_gap:.0%} early")
        elif len(pages) > 1 and end_gap > 0.65:
            warns.append(f"last page is {end_gap:.0%} empty")
        for r, nxt in zip(rows, rows[1:] + [None]):
            words = [w for w in " ".join(c[4] for c in r).split() if any(ch.isalnum() for ch in w)]
            if r[0][1] > left + 5 and len(words) == 1 and (nxt is None or nxt[0][1] <= left + 5):
                warns.append(f"page {i+1}: single word alone on a line: {words[0]!r}")
    return fails, warns


def source_text(posting: str = "") -> str:
    """Everything a document may state: the master profile, the posting, today's date."""
    path = pf.folder() / pf.FILES["profile"]
    master = path.read_text(encoding="utf-8") if path.exists() else ""
    return master + "\n" + posting + datetime.date.today().strftime(" %d.%m.%Y %Y-%m-%d")


def asked_missing(n: int = 15) -> list[tuple[str, int]]:
    """Over every archived application: the posting keywords the master CV never mentions, most asked first.
    What to learn (or add to the CV, if it's true) next."""
    import json
    from collections import Counter
    from ruamel.yaml import YAML
    from careerpilot.core.data import home
    master, count = source_text().lower(), Counter()
    for d in (home() / "applications").glob("*/"):
        fit, job = d / "fit.json", d / "job.yaml"  # job.yaml: applications imported from the original pipeline
        try:
            kws = (json.loads(fit.read_text(encoding="utf-8")) if fit.exists()
                   else YAML(typ="safe").load(job.read_text(encoding="utf-8")) if job.exists() else {}).get("keywords") or []
        except Exception:
            continue
        count.update({k.lower() for k in coverage(master, [str(k) for k in kws])[1]})
    return count.most_common(n)


def check(text: str, posting: str = "", keywords=(), pdf=None, max_pages: int = 0) -> dict:
    """{"fails": [...], "warns": [...], "coverage": [hit, missing]}. Any fail blocks the document."""
    fails, warns = [], []
    if pdf:
        from pypdf import PdfReader
        pages = PdfReader(pdf).pages
        text = "\n".join(p.extract_text() for p in pages)
        if max_pages and len(pages) > max_pages:
            fails.append(f"more than {max_pages} page(s): {len(pages)}")
        if len(text.split()) < 50:
            fails.append("almost no extractable text: applicant-tracking software would see an empty document")
        f, w = layout(pdf)
        fails += f
        warns += w
    bad = find_banned(text, banned_phrases())
    if bad:
        fails.append("banned phrases: " + ", ".join(bad))
    if secret_shaped(text):  # an AI with tools, tricked by a posting, could copy one from this computer into a line
        fails.append("looks like a password, key or token: don't send this, and change that line")
    src = source_text(posting)
    invented, unknown = fact_check(text, src)
    if invented:
        fails.append("numbers not in your profile or the posting: " + ", ".join(invented))
    if unknown:
        warns.append("tool names not in your profile or the posting: " + ", ".join(unknown))
    reach = adoption_claims(text, src)
    if reach:
        warns.append("reach claims your profile doesn't make: " + ", ".join(reach))
    hit, miss = coverage(text, keywords)
    return {"fails": fails, "warns": warns, "coverage": [hit, miss]}
