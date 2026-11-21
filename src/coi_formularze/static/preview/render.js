// DOM rendering of one Tab of a FormModel, plus validation display and the
// FINISH-tab answer summary. A tab is rendered once; afterwards States only
// toggle visibility and read-only on the existing elements, so typing keeps focus.

import { tree } from "./demo-data.js";
import { richText } from "./markdown.js";
import { CONTAINER_TYPES, INPUT_TYPES, isTree } from "./model.js";
import { check, isEmpty } from "./validators.js";

const UPLOAD = { extensions: [".jpg", ".pdf", ".png", ".jpeg"], maxBytes: 10 * 1024 * 1024, maxFiles: 3 };

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}

const maskZip = (v) => {
  const d = v.replace(/\D/g, "").slice(0, 5);
  return d.length > 2 ? `${d.slice(0, 2)}-${d.slice(2)}` : d;
};

const isCell = (q) => ["x", "y", "rows", "cols"].every((k) => Number.isInteger(q[k]) && q[k] >= 0)
  && q.rows > 0 && q.cols > 0;

/** Column count of a grid: the widest x + cols among its questions (12 when unknown). */
const gridWidth = (questions) => Math.max(0, ...questions.filter(isCell).map((q) => q.x + q.cols)) || 12;

/** A description block, or null when the (possibly HTML) text has nothing visible. */
function desc(text, cls) {
  const html = richText(text);
  return html ? h("div", { class: cls, html }) : null;
}

export class Renderer {
  constructor(model, { onChange, onStructure }) {
    this.model = model;
    this.onChange = onChange;
    this.onStructure = onStructure;
    this.views = [];
    this.seq = 0;
  }

  // ---- tab ----

  renderTab(tab) {
    this.views = [];
    const grid = this.gridEl("fp-grid fp-tabgrid", tab.questions);
    for (const q of tab.questions) {
      const node = this.topLevel(q.field);
      this.place(node, q, grid.dataset.width);
      grid.append(node);
    }
    return grid;
  }

  gridEl(cls, questions) {
    const width = gridWidth(questions);
    const grid = h("div", { class: cls, "data-width": width });
    grid.style.gridTemplateColumns = `repeat(${width}, minmax(0, 1fr))`;
    return grid;
  }

  place(node, q, width) {
    if (isCell(q) && q.x + q.cols <= Number(width)) {
      node.style.gridArea = `${q.y + 1} / ${q.x + 1} / span ${q.rows} / span ${q.cols}`;
    } else {
      node.style.gridColumn = "1 / -1";
    }
  }

  topLevel(f) {
    switch (f.type) {
      case "SECTION":
        return this.section(f, false);
      case "REPEATING_SECTION":
        return this.repeating(f);
      case "INFO_SECTION": {
        const card = h("section", { class: "fp-card" }, this.techTag(f, null));
        if (f.title) card.append(h("h2", { class: "fp-card-title" }, f.title));
        { const d = desc(f.description, "fp-md"); if (d) card.append(d); }
        this.register(f, null, card);
        return card;
      }
      case "CONSENT_SECTION": {
        // The cell carries the tech tag and error line; States hide the whole card.
        const card = h("section", { class: "fp-card" });
        if (f.title) card.append(h("h2", { class: "fp-card-title" }, f.title));
        card.append(this.field(f, null, false));
        this.views.find((v) => v.field === f).el = card;
        return card;
      }
      default: {
        // A field outside any section (a rule violation): keep it visible in its own card.
        const card = h("section", { class: "fp-card fp-orphan" });
        card.append(this.field(f, null, true));
        return card;
      }
    }
  }

  section(f, nested) {
    const card = h("section", { class: nested ? "fp-subsection" : "fp-card" }, this.techTag(f, null));
    if (f.title) card.append(h(nested ? "h3" : "h2", { class: "fp-card-title" }, f.title));
    const d = desc(f.description, "fp-md fp-card-desc");
    if (d) card.append(d);
    card.append(this.grid(f.repeatingSectionGrid, null));
    this.register(f, null, card);
    return card;
  }

