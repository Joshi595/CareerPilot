// Settings in groups (#settings/<group>), each change saved as it's made, plus "Ask JobQuill".
// The email and alerts parts stand alone, so setup can include them too.
const GROUPS = ["search", "sites", "ranking", "writing", "ai", "email", "alerts", "appearance", "data"];
window.shownGroups = () => GROUPS;  // Settings' groups this computer shows
const PROVIDER_NAMES = { gmail: "Gmail", outlook: "Outlook / Hotmail", yahoo: "Yahoo", icloud: "iCloud", gmx: "GMX", "web.de": "WEB.DE",
                         zoho: "Zoho", fastmail: "Fastmail" };
const PW_RULES = { length: p => p.length >= 12 && p.length <= 16, upper: p => /[A-Z]/.test(p), lower: p => /[a-z]/.test(p),
                   digit: p => /\d/.test(p), special: p => /[!#%+\-=?@_]/.test(p), space: p => !/\s/.test(p), repeat: p => !/(.)\1\1/.test(p) };

const randomFrom = (chars, n) => Array.from(crypto.getRandomValues(new Uint32Array(n)), x => chars[x % chars.length]).join("");

window.patchPrefs = async json => {  // {key: value} -> saved; the group header shows "Saved"
  try { await api("/api/preferences", { method: "PATCH", json }); Alpine.store("saved").ping(); return true; }
  catch (e) { Alpine.store("toast").show(e.message, "bad"); return false; }
};

window.applyTheme = theme => {
  if (!theme || theme === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
};

window.copyText = async text => {
  try { await navigator.clipboard.writeText(text); Alpine.store("toast").show(t("alerts.copied")); }
  catch { Alpine.store("toast").show(text); }
};

document.addEventListener("alpine:init", () => {
  Alpine.store("saved", {
    on: false, timer: 0,
    ping() { this.on = true; clearTimeout(this.timer); this.timer = setTimeout(() => (this.on = false), 2500); },
  });

  Alpine.data("settings", () => ({
    GROUPS, JOB_TYPES, LISTS: { prefer: "prefer_companies", avoid: "avoid_companies", exclude: "exclude_words" },
    st: null, p: null, err: "", pick: "", keywords: [], dropped: [], style: "", lessons: "", sources: [], health: {}, coverage: {}, aiTest: "", confirm: "",
    newWords: null, wordsTimer: 0, wordsBusy: false,

    async init() { await this.load(); },
    async load() {
      try {
        const [st, src, s, cov] = await Promise.all([api("/api/state"), api("/api/sources"), api("/api/search/status"),
                                                     api("/api/search/coverage").catch(() => ({}))]);
        this.coverage = cov;
        this.st = st; this.p = st.preferences;
        this.keywords = Object.entries(this.p.keywords).map(([lang, w]) => ({ lang, words: w.join(", ") }));
        this.style = st.style || st.default_style; this.lessons = st.lessons;
        this.sources = src.sources; this.health = s.health; this.dropped = s.dropped || [];
      } catch (e) { this.err = t("settings.load_failed") + " " + e.message; }
    },
    groups() { return shownGroups(); },
    group() { return this.groups().includes(this.$store.app.sub) ? this.$store.app.sub : "search"; },
    go(g) { this.$store.app.sub = g; history.replaceState(null, "", "#settings/" + g); },
    country(code) { return this.st.countries[code] || code; },
    save(key) { return patchPrefs({ [key]: Alpine.raw(this.p[key]) }); },
    saveList(key, text) { this.p[key] = csv(text); return this.save(key); },
    toggleSide(j) {  // unticked = "Also": searched half as often; saving re-runs the search
      const s = this.p.side_types || [];
      this.p.side_types = s.includes(j) ? s.filter(x => x !== j) : [...s, j];
      this.save("side_types");
    },
    typesChanged() {  // the search follows on its own; new search words for these job types come as a suggestion to take or leave
      this.save("job_types");
      this.newWords = null; clearTimeout(this.wordsTimer);
      if (!this.p.job_types.length) return;
      this.wordsTimer = setTimeout(async () => {  // a few clicks in a row: one AI run
        this.wordsBusy = true;
        try {
          this.newWords = await api("/api/suggest/targets", { json: { countries: this.p.targets.map(c => this.country(c.code)),
                                                                      job_types: this.p.job_types.map(j => t("jobtypes." + j)) } });
        } catch (e) { Alpine.store("toast").show(e.message, "bad"); }
        this.wordsBusy = false;
      }, 2500);
    },
    useWords() {
      this.p.titles = this.newWords.titles;
      this.keywords = Object.entries(this.newWords.keywords).map(([lang, w]) => ({ lang, words: w.join(", ") }));
      this.save("titles"); this.saveKeywords();
      this.newWords = null;
    },
    saveKeywords() {
      this.p.keywords = Object.fromEntries(this.keywords.filter(k => k.lang.trim()).map(k => [k.lang.trim().toLowerCase(), csv(k.words)]));
      return this.save("keywords");
    },
    addTarget() {
      if (this.pick && !this.p.targets.some(c => c.code === this.pick)) {
        this.p.targets.push({ code: this.pick, permit: "", needs_sponsorship: false });
        this.save("targets");
      }
      this.pick = "";
    },
    toggleSource(s) {
      this.p.sources_off = this.p.sources_off.includes(s) ? this.p.sources_off.filter(x => x !== s) : [...this.p.sources_off, s];
      return this.save("sources_off");
    },
    sourceHealth(s) {  // its last result, and how often each search word comes round there
      const h = this.health[s], c = this.coverageOf(s);
      const last = !h ? t("settings.never") : h.error || `${h.count} ${t("settings.found")} · ${h.last}`;
      return c ? last + " · " + t(c.gap ? "settings.coverage_gap" : "settings.coverage", { n: c.queries, d: c.cycle, back: c.lookback }) : last;
    },
    coverageOf(s) { return Object.values(this.coverage).map(x => x[s]).find(Boolean); },
    async saveText(kind) {
      try { await api(`/api/data/${kind}`, { method: "PUT", json: { text: this[kind] } }); Alpine.store("saved").ping(); }
      catch (e) { Alpine.store("toast").show(e.message, "bad"); }
    },
    async testAI() {
      this.aiTest = t("settings.testing");
      try {
        const r = await api(`/api/ai/test/${this.p.agent}`, { method: "POST" });
        this.aiTest = r.ok ? t("settings.ai_works") : t("settings.ai_failed", { e: r.error });
      } catch (e) { this.aiTest = t("settings.ai_failed", { e: e.message }); }
    },

    // Ask JobQuill: the server proposes, the person applies
    label(key) { const s = t("settings.keys." + key); return s === "settings.keys." + key ? key : s; },
    fmt(v) {
      if (v === "" || v == null || (Array.isArray(v) && !v.length)) return t("settings.ask.empty");
      if (typeof v === "boolean") return v ? t("settings.ask.on") : t("settings.ask.off");
      if (Array.isArray(v)) return v.map(x => typeof x === "object" ? x.code || JSON.stringify(x) : x).join(", ");
      if (typeof v === "object") return Object.entries(v).map(([k, x]) => `${k}: ${Array.isArray(x) ? x.join(", ") : x}`).join(" · ");
      return String(v);
    },

    async data(k) {
      if (k === "folder") api("/api/open-folder", { method: "POST" });
      if (k === "backup") download(await api("/api/backup"), "jobquill-backup.zip");
      if (k === "csv") download(await api("/api/export/applications.csv"), "my-applications.csv");
      if (k === "json") {
        const data = await api("/api/export/json-resume");
        download(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }), "resume.json");
      }
      if (k === "prompts") api("/api/playbooks/open", { method: "POST" });
    },
    async deleteAll() {
      if (this.confirm !== "DELETE") return;
      await api("/api/delete-everything", { json: { confirm: "DELETE" } });
      await api("/api/quit", { method: "POST" });
      document.body.innerHTML = `<main><div class="card">${t("settings.deleted")}</div></main>`;
    },
  }));

  Alpine.data("emailPart", () => ({  // parts/email.html
    e: null, password: "", busy: false, result: "", err: "", aiMail: true,
    async init() {
      try { this.e = await api("/api/email"); this.aiMail = Alpine.store("app").state?.preferences.ai_mail ?? true; }
      catch (x) { this.err = x.message; }
    },
    name(p) { return PROVIDER_NAMES[p] || (p === "custom" ? t("email.custom_name") : p); },
    fields() { return { address: this.e.address, provider: this.e.provider, imap: this.e.imap, smtp: this.e.smtp, port: this.e.port }; },
    async detect() {
      this.err = this.result = "";
      if (!this.e.address.includes("@")) return;
      try {
        const d = await api("/api/email/detect?address=" + encodeURIComponent(this.e.address));
        Object.assign(this.e, { provider: d.provider, imap: d.imap, smtp: d.smtp, port: d.port });
      } catch {}
      await this.save();
    },
    async save() {
      try { await api("/api/email", { method: "PUT", json: this.fields() }); Alpine.store("saved").ping(); }
      catch (x) { this.err = x.message; }
    },
    async connect() {
      if (!this.e.address.includes("@")) { this.err = t("email.need_address"); return; }
      this.busy = true; this.result = this.err = "";
      try {
        await api("/api/email", { method: "PUT", json: { ...this.fields(), password: this.password } });
        this.password = "";
        this.e = await api("/api/email");
        this.result = (await api("/api/email/test", { method: "POST" })).result;
      } catch (x) { this.err = x.message; }
      this.busy = false;
    },
  }));

  Alpine.data("alertsPart", () => ({  // parts/alerts.html
    CARDS: ["phone", "telegram", "email", "hook", "other"],
    loaded: false, channels: [], events: [], always: [], on: [], email: "", alertEmail: false, desktop: true, open: "",
    topic: "", token: "", hook: "", other: "", busy: false, err: "",
    async init() {
      this.topic = this.newTopic();
      try {
        const [a, st] = await Promise.all([api("/api/alerts"), api("/api/state")]);
        Object.assign(this, { channels: a.channels, events: a.events, always: a.always, email: a.email, desktop: st.preferences.desktop_alerts,
                              on: st.preferences.alert_events, alertEmail: st.preferences.alert_email, loaded: true });
      } catch (e) { this.err = e.message; }
    },
    newTopic() { return "jobquill-" + randomFrom("abcdefghijkmnpqrstuvwxyz23456789", 12); },
    copy(text) { copyText(text); },
    async run(fn, done) {
      this.busy = true; this.err = "";
      try { await fn(); if (done) Alpine.store("toast").show(done); } catch (e) { this.err = e.message; Alpine.store("toast").show(e.message, "bad"); }
      this.busy = false;
    },
    added(r) { this.channels = r.channels; Alpine.store("toast").show(t(r.existing ? "alerts.already" : "alerts.added")); },
    add(url) {
      return this.run(async () => {
        this.added(await api("/api/alerts/channels", { json: { url } }));
        this.open = this.hook = this.other = "";
      });
    },
    telegram() {
      return this.run(async () => {
        this.added(await api("/api/alerts/telegram", { json: { token: this.token } }));
        this.open = this.token = "";
      });
    },
    test(i) { return this.run(() => api("/api/alerts/test", { json: { i } }), t("alerts.sent")); },
    remove(i) {
      if (!confirm(t("alerts.remove_confirm", { c: this.channels.find(c => c.i === i)?.kind || "" }))) return;
      return this.run(async () => { this.channels = (await api(`/api/alerts/channels/${i}`, { method: "DELETE" })).channels; }, t("alerts.removed"));
    },
  }));

});
