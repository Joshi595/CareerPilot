"""One search run: every board for each target country, the remote feeds, and company career sites; then
dedupe, score, store, fetch missing descriptions, re-check open postings. Ported from the original jobs.py search().

Run from the app (Board → Search now), from the daily schedule (`jobgambit search`), or tests.
"""
import html
import importlib
import itertools
import json
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from babel import Locale
from rapidfuzz import fuzz

from jobgambit.core import countries, posting, score, sites, sources
from jobgambit.core import profile as pf
from jobgambit.core.data import db, home

STATUS = {"running": False, "log": [], "summary": ""}  # what the Board shows while a search runs
LOCK = threading.Lock()
QUERIES_PER_BOARD = {"linkedin": 3}  # LinkedIn rate-limits bursts; the rest rotates through more
DEFAULT_QUERIES = 6
MAX_LOOKBACK = 30  # days a search looks back at most: a word searched again after N days looks back N + 1 (up to this)
MAX_PER_RUN = 30  # searches per board and run when a long word list needs more to come round within MAX_LOOKBACK
SIDE_TITLES = 5  # a job type marked "Also" searches its words with this many of the person's titles


def say(msg):
    STATUS["log"] = (STATUS["log"] + [msg])[-200:]


def state_path():
    return home() / "jobs" / "search_state.json"


def load_state():
    p = state_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"last_q": {}, "health": {}}


def save_state(s):
    state_path().parent.mkdir(parents=True, exist_ok=True)
    state_path().write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


def pack_module(country):
    try:
        return importlib.import_module(f"jobgambit.countries.{country.lower()}.sources") if country else None
    except ModuleNotFoundError:
        return None


def pack_sources(country):
    mod = pack_module(country)
    return getattr(mod, "BOARDS", {}), getattr(mod, "SCANNERS", {}), getattr(mod, "FEEDS", set())


PLAIN = {"full_time", "part_time", "freelance", "phd"}  # job types searched with the person's words alone


def type_words(kind, country, prefs) -> list[str]:
    """The words searched in front of the person's words for a job type: the country pack's, then the worldwide ones."""
    words = [*(countries.pack(country).get("search_words") or {}).get(kind, []), *score.SEARCH_WORDS.get(kind, [])]
    level = (prefs.get("thesis_level") or "").lower()
    if kind == "thesis" and level in ("bachelor", "master"):
        words = [f"{level} thesis" if w == "thesis" else w for w in words if not re.search("bachelor|master", w, re.I) or level in w.lower()]
    if kind == "internship" and not prefs.get("mandatory_internship"):
        words = [w for w in words if w.lower() != "pflichtpraktikum"]
    return list(dict.fromkeys(words))