  repeating(f) {
    const card = h("section", { class: "fp-card" }, this.techTag(f, null));
    if (f.title) card.append(h("h2", { class: "fp-card-title" }, f.title));
    const d = desc(f.description, "fp-md fp-card-desc");
    if (d) card.append(d);
    const n = this.model.instanceCount(f);
    for (let i = 0; i < n; i++) {
      const head = h("div", { class: "fp-instance-head" }, h("h3", {}, `${f.title || "Pozycja"} nr ${i + 1}`));
      if (n > 1) {
        head.append(h("button", {
          type: "button", class: "fp-linkbtn",
          onclick: () => { this.model.removeInstance(f, i); this.onStructure(); },
        }, "Usuń"));
      }
      card.append(h("div", { class: "fp-instance" }, head, this.grid(f.repeatingSectionGrid, i)));
    }
    card.append(h("button", {
      type: "button", class: "fp-btn fp-btn-outline fp-add",
      onclick: () => { this.model.addInstance(f); this.onStructure(); },
    }, "+ Dodaj kolejny"));
    this.register(f, null, card);
    return card;
  }

  grid(questions, index) {
    const grid = this.gridEl("fp-grid", questions);
    for (const q of questions) {
      const f = q.field;
      const node = CONTAINER_TYPES.has(f.type) ? this.section(f, true) : this.field(f, index, true);
      this.place(node, q, grid.dataset.width);
      grid.append(node);
    }
    return grid;
  }

  // ---- fields ----

  /** A field cell: label, control, help text, error line. */
  field(f, index, asCell) {
    const id = `fp-${this.model.key(f, index).replace("#", "-")}-${++this.seq}`;
    const cell = h("div", { class: asCell ? "fp-cell" : "fp-cell fp-inline" }, this.techTag(f, index));
    const view = this.register(f, index, cell);
    const error = h("div", { class: "fp-error", role: "alert", hidden: true });
    view.setError = (msg) => {
      error.hidden = !msg;
      error.textContent = msg ? msg : "";
      cell.classList.toggle("fp-invalid", Boolean(msg));
    };
    const clearError = () => view.setError(null);

    const label = f.title
      ? h("label", { class: "fp-label", for: id }, f.title,
        f.tooltipMessage ? h("span", { class: "fp-tip", title: f.tooltipMessage, "aria-label": f.tooltipMessage }, "?") : null)
      : null;
    const help = ["TEXT_BLOCK", "INFO_SECTION"].includes(f.type) ? null : desc(f.description, "fp-md fp-help");

    let control;
    switch (f.type) {
      case "TEXT_FIELD": case "TEXTAREA": case "NUMBER": case "DATEPICKER":
        control = this.textInput(f, index, id, view, clearError); break;
      case "RADIO": case "CHECKBOX":
        control = this.choices(f, index, id, view, clearError); break;
      case "SELECT":
        if (isTree(f)) {
          // Each level carries its own label, so the field label isn't shown.
          cell.append(...[help, this.treeSelect(f, index, id, view, clearError), error].filter(Boolean));
          return cell;
        }
        control = this.select(f, index, id, view, clearError); break;
      case "FILE_UPLOADER":
        control = this.uploader(f, index, id, view); break;
      case "CONSENT_SECTION":
        control = this.consents(f, index, view, clearError); break;
      case "TEXT_BLOCK":
        cell.classList.add("fp-textblock");
        if (f.title) cell.append(h("h3", { class: "fp-block-title" }, f.title));
        { const d = desc(f.description, "fp-md"); if (d) cell.append(d); }
        return cell;
      case "INFO_SECTION":
        cell.classList.add("fp-info");
        if (f.title) cell.append(h("h3", { class: "fp-block-title" }, f.title));
        { const d = desc(f.description, "fp-md"); if (d) cell.append(d); }
        return cell;
      default:
        cell.append(h("div", { class: "fp-unknown" },
          `Nieobsługiwany komponent: ${f.type ?? "(brak typu)"}`, f.title ? ` – ${f.title}` : ""));
        return cell;
    }
    if (["RADIO", "CHECKBOX"].includes(f.type)) {
      if (!f.title) cell.classList.add("fp-compact");
      cell.append(...[control, help, error].filter(Boolean));
    } else {
      // A top-level consent section already shows its title as the card heading.
      cell.append(...[f.type === "CONSENT_SECTION" && !asCell ? null : label, help, control, error].filter(Boolean));
    }
    return cell;
  }

