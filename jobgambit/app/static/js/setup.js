// Setup: one step per page, resumable (prefs.setup_step). The CV comes first, so later steps start from what it says.
// Steps 4-9 are optional and reuse the Profile/Settings parts; what's skipped shows up on the Board's "Finish setting up".
const JOB_TYPES = ["full_time", "part_time", "student", "internship", "thesis", "apprenticeship", "graduate", "phd", "freelance"];
const STUDENT_TYPES = ["student", "internship", "thesis"];  // "I'm a student" picks all three
const PERMITS = ["free", "have", "need", "unsure"];
const STEPS = ["want", "cv", "about", "looking", "details", "writing", "design", "email", "alerts", "autopilot"];
const REQUIRED = 6;  // steps 1-6: everything an application needs (what they look for, CV, contact, targets + permits, start
                     // date, documents, CV and letter instructions); design, email, alerts and autopilot can wait
const FINISH_LINKS = { details: ["profile", "details"], style: ["profile", "writing"], design: ["designs", ""], email: ["settings", "email"],
                       alerts: ["settings", "alerts"], search: ["settings", "search"], documents: ["profile", "details"] };

document.addEventListener("alpine:init", () => {
  Alpine.data("setup", () => ({
    STEPS, REQUIRED, JOB_TYPES, STUDENT_TYPES, PERMITS,
    step: 1, phase: "upload", st: null, prefs: null, loadErr: "", busy: "", err: "", agentNames: [],
    text: "", draft: null, questions: [], replies: {}, changes: [],               // 2 CV
    me: null, links: [], photoV: 0,                                             // 3 about you
    graduation: "",                                                             // 1 what they look for
    titles: "", keywords: [], pick: "", aiTitles: "",                                         // 4 looking for
    docsLater: false,                                                           // 5 details + documents
    cvRules: "", styleWay: "", samples: "", tone: "neutral", length: "standard", openings: [], opening: -1, styleText: "",  // 6 writing
    daily: true, time: "09:00",                                                 // 10 autopilot
    async init() {
      const app = Alpine.store("app");
      try { this.st = await api("/api/state"); } catch (e) { this.loadErr = e.message; return; }
      this.prefs = this.st.preferences;
      if (!this.prefs.setup_done && !this.prefs.setup_step) this.prefs.job_types = [];  // a fresh start: nothing picked for them
      this.graduation = this.st.answers?.["Expected graduation"] || "";
      this.cvRules = this.st.cv_rules || this.st.default_cv_rules || "";  // sensible defaults to start from, CV and letter alike
      this.styleText = this.st.style || this.st.default_style || "";
      this.agentNames = Object.keys(this.st.agents).filter(k => this.st.agents[k]);
      if (this.agentNames.length && !this.agentNames.includes(this.prefs.agent)) this.setAgent(this.agentNames[0]);
      this.phase = app.setupPhase || "upload";  // back from Profile's "Fix something" review: the questions come next
      this.enter(app.setupStep || this.prefs.setup_step || 1);
      app.setupStep = 0; app.setupPhase = "";
    },
    stepName(n) { return t("setup.steps." + STEPS[n - 1]); },
    go(n) {
      this.err = "";
      patchPrefs({ setup_step: n });
      if (n === 2) this.phase = "upload";  // back to step 1: its summary, not the questions again
      this.enter(n);
      this.$nextTick(() => { window.scrollTo(0, 0); document.getElementById("setup-h")?.focus(); });
    },
    enter(n) {
      this.step = n;
      if (n === 2) {
        if (this.phase === "questions") this.askQuestions();
        else if (this.st.profile.basics.name) { this.draft = this.st.profile; this.phase = "summary"; }  // resumed after a reload
      }
      if (n === 3) this.fillMe();
      if (n === 4) this.fillLooking();
    },
    country(code) { return this.st.countries[code] || code; },
    async run(label, fn) {
      this.busy = label; this.err = "";
      try { await fn(); } catch (e) { this.err = e.message; }
      this.busy = "";
    },

    // ---- 1 What they look for: everything after it (search words, sources, Board tabs, letters) follows from it
    toggleType(j) {
      const list = this.prefs.job_types;
      this.prefs.job_types = list.includes(j) ? list.filter(x => x !== j) : [...list, j];
    },
    toggleSide(j) {  // unticked = "Also": searched half as often
      const s = this.prefs.side_types || [];
      this.prefs.side_types = s.includes(j) ? s.filter(x => x !== j) : [...s, j];
    },
    isStudent() { return STUDENT_TYPES.every(j => this.prefs.job_types.includes(j)); },
    toggleStudent() {
      const on = this.isStudent();
      this.prefs.job_types = on ? this.prefs.job_types.filter(j => !STUDENT_TYPES.includes(j))
                                : [...new Set([...this.prefs.job_types, ...STUDENT_TYPES])];
    },
    wants(...types) { return types.some(j => this.prefs.job_types.includes(j)); },
    wantDone() {
      if (!this.prefs.job_types.length) { this.err = t("setup.need_type"); return; }
      return this.run(t("working"), async () => {
        const p = this.prefs;
        await api("/api/preferences", { method: "PATCH", json: Alpine.raw({ job_types: p.job_types, side_types: p.side_types || [], hours: p.hours,
          internship_months: p.internship_months, mandatory_internship: p.mandatory_internship, thesis_level: p.thesis_level }) });
        if (this.graduation.trim() && this.wants(...STUDENT_TYPES))
          await api("/api/answers", { method: "PATCH", json: { "Expected graduation": this.graduation.trim() } });
        await Alpine.store("app").refresh();
        this.go(2);
      });
    },

    // ---- 2 Your CV
    setAgent(a) { this.prefs.agent = a; patchPrefs({ agent: a }); },
    async recheck() {
      this.st.agents = (await api("/api/state")).agents;
      this.agentNames = Object.keys(this.st.agents).filter(k => this.st.agents[k]);
      if (this.agentNames.length && !this.agentNames.includes(this.prefs.agent)) this.setAgent(this.agentNames[0]);
    },
    upload(ev) {
      const f = ev.target.files[0];
      ev.target.value = "";
      if (f) this.run(t("setup.reading"), async () => {
        this.draft = (await api(`/api/import?name=${encodeURIComponent(f.name)}`, { body: f })).profile;
        this.phase = "summary";
      });
    },
    paste() {
      this.run(t("setup.reading"), async () => {
        this.draft = (await api("/api/import-text", { json: { text: this.text } })).profile;
        this.phase = "summary";
      });
    },
    empty() { this.draft = { basics: { links: {} }, summaries: {}, skills: {}, interests: [] }; this.phase = "summary"; },
    count(k) { return Array.isArray(this.draft?.[k]) ? this.draft[k].length : Object.keys(this.draft?.[k] || {}).length; },
    skillCount() { return Object.values(this.draft?.skills || {}).flat().length; },
    async saveProfile(p) {
      await api("/api/data/profile", { method: "PUT", json: p });  // also starts warming the design thumbnails
      this.st.profile = (await api("/api/state")).profile;  // as validated and saved
    },
    looksRight() {
      return this.run(t("working"), async () => {
        await this.saveProfile(Alpine.raw(this.draft));
        await this.askQuestions();
      });
    },
    fixIt() {
      const app = Alpine.store("app");
      app.draft = this.draft;
      app.show("profile");
    },
    async askQuestions() {
      this.phase = "questions"; this.questions = [];
      await this.run(t("setup.thinking"), async () => { this.questions = (await api("/api/profile/gaps", { json: {} })).questions; });
    },
    addAnswers() {
      const text = this.questions.filter(q => (this.replies[q.id] || "").trim())
        .map(q => `Q: ${q.question}\nA: ${this.replies[q.id].trim()}`).join("\n\n");
      if (!text) return this.go(3);
      this.run(t("setup.adding"), async () => {
        const r = await api("/api/profile/merge", { json: { kind: "answers to questions about their CV", text } });
        this.draft = r.profile; this.changes = r.changes; this.phase = "changes";
      });
    },
    keepChanges() { return this.run(t("working"), async () => { await this.saveProfile(Alpine.raw(this.draft)); this.go(3); }); },
    again() { this.phase = "upload"; this.draft = null; },

    // ---- 3 About you: what the CV says is the default
    fillMe() {
      const b = this.st.profile.basics, loc = (b.location || "").split(",").map(s => s.trim()).filter(Boolean);
      this.me = { name: b.name || "", email: b.email || "", phone: b.phone || "", city: loc[0] || "" };
      this.links = Object.entries(b.links || {}).map(([k, v]) => ({ k, v }));
      if (!this.prefs.home_country) this.prefs.home_country = this.guessCountry(loc);
    },
    guessCountry(parts) {  // "Munich, DE" or "Munich, Germany" -> DE
      const names = Object.entries(this.st.countries);
      for (const p of [...parts].reverse().map(s => s.toLowerCase())) {
        const hit = names.find(([code, name]) => p === code.toLowerCase() || p === name.toLowerCase());
        if (hit) return hit[0];
      }
      return "";
    },
    photoUrl() { return this.st.profile.basics.photo ? fileUrl("profile/" + this.st.profile.basics.photo) + "&v=" + this.photoV : ""; },
    photo(ev) {
      const f = ev.target.files[0];
      ev.target.value = "";
      if (f) this.run(t("working"), async () => {
        const r = await api(`/api/upload?kind=photo&name=${encodeURIComponent(f.name)}`, { body: f });
        this.st.profile.basics.photo = r.file; this.photoV = Date.now();
      });
    },
    aboutDone() {
      return this.run(t("working"), async () => {
        const p = structuredClone(Alpine.raw(this.st.profile)), b = p.basics;
        const same = (b.location || "").split(",")[0].trim() === this.me.city.trim();
        Object.assign(b, { name: this.me.name.trim(), email: this.me.email.trim(), phone: this.me.phone.trim(),
                           location: same ? b.location : [this.me.city.trim(), this.country(this.prefs.home_country)].filter(Boolean).join(", "),
                           links: Object.fromEntries(this.links.filter(l => l.k.trim() && l.v.trim()).map(l => [l.k.trim(), l.v.trim()])) });
        await this.saveProfile(p);
        if (!this.prefs.targets.length) this.prefs.targets = [{ code: this.prefs.home_country, permit: "", needs_sponsorship: false }];
        await api("/api/preferences", { method: "PATCH", json: { home_country: this.prefs.home_country, targets: Alpine.raw(this.prefs.targets) } });
        this.go(4);
      });
    },

    // ---- 4 What you're looking for
    fillLooking() {
      this.titles = this.prefs.titles.join(", ");
      this.keywords = Object.entries(this.prefs.keywords).map(([lang, w]) => ({ lang, words: w.join(", ") }));
      const city = (this.st.profile.basics.location || "").split(",")[0].trim();
      if (!this.prefs.places.length && city) this.prefs.places = [city];
      if (!this.titles || this.titles === this.aiTitles) this.suggest();  // still the AI's words: they follow the job types
    },
    suggest() {
      return this.run(t("setup.suggesting"), async () => {
        const r = await api("/api/suggest/targets", { json: { countries: this.prefs.targets.map(c => this.country(c.code)),
                                                               job_types: this.prefs.job_types.map(j => t("jobtypes." + j)) } });
        this.titles = this.aiTitles = r.titles.join(", ");
        this.keywords = Object.entries(r.keywords).map(([lang, w]) => ({ lang, words: w.join(", ") }));
      });
    },
    lang(code) { try { return new Intl.DisplayNames(["en"], { type: "language" }).of(code); } catch { return code; } },
    addTarget() {
      if (this.pick && !this.prefs.targets.some(c => c.code === this.pick)) this.prefs.targets.push({ code: this.pick, permit: "", needs_sponsorship: false });
      this.pick = "";
    },
    lookingDone() {
      return this.run(t("working"), async () => {
        const p = this.prefs;
        const open = p.targets.filter(c => !(c.permit || "").trim()).map(c => this.country(c.code));
        if (open.length) throw new Error(t("setup.need_permit", { list: open.join(", ") }));
        p.titles = csv(this.titles);
        p.keywords = Object.fromEntries(this.keywords.filter(k => k.lang.trim()).map(k => [k.lang.trim().toLowerCase(), csv(k.words)]));
        await api("/api/preferences", { method: "PATCH", json: Alpine.raw({ titles: p.titles, keywords: p.keywords,
          targets: p.targets, places: p.places, radius_km: p.radius_km, remote: p.remote }) });
        await Alpine.store("app").refresh();
        this.go(5);
      });
    },

    // ---- 5 Application details: Continue keeps every value shown, suggestions included
    async detailsDone() {  // required: an earliest start date, and documents (or "I'll add them later")
      const el = document.querySelector("#view .details"), d = el && Alpine.$data(el);
      this.err = "";
      if (d?.prefs && !(d.prefs.start || "").trim()) { this.err = t("setup.need_start"); return window.scrollTo(0, 0); }
      const docs = (await api("/api/upload-folder").catch(() => ({ documents: [] }))).documents;
      if (!docs.length && !this.docsLater) { this.err = t("setup.need_docs"); return window.scrollTo(0, 0); }
      if (d?.prefs) await d.keepAll();
      this.go(6);
    },
    addRule(field, line) { this[field] = addRuleLine(this[field], line); },

    // ---- 6 Cover-letter style: learned from their letters, or from the opening they pick
    letters(ev) {
      const files = [...ev.target.files];
      ev.target.value = "";
      this.run(t("setup.style.reading"), async () => {
        for (const f of files) {
          const r = await api(`/api/extract-text?name=${encodeURIComponent(f.name)}`, { body: f });
          if (!this.samples.trim() && !this.st.letter) {  // the first letter also becomes Profile › Cover letter
            await api("/api/data/letter", { method: "PUT", json: { text: r.text.trim() } });
            this.st.letter = r.text.trim();
          }
          this.samples = [this.samples.trim(), r.text.trim()].filter(Boolean).join("\n\n---\n\n");
        }
      });
    },
    getOpenings() {
      return this.run(t("setup.style.writing"), async () => {
        this.openings = (await api("/api/suggest/openings", { json: { tone: this.tone, length: t("setup.length_" + this.length) } })).openings;
        this.opening = -1;
      });
    },
    makeStyle() {
      const o = this.openings[this.opening];
      const [answers, sample] = this.styleWay === "own"
        ? [{ source: "letters they wrote themselves" }, this.samples.slice(0, 15000)]
        : [{ tone: this.tone, length: t("setup.length_" + this.length), opening_they_picked: o.style }, o.text];
      return this.run(t("setup.style.making"), async () => {
        this.styleText = (await api("/api/suggest/style", { json: { answers, sample } })).markdown;
      });
    },
    styleDone() {  // CV instructions (or the defaults) and letter instructions (learned, kept, or the defaults)
      return this.run(t("working"), async () => {
        const rules = this.cvRules.trim() || this.st.default_cv_rules;
        await api("/api/data/cv_rules", { method: "PUT", json: { text: rules } });
        this.st.cv_rules = this.cvRules = rules;
        const style = this.styleText.trim() || this.st.style || this.st.default_style;
        await api("/api/data/style", { method: "PUT", json: { text: style } });
        this.st.style = style;
        this.go(7);
      });
    },

    // ---- 7 CV design (inside the Designs component). Picking one, even the default, settles "Finish setting up"'s item.
    hide(id) {
      if (this.prefs.setup_hidden.includes(id)) return;
      this.prefs.setup_hidden = [...this.prefs.setup_hidden, id];
      api("/api/preferences", { method: "PATCH", json: { setup_hidden: Alpine.raw(this.prefs.setup_hidden) } }).catch(() => {});
    },

    // ---- 10 Autopilot, then done: the first search starts and the Board opens
    finish(withSearch) {
      return this.run(t("working"), async () => {
        if (withSearch && this.daily) await api("/api/preferences", { method: "PATCH", json: { search_time: this.time } });
        if (!this.st.style) await api("/api/data/style", { method: "PUT", json: { text: this.st.default_style } });  // skipped: the default
        await api("/api/preferences", { method: "PATCH", json: { setup_done: true, setup_step: 0 } });
        await Alpine.store("app").refresh();
        api("/api/search/run", { method: "POST" }).catch(() => {});
        Alpine.store("app").show("board");
      });
    },
    example(name) {
      this.run(t("working"), async () => {
        await api(`/api/example/${name}`, { method: "POST" });
        await Alpine.store("app").refresh();
        Alpine.store("app").show("board");
      });
    },
  }));

  // Your documents (setup step 4, Profile › Application details): certificates, transcripts, references… kept in the
  // upload folder, next to the CV and letter of the application open at the moment.
  Alpine.data("uploadDocs", () => ({
    folder: "", docs: [], err: "",
    async init() { await this.load(); },
    async load() {
      try { const r = await api("/api/upload-folder"); this.folder = r.folder; this.docs = r.documents; } catch (e) { this.err = e.message; }
    },
    async add(ev) {
      this.err = "";
      for (const f of [...ev.target.files]) {
        try { await api(`/api/upload-folder/files?name=${encodeURIComponent(f.name)}`, { body: f }); } catch (e) { this.err = e.message; }
      }
      ev.target.value = "";
      await this.load();
    },
    openFolder() { api("/api/upload-folder/open", { method: "POST" }).catch(e => { this.err = e.message; }); },
  }));

  // The Board's "Finish setting up" card: what's actually missing, so it works for installs that finished setup long ago
  Alpine.data("finishSetup", () => ({
    items: [],
    async init() { try { this.items = (await api("/api/setup/missing")).missing; } catch {} },
    open(id) { Alpine.store("app").show(...FINISH_LINKS[id]); },
    async dismiss(id) {
      this.items = this.items.filter(x => x !== id);
      const app = Alpine.store("app");
      if (await patchPrefs({ setup_hidden: [...(app.state?.preferences.setup_hidden || []), id] })) app.refresh();
    },
  }));
});
