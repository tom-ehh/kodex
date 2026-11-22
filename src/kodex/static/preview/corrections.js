// Worker corrections in the preview: edit a generated field (label, type, required,
// validators), add an optional "why", send them to Morpheus
// (POST /api/v1/forms/{form_id}/corrections). Active only for a form that came from
// the analyzer with a form_id (hand-off from the upload page). Only the structured
// edit is kept by the server; the "why" is used once in memory and discarded.

import { h } from "./render.js";

export const FORM_META_KEY = "coi.previewFormMeta"; // {form_id, needs_review}

const TYPES = ["TEXT_FIELD", "TEXTAREA", "NUMBER", "DATEPICKER", "CHECKBOX", "RADIO", "SELECT",
  "FILE_UPLOADER", "TEXT_BLOCK"];
const VALIDATORS = ["PESEL", "NIP", "REGON", "ZIP_CODE", "EMAIL"];
// Containers and text blocks hold no value: only their label can be corrected
// (a validator on a section would teach Morpheus a nonsense pattern).
const LABEL_ONLY = new Set(["SECTION", "REPEATING_SECTION", "TEXT_BLOCK", "INFO_SECTION"]);

const sameSet = (a, b) => a.length === b.length && a.every((x) => b.includes(x));

export class Corrections {
  /** @param meta {form_id, needs_review}; rerender(): re-draws the current tab. */
  constructor(meta, { token, rerender, pagebar }) {
    this.meta = meta;
    this.token = token;
    this.rerender = rerender;
    this.edits = new Map(); // `${techName}|${attr}` -> {field_path, attr, before, after}
    this.on = false;
    this.toggle = h("button", { type: "button", class: "fp-btn fp-btn-sm", "aria-pressed": "false",
      onclick: () => this.setOn(!this.on) }, "Popraw formularz");
    pagebar.prepend(this.toggle);
    this.tray = h("aside", { class: "fp-corr-tray", "aria-label": "Poprawki", hidden: true });
    this.dialog = h("dialog", { class: "fp-corr-dialog" });
    document.body.append(this.tray, this.dialog);
    if (meta.needs_review) this.setOn(true);
  }

  banner() {
    if (!this.meta.needs_review) return null;
    return h("div", { class: "fp-corr-banner", role: "status" },
      h("strong", {}, "Formularz wymaga weryfikacji. "),
      "Żaden model nie wygenerował w pełni poprawnej konfiguracji. To jest ostatni szkic: popraw pola, ",
      "a Morpheus nauczy się na Twoich poprawkach.");
  }

  setOn(on) {
    this.on = on;
    document.body.classList.toggle("fp-correcting", on);
    this.toggle.setAttribute("aria-pressed", String(on));
    this.toggle.textContent = on ? "Zakończ poprawianie" : "Popraw formularz";
    this.drawTray();
  }

  /** The per-field button the renderer puts in each field's tech tag. */
  button(f) {
    if (!f.techName) return null;
    return h("button", { type: "button", class: "fp-corr-btn", onclick: (e) => { e.preventDefault(); this.edit(f); } },
      this.edits.size && [...this.edits.values()].some((x) => x.field_path === f.techName) ? "Poprawione ✎" : "Popraw pole");
  }

  edit(f) {
    const labelOnly = LABEL_ONLY.has(f.type);
    const val = (f.validation ?? []).filter((v) => v !== "REQUIRED");
    const title = h("input", { class: "fp-input", id: "corr-title", value: f.title ?? "" });
    const type = h("select", { class: "fp-input", id: "corr-type" },
      [...new Set([f.type, ...TYPES])].map((t) => h("option", { value: t, selected: t === f.type }, t)));
    const req = h("input", { type: "checkbox", id: "corr-req", checked: (f.validation ?? []).includes("REQUIRED") });
    const checks = VALIDATORS.map((v) => h("label", { class: "fp-corr-check" },
      h("input", { type: "checkbox", value: v, checked: val.includes(v) }), ` ${v}`));
    const save = () => {
      const before = { label: f.title ?? "", type: f.type, required: (f.validation ?? []).includes("REQUIRED"), validator: val };
      const after = labelOnly ? { ...before, label: title.value.trim() } : {
        label: title.value.trim(),
        type: type.value,
        required: req.checked,
        validator: checks.map((c) => c.firstChild).filter((i) => i.checked).map((i) => i.value),
      };
      for (const attr of labelOnly ? ["label"] : ["label", "type", "required", "validator"]) {
        const changed = attr === "validator" ? !sameSet(before[attr], after[attr]) : before[attr] !== after[attr];
        const key = `${f.techName}|${attr}`;
        const prev = this.edits.get(key);
        if (changed) this.edits.set(key, { field_path: f.techName, attr, before: prev ? prev.before : before[attr], after: after[attr] });
      }
      // Apply locally so the preview shows the corrected form.
      f.title = after.label || f.title;
      if (!labelOnly) {
        f.type = after.type;
        f.validation = [...(after.required ? ["REQUIRED"] : []), ...after.validator];
      }
      this.dialog.close();
      this.drawTray();
      this.rerender();
    };
    this.dialog.replaceChildren(h("form", { method: "dialog", class: "fp-corr-form", onsubmit: (e) => { e.preventDefault(); save(); } },
      h("h2", { class: "fp-card-title" }, "Popraw pole"),
      h("p", { class: "fp-help" }, h("code", {}, f.techName)),
      h("label", { class: "fp-label", for: "corr-title" }, "Etykieta"), title,
      ...(labelOnly ? [h("p", { class: "fp-help" }, "W sekcji i bloku tekstu można poprawić tylko etykietę.")] : [
        h("label", { class: "fp-label", for: "corr-type" }, "Typ pola"), type,
        h("label", { class: "fp-corr-check" }, req, " Pole wymagane (REQUIRED)"),
        h("fieldset", { class: "fp-corr-set" }, h("legend", {}, "Walidatory"), ...checks)]),
      h("div", { class: "fp-actions" },
        h("button", { type: "button", class: "fp-btn fp-btn-outline", onclick: () => this.dialog.close() }, "Anuluj"),
        h("button", { type: "submit", class: "fp-btn" }, "Zapisz poprawkę"))));
    this.dialog.showModal();
    title.focus();
  }