  textInput(f, index, id, view, clearError) {
    const m = this.model;
    const isZip = (f.validation ?? []).includes("ZIP_CODE");
    const init = m.initial(f);
    const tag = f.type === "TEXTAREA" ? "textarea" : "input";
    const input = h(tag, {
      id, class: "fp-input",
      type: f.type === "DATEPICKER" ? "date" : tag === "input" ? "text" : null,
      inputmode: f.type === "NUMBER" ? "decimal" : isZip ? "numeric" : null,
      placeholder: f.placeholder ?? (isZip ? "__-___" : null),
      rows: tag === "textarea" ? Math.max(4, 3 * (f.rows ?? 1)) : null,
    });
    input.value = m.get(f, index) ?? "";
    const wrap = h("div", { class: "fp-inputwrap" }, input);
    const clear = h("button", { type: "button", class: "fp-clear", "aria-label": "Wyczyść", title: "Wyczyść" }, "×");
    const clearable = !["TEXTAREA", "DATEPICKER"].includes(f.type);
    if (clearable) wrap.append(clear);
    const sync = () => { clear.hidden = !input.value || input.readOnly; };
    input.addEventListener("input", () => {
      if (isZip) input.value = maskZip(input.value);
      m.set(f, index, input.value);
      clearError();
      sync();
      this.onChange(true);
    });
    clear.addEventListener("click", () => {
      input.value = "";
      m.set(f, index, "");
      sync();
      input.focus();
      this.onChange(false);
    });
    if (init && !init.known) {
      wrap.append(h("span", { class: "fp-chip", title: "Brak danych demonstracyjnych dla tego źródła" }, init.ref));
    }
    view.setReadOnly = (ro) => {
      input.readOnly = ro;
      wrap.classList.toggle("fp-ro", ro);
      sync();
    };
    view.focus = () => input.focus();
    sync();
    return wrap;
  }

  choices(f, index, id, view, clearError) {
    const m = this.model;
    const radio = f.type === "RADIO";
    const name = `${id}-opt`;
    const options = m.options(f);
    const box = h(f.title ? "fieldset" : "div", { class: "fp-fieldset", id });
    if (f.title) {
      box.append(h("legend", { class: "fp-label" }, f.title,
        f.tooltipMessage ? h("span", { class: "fp-tip", title: f.tooltipMessage }, "?") : null));
    }
    const inputs = options.map((opt) => {
      const current = m.get(f, index);
      const input = h("input", {
        type: radio ? "radio" : "checkbox", name, value: opt,
        checked: radio ? current === opt : Array.isArray(current) && current.includes(opt),
      });
      input.addEventListener("change", () => {
        if (radio) m.set(f, index, opt);
        else m.set(f, index, inputs.filter((i) => i.checked).map((i) => i.value));
        clearError();
        this.onChange(false);
      });
      box.append(h("label", { class: "fp-choice" }, input, h("span", {}, opt)));
      return input;
    });
    view.setReadOnly = (ro) => inputs.forEach((i) => { i.disabled = ro; });
    view.focus = () => inputs[0]?.focus();
    return box;
  }

  select(f, index, id, view, clearError) {
    const m = this.model;
    const options = m.options(f);
    const current = m.get(f, index);
    if (current && !options.includes(current)) options.unshift(current);
    const select = h("select", { id, class: "fp-select" },
      h("option", { value: "" }, "Wybierz"),
      options.map((o) => h("option", { value: o, selected: o === current }, o)));
    const clear = h("button", { type: "button", class: "fp-clear fp-clear-select", "aria-label": "Wyczyść", title: "Wyczyść" }, "×");
    const wrap = h("div", { class: "fp-selectwrap" }, select, clear);
    const sync = () => {
      clear.hidden = !select.value || select.disabled;
      select.classList.toggle("fp-placeholder", !select.value);
    };
    select.addEventListener("change", () => {
      m.set(f, index, select.value);
      clearError();
      sync();
      this.onChange(false);
    });
    clear.addEventListener("click", () => {
      select.value = "";
      m.set(f, index, "");
      sync();
      this.onChange(false);
    });
    view.setReadOnly = (ro) => {
      select.disabled = ro;
      wrap.classList.toggle("fp-ro", ro);
      sync();
    };
    view.focus = () => select.focus();
    sync();
    return wrap;
  }

