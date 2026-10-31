// The apply flow (review, documents, finish). fileUrl is shared with applications.js.
const fileUrl = path => `/api/file?path=${encodeURIComponent(path)}&t=${TOKEN}`;
// Tokens per AI call (the ai_calls ledger), shared with applications.html and Settings › AI.
window.stepName = s => { const k = `apply.u.${s}`, v = t(k); return v === k ? s : v; };
window.kTok = n => n == null ? "–" : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
const TOKS = ["input", "fresh", "cache_write", "cached", "output", "thinking", "cost_usd"];
window.usageTotal = rows => rows.reduce((a, u) => ({ ...a, ...Object.fromEntries(TOKS.map(k => [k, a[k] + (u[k] || 0)])) }),
  { step: "total", total: true, ...Object.fromEntries(TOKS.map(k => [k, 0])) });
// What changed in a line, word by word: [{t, op: "" | "add" | "del", b}] (longest common subsequence of the words).
// **bold** marks are read (b), never shown. Shown with x-text spans: no HTML from a line ever reaches the page.
window.wordDiff = (a = "", b = "") => {
  // words, spaces and punctuation each a token: "Python." and "Python" share the word, only the dot differs
  const tok = s => { let bold = false; return s.split(/(\s+|[.,;:!?()])/).filter(Boolean).flatMap(w => w.split("**").flatMap((p, i) => {
    if (i) bold = !bold; return p ? [{ t: p, b: bold }] : []; })); };
  const x = tok(a), y = tok(b), L = Array.from({ length: x.length + 1 }, () => new Uint16Array(y.length + 1));
  for (let i = x.length - 1; i >= 0; i--) for (let j = y.length - 1; j >= 0; j--)
    L[i][j] = x[i].t === y[j].t ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = []; let i = 0, j = 0;
  while (i < x.length || j < y.length)
    if (i < x.length && j < y.length && x[i].t === y[j].t) { out.push({ ...y[j++], op: "" }); i++; }
    else if (i < x.length && (j === y.length || L[i + 1][j] >= L[i][j + 1])) out.push({ ...x[i++], op: "del" });
    else out.push({ ...y[j++], op: "add" });
  return out;
};

