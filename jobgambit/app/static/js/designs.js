// Designs: the gallery (your own CV in every design and saved look) and the customiser at #designs/<id or look name>
// (every section, every page live, saved looks).
const FONTS = ["Libertinus Serif", "New Computer Modern", "Lato", "Fira Sans"];
const STYLE_CHOICES = {
  section_style: ["rule", "caps", "bar", "plain"], header: ["left", "center", "band"], layout: ["single", "sidebar"], side: ["left", "right"],
  date_pos: ["right", "left"], skills_style: ["lines", "tags", "stacked"], languages_style: ["text", "dots"], photo: ["circle", "square", "none"],
  paper: ["a4", "us-letter"],
};

document.addEventListener("alpine:init", () => {
  Alpine.data("designs", () => ({
    FONTS, STYLE_CHOICES,
    list: [], presets: {}, keys: {}, current: "", defaults: {}, sections: [], loading: true, loadErr: "", failed: {},
    mode: "gallery", look: "", name: "", design: "", s: null, style: {}, rows: [], timer: null, err: "",
    pages: [], limit: 0, updating: false, ctrl: null, saveName: "", saveUse: false, saveErr: "",
    async init() {
      await this.load();
      this.$watch("$store.app.sub", v => this.route(v));
      this.route(Alpine.store("app").sub);
    },
    async load() {
      this.loadErr = "";
      try {
        const r = await api("/api/designs");
        Object.assign(this, { list: r.designs, presets: r.presets, keys: r.keys, current: r.current, defaults: r.defaults, sections: r.sections });
      } catch (e) { this.loadErr = e.message; }
      this.loading = false;
    },
    // ---- gallery
    link(id) { return "#designs/" + encodeURIComponent(id); },
    go(id = "") {
      const app = Alpine.store("app");
      app.sub = id && encodeURIComponent(id);
      history.replaceState(null, "", "#designs" + (id ? "/" + app.sub : ""));
    },
    route(sub) {
      let id = "";
      try { id = decodeURIComponent(sub || ""); } catch {}
      if (id && (this.presets[id] || this.list.some(d => d.id === id))) this.edit(id);
      else { this.mode = "gallery"; this.ctrl?.abort(); }
    },
    items(kind) {
      return kind === "looks"
        ? Object.entries(this.presets).map(([n, p]) => ({ id: n, name: n, sub: t("designs.based_on", { name: this.base(p.design)?.name }) }))
        : this.list.map(d => ({ id: d.id, name: d.name, sub: d.family, desc: d.description }));
    },
    groups() { return [{ id: "looks", items: this.items("looks") }, { id: "all", items: this.items("all") }]; },
    mine() { return [...this.items("looks"), ...this.items("all")].find(it => it.id === this.current); },
    thumb(id) { return `/api/designs/${encodeURIComponent(id)}/thumb.png?v=${this.keys[id]}&t=${TOKEN}`; },
    async use(id) {
      try {
        await api("/api/designs/current", { method: "PUT", json: { design: id } });
        this.current = id;
        Alpine.store("toast").show(t("designs.now_using", { name: this.title() }));
      } catch (e) { Alpine.store("toast").show(e.message, "bad"); }
    },
    base(id) { return this.list.find(d => d.id === id) || this.list[0]; },
    // ---- customiser
    title() { return this.presets[this.look] ? this.look : this.base(this.look)?.name; },
    edit(id) {  // a design id or a saved look's name
      const preset = this.presets[id];
      this.look = id;
      this.design = preset ? preset.design : id;
      this.name = preset ? id : "";
      const d = this.base(this.design);
      this.s = JSON.parse(JSON.stringify(preset?.settings || {}));  // structuredClone can't copy Alpine's proxies
      this.style = { ...this.defaults, ...d.style, ...(this.s.style || {}) };
      const order = this.s.order?.length ? this.s.order : d.order?.length ? d.order : this.sections;
      const side = this.s.sidebar || d.sidebar || [];
      this.rows = order.map(key => ({ key, show: !(this.s.hidden || []).includes(key), title: (this.s.titles || {})[key] || "",
                                      side: side.includes(key), max: (this.s.max_items || {})[key] || "" }));
      if (this.mode !== "designer") { this.pages.forEach(u => URL.revokeObjectURL(u)); this.pages = []; }
      this.mode = "designer";
      this.refresh(true);
    },
    changeBase() {
      if (!this.presets[this.look]) return this.go(this.design);  // another design: its own page
      this.s.style = {};
      this.style = { ...this.defaults, ...this.base(this.design).style };
      this.refresh();
    },
    settings() {
      const d = this.base(this.design);
      const style = Object.fromEntries(Object.entries(this.style).filter(([k, v]) => v !== ({ ...this.defaults, ...d.style })[k]));
      return {
        ...this.s, style, order: this.rows.map(r => r.key), hidden: this.rows.filter(r => !r.show).map(r => r.key),
        sidebar: this.rows.filter(r => r.side).map(r => r.key),
        titles: Object.fromEntries(this.rows.filter(r => r.title.trim()).map(r => [r.key, r.title.trim()])),
        max_items: Object.fromEntries(this.rows.filter(r => +r.max > 0).map(r => [r.key, +r.max])),
      };
    },
    sectionName(key) { const k = "profile.sections." + key; return t(k) !== k ? t(k) : key; },
    move(i, d) { const j = i + d; if (j >= 0 && j < this.rows.length) [this.rows[i], this.rows[j]] = [this.rows[j], this.rows[i]]; this.refresh(); },
    refresh(now) {  // first render at once, then 250 ms after the last change; a newer request cancels the older one
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.render(), now ? 0 : 250);
    },
    async render() {
      this.ctrl?.abort();
      const ctrl = this.ctrl = new AbortController();
      this.updating = true;
      try {
        const r = await api("/api/designs/preview", { json: { design: this.design, settings: this.settings() }, signal: ctrl.signal });
        this.pages.forEach(u => URL.revokeObjectURL(u));
        this.pages = r.pages.map(svg => URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" })));
        this.limit = r.limit; this.err = "";
      } catch (e) { if (e.name !== "AbortError") this.err = e.message; }
      if (this.ctrl === ctrl) this.updating = false;
    },
    openSave() { this.saveName = this.name; this.saveUse = false; this.saveErr = ""; this.$refs.save.showModal(); },
    async save() {
      const name = this.saveName.trim();
      if (!name) { this.saveErr = t("designer.need_name"); return; }
      try {
        await api("/api/designs/presets", { json: { name, design: this.design, settings: this.settings(), default: this.saveUse } });
        this.$refs.save.close();
        await this.load();
        Alpine.store("toast").show(t("designs.look_saved", { name }));
        this.go(name);
      } catch (e) { this.saveErr = e.message; }
    },
    async removePreset(name) {
      if (!confirm(t("designs.delete_confirm", { name }))) return;
      await api(`/api/designs/presets/${encodeURIComponent(name)}`, { method: "DELETE" });
      await this.load();
      this.go();
    },
    async signature(ev) {
      const f = ev.target.files[0];
      if (!f) return;
      try {
        const r = await api(`/api/upload?kind=signature&name=${encodeURIComponent(f.name)}`, { body: f });
        this.s.signature = r.file;
        Alpine.store("toast").show(t("designer.signature_saved"));
      } catch (e) { Alpine.store("toast").show(e.message, "bad"); }
    },
  }));
});