def queries(prefs, country, board, state, every: bool = False):
    """(query, job type) pairs. The person's titles and keywords in the country's languages; each chosen job type with
    search words of its own (Werkstudent, Praktikum, Masterarbeit…) is its own group of "<type word> <word>" queries.
    The groups take turns, a board's worth each, at most three boards' worth in all; a type marked "Also" (side_types)
    gets every other turn, so a main type is searched twice as often, and pairs its type words with the first
    SIDE_TITLES titles only. A long word list gets more searches per run (boards other than LinkedIn, up to
    MAX_PER_RUN), so every query comes round within MAX_LOOKBACK days. Least recently searched first. A search word
    typed for a job type that wasn't chosen ("Praktikum X") is left out. every: all of them, not one run's share."""
    from babel.languages import get_official_languages
    langs = {"en", *(get_official_languages(country, de_facto=True) if country else ())}
    words = list(dict.fromkeys(prefs.get("titles", []) + [w for lang, ws in prefs.get("keywords", {}).items() if lang in langs for w in ws]))
    kinds = prefs.get("job_types") or ["full_time"]
    order = list(dict.fromkeys("" if k in PLAIN or not type_words(k, country, prefs) else k for k in kinds))
    own = [k for k in order if k in getattr(pack_module(country), "TYPED", {}).get(board, ())]
    if own:  # the board filters these job types itself: one group, the plain words, every one of these types at once
        order = list(dict.fromkeys("+".join(own) if k in own else k for k in order))
    m = score.Matcher({}, {})
    typed = {w: k for w in words if (k := m.kind(w, country)) != "full_time"}  # "Werkstudent Data": searched as it is
    wanted = lambda k: k in kinds or (k in ("graduate", "phd") and bool(set(kinds) & score.FULL))  # as score.assess shows them
    side = set(prefs.get("side_types") or ())
    group = lambda k: (lambda g: "+".join(own) if g in own else g)("" if k in PLAIN or not type_words(k, country, prefs) else k)
    main = {group(k) for k in kinds if k not in side} or set(order)  # all marked "Also": the same as none
    groups = {g: [] for g in order}
    for w in words:
        if w in typed:
            if wanted(typed[w]):
                groups[typed[w] if typed[w] in groups else order[0]].append((w, typed[w]))
        elif "" in groups:
            groups[""].append((w, ""))
    for k in order:
        if "+" in k or k in own:
            groups[k] += [(w, k) for w in words if w not in typed]
        elif k:
            pool = [w for w in prefs.get("titles", [])[:SIDE_TITLES] if w not in typed] if k not in main else words
            groups[k] += [(f"{tw} {w}", k) for tw in type_words(k, country, prefs) for w in pool if w not in typed]
    last = state["last_q"].get(f"{board}:{country}", {})
    for g in groups.values():
        g.sort(key=lambda qk: last.get(qk[0], ""))
    total = sum(len(g) for g in groups.values())
    if every:
        return [qk for g in groups.values() for qk in g]
    cap = QUERIES_PER_BOARD.get(board, DEFAULT_QUERIES) * min(len(order), 3)
    if board not in QUERIES_PER_BOARD:  # a long word list: enough per run to come round within MAX_LOOKBACK days
        cap = min(MAX_PER_RUN, max(cap, -(-total // MAX_LOOKBACK)))
    out, rest, turn = [], [(groups[g], g in main) for g in order], 0
    while len(out) < cap and any(g for g, _ in rest):  # a group with fewer words leaves its turns to the others
        for g, is_main in rest:
            if g and len(out) < cap and (is_main or turn % 2 == 0):
                out.append(g.pop(0))
        turn += 1
    return out


def days_back(state, board, country, q):
    """How far a search looks back: since it last ran (a word comes round every few days), at most MAX_LOOKBACK. LinkedIn
    was capped at 7 days while its words came round every ~3 weeks: most postings were never searched for."""
    last = state["last_q"].get(f"{board}:{country}", {}).get(q)
    if not last:
        return 14
    return max(3, min(MAX_LOOKBACK, (date.today() - date.fromisoformat(last)).days + 1))


def coverage(prefs, state) -> dict:
    """{country: {board: {queries, per_run, cycle (days for every query to come round once), lookback, gap}}} for the
    boards searched with the person's words (Settings › Search shows it; gap: postings older than the look-back can be
    missed, fewer search words help)."""
    out = {}
    for t in prefs.get("targets") or []:
        cc = t["code"] if isinstance(t, dict) else t.code
        boards, _, feeds = pack_sources(cc)
        out[cc] = {}
        for board in [b for b in {**sources.global_boards(cc), **boards} if b not in feeds and b not in prefs.get("sources_off", [])]:
            total, per_run = len(queries(prefs, cc, board, state, every=True)), len(queries(prefs, cc, board, state))
            cycle = -(-total // max(per_run, 1))
            out[cc][board] = {"queries": total, "per_run": per_run, "cycle": cycle, "lookback": MAX_LOOKBACK, "gap": cycle > MAX_LOOKBACK}
    return out


def board_ctx(prefs, cc) -> dict:
    """What a board search gets for a country. One place: searched around it (the radius in miles, as JobSpy wants it).
    Several places in the country: the whole country, and the distance score judges (searching only the first place
    missed every other city)."""
    name = Locale("en").territories.get(cc, cc)
    here = [p for p in prefs.get("places") or [] if (score.place(p) or (0, 0, cc))[2] == cc]
    one, radius = (here[0] if len(here) == 1 else ""), prefs.get("radius_km") or 50
    return {"country": cc, "country_name": name.lower(), "location": one or name, "remote": prefs.get("remote", "any"),
            "place": one, "radius": radius, "distance": round(radius / 1.609), "job_types": prefs.get("job_types") or []}


def run(only: str = "") -> str:
    """The whole search. Returns a one-line summary."""
    if not LOCK.acquire(blocking=False):
        return "A search is already running."
    STATUS.update(running=True, log=[], summary="")
    try:
        return _run(only)
    finally:
        STATUS["running"] = False
        LOCK.release()


def _run(only):
    prefs = pf.Preferences.model_validate(pf.read("preferences")).model_dump()
    profile = pf.profile().model_dump()
    matcher = score.Matcher(prefs, profile)
    state, today = load_state(), date.today().isoformat()
    tasks = []  # (source name, country, fn, query or None, ctx)
    for target in prefs["targets"]:
        cc = target["code"]
        ctx = board_ctx(prefs, cc)
        boards, scanners, feeds = pack_sources(cc)
        for bname, fn in {**sources.global_boards(cc), **boards}.items():
            if (only and only != bname) or bname in prefs["sources_off"]:
                continue
            if bname in feeds:
                tasks.append((bname, cc, fn, None, {**ctx, "days": getattr(pack_module(cc), "FEED_DAYS", {}).get(bname, 3)}))
                continue
            for q, kind in queries(prefs, cc, bname, state):
                tasks.append((bname, cc, fn, q, {**ctx, "days": days_back(state, bname, cc, q), "kind": kind}))
    if prefs["remote"] in ("remote", "any", "hybrid") and not only:
        for bname, fn in sources.REMOTE_BOARDS.items():
            if bname in prefs["sources_off"]:
                continue
            for q in prefs["titles"][:3]:
                tasks.append((bname, "", fn, q, {"country": "", "days": 7}))
    say(f"Searching {len({t[0] for t in tasks})} sources with {len(tasks)} searches…")

    by_board = {}
    for t in tasks:
        by_board.setdefault(t[0], []).append(t)

    def board_run(items):
        rows, ok, err = [], [], ""
        for bname, cc, fn, q, ctx in items:
            for wait in (0, 20):
                time.sleep(wait)
                sources.JOBSPY_ERRORS.pop(bname, None)
                try:
                    got = fn(q, ctx)
                    err = "; ".join(sources.JOBSPY_ERRORS.get(bname, []))[:200]
                except Exception as e:  # one broken query never stops the run
                    got, err = [], f"{type(e).__name__}: {str(e)[:150]}"
                if not err or "not available" in err.lower():
                    break
            rows += [{**r, "country": r.get("country") or cc} for r in got if sources.posted_ok(r.get("posted", ""), ctx.get("days", 30))]
            if not err and q:
                ok.append((cc, q))
            say(f"{bname}{' ' + cc if cc else ''} · {q or 'feed'}: {len(got)}" + (f" (problem: {err[:80]})" if err else ""))
            time.sleep(2 if bname == "linkedin" else 0)
        return bname, rows, ok, err

    scanners = sites.scanners()

    def scan(c):
        fn = scanners.get(c["kind"])
        if not fn:
            return c, [], "unknown hiring system"
        try:
            return c, fn(c["company"] or c["ref"], c["ref"], {"country": "", "country_name": ""}), ""
        except Exception as e:
            return c, [], f"{type(e).__name__}: {str(e)[:100]}"

    if not only:
        sites.seed()
    todo = [] if only else sites.active()
    with ThreadPoolExecutor(max(1, len(by_board)) + 4) as pool:
        site_jobs = pool.map(scan, todo)
        board_results = list(pool.map(board_run, by_board.values()))
        site_results = list(site_jobs)
    wanted = {t["code"] for t in prefs["targets"]}
    open_to_all = re.compile(r"anywhere|worldwide|global|remote|work from home|europe|emea", re.I)

    def reachable(r):  # remote feeds list jobs limited to one region: keep the ones open to the person's countries
        if r["source"] not in sources.REMOTE_BOARDS:
            return True
        here = score.place(r["location"])
        return (here and here[2] in wanted) or bool(open_to_all.search(r["location"])) or any(
            Locale("en").territories.get(c, "~").lower() in r["location"].lower() for c in wanted)

    found = [r for _, rows, _, _ in board_results for r in rows if reachable(r)]
    for c, rows, err in site_results:  # a company's whole career site: keep its postings in the target countries or remote
        here = lambda r: score.place(r["location"])
        keep = [{**r, "country": (here(r) or (0, 0, ""))[2]} for r in rows
                if "remote" in r["location"].lower() or not here(r) or here(r)[2] in wanted]
        found += keep
        sites.record(c["kind"], c["ref"], len(rows), err)
        say(f"career site {c['company']}: {len(keep)}" + (f" (problem: {err})" if err else ""))

    # health + query dates (a failed query keeps its old date and is searched again next run)
    for bname, rows, ok, err in board_results:
        h = state["health"].setdefault(bname, {"streak": 0})
        h.update(last=today, count=len(rows), error=err if not ok else "")
        h["streak"] = 0 if rows else h["streak"] + 1  # an error or nothing found: 3 runs in a row means it broke
        if h["streak"] == 3 and not only:
            from jobgambit.core import alerts
            try:
                alerts.send("sources", f"JobGambit: {bname} found nothing 3 searches in a row",
                            err or "It may have changed its pages. Settings › Search shows each source's last result.")
            except Exception as e:
                say(f"Alert not sent: {e}")
        for cc, q in ok:
            state["last_q"].setdefault(f"{bname}:{cc}", {})[q] = today
    if todo:
        bad = sum(1 for _, _, e in site_results if e)
        state["health"]["career sites"] = {"last": today, "count": sum(len(r) for _, r, _ in site_results),
                                           "error": f"{bad} of {len(todo)} failed" if bad else "", "streak": 0}
    new, filtered = store(found, matcher)
    state["dropped"] = DROPPED[:]  # the titles filtered this run, for Settings › Search after changing the filters
    save_state(state)
    if not only:
        say(f"Checked company names on common hiring systems: {sites.probe()} new career sites.")
        enrich(matcher)
    with db() as con:  # postings the person added by hand that no search had found: recall
        missed = con.execute("select count(*) from jobs where json_extract(data, '$.missed') >= ?",
                             ((date.today() - timedelta(days=30)).isoformat(),)).fetchone()[0]
    summary = f"{new} new postings on your board, {filtered} filtered out" + (
        f". In the last 30 days you added {missed} postings the search had missed" if missed else "") + (
        f". Sources with problems: {', '.join(b for b, h in state['health'].items() if h.get('error'))}" if any(
            h.get("error") for h in state["health"].values()) else "")
    STATUS["summary"] = summary
    say(summary)
    if not only:
        after_search(prefs)
    return summary


def after_search(prefs):
    """Optional AI ranking of the best new postings, one alert for strong new matches, and a check for replies."""
    from jobgambit.core import mail
    if mail.configured():
        try:
            r = mail.check_replies()
            say(f"Email: {len(r['changed'])} status changes from replies" + (f", {len(r['to_check'])} for you in Needs you" if r["to_check"] else ""))
        except Exception as e:
            say(f"Email check skipped: {e}")
    stale = mail.sweep()
    if stale:
        say(f"No news for {mail.STALE_DAYS} days, moved to no response: {', '.join(stale)}")
    if prefs.get("ai_second_look"):
        try:
            n = second_look()
            say(f"AI second look: {n} filtered postings could fit you, back on the Board" if n else "AI second look: nothing more for you")
        except Exception as e:  # a bonus: no AI, or it failed
            say(f"AI second look skipped: {e}")
    if prefs.get("ai_ranking"):
        try:
            rank()
        except Exception as e:  # ranking is a bonus
            say(f"AI ranking skipped: {e}")
    if "linkedin" not in prefs.get("sources_off", []):
        try:
            say(f"How many applied: found for {applicant_numbers()} of today's best LinkedIn matches")
        except Exception as e:  # a bonus
            say(f"Applicant numbers skipped: {e}")
    with db() as con:
        top = con.execute("select title, company, score from jobs where status='new' and first_seen=? and score >= 60 "
                          "order by score desc limit 5", (date.today().isoformat(),)).fetchall()
    if top:
        from jobgambit.core import alerts
        try:
            alerts.send("matches", f"JobGambit: {len(top)} strong new matches",
                        "\n".join(f"{int(r['score'])} · {r['title']} · {r['company']}" for r in top))
        except Exception as e:
            say(f"Alert not sent: {e}")


def off_status(why_off):
    """A posting past its deadline is expired; any other reason puts it under Less relevant."""
    return "expired" if why_off == "expired" else "filtered" if why_off else ""


DROPPED = []  # titles the last store() filtered, with why


def store(found, matcher):
    """New postings only (by key, link, or a near-identical title at the same company). Returns (kept, filtered).
    A career-site posting that matches a board posting is not stored twice: it becomes that row's site_url."""
    with db() as con:
        rows = con.execute("select key, url, company, title, source from jobs").fetchall()
        known_urls, source = {r["url"] for r in rows}, {r["key"]: r["source"] for r in rows}
        by_title = {}  # one job, several boards: near-identical title and a company name that contains the other
        for r in rows:
            by_title.setdefault(sources.slug(sources.clean_title(r["title"]), 60), []).append((r["company"] or "", r["key"]))
        twin = lambda j: next((k for c, k in by_title.get(sources.slug(j["title"], 60), [])
                               if fuzz.partial_ratio(j["company"].lower(), c.lower()) >= 85), None)
        new, site_url = {}, {}
        for j in found:
            if not j.get("url") or not j.get("title") or j["url"] in known_urls:
                continue
            same = j["key"] if j["key"] in source or j["key"] in new else twin(j)
            if same:
                if j["source"] == "career site" and (new[same]["source"] if same in new else source[same]) != "career site":
                    site_url.setdefault(same, j["url"])
                if same in new and j.get("desc") and not new[same].get("desc"):
                    new[same]["desc"] = j["desc"]
                continue
            by_title.setdefault(sources.slug(j["title"], 60), []).append((j["company"], j["key"]))
            new[j["key"]] = j
        sims = {}
        for k, u in site_url.items():
            if k not in new:
                con.execute("update jobs set data=json_set(coalesce(data, '{}'), '$.site_url', ?) where key=?", (u, k))
        kept = off = 0
        links = []
        DROPPED.clear()
        for k, j in new.items():
            fields, why_off = matcher.assess(j, j.get("desc", ""), sims.get(k))  # nothing is thrown away: junk shows as off-topic
            status = off_status(why_off) or "new"
            kept += status == "new"
            off += status == "filtered"
            if status == "filtered":
                DROPPED.append(f"{j['title']} · {j['company']} · {why_off}")
            if why_off not in ("junk", "excluded"):
                links += [(j["url"], j["company"], "posting", j["title"]), (j.get("apply_url"), j["company"], "apply link", j["title"])]
            if j.get("desc"):
                (home() / "jobs" / "desc").mkdir(parents=True, exist_ok=True)
                (home() / "jobs" / "desc" / f"{k}.txt").write_text(j["desc"][:12000], encoding="utf-8")
            con.execute("insert or ignore into jobs(key, title, company, location, country, url, source, posted, first_seen, deadline,"
                        " type, status, score, why, data) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (k, j["title"], j["company"], j["location"], j.get("country", ""), j["url"], j["source"], j.get("posted", ""),
                         date.today().isoformat(), fields["deadline"], fields["type"], status, fields["score"],
                         (f"[{why_off}] " if why_off else "") + fields["why"],
                         json.dumps({"language": fields["language"], **({"apply_url": j["apply_url"]} if j.get("apply_url") else {}),
                                     **({"site_url": site_url[k]} if k in site_url else {})})))
    sites.learn_all(links)
    return kept, off


def by_host(rows):
    """Rows taken in turns from each site, so parallel requests go to different sites."""
    hosts = {}
    for r in rows:
        hosts.setdefault(urllib.parse.urlparse(r["url"] or "").netloc, []).append(r)
    return [r for turn in itertools.zip_longest(*hosts.values()) for r in turn if r]


def enrich(matcher, limit=40):
    """Fetch the descriptions of every open posting that came without one (LinkedIn, university lists…), and of every
    one from the last 14 days filtered on its title alone, and judge them again from their text: one whose text fits
    goes to the Board, saying so. Also re-checks open postings older than 3 days: one that was taken down is marked expired.
    A site that says "too many requests" (429) is left alone for the rest of the run."""
    due, recent = (date.today() - timedelta(days=3)).isoformat(), (date.today() - timedelta(days=14)).isoformat()
    has_text = lambda r: (home() / "jobs" / "desc" / f"{r['key']}.txt").exists()
    with db() as con:
        rows = [dict(r) for r in con.execute("select * from jobs where status in ('new', 'shortlisted') order by score desc")]
        titled = [dict(r) for r in con.execute("select * from jobs where status='filtered' and (why like '[junk]%' or why like '[weak]%') "
                                               "and first_seen >= ? order by first_seen desc, key", (recent,))]
    todo = by_host([r for r in rows if not has_text(r)] + [r for r in titled if not has_text(r)])
    stale = [r for r in rows if r["first_seen"] <= due and json.loads(r["data"] or "{}").get("checked", r["first_seen"]) <= due][:limit]
    busy = set()  # sites that answered 429

    def fetch(r):
        host = urllib.parse.urlparse(r["url"] or "").netloc
        if host in busy:
            return r, "", "uncertain"
        for pattern, describe in getattr(pack_module(r.get("country") or ""), "DESCRIBE", {}).items():
            if re.search(pattern, r["url"] or ""):  # a page built by JavaScript: its text from the source's own API
                status, text = describe(r["url"])
                if status == 429:
                    busy.add(host)
                return r, text, "expired" if status in (404, 410) else "active" if text else "uncertain"
        try:
            status, page, final = sources.http(r["url"], timeout=20)
        except Exception:
            return r, "", "uncertain"
        if status == 429:
            busy.add(host)
            return r, "", "uncertain"
        refs = {sources.ats_ref(html.unescape(h)) for h in re.findall(r'href="([^"]+)"', page)} - {None}
        if len(refs) == 1:  # one hiring system linked from the posting page: the company's (several: an aggregator's list)
            sites.add(*refs.pop(), r["company"], "posting page")
        verdict = sources.classify(status, page, r["url"], final)[0]
        jp = posting.job_posting(page)
        text = posting.from_jsonld(jp)["description"] if jp else ""
        if len(text.split()) < 60:
            import trafilatura
            text = trafilatura.extract(page) or ""
        return r, text if verdict != "expired" else "", verdict

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(fetch, todo + [r for r in stale if r not in todo]))
    sims = {}
    with db() as con:
        for r, text, verdict in results:
            data = json.loads(r["data"] or "{}")
            if verdict == "expired":
                con.execute("update jobs set status='expired' where key=?", (r["key"],))
                continue
            if verdict == "active":
                data["checked"] = date.today().isoformat()
            if text and len(text.split()) >= 60:
                (home() / "jobs" / "desc").mkdir(parents=True, exist_ok=True)
                (home() / "jobs" / "desc" / f"{r['key']}.txt").write_text(text[:12000], encoding="utf-8")
                fields, why_off = matcher.assess(r, text, sims.get(r["key"]))
                rescued = r["status"] == "filtered" and not why_off  # the title said no, the posting says yes
                status = r["status"] if data.get("kept") and why_off != "expired" else off_status(why_off) or (
                    "new" if rescued else r["status"])
                why = (f"[{why_off}] " if why_off else "found after reading the posting · " if rescued else "") + fields["why"]
                con.execute("update jobs set score=?, why=?, deadline=?, type=?, status=? where key=?",
                            (fields["score"], why, fields["deadline"], fields["type"], status, r["key"]))
                data["language"] = fields["language"]
            con.execute("update jobs set data=? where key=?", (json.dumps(data), r["key"]))
    say(f"Read {sum(bool(t) for _, t, _ in results)} job descriptions; {sum(v == 'expired' for _, _, v in results)} postings were taken down.")


def rescore() -> int:
    """After the person changed what they look for: every open posting judged again (score, type, reason, status), so
    the Board's tabs and Less relevant follow at once. Starred postings and ones moved to the board by hand stay where
    they are. Returns how many changed status."""
    prefs = pf.Preferences.model_validate(pf.read("preferences")).model_dump()
    matcher, changed, rows = score.Matcher(prefs, pf.profile().model_dump()), 0, []
    with db() as con:
        jobs = [dict(r) for r in con.execute("select * from jobs where status in ('new', 'filtered', 'shortlisted')")]
    path = lambda k: home() / "jobs" / "desc" / f"{k}.txt"
    text = {r["key"]: path(r["key"]).read_text(encoding="utf-8") if path(r["key"]).exists() else "" for r in jobs}
    sims = {}
    for r in jobs:  # judged first, written after in one short write: the app stays usable meanwhile
        fields, why_off = matcher.assess({**r, "company": r["company"] or "", "location": r["location"] or ""},
                                         text[r["key"]], sims.get(r["key"]))
        kept = r["status"] == "shortlisted" or (json.loads(r["data"] or "{}").get("kept") and why_off != "expired")
        status = r["status"] if kept else off_status(why_off) or "new"
        changed += status != r["status"]
        rows.append((fields["score"], (f"[{why_off}] " if why_off else "") + fields["why"], fields["type"], status, r["key"], r["status"]))
    with db() as con:  # a posting the person moved meanwhile (applying, skipped) keeps its new status
        con.executemany("update jobs set score=?, why=?, type=?, status=? where key=? and status=?", rows)
    return changed


def refresh(search_too: bool):
    """What the person looks for changed (Settings): judge the stored postings again, then search with the new words."""
    say(f"Your search settings changed: {rescore()} postings moved.")
    if search_too:
        run()


def health() -> dict:
    return load_state().get("health", {})


def applicant_numbers(limit: int = 10) -> int:
    """How many applied, for the best new LinkedIn matches of today (score 40+), 3 s apart; stops at LinkedIn's first
    429 (sources.applicants). Stored as data.applicants for the Board. Returns how many were found."""
    with db() as con:
        rows = con.execute("select key, url, data from jobs where status in ('new', 'shortlisted') and score >= 40 and "
                           "url like '%linkedin.com/jobs/view/%' and first_seen=? order by score desc limit ?",
                           (date.today().isoformat(), limit)).fetchall()
    found = 0
    for r in rows:
        got = sources.applicants(r["url"])
        if got:
            found += 1
            with db() as con:
                con.execute("update jobs set data=json_set(coalesce(data, '{}'), '$.applicants', json(?)) where key=?",
                            (json.dumps(got), r["key"]))
        if sources.BLOCKED.get("linkedin"):
            break
        time.sleep(3)
    return found


def second_look(limit: int = 400) -> int:
    """One AI run (economy level) over the titles filtered as off-topic today that came without a text: the ones that
    could fit this person go to the Board, with why ("Perception Engineer" for someone who searched "Computer Vision").
    Titles are untrusted. Settings › Search can switch it off. Returns how many came back."""
    from jobgambit.core import ai, apply, prompts
    from jobgambit.core.profile import Model
    prefs = pf.Preferences.model_validate(pf.read("preferences"))
    if not prefs.ai_second_look:
        return 0
    with db() as con:
        rows = [dict(r) for r in con.execute("select key, title, company, location from jobs where status='filtered' and "
                                             "why like '[junk]%' and first_seen=? order by key", (date.today().isoformat(),))]
    rows = [r for r in rows if not (home() / "jobs" / "desc" / f"{r['key']}.txt").exists()][:limit]
    if not rows:
        return 0

    class Pick(Model):
        key: str
        why: str

    class SecondLook(Model):
        picks: list[Pick]

    wants = f"titles {prefs.titles}; search words {prefs.keywords}; job types {prefs.job_types}"
    titles = "\n".join(f"{r['key']} | {r['title']} | {r['company']} | {r['location']}" for r in rows)
    out = ai.answer(prompts.build("second_look", profile=apply.factsheet(pf.profile().model_dump())[:5000], wants=wants,
                                  titles=apply.untrusted(titles)), SecondLook, ai.chosen(prefs.agent, "economy"), timeout=600)
    keys, n = {r["key"] for r in rows}, 0
    with db() as con:
        for p in out.picks:
            if p.key in keys:
                n += con.execute("update jobs set status='new', why=? where key=? and status='filtered'",
                                 ("AI second look: " + " ".join(p.why.split())[:200], p.key)).rowcount
    return n


def rank(n: int = 20) -> int:
    """One AI answer run re-scores the n best new postings from their texts. Returns how many were ranked."""
    from jobgambit.core import ai, apply, prompts
    from jobgambit.core.profile import Model

    class Ranked(Model):
        key: str
        score: int
        why: str

    class Ranking(Model):
        ranking: list[Ranked]

    with db() as con:
        rows = [dict(r) for r in con.execute("select key, title, company, location from jobs where status='new' "
                                             "order by score desc limit ?", (n,))]
    if not rows:
        return 0
    desc = lambda k: (home() / "jobs" / "desc" / f"{k}.txt").read_text(encoding="utf-8")[:1200] if (home() / "jobs" / "desc" / f"{k}.txt").exists() else ""
    text = "\n\n".join(f"KEY {r['key']}: {r['title']} | {r['company']} | {r['location']}\n{desc(r['key'])}" for r in rows)
    prefs = pf.Preferences.model_validate(pf.read("preferences"))
    wants = f"titles {prefs.titles}; types {prefs.job_types}; places {prefs.places}; remote {prefs.remote}; " + \
            "; ".join(f"{t.code}: {t.permit}" for t in prefs.targets)
    out = ai.answer(prompts.build("rank", profile=apply.factsheet(pf.profile().model_dump())[:6000], wants=wants,
                                  postings=apply.untrusted(text)), Ranking, ai.chosen(prefs.agent), timeout=600)
    keys = {r["key"] for r in rows}
    with db() as con:
        for r in out.ranking:
            if r.key in keys:
                con.execute("update jobs set score=?, why=? where key=?", (max(0, min(100, r.score)), "AI: " + r.why, r.key))
    return len(out.ranking)
