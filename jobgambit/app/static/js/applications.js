// My applications: grouped by stage (attention first), filters, a detail drawer with replies, form answers and follow-ups.
// The mails that need the person, and the interviews coming up, are on Needs you (needs.js).
const STAGE_OF = { pending: "send", applied: "applied", assessment: "test", interview: "interview", offer: "offer",
                   rejected: "closed", withdrawn: "closed", no_response: "closed", ghosted: "closed" };
const STAGES = ["interview", "test", "offer", "send", "applied", "closed"];

document.addEventListener("alpine:init", () => {
  Alpine.data("applications", () => ({
    rows: [], statuses: [], stages: STAGES, stage: "", q: "", sort: "new", loading: true, err: "", busy: false, cur: null, drafts: {},
    closedOpen: false,  // Closed rows render only once opened: most of a long list
    posting: null, full: false,  // the drawer's job description (interviews, tests, offers): null while loading
    async init() { await this.load(); },
    async load() {
      this.err = "";
      try { ({ rows: this.rows, statuses: this.statuses } = await api("/api/applications")); }
      catch (e) { this.err = t("applications.load_failed") + " " + e.message; }
      this.loading = false;
    },
    soon(r) { return new Date(r.data.interview_at || 0).getTime() > Date.now() - 2 * 36e5; },  // an interview still ahead
    async saveInterview(r) {
      try { await api(`/api/applications/${r.id}`, { method: "PUT", json: { interview_at: r.data.interview_at || "" } }); Alpine.store("toast").show(t("saved")); }
      catch (e) { Alpine.store("toast").show(e.message, "bad"); }
    },
    reload() { this.loading = true; this.load(); },
    stageOf(r) { return STAGE_OF[r.status] || "applied"; },
    count(s) { return s ? this.rows.filter(r => this.stageOf(r) === s).length : this.rows.length; },
    groups() {
      const q = this.q.toLowerCase();
      const at = r => this.stageOf(r) === "closed" ? this.closedAt(r) : r.date_applied || "";  // Closed: the latest rejection first
      const by = { new: (a, b) => at(b).localeCompare(at(a)) || b.id - a.id,
                   old: (a, b) => at(a).localeCompare(at(b)) || a.id - b.id,
                   company: (a, b) => a.company.localeCompare(b.company) }[this.sort];
      const rows = this.rows.filter(r => (!this.stage || this.stageOf(r) === this.stage)
        && (!q || `${r.role} ${r.company} ${r.note || ""}`.toLowerCase().includes(q))).sort(by);
      return STAGES.map(s => ({ s, rows: rows.filter(r => this.stageOf(r) === s) })).filter(g => g.rows.length);
    },
    closedAt(r) {  // when it closed: its latest reply or status set by hand (the day it was sent when neither is known)
      return [r.date_applied, r.data.status_at, ...(r.data.replies || []).map(x => x.date)].filter(Boolean).sort().pop() || "";
    },
    totals() {
      const open = this.rows.filter(r => !["send", "closed"].includes(this.stageOf(r))).length;
      return t("applications.totals", { n: this.rows.length, open });
    },
    days(d) { return d ? Math.floor((Date.now() - new Date(d)) / 864e5) : 0; },
    nudge(r) {  // applied, nothing heard back, older than the follow-up setting
      const wait = Alpine.store("app").state?.preferences.follow_up_days || 10;
      return r.status === "applied" && !(r.data.replies || []).length && !r.follow_up && this.days(r.date_applied) >= wait;
    },
    methodIcon(m = "") {
      return m.includes("email") ? "mail" : m.includes("board") ? "layout-list" : m ? "globe" : "file-text";
    },
    timeline(r) {  // replies and the follow-ups sent, newest first
      return [...(r.data.replies || []), ...(r.data.followups || []).map(f => ({ ...f, out: true }))]
        .sort((a, b) => new Date(b.date) - new Date(a.date));
    },
    doc(r, kind) { return r.files.find(f => f.includes(kind === "cv" ? "_CV_" : "Cover_Letter")); },
    openFolder(r) { api(`/api/applications/${r.id}/folder`, { method: "POST" }).catch(e => Alpine.store("toast").show(e.message, "bad")); },
    file(r, name) { return fileUrl(`applications/${r.folder}/${name}`); },
    show(r) { this.cur = r; this.posting = null; this.full = false; this.$refs.drawer.showModal(); this.loadPosting(r); },
    interviewing(r) { return ["interview", "assessment", "offer"].includes(r.status); },
    async loadPosting(r) {  // what the job asks, to prepare with: only once there is an interview, test or offer
      if (!this.interviewing(r) || this.posting) return;
      try { const x = await api(`/api/applications/${r.id}/posting`); if (this.cur === r) this.posting = x.text; }
      catch { if (this.cur === r) this.posting = ""; }
    },
    rowClick(e, r) { if (!e.target.closest("a, button, select, input, textarea")) this.show(r); },
    async save(r, field) {
      try { await api(`/api/applications/${r.id}`, { method: "PUT", json: { [field]: r[field] } }); Alpine.store("toast").show(t("saved")); }
      catch (e) { Alpine.store("toast").show(e.message, "bad"); }
      if (field === "status") { Alpine.store("app").counts(); if (r === this.cur) this.loadPosting(r); }
    },
    async exportCsv() { download(await api("/api/export/applications.csv"), "my-applications.csv"); },
    async followUp(r) {
      if (this.cur !== r) this.show(r);
      this.busy = true;
      try { this.drafts[r.id] = await api(`/api/applications/${r.id}/followup`, { method: "POST" }); }
      catch (e) { Alpine.store("toast").show(e.message, "bad"); }
      this.busy = false;
    },
    async sendFollowUp(r) {
      try { await api(`/api/applications/${r.id}/send`, { json: this.drafts[r.id] }); delete this.drafts[r.id];
            r.follow_up = new Date().toISOString().slice(0, 10); Alpine.store("toast").show(t("applications.sent")); }
      catch (e) { Alpine.store("toast").show(e.message, "bad"); }
    },
    async copy(text) { await navigator.clipboard.writeText(text); Alpine.store("toast").show(t("applications.copied")); },
    prepare(r) { this.$refs.drawer.close(); Alpine.store("app").practiceApp = String(r.id); Alpine.store("app").show("practice"); },
  }));
});