  /** Cascading selects over a tree Dictionary; the Answer is the chosen path. */
  treeSelect(f, index, id, view, clearError) {
    const m = this.model;
    const t = tree(f.dictionaryName) ?? tree("TERYT");
    const box = h("div", { class: "fp-tree" });
    const selects = [];
    // The exports title these fields with the dictionary name; use it only if it reads like a label.
    const firstLabel = f.title && f.title !== f.dictionaryName ? f.title : t.levels[0];
    const draw = () => {
      const path = [...(m.get(f, index) ?? [])];
      box.replaceChildren();
      selects.length = 0;
      for (let level = 0; level < t.levels.length; level++) {
        const options = t.options(path.slice(0, level));
        if (level > 0 && (path.length < level || !options.length)) break;
        const sid = level ? `${id}-${level}` : id;
        const current = path[level] ?? "";
        const select = h("select", { id: sid, class: "fp-select" },
          h("option", { value: "" }, "Wybierz"),
          options.map((o) => h("option", { value: o, selected: o === current }, o)));
        select.classList.toggle("fp-placeholder", !current);
        select.addEventListener("change", () => {
          const next = path.slice(0, level);
          if (select.value) next.push(select.value);
          m.set(f, index, next);
          clearError();
          draw();
          this.onChange(false);
        });
        selects.push(select);
        box.append(h("div", { class: "fp-tree-level" },
          h("label", { class: "fp-label", for: sid }, level ? t.levels[level] : firstLabel),
          h("div", { class: "fp-selectwrap" }, select)));
      }
      view.setReadOnly?.(m.isReadOnly(f, index));
    };
    view.setReadOnly = (ro) => {
      selects.forEach((s) => { s.disabled = ro; });
      box.classList.toggle("fp-ro", ro);
    };
    view.focus = () => selects[0]?.focus();
    draw();
    return box;
  }

  uploader(f, index, id, view) {
    const m = this.model;
    const picker = h("input", { type: "file", id, multiple: true, accept: UPLOAD.extensions.join(","), hidden: true });
    const button = h("button", { type: "button", class: "fp-btn fp-btn-outline" }, "Dodaj lub przeciągnij plik");
    const list = h("ul", { class: "fp-files" });
    const box = h("div", { class: "fp-upload" }, button, picker, list,
      h("table", { class: "fp-upload-rules" },
        h("tr", {}, h("td", {}, "Dopuszczalny format pliku:"), h("td", {}, UPLOAD.extensions.join(", "))),
        h("tr", {}, h("td", {}, "Maksymalny rozmiar pliku:"), h("td", {}, "10 MB")),
        h("tr", {}, h("td", {}, "Maksymalna liczba plików:"), h("td", {}, String(UPLOAD.maxFiles)))));
    const names = () => m.get(f, index) ?? [];
    const draw = () => {
      list.replaceChildren(...names().map((n, i) => h("li", {}, h("span", {}, n), h("button", {
        type: "button", class: "fp-linkbtn",
        onclick: () => { m.set(f, index, names().filter((_, j) => j !== i)); draw(); this.onChange(false); },
      }, "Usuń"))));
    };
    const add = (files) => {
      const next = [...names()];
      for (const file of files) {
        const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
        if (!UPLOAD.extensions.includes(ext)) { view.setError(`Plik ${file.name} ma niedozwolony format.`); continue; }
        if (file.size > UPLOAD.maxBytes) { view.setError(`Plik ${file.name} jest większy niż 10 MB.`); continue; }
        if (next.length >= UPLOAD.maxFiles) { view.setError(`Możesz dodać najwyżej ${UPLOAD.maxFiles} pliki.`); break; }
        next.push(file.name); // only the name is kept; the file never leaves the browser
        view.setError(null);
      }
      m.set(f, index, next);
      draw();
      this.onChange(false);
    };
    button.addEventListener("click", () => picker.click());
    picker.addEventListener("change", () => { add([...picker.files]); picker.value = ""; });
    box.addEventListener("dragover", (e) => { e.preventDefault(); box.classList.add("fp-drag"); });
    box.addEventListener("dragleave", () => box.classList.remove("fp-drag"));
    box.addEventListener("drop", (e) => { e.preventDefault(); box.classList.remove("fp-drag"); add([...e.dataTransfer.files]); });
    view.setReadOnly = (ro) => { button.disabled = ro; };
    view.focus = () => button.focus();
    draw();
    return box;
  }

