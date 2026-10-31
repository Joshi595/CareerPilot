// Needs you: everything only the person can do. Interviews coming up, mails to act on (an interview to answer, a test,
// an offer, something to confirm or send), mails whose application isn't clear, and what JobGambit filed by itself.
const SIGNAL_TONE = { interview: "ok", assessment: "ok", offer: "ok", action: "warn", unclear: "warn", rejected: "danger" };
window.when = x => new Date(x).toLocaleString(undefined, { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

document.addEventListener("alpine:init", () => {
  Alpine.data("needs", () => ({
    items: [], filed: [], coming: [], apps: [], okAt: "", mailOn: false, failing: "", loading: true, err: "", busy: false,
    open: 0, relink: 0, picks: {}, at: {}, SIGNAL_TONE,
    async init() { await this.load(); },
    async load() {
      this.err = "";
      try {
        const r = await api("/api/mail/items");
        Object.assign(this, { items: r.items, filed: r.filed, coming: r.coming_up, okAt: r.ok_at, mailOn: r.configured, failing: r.alerts_failing });
        for (const m of this.items) this.picks[m.id] ??= String(m.app_id || (m.candidates[0]?.why.startsWith("the AI's pick") ? m.candidates[0].id : ""));
      } catch (e) { this.err = e.message; }
      this.loading = false;
    },
    act() { return this.items.filter(m => m.group === "act").sort((a, b) => (SIGNAL_TONE[b.signal] === "ok") - (SIGNAL_TONE[a.signal] === "ok")); },
    link() { return this.items.filter(m => m.group === "link"); },
    asks(m) { return !m.app_id && !m.account; },  // which application? (a portal account mail is the company's)
    timeless(m) { return ["interview", "assessment"].includes(m.signal) && !m.at && (m.app_id || this.picks[m.id]); },
    async loadApps() { if (!this.apps.length) try { this.apps = (await api("/api/applications")).rows; } catch {} },
    others(m) {  // after its candidates (with why): every other application, for one that matched none
      const ids = new Set(m.candidates.map(c => c.id));
      return this.apps.filter(a => !ids.has(a.id)).sort((a, b) => a.company.localeCompare(b.company));
    },
    mailStale() { return this.mailOn && (!this.okAt || Date.now() - new Date(this.okAt) > 3 * 36e5); },
    toggle(m) { this.open = this.open === m.id ? 0 : m.id; if (this.open) this.loadApps(); },
    openRelink(m) { this.relink = this.relink === m.id ? 0 : m.id; this.picks[m.id] = ""; this.loadApps(); },
    async settle(m, json) {
      this.busy = true;
      try {
        const app = Number(this.picks[m.id] || m.app_id || 0);
        if (this.at[m.id] && app) {
          await api(`/api/applications/${app}`, { method: "PUT", json: { interview_at: this.at[m.id] } });
          Alpine.store("toast").show(t("needs.time_saved"));
        }
        await api(`/api/mail/items/${m.id}`, { json });
        this.open = this.relink = 0;
        await this.load();
        Alpine.store("app").counts();
      } catch (e) { Alpine.store("toast").show(e.message, "bad"); }
      this.busy = false;
    },
    done(m) {  // act: done (with the application picked, if asked); link: this application (the server sets the status the mail means)
      const app = Number(this.picks[m.id] || m.app_id || 0);
      return this.settle(m, app ? { app_id: app } : {});
    },
    dismiss(m) { return this.settle(m, {}); },
    async checkMail() {
      this.busy = true;
      try { const r = await api("/api/email/check", { method: "POST" });
            Alpine.store("toast").show(t("needs.mail_result", { n: r.changed.length, u: r.to_check.length })); await this.load(); Alpine.store("app").counts(); }
      catch (e) { Alpine.store("toast").show(e.message, "bad"); }
      this.busy = false;
    },
    async recheck() {
      this.busy = true;
      try { const r = await api("/api/mail/recheck", { method: "POST" }); Alpine.store("toast").show(t("needs.rechecked", { n: r.found }));
            await this.load(); Alpine.store("app").counts(); }
      catch (e) { Alpine.store("toast").show(e.message, "bad"); }
      this.busy = false;
    },
    prepare(id) { const app = Alpine.store("app"); app.practiceApp = String(id); app.show("practice"); },
  }));
});
