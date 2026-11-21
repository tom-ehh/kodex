// Runtime model of a Form Definition for the Form Preview.
//
// The normalised definition doubles as the JSONata document: Answers are written
// onto the field objects as `__userAnswer`, and the grid key is exposed as
// `repeatingSectionGrid` (the JSONata spelling; the schema says `repetingSectionGrid`).
// A Repeating Section's Answer is a list of instances, each a list of
// `{fieldTechName, answers}`, as in the organisers' rules document.
/* global jsonata */

import { INITIAL_DATA_REF, dictionary, initialData, tree } from "./demo-data.js";
import { isEmpty, knownValidator, validatorName } from "./validators.js";

export const INPUT_TYPES = new Set([
  "TEXT_FIELD", "TEXTAREA", "NUMBER", "CHECKBOX", "RADIO", "SELECT", "DATEPICKER",
  "FILE_UPLOADER", "CONSENT_SECTION",
]);
export const CONTAINER_TYPES = new Set(["SECTION", "REPEATING_SECTION"]);
const KNOWN_TYPES = new Set([...INPUT_TYPES, ...CONTAINER_TYPES, "TEXT_BLOCK", "INFO_SECTION"]);
const CHOICE_TYPES = new Set(["CHECKBOX", "RADIO", "SELECT"]);

/** A SELECT over a multi-level dictionary (TERYT address tree); its Answer is the chosen path. */
export const isTree = (f) => f.type === "SELECT" && (f.multiLevelTree === true || /^teryt/i.test(f.dictionaryName ?? ""));

/** Problems the preview runs into while rendering, deduplicated. */
export class Problems {
  constructor() {
    this.list = [];
    this.seen = new Set();
    this.listeners = [];
  }
  add(level, message, where = "") {
    const key = `${level}|${message}|${where}`;
    if (this.seen.has(key)) return;
    this.seen.add(key);
    this.list.push({ level, message, where });
    this.listeners.forEach((fn) => fn());
  }
}

const byPosition = (a, b) => (a.y ?? 0) - (b.y ?? 0) || (a.x ?? 0) - (b.x ?? 0)
  || (a.field?.questionOrder ?? 0) - (b.field?.questionOrder ?? 0);

function truthy(r) {
  if (Array.isArray(r)) return r.some(truthy);
  return Boolean(r);
}

export class FormModel {
  constructor(raw, problems) {
    if (!raw || typeof raw !== "object" || !Array.isArray(raw.tabs)) {
      throw new Error("To nie jest Form Definition: brak tablicy „tabs”.");
    }
    this.problems = problems;
    this.def = structuredClone(raw);
    this.meta = new WeakMap(); // field -> {uid, tab, parent}
    this.instances = new Map(); // repeating section -> array of Map(techName -> value)
    this.stateResults = new Map(); // key -> {show, readOnly, details}
    this.compiled = new Map(); // query -> {expr} | {error}
    this.uid = 0;

    if (!this.def.title) problems.add("warn", "Brak tytułu formularza (title).");
    for (const tab of this.def.tabs) {
      tab.questions = Array.isArray(tab.questions) ? tab.questions : [];
      tab.questions.sort(byPosition);
      if (!tab.questions.length) problems.add("warn", "Zakładka nie ma żadnych pól.", tab.title);
      this.checkSiblings(tab.questions, tab.title);
      for (const q of tab.questions) this.visit(q, tab, null);
    }
    this.tabs = this.orderTabs();
  }

  // ---- structure ----

  visit(q, tab, parent) {
    const f = (q.field ??= {});
    const grid = f.repetingSectionGrid ?? f.repeatingSectionGrid ?? [];
    delete f.repetingSectionGrid;
    f.repeatingSectionGrid = Array.isArray(grid) ? grid : [];
    f.repeatingSectionGrid.sort(byPosition);
    this.meta.set(f, { uid: ++this.uid, tab, parent });
    const where = this.where(f);

    // Real exports sometimes carry an object where the schema says list (e.g. `validation: {}`).
    for (const key of ["validation", "states", "options"]) {
      const v = f[key];
      if (v == null || Array.isArray(v)) continue;
      this.problems.add("warn", `„${key}” powinno być listą, jest ${typeof v}: potraktowano jako ${key === "validation" && typeof v === "object" ? "listę kluczy" : "pustą listę"}.`, where);
      f[key] = key === "validation" && typeof v === "object" ? Object.keys(v) : [];
    }

    if (!KNOWN_TYPES.has(f.type)) this.problems.add("error", `Nieobsługiwany typ komponentu: ${f.type ?? "(brak)"}.`, where);
    if (!f.techName) this.problems.add("warn", "Pole bez techName: zapytania JSONata nie mogą go wskazać.", where);
    for (const name of f.validation ?? []) {
      if (!knownValidator(name)) this.problems.add("warn", `Nieznany walidator „${validatorName(name)}”: pominięty.`, where);
    }
    if (isTree(f)) {
      if (!tree(f.dictionaryName)) this.problems.add("warn", `Słownik drzewiasty „${f.dictionaryName ?? "(brak)"}” nie ma danych demonstracyjnych: użyto przykładowych poziomów.`, where);
    } else if (f.dictionaryName) {
      if (!CHOICE_TYPES.has(f.type)) this.problems.add("warn", "Słownik dozwolony tylko w SELECT, CHECKBOX, RADIO.", where);
      if (!dictionary(f.dictionaryName)) this.problems.add("warn", `Słownik „${f.dictionaryName}” nie ma danych demonstracyjnych.`, where);
    } else if (CHOICE_TYPES.has(f.type) && !(f.options ?? []).length) {
      this.problems.add("warn", "Pole wyboru bez opcji i bez słownika.", where);
    }
    if (parent && CONTAINER_TYPES.has(f.type)) this.problems.add("warn", "Sekcja zagnieżdżona w sekcji.", where);
    if (!parent && !CONTAINER_TYPES.has(f.type) && !["CONSENT_SECTION", "INFO_SECTION"].includes(f.type)) {
      this.problems.add("warn", "Pole poza sekcją.", where);
    }
    const initial = this.initialRef(f);
    if (initial && !initialData(initial).known) {
      this.problems.add("warn", `Dane inicjalne „${initial}” nie mają danych demonstracyjnych.`, where);
    }

    this.checkSiblings(f.repeatingSectionGrid, where);
    for (const child of f.repeatingSectionGrid) this.visit(child, tab, f);

    if (f.type === "REPEATING_SECTION") this.instances.set(f, [this.newInstance(f)]);
    else if (!this.repeatingParent(f)) this.applyDefault(f, (v) => this.setPlain(f, v));
  }