  consents(f, index, view, clearError) {
    const m = this.model;
    const cfg = f.consentSectionConfig ?? {};
    const list = cfg.consents ?? [];
    const box = h("div", { class: "fp-fieldset" });
    if (!list.length) box.append(h("div", { class: "fp-unknown" }, "Sekcja zgód bez zgód (consentSectionConfig.consents)."));
    const inputs = list.map((c) => {
      const input = h("input", { type: "checkbox", value: c.description ?? "" });
      input.checked = (m.get(f, index) ?? []).includes(input.value);
      input.addEventListener("change", () => { save(); clearError(); });
      box.append(h("label", { class: "fp-choice fp-consent" }, input, h("span", { class: "fp-md", html: richText(c.description) })));
      return input;
    });
    const save = () => {
      m.set(f, index, inputs.filter((i) => i.checked).map((i) => i.value));
      if (all) all.checked = inputs.length > 0 && inputs.every((i) => i.checked);
      this.onChange(false);
    };
    let all = null;
    if ((f.selectAllEnabled || cfg.selectAllEnabled) && inputs.length > 1) {
      all = h("input", { type: "checkbox" });
      all.addEventListener("change", () => { inputs.forEach((i) => { i.checked = all.checked; }); save(); clearError(); });
      box.prepend(h("label", { class: "fp-choice fp-select-all" }, all, h("span", {}, "Zaznacz wszystkie")));
    }
    for (const fl of cfg.files ?? []) {
      const name = typeof fl === "string" ? fl : fl?.name ?? fl?.fileName ?? JSON.stringify(fl);
      box.append(h("div", { class: "fp-help" }, `Plik: ${name}`));
    }
    view.setReadOnly = (ro) => [...inputs, all].forEach((i) => { if (i) i.disabled = ro; });
    view.focus = () => inputs[0]?.focus();
    return box;
  }

  // ---- states, tech names, validation ----

  register(f, index, el) {
    const view = { field: f, index, el, techEl: el.querySelector(":scope > .fp-tech") };
    this.views.push(view);
    return view;
  }

  techTag(f, index) {
    const tag = h("div", { class: "fp-tech" },
      h("code", {}, f.techName ?? "(brak techName)"), " · ", h("span", {}, f.type ?? "?"),
      index != null ? ` · instancja ${index + 1}` : "");
    if ((f.states ?? []).length) tag.append(h("ul", { class: "fp-tech-states" }));
    return tag;
  }

  /** Apply the current State results to the rendered tab. */
  applyStates() {
    const m = this.model;
    for (const v of this.views) {
      const st = m.state(v.field, v.index);
      const hidden = st?.show === false;
      v.el.hidden = hidden;
      if (hidden) v.setError?.(null);
      v.setReadOnly?.(m.isReadOnly(v.field, v.index));
      const ul = v.techEl?.querySelector(".fp-tech-states");
      if (ul && st) {
        ul.replaceChildren(...st.details.map((d) => h("li", {
          class: d.error ? "fp-st-err" : d.value ? "fp-st-true" : "fp-st-false", title: d.query,
        }, `${d.type}: ${d.error ? "błąd" : d.value ? "prawda" : "fałsz"}`)));
      }
    }
  }

