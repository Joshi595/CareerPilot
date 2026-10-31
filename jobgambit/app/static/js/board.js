// Board: the search, postings in sections by job type, filters, and adding a posting by hand. Settings: search parts.
const SECTION_OF = { part_time: "full_time", graduate: "full_time", freelance: "full_time" };
const STATUS_SECTION = { applying: "applying", starred: "shortlisted" };
window.personalSections = () => ["applying", "starred"];  // the Board's sections of the person's own lists

document.addEventListener("alpine:init", () => {
  Alpine.data("board", () => ({
    jobs: [], less: null, status: { running: false, log: [], summary: "", health: {} }, section: "full_time", country: "", sort: "fit",
    q: "", days: "", reason: "", limit: 100, url: "", text: "", busy: "", err: "", addErr: "", adding: false, loading: true, poll: null,
    async init() {
      this.country = Alpine.store("app").state?.preferences.targets?.[0]?.code || "";  // the first target country, not all
      this.section = this.sections()[0];  // the kind of job they picked first
      await this.load();
      await this.watch();
    },
    async load() {  // the open postings; Less relevant loads on first visit
      this.err = "";
      try { this.jobs = await api("/api/board?show=open"); if (this.less) this.less = await api("/api/board?show=filtered"); }
      catch (e) { this.err = t("board.load_failed") + " " + e.message; }
      this.loading = false;
    },
    reload() { this.loading = true; this.load(); },
    async pick(s) {
      this.section = s; this.limit = 100;
      if (s !== "less" || this.less) return;
      this.sort = "new"; this.loading = true; this.err = "";
      try { this.less = await api("/api/board?show=filtered"); } catch (e) { this.err = t("board.load_failed") + " " + e.message; }
      this.loading = false;
    },
    sections() {
      const types = Alpine.store("app").state?.preferences.job_types || ["full_time"];
      return [...new Set(types.map(x => SECTION_OF[x] || x))];
    },
    sectionOf(j) { const s = SECTION_OF[j.type] || j.type || "full_time"; return this.sections().includes(s) ? s : this.sections()[0]; },
    byStatus(s) { return STATUS_SECTION[s] !== undefined; },  // ⭐ / ⚡: every job type in one list
    inSection(j, s) { return this.byStatus(s) ? j.status === STATUS_SECTION[s] : this.sectionOf(j) === s; },
    inCountry(j) { return !this.country || !j.country || j.country === this.country; },  // no known country: shown everywhere;
    // the person's own lists (In progress, Starred…): the country filter never hides them
    count(s) { return s === "less" ? (this.less ? this.less.filter(j => this.inCountry(j)).length : this.country ? null : Alpine.store("app").n.less ?? 0)
                                   : this.jobs.filter(j => this.inSection(j, s) && (this.byStatus(s) || this.inCountry(j))).length; },
    rows() { return this.section === "less" ? this.less || [] : this.jobs.filter(j => this.inSection(j, this.section)); },
    countries() { return [...new Set([this.country, ...this.rows().map(j => j.country)].filter(Boolean))]; },
    countryName(c) { try { return new Intl.DisplayNames(["en"], { type: "region" }).of(c); } catch { return c; } },
    matching() {
      const q = this.q.toLowerCase(), since = this.days && new Date(Date.now() - this.days * 864e5).toISOString().slice(0, 10);
      return this.rows().filter(j => (this.byStatus(this.section) || this.inCountry(j)) && (this.section !== "less" || !this.reason || j.reason === this.reason)
        && (!since || (j.posted || j.first_seen || "").slice(0, 10) >= since)
        && (!q || `${j.title} ${j.company} ${j.location}`.toLowerCase().includes(q)));
    },
    visible() {
      const by = { fit: (a, b) => (b.score || 0) - (a.score || 0),
                   new: (a, b) => (b.posted || b.first_seen || "").localeCompare(a.posted || a.first_seen || ""),
                   deadline: (a, b) => (a.deadline || "9999").localeCompare(b.deadline || "9999") }[this.sort];
      return this.matching().sort(by).slice(0, this.limit);
    },
    left() { return this.matching().length - this.limit; },
    emptyText() {
      if (this.rows().length) return t("board.empty_filter");
      return t(this.section === "less" ? "board.empty_less" : "board.empty");
    },
    statusLine() {
      if (this.status.running) return this.status.log.at(-1) || t("board.searching");
      if (this.status.summary) return this.status.summary + ".";
      const h = Object.values(this.status.health || {}), last = h.map(x => x.last || "").sort().at(-1), bad = h.filter(x => x.error).length;
      if (!last) return t("board.never");
      return t("board.last", { when: last === new Date().toISOString().slice(0, 10) ? t("board.today") : fmtDate(last) })
        + (bad ? " · " + t("board.problems", { n: bad }) : "") + ".";
    },
    band(s) { return s >= 60 ? "strong" : s >= 40 ? "good" : s >= 20 ? "possible" : "long"; },
    meta(j) {
      const when = j.posted ? t("board.posted", { d: fmtDate(j.posted.slice(0, 10)) }) : t("board.seen", { d: fmtDate(j.first_seen) });
      return [j.company, j.location, j.source + (j.site_url ? " · " + t("board.also_site") : ""), when].filter(Boolean).join(" · ");
    },
    parts(j) { return (j.why || "").split(" · ").filter(Boolean); },
    warns(j) { return this.parts(j).filter(p => p.startsWith("⚠")); },
    whyText(j) { return this.parts(j).filter(p => !p.startsWith("⚠")).join(" · "); },
    closes(d) {
      const n = Math.ceil((new Date(d) - new Date(new Date().toISOString().slice(0, 10))) / 864e5);
      return n <= 0 ? t("board.closes_today") : t("board.closes_in", { n });
    },
    async search() { await api("/api/search/run", { method: "POST" }); this.watch(); },
    async watch() {
      clearTimeout(this.poll);
      this.status = await api("/api/search/status");
      if (this.status.running) this.poll = setTimeout(() => this.watch(), 2000);
      else if (this.status.summary) this.load();
    },
    async rank() {
      this.busy = t("board.ranking"); this.err = "";
      try { await api("/api/search/rank", { method: "POST" }); await this.load(); } catch (e) { this.err = e.message; }
      this.busy = "";
    },
    async add() {
      this.adding = true; this.addErr = "";
      try { this.open((await api("/api/jobs/intake", { json: { url: this.url, text: this.text } })).key); }
      catch (e) { this.addErr = e.message; }
      this.adding = false;
    },
    open(key) { Alpine.store("app").applyKey = key; Alpine.store("app").show("apply"); },
    async setStatus(j, status) {
      try { await api(`/api/jobs/${j.key}/status`, { method: "PUT", json: { status } }); }
      catch (e) { return Alpine.store("toast").show(e.message, "bad"); }
      j.status = status;
      if (status === "skipped") {  // gone from the board at once, with a few seconds to take it back
        this.jobs = this.jobs.filter(x => x !== j);
        Alpine.store("toast").show(t("board.skipped"), "ok", async () => { await this.setStatus(j, "new"); if (j.status === "new") this.jobs.push(j); });
      }
      Alpine.store("app").counts();
    },
    async saveNote(j) {
      try { await api(`/api/jobs/${j.key}/note`, { method: "PUT", json: { note: j.note || "" } }); Alpine.store("saved").ping(); }
      catch (e) { Alpine.store("toast").show(e.message, "bad"); }
    },
    async discard(j) {  // the draft is thrown away; the job stays on the board as new
      if (!confirm(t("board.discard_confirm"))) return;
      try { await api(`/api/apply/${j.key}`, { method: "DELETE" }); }
      catch (e) { return Alpine.store("toast").show(e.message, "bad"); }
      j.status = "new";
      Alpine.store("toast").show(t("board.discarded"));
      Alpine.store("app").counts();
    },
    async keep(j, status = "new") {  // Less relevant -> the board, for good (also ⚡ and Apply)
      await this.setStatus(j, status);
      if (j.status !== status) return false;
      this.less = this.less.filter(x => x !== j);
      this.jobs.push({ ...j, reason: "" });
      Alpine.store("toast").show(t(status === "new" ? "board.moved" : "board.queued_one"));
      return true;
    },
    async applyLess(j) { if (await this.keep(j)) this.open(j.key); },
    soon(d) { return d && (new Date(d) - new Date()) / 864e5 < 7; },
  }));

  Alpine.data("sites", () => ({  // Settings → Career sites (parts/sites.html)
    rows: [], summary: null, q: "", limit: 50, url: "", name: "", adding: false, err: "",
    async init() { await this.load(); },
    async load() {
      try { ({ rows: this.rows, summary: this.summary } = await api("/api/sites")); } catch (e) { this.err = e.message; }
    },
    matching() {
      const q = this.q.toLowerCase();
      return this.rows.filter(s => !q || `${s.company} ${s.kind} ${s.ref}`.toLowerCase().includes(q));
    },
    summaryText() {
      if (!this.summary) return "";
      const how = Object.entries(this.summary.origins).map(([o, n]) => `${n} ${t("sites.origin." + o)}`).join(" · ");
      return t("sites.summary", { on: this.summary.on, total: this.summary.total }) + (how ? " · " + how : "");
    },
    lastScan(s) {
      if (!s.last_scan) return t("sites.not_yet");
      if (s.error) return (s.fails >= 3 ? t("sites.paused") + " · " : "") + s.error;
      if (s.empty_since) return t("sites.empty_for", { n: s.zeros }) + " · " + fmtDate(s.last_scan);
      return t("sites.jobs", { n: s.last_count }) + " · " + fmtDate(s.last_scan);
    },
    async toggle(s) {
      const off = !s.off;
      try { await api("/api/sites", { method: "PUT", json: { kind: s.kind, ref: s.ref, off } }); s.off = off; Alpine.store("toast").show(t("saved")); }
      catch (e) { Alpine.store("toast").show(e.message, "bad"); }
    },
    async add() {
      this.adding = true; this.err = "";
      try { await api("/api/sites", { json: { url: this.url, name: this.name } }); this.url = this.name = ""; await this.load();
            Alpine.store("toast").show(t("sites.added")); }
      catch (e) { this.err = e.message; }
      this.adding = false;
    },
  }));
});