  checkSiblings(questions, where) {
    const seen = new Set();
    for (const q of questions) {
      const t = q.field?.techName;
      if (!t) continue;
      if (seen.has(t)) this.problems.add("warn", `Powtórzony techName „${t}” w tej samej sekcji lub zakładce.`, where);
      seen.add(t);
    }
  }

  orderTabs() {
    const tabs = this.def.tabs;
    const byId = new Map(tabs.map((t) => [t.id, t]));
    const links = this.def.flow?.links ?? [];
    const next = new Map();
    for (const l of links) {
      if (!byId.has(l.source) || !byId.has(l.target)) {
        this.problems.add("error", "Link flow wskazuje nieistniejącą zakładkę.", l.id ?? "");
        continue;
      }
      if (next.has(l.source)) this.problems.add("warn", "Rozgałęzienie flow: użyto pierwszego linku.", byId.get(l.source).title);
      else next.set(l.source, l.target);
    }
    let order = [];
    if (!next.size) {
      if (tabs.length > 1) this.problems.add("warn", "Brak flow.links: użyto kolejności zakładek z pliku.");
      order = [...tabs];
    } else {
      const targets = new Set(next.values());
      let cur = tabs.find((t) => !targets.has(t.id)) ?? tabs[0];
      const seen = new Set();
      while (cur && !seen.has(cur)) {
        seen.add(cur);
        order.push(cur);
        cur = byId.get(next.get(cur.id));
      }
      for (const t of tabs) {
        if (!seen.has(t)) {
          this.problems.add("warn", "Zakładka poza flow: dodana na końcu.", t.title);
          order.push(t);
        }
      }
    }
    const finishes = order.filter((t) => t.type === "FINISH");
    if (!finishes.length) this.problems.add("warn", "Brak zakładki FINISH.");
    else if (order.at(-1).type !== "FINISH") this.problems.add("warn", "Zakładka FINISH nie jest ostatnia.");
    if (tabs.length < 2) this.problems.add("warn", "Formularz powinien mieć co najmniej 2 zakładki.");
    return order;
  }

  where(f) {
    const m = this.meta.get(f);
    const names = [];
    for (let x = f; x; x = this.meta.get(x)?.parent) names.unshift(x.techName ?? x.type ?? "?");
    return `${m?.tab?.title ?? "?"} › ${names.join(" › ")}`;
  }

  parent(f) {
    return this.meta.get(f)?.parent ?? null;
  }

  repeatingParent(f) {
    const p = this.parent(f);
    return p?.type === "REPEATING_SECTION" ? p : null;
  }

  *walk(questions = null) {
    for (const q of questions ?? this.def.tabs.flatMap((t) => t.questions)) {
      yield q.field;
      yield* this.walk(q.field.repeatingSectionGrid);
    }
  }

  // ---- defaults, Initial Data, options ----

  initialRef(f) {
    const v = f.defaultValue;
    if (typeof v !== "string" || !v) return null;
    if (f.defaultValueType === "EXTERNAL_SYSTEM" || INITIAL_DATA_REF.test(v)) return v;
    return null;
  }

  /** {ref, known, value} for a field fed from Initial Data, else null. */
  initial(f) {
    const ref = this.initialRef(f);
    return ref ? { ref, ...initialData(ref) } : null;
  }