  errorFor(v) {
    const m = this.model;
    const f = v.field;
    if (!INPUT_TYPES.has(f.type) || !v.setError) return null;
    if (!m.isVisible(f, v.index) || m.isReadOnly(f, v.index)) return null;
    const value = m.get(f, v.index);
    const rules = f.validation ?? [];
    if (f.type === "CONSENT_SECTION") {
      const cfg = f.consentSectionConfig ?? {};
      const required = (cfg.consents ?? []).filter((c) =>
        c.required === true || (c.required == null && (cfg.required || rules.includes("REQUIRED"))));
      const checked = value ?? [];
      return required.every((c) => checked.includes(c.description ?? "")) ? null : "Oświadczenie jest wymagane.";
    }
    if (rules.includes("REQUIRED") && isEmpty(value)) {
      if (["RADIO", "SELECT", "CHECKBOX"].includes(f.type)) return "Wybierz odpowiedź.";
      if (f.type === "FILE_UPLOADER") return "Dodaj plik.";
      return "To pole jest wymagane.";
    }
    if (typeof value === "string" && !isEmpty(value)) {
      if (f.type === "NUMBER" && !/^-?\d+([.,]\d+)?$/.test(value.trim())) return "Wpisz liczbę.";
      return check(rules, value);
    }
    return null;
  }

  /** Validate the visible, editable fields; show errors; return the first invalid view. */
  validate() {
    let first = null;
    for (const v of this.views) {
      const msg = this.errorFor(v);
      v.setError?.(msg);
      if (msg && !first) first = v;
    }
    return first;
  }

  clearErrors() {
    this.views.forEach((v) => v.setError?.(null));
  }

  // ---- FINISH summary ----

  summary(onEdit) {
    const m = this.model;
    const box = h("section", { class: "fp-card fp-summary" },
      h("div", { class: "fp-summary-head" }, h("h2", { class: "fp-card-title" }, "Twoje odpowiedzi"),
        h("span", { class: "fp-badge", title: "Ta część nie pochodzi z Form Definition" }, "wygenerowane przez podgląd")));
    m.tabs.forEach((tab, ti) => {
      if (tab.type === "FINISH") return;
      const block = h("div", { class: "fp-summary-tab" },
        h("div", { class: "fp-summary-tabhead" }, h("h3", {}, tab.title ?? `Zakładka ${ti + 1}`),
          h("button", { type: "button", class: "fp-linkbtn", onclick: () => onEdit(ti) }, "Edytuj")));
      for (const q of tab.questions) this.summarise(q.field, block, 0);
      box.append(block);
    });
    return box;
  }

  summarise(f, block, depth) {
    const m = this.model;
    if (!m.isVisible(f, null)) return;
    if (CONTAINER_TYPES.has(f.type)) {
      const group = h("div", { class: "fp-summary-group" }, h(depth ? "h5" : "h4", {}, f.title || f.techName || ""));
      const n = f.type === "REPEATING_SECTION" ? m.instanceCount(f) : 1;
      for (let i = 0; i < n; i++) {
        const dl = h("dl", {});
        if (f.type === "REPEATING_SECTION") group.append(h("div", { class: "fp-summary-inst" }, `Nr ${i + 1}`));
        for (const q of f.repeatingSectionGrid) {
          const c = q.field;
          if (CONTAINER_TYPES.has(c.type)) { this.summarise(c, group, depth + 1); continue; }
          const idx = f.type === "REPEATING_SECTION" ? i : null;
          if (!INPUT_TYPES.has(c.type) || !m.isVisible(c, idx)) continue;
          dl.append(...this.row(c, idx));
        }
        if (dl.children.length) group.append(dl);
      }
      block.append(group);
    } else if (INPUT_TYPES.has(f.type)) {
      block.append(h("dl", {}, ...this.row(f, null)));
    }
  }

  row(f, index) {
    const m = this.model;
    const v = m.get(f, index);
    const opts = m.options(f);
    let label = (f.title && f.title !== f.dictionaryName ? f.title : "") || (isTree(f) ? "Adres (TERYT)" : "");
    let text;
    if (f.type === "CONSENT_SECTION") {
      const total = f.consentSectionConfig?.consents?.length ?? 0;
      label ||= "Zgody";
      text = `zaznaczono ${(v ?? []).length} z ${total}`;
    } else if (f.type === "CHECKBOX" && !f.title) {
      label = opts.join(", ") || f.techName;
      text = isEmpty(v) ? "nie" : "tak";
    } else {
      label ||= f.techName || "(bez etykiety)";
      text = isEmpty(v) ? "—" : Array.isArray(v) ? v.join(isTree(f) ? " › " : ", ") : String(v);
    }
    return [h("dt", {}, label), h("dd", { class: isEmpty(v) ? "fp-empty" : null }, text)];
  }
}
