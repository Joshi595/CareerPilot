// Practice: your interviews first (brief, STAR stories, defend your CV, likely questions, a mock interview), then general
// practice. Answers are typed; the AI's feedback only uses the person's real experience.
const PREP_TABS = ["brief", "stories", "defend", "questions", "mock"];

document.addEventListener("alpine:init", () => {
  Alpine.data("practice", () => ({
    PREP_TABS, apps: [], which: "general", qs: [], answers: {}, prep: null, tab: "brief", open: null, text: "", fb: null,
    busy: "", err: "", loading: true, find: "", posting: null, mock: null, timer: false, left: 0, tick: 0,
    async init() {
      try { this.apps = (await api("/api/applications")).rows.filter(r => r.folder); } catch {}
      const app = Alpine.store("app");  // "Prepare" in My applications or Needs you picks the application
      const want = this.apps.find(a => String(a.id) === app.practiceApp) || this.interviews()[0];
      app.practiceApp = "";
      this.which = want ? String(want.id) : "general";
      await this.load();
    },
    destroy() { clearInterval(this.tick); },
    interviews() {  // interview, test or offer, or an interview still ahead: soonest first
      const at = a => a.data.interview_at || "9999";
      return this.apps.filter(a => ["interview", "assessment", "offer"].includes(a.status)
        || (a.data.interview_at && new Date(a.data.interview_at) > Date.now() - 2 * 36e5)).sort((a, b) => at(a).localeCompare(at(b)));
    },
    countdown(at) {
      if (!at) return "";
      const days = Math.round((new Date(at.slice(0, 10)) - new Date(new Date().toISOString().slice(0, 10))) / 864e5);
      return days < 0 ? "" : days === 0 ? t("practice.today") : days === 1 ? t("practice.tomorrow") : t("practice.in_days", { n: days });
    },
    cur() { return this.apps.find(a => String(a.id) === this.which); },
    label(a) { return `${a.role} · ${a.company}`; },
    async choose(id) { this.which = String(id); this.find = ""; this.tab = "brief"; this.mock = null; await this.load(); },
    pickFind() { const a = this.apps.find(a => this.label(a) === this.find); if (a) this.choose(a.id); },
    async load() {
      this.loading = true; this.err = ""; this.posting = null; clearInterval(this.tick);
      try {
        const r = await api(`/api/practice/${this.which}`);
        Object.assign(this, { qs: r.questions, answers: r.answers, prep: r.prep || null, open: null, fb: null, text: "" });
        if (!this.prep) this.tab = "questions";
      } catch (e) { this.err = e.message; }
      this.loading = false;
    },
    list() { return this.tab === "defend" ? this.prep?.defend || [] : this.qs; },  // the questions the answer pane works on
    async run(label, fn) { this.busy = label; this.err = ""; try { await fn(); } catch (e) { this.err = e.message; } this.busy = ""; },
    makeBrief() { return this.run(t("practice.making_brief"), async () => { this.prep.brief = (await api(`/api/practice/${this.which}/brief`, { method: "POST" })).brief; }); },
    makeDefend() { return this.run(t("practice.making_drill"), async () => { this.prep.defend = (await api(`/api/practice/${this.which}/defend`, { method: "POST" })).defend; }); },
    generate() {
      return this.run(t("practice.generating"), async () => {
        this.qs = (await api(`/api/practice/${this.which}/generate`, { method: "POST" })).questions; this.openQ(this.qs[0]);
      });
    },
    async loadPosting() {
      if (this.posting === null) try { this.posting = (await api(`/api/applications/${this.which}/posting`)).text; } catch { this.posting = ""; }
    },
    openQ(q) {
      this.open = q; this.text = this.answers[q?.question]?.answer || ""; this.fb = this.answers[q?.question]?.feedback || null;
      if (!matchMedia("(min-width: 900px)").matches) this.$nextTick(() => this.$root.querySelector(".answer-pane")?.scrollIntoView());
    },
    async save(feedback) {
      this.busy = feedback ? t("practice.reviewing") : ""; this.err = "";
      try {
        const r = await api(`/api/practice/${this.which}/answer`, { json: { question: this.open.question, why: this.open.why, answer: this.text, feedback } });
        if (feedback) this.fb = r;
        this.answers[this.open.question] = { answer: this.text, feedback: this.fb };
        if (!feedback) Alpine.store("toast").show(t("saved"));
      } catch (e) { this.err = e.message; }
      this.busy = "";
    },
    // mock interview: one question at a time (the likely ones and the drill, mixed), feedback each, a summary at the end
    startMock() {
      const a = this.qs, b = this.prep?.defend || [], list = [];
      for (let i = 0; i < Math.max(a.length, b.length); i++) list.push(...[a[i], b[i]].filter(Boolean));
      this.mock = { list: list.slice(0, 10), i: 0, done: false };
      this.ask();
    },
    ask() {
      this.open = this.mock.list[this.mock.i]; this.text = ""; this.fb = null;
      clearInterval(this.tick); this.left = 120;
      if (this.timer) this.tick = setInterval(() => { if (--this.left <= 0) clearInterval(this.tick); }, 1000);
    },
    nextQ() {
      if (++this.mock.i >= this.mock.list.length) { this.mock.done = true; clearInterval(this.tick); return; }
      this.ask();
    },
    mockScores() {  // [{question, score}] for the questions of this mock interview that got feedback, weakest first
      return (this.mock?.list || []).map(q => ({ question: q.question, score: this.answers[q.question]?.feedback?.score }))
        .filter(x => x.score).sort((a, b) => a.score - b.score);
    },
    average() { const s = this.mockScores(); return s.length ? (s.reduce((a, x) => a + x.score, 0) / s.length).toFixed(1) : "–"; },
    again(question) { const q = this.mock.list.find(x => x.question === question); this.mock = null; this.tab = this.qs.includes(q) ? "questions" : "defend"; this.openQ(q); },
    mmss() { return `${Math.floor(this.left / 60)}:${String(this.left % 60).padStart(2, "0")}`; },
  }));
});