document.addEventListener("alpine:init", () => {
  Alpine.data("applyFlow", () => ({
    STEPS: ["review", "ready", "sent"],
    key: "", job: null, stage: "", fit: null, prop: null, files: [], usage: [], folder: "", docs: [], saving: null, checks: null, design: "", loading: true, again: null,
    busy: "", err: "", dec: {}, applied: null, missing: [], rule: "", ruleFor: "cv_rules", ruleNote: "", designs: [], looks: [], method: "company website", note: "", done: null, ask: "", changes: [], redo: "",
    edits: {}, show: "all", diffs: [],  // review cards: which show their text box, the filter; the Documents step's last changes
    applicants: null,  // how many applied (LinkedIn postings), asked once a day
    LANGS: ["en", "de", "fr", "es", "it", "nl", "pt", "pl", "sv", "da"],
    async init() {
      this.key = Alpine.store("app").applyKey;
      this.loading = true; this.err = "";
      try {
        const [s, d] = await Promise.all([api(`/api/apply/${this.key}`), api("/api/designs")]);
        this.designs = d.designs;
        this.looks = Object.keys(d.presets);
        this.load(s);
        this.design = s.design || d.current;
      } catch (e) { this.err = e.message; }
      this.loading = false;
      api("/api/setup/ready").then(r => { this.missing = r.missing; }).catch(() => {});
      if (this.job) api(`/api/jobs/${this.key}/applicants`, { method: "POST" }).then(r => { this.applicants = r.applicants; }).catch(() => {});
      this.$watch("dec", () => this.saveChoices());
      if (this.stage === "drafting") return this.waitDraft();
      if (this.stage === "ready") this.makeCurrent();
      const fresh = ["new", "fit", "blocked"].includes(this.stage);  // "fit": a work folder from before the fit check was dropped
      if (fresh && !this.applied) this.draft();  // already applied: never a second application
    },
    async waitDraft() {  // opened while a draft is being written (another tab, a reload): wait for it, never a second run
      this.busy = t("apply.drafting");
      while (this.stage === "drafting") {
        await new Promise(r => setTimeout(r, 5000));
        try { this.load(await api(`/api/apply/${this.key}`)); } catch (e) { this.err = e.message; break; }
      }
      this.busy = "";
      if (this.stage === "new") this.draft();
    },
    makeCurrent() {
      api(`/api/apply/${this.key}/current`, { method: "POST" }).then(r => { if (r.warning) this.err = r.warning; }).catch(() => {});
    },
    stepNo() { return this.done ? 2 : Math.max(0, this.STEPS.indexOf(this.stage)); },
    tally(d) { return Object.values(this.dec).filter(x => x.decision === d).length; },
    load(s) {
      if (s.job) this.job = s.job;
      if ("applied" in s) this.applied = s.applied;
      this.stage = s.stage; this.fit = s.fit ?? this.fit; this.prop = s.proposal ?? this.prop;
      this.files = s.files || []; this.usage = s.usage || []; this.checks = s.checks || null; this.folder = s.folder || ""; this.docs = s.documents || [];
      if (this.prop) this.resetDecisions(s.choices);
    },
    resetDecisions(saved = null) {  // saved: the choices made before leaving the page
      this.dec = {}; this.edits = {}; this.show = "all";
      for (const i of [...this.prop.items, ...this.prop.letter])
        this.dec[i.id] = saved?.[i.id] || { decision: i.flag ? "reject" : "accept", text: i.new, comment: "" };
    },
    saveChoices() {  // each click on a review card is kept (debounced), so stopping half-way loses nothing
      clearTimeout(this.saving);
      if (this.stage !== "review") return;
      this.saving = setTimeout(() => api(`/api/apply/${this.key}/choices`, { method: "PUT", json: { choices: this.dec } }).catch(() => {}), 600);
    },
    async step(label, fn) {
      this.busy = label; this.err = ""; this.again = () => this.step(label, fn);
      try { await fn(); } catch (e) { this.err = e.message; }
      this.busy = "";
      api(`/api/apply/${this.key}`).then(s => { this.usage = s.usage || []; }).catch(() => {});  // tokens of the step just run
      window.scrollTo(0, 0);
    },
    draft(wish = {}) {  // wish: {note, language} for this draft only
      this.step(t("apply.drafting"), async () => {
        this.prop = await api(`/api/apply/${this.key}/draft`, { json: wish });
        this.stage = "review"; this.resetDecisions();
      });
    },
    langName(code) { try { return new Intl.DisplayNames(["en"], { type: "language" }).of(code); } catch { return code; } },
    startOver(note = "", language = "") {  // throw this draft away and write a new one (one more AI run)
      if (!confirm(t("apply.start_over_confirm"))) return;
      clearTimeout(this.saving);
      this.step(t("apply.drafting"), async () => {
        await api(`/api/apply/${this.key}`, { method: "DELETE" });
        this.files = []; this.checks = null; this.changes = []; this.redo = ""; this.ask = "";
        this.prop = await api(`/api/apply/${this.key}/draft`, { json: { note, language } });
        this.stage = "review"; this.resetDecisions();
      });
    },
    kind(c, letter) {  // what sort of change a card is (its tag, the summary, the filter)
      return letter ? "letter" : "order" in c ? "order" : c.id === "summary" ? "summary" : c.id.startsWith("skills_") ? "skills"
        : !c.new ? "removed" : /reviewer/i.test(`${c.reason} ${c.section}`) ? "reviewer" : "reworded";
    },
    summary() {
      const n = {};
      for (const c of this.prop.items) n[this.kind(c)] = (n[this.kind(c)] || 0) + 1;
      const parts = Object.entries(n).map(([k, v]) => `${v} ${t("apply.kind." + k).toLowerCase()}`);
      return t("apply.sum", { n: this.prop.items.length, parts: parts.join(" · ") }) +
        (this.prop.letter.length ? " · " + t("apply.sum_letter", { n: this.prop.letter.length }) : "");
    },
    visible(c, letter) {
      return this.show === "all" || (this.show === "flagged" ? c.flag : ["reworded", "reviewer"].includes(this.kind(c, letter)));
    },
    editing(c) { return this.edits[c.id] ?? !!this.prop.final; },  // Change text: every line is a text box from the start
    diffOf(c) {  // the suggestion (or the person's edit of it), its changed words marked against the original; a card
      // kept as it was still shows what was suggested, dimmed
      const now = this.dec[c.id].text;
      return c.old ? wordDiff(c.old, now) : wordDiff("", now).map(w => ({ ...w, op: "" }));
    },
    // a diff read as one whole sentence: "now" (what the document will say, added words marked) or "was" (the
    // original, removed words marked). Both in one line jumble the two sentences.
    side(diff, which) {  // a space is marked only between two marked words: the mark covers exactly the changed words
      const s = diff.filter(w => w.op !== (which === "now" ? "del" : "add"));
      return s.map((w, i) => w.op && !w.t.trim() && !(s[i - 1]?.op && s[i + 1]?.op) ? { ...w, op: "" } : w);
    },
    said(diff, which) { return this.side(diff, which).some(w => w.t.trim()); },
    steps(s) {  // "1. A  2. B   Left out: C" -> ["A", "B"] (the list numbers them)
      return (s || "").split(/\s{2,}/).filter(x => x && !x.startsWith("Left out:")).map(x => x.replace(/^\d+\.\s*/, ""));
    },
    leftOut(s) { return (s || "").split(/\s{2,}/).find(x => x.startsWith("Left out:")) || ""; },
    covered(k) {  // keyword appears in what the CV will say
      const texts = [this.prop.context, ...Object.values(this.dec).filter(d => d.decision !== "reject").map(d => d.text)];
      return texts.join(" ").toLowerCase().includes(k.toLowerCase());
    },
    changedCount() { return this.finalItems().length; },
    finalItems() {  // Change text: only what the person changed (an edit) or asked AI about (a comment) goes back
      return [...this.prop.items, ...this.prop.letter].flatMap(c => {
        const d = this.dec[c.id];
        return d.comment.trim() ? [{ id: c.id, decision: "revise", text: c.new, comment: d.comment }]
             : d.text !== c.new ? [{ id: c.id, decision: "edit", text: d.text.trim(), comment: "" }] : [];
      });
    },
    editText() {
      this.step(t("working"), async () => {
        this.prop = await api(`/api/apply/${this.key}/edit`, { method: "POST" });
        this.stage = "review"; this.resetDecisions();
        for (const d of Object.values(this.dec)) d.decision = "accept";  // flags don't apply to the final text
      });
    },
    submit(nothing = false) {
      clearTimeout(this.saving);  // a late choices save would bring back what this submit decides
      const items = this.prop.final ? (nothing ? [] : this.finalItems()) : Object.entries(this.dec).map(([id, d]) => {
        const card = [...this.prop.items, ...this.prop.letter].find(i => i.id === id);
        const decision = d.decision === "accept" && d.text !== card.new ? "edit" : d.decision;
        return { id, decision, text: decision === "reject" ? card.old : d.text, comment: d.comment };
      });
      this.step(t("apply.building"), async () => {
        const r = await api(`/api/apply/${this.key}/decide`, { json: { items } });
        if (r.stage === "review") { this.prop = r.proposal; this.resetDecisions(); this.stage = "review"; }
        else { this.stage = "ready"; this.files = r.files; this.checks = r.checks; this.folder = (await api(`/api/apply/${this.key}`)).folder;
               if (r.warning) this.err = r.warning; }
      });
    },
    changeDocs() {  // the AI box: the request (empty = fix what the checks found) is applied and the PDFs rebuilt
      this.step(t("apply.building"), async () => {
        const r = await api(`/api/apply/${this.key}/change`, { json: { request: this.ask } });
        if (r.restart) return setTimeout(() => this.startOver(this.ask, r.restart));  // another language: a new draft in it
        this.files = r.files; this.checks = r.checks; this.diffs = r.diffs || [];
        this.changes = this.diffs.length ? [] : [t("apply.docs_ai_none")];
        this.ask = ""; if (r.warning) this.err = r.warning;
      });
    },
    rebuild() {
      this.step(t("apply.building"), async () => {
        const r = await api(`/api/apply/${this.key}/build`, { json: { design: this.design } });
        this.files = r.files; this.checks = r.checks;
      });
    },
    async addRule() {  // "always do this": one line added to the CV or letter instructions, used from the next application on
      if (!this.rule.trim()) return;
      try {
        await api("/api/instructions", { json: { kind: this.ruleFor, line: this.rule.trim() } });
        this.rule = ""; this.ruleNote = t("apply.rule_added");
      } catch (e) { this.ruleNote = e.message; }
    },
    pause() {  // Stop for now: the drafts are kept; opening the job again continues here
      api(`/api/apply/${this.key}/pause`, { method: "POST" }).then(() => Alpine.store("app").show("board")).catch(e => { this.err = e.message; });
    },
    openFolder() { api(`/api/apply/${this.key}/folder`, { method: "POST" }).catch(e => { this.err = e.message; }); },
    fixAt(k) {  // where a missing setup step is fixed: [tab, sub]
      return ({ basics: ["profile"], targets: ["settings", "search"], ai: ["help"], writing: ["profile", "writing"] })[k] || ["profile", "details"];
    },
    pdf(name) { return fileUrl(`work/${this.key}/${name}`) + `&v=${Date.now()}`; },
    finish(status) {
      this.step(t("working"), async () => {
        this.done = await api(`/api/apply/${this.key}/finish`, { json: { status, method: this.method, note: this.note } });
        Alpine.store("app").show("applications");
      });
    },
  }));

});