  applyDefault(f, set) {
    const init = this.initial(f);
    if (init) {
      if (init.known && !isEmpty(init.value)) set(init.value);
      return;
    }
    let v = f.defaultValue;
    if (v == null || v === "") return;
    // The organisers' exports give choice defaults as lists (`["Polska"]`, `[]`).
    if (isTree(f) || f.type === "CHECKBOX") v = Array.isArray(v) ? v : [v];
    else if (Array.isArray(v)) v = ["SELECT", "RADIO"].includes(f.type) ? v[0] : v.join(", ");
    if (v == null || isEmpty(v)) return;
    set(v);
  }

  options(f) {
    if ((f.options ?? []).length) return f.options.map((o) => o.name ?? o.techName ?? "");
    if (f.dictionaryName) return dictionary(f.dictionaryName) ?? [`‹${f.dictionaryName}› przykład 1`, `‹${f.dictionaryName}› przykład 2`];
    return [];
  }

  // ---- Answers ----

  newInstance(section) {
    const values = new Map();
    for (const q of section.repeatingSectionGrid) {
      const f = q.field;
      this.applyDefault(f, (v) => values.set(f.techName, v));
    }
    return values;
  }

  instanceCount(section) {
    return this.instances.get(section)?.length ?? 1;
  }

  addInstance(section) {
    this.instances.get(section).push(this.newInstance(section));
    this.syncRepeating(section);
  }

  removeInstance(section, index) {
    const list = this.instances.get(section);
    if (list.length > 1) list.splice(index, 1);
    this.syncRepeating(section);
  }

  syncRepeating(section) {
    section.__userAnswer = this.instances.get(section).map((values) =>
      [...values].filter(([, v]) => !isEmpty(v)).map(([fieldTechName, answers]) => ({ fieldTechName, answers })));
  }

  setPlain(f, value) {
    if (isEmpty(value)) delete f.__userAnswer;
    else f.__userAnswer = value;
  }

  get(f, index = null) {
    const rep = this.repeatingParent(f);
    if (rep) return this.instances.get(rep)?.[index ?? 0]?.get(f.techName);
    return f.__userAnswer;
  }

  set(f, index, value) {
    const rep = this.repeatingParent(f);
    if (rep) {
      this.instances.get(rep)[index ?? 0].set(f.techName, value);
      this.syncRepeating(rep);
    } else {
      this.setPlain(f, value);
    }
  }

  reset() {
    for (const f of this.walk()) {
      delete f.__userAnswer;
      if (f.type === "REPEATING_SECTION") this.instances.set(f, [this.newInstance(f)]);
      else if (!this.repeatingParent(f)) this.applyDefault(f, (v) => this.setPlain(f, v));
    }
    for (const s of this.instances.keys()) this.syncRepeating(s);
  }

  // ---- States ----

  key(f, index = null) {
    const uid = this.meta.get(f)?.uid;
    return index == null ? `${uid}` : `${uid}#${index}`;
  }

  compile(query) {
    if (!this.compiled.has(query)) {
      try {
        this.compiled.set(query, { expr: jsonata(query) });
      } catch (e) {
        this.compiled.set(query, { error: e.message ?? String(e) });
      }
    }
    return this.compiled.get(query);
  }

  /** Re-evaluate every State over the current Answers. */
  async evaluate() {
    for (const s of this.instances.keys()) this.syncRepeating(s);
    const jobs = [];
    for (const f of this.walk()) {
      if (!(f.states ?? []).length) continue;
      const rep = this.repeatingParent(f);
      const indexes = rep ? [...Array(this.instanceCount(rep)).keys()] : [null];
      for (const index of indexes) jobs.push(this.evaluateField(f, index));
    }
    await Promise.all(jobs);
  }

  async evaluateField(f, index) {
    const bindings = index == null ? {} : { repeatingSectionIndex: index };
    const details = [];
    for (const state of f.states) {
      const c = this.compile(state.query ?? "");
      let value = null;
      let error = c.error ?? null;
      if (!error) {
        try {
          value = truthy(await c.expr.evaluate(this.def, bindings));
        } catch (e) {
          error = e.message ?? String(e);
        }
      }
      if (error) this.problems.add("error", `Zapytanie JSONata (${state.type}) nie działa: ${error}`, this.where(f));
      if (!["SHOW", "READ_ONLY"].includes(state.type)) {
        this.problems.add("warn", `Nieznany typ stanu „${state.type}”: pominięty.`, this.where(f));
      }
      details.push({ type: state.type, query: state.query, value, error });
    }
    // Several SHOW states must all hold; any READ_ONLY state locks. Broken queries fail open.
    const show = details.filter((d) => d.type === "SHOW").every((d) => d.error || d.value);
    const readOnly = details.filter((d) => d.type === "READ_ONLY").some((d) => !d.error && d.value);
    this.stateResults.set(this.key(f, index), { show, readOnly, details });
  }

  state(f, index = null) {
    return this.stateResults.get(this.key(f, index)) ?? this.stateResults.get(this.key(f)) ?? null;
  }

  isVisible(f, index = null) {
    if (this.state(f, index)?.show === false) return false;
    const p = this.parent(f);
    return p ? this.isVisible(p, null) : true;
  }

  isReadOnly(f, index = null) {
    if (f.readOnly === true || this.state(f, index)?.readOnly) return true;
    const p = this.parent(f);
    return p ? this.isReadOnly(p, null) : false;
  }
}