  drawTray(message, level) {
    const n = this.edits.size;
    this.tray.hidden = !this.on && !message;
    if (this.tray.hidden) return;
    const why = h("textarea", { class: "fp-input", id: "corr-why", rows: 2, maxlength: 2000,
      placeholder: "Dlaczego? (opcjonalnie; nie jest zapisywane)" });
    const send = (learnNow) => this.send(why.value.trim() || null, learnNow);
    const fmt = (v) => (Array.isArray(v) ? v.join(", ") || "brak" : typeof v === "boolean" ? (v ? "tak" : "nie") : String(v));
    const names = { label: "etykieta", type: "typ", required: "wymagane", validator: "walidatory" };
    this.tray.replaceChildren(...[
      h("strong", {}, n ? `Poprawki: ${n}` : "Kliknij „Popraw pole” przy polu, które jest źle wygenerowane."),
      message ? h("p", { class: level === "error" ? "fp-error" : "fp-corr-ok", role: "alert" }, message) : null,
      n ? h("ul", { class: "fp-corr-list" }, [...this.edits.entries()].map(([k, e]) => h("li", {},
        h("code", {}, e.field_path), ` ${names[e.attr]}: ${fmt(e.before)} → ${fmt(e.after)} `,
        h("button", { type: "button", class: "fp-linkbtn", onclick: () => { this.edits.delete(k); this.drawTray(); } }, "usuń")))) : null,
      n ? why : null,
      n ? h("div", { class: "fp-actions" },
        h("button", { type: "button", class: "fp-btn fp-btn-outline", onclick: () => send(false) }, "Wyślij poprawki"),
        h("button", { type: "button", class: "fp-btn", title: "Morpheus przetworzy poprawki od razu (bramka jakości nadal działa)",
          onclick: () => send(true) }, "Wyślij i ucz się teraz")) : null].filter(Boolean));
  }

  async send(why, learnNow) {
    const headers = { "Content-Type": "application/json" };
    const token = this.token();
    if (token) headers.Authorization = `Bearer ${token}`;
    let res;
    try {
      res = await fetch(`/api/v1/forms/${encodeURIComponent(this.meta.form_id)}/corrections`, {
        method: "POST", headers, body: JSON.stringify({ edits: [...this.edits.values()], why, learn_now: learnNow }),
      });
    } catch (e) {
      this.drawTray(`Nie udało się wysłać: ${e.message}`, "error");
      return;
    }
    const msg = {
      401: "Serwer wymaga tokenu JWT. Wpisz go na stronie głównej.",
      404: "Wynik analizy wygasł na serwerze (jest trzymany tylko przez krótki czas). Przeanalizuj plik ponownie, aby wysłać poprawki.",
      503: "Morpheus jest wyłączony na tym serwerze (COI_MORPHEUS_ENABLED).",
    }[res.status];
    if (!res.ok) {
      this.drawTray(msg ?? `Nie udało się wysłać poprawek (HTTP ${res.status}).`, "error");
      return;
    }
    const body = await res.json();
    this.edits.clear();
    this.drawTray(learnNow
      ? `Wysłano ${body.episode_ids.length}. Morpheus uczy się teraz; nowy wzorzec czeka na akceptację w kolejce Morpheusa (strona główna).`
      : `Wysłano ${body.episode_ids.length}. Morpheus przetworzy je w tle.`);
    this.rerender();
  }
}
