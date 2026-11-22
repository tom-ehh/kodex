// Form Preview page: loads a Form Definition (file, paste, job result or a hand-off
// from the upload page), then lets you fill it in tab by tab. Nothing is submitted.

import { FormModel, Problems } from "./model.js";
import { Renderer, h } from "./render.js";

const TOKEN_KEY = "coi.token";
const HANDOFF_KEY = "coi.previewDefinition";

const store = {
  get(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { sessionStorage.setItem(k, v); } catch { /* storage unavailable: ignore */ } },
};

const $ = (id) => document.getElementById(id);
const main = $("main");
const controls = $("tb-controls");
const pagebar = $("pagebar");
const tabJump = $("tab-jump");
const skip = $("skip-validation");
const showTech = $("show-tech");
const problemsBtn = $("problems-btn");
const problemsPanel = $("problems-panel");

let model = null;
let renderer = null;
let problems = null;
let current = 0;
let evalSeq = 0;
let debounce = null;

// ---- loading ----

function showLoader(message = "") {
  model = null;
  controls.hidden = true;
  pagebar.hidden = true;
  problemsPanel.hidden = true;
  const fileInput = h("input", { type: "file", accept: ".json,application/json", id: "load-file" });
  const paste = h("textarea", { class: "fp-input fp-paste", id: "load-paste", rows: 10, placeholder: '{ "title": "…", "tabs": [ … ], "flow": { "links": [ … ] } }' });
  const drop = h("label", { class: "fp-upload fp-loader-drop", for: "load-file" },
    h("span", { class: "fp-btn fp-btn-outline" }, "Wybierz lub przeciągnij plik .json"), fileInput);
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("fp-drag"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("fp-drag"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("fp-drag");
    if (e.dataTransfer.files[0]) readFile(e.dataTransfer.files[0]);
  });
  fileInput.addEventListener("change", () => { if (fileInput.files[0]) readFile(fileInput.files[0]); });
  main.replaceChildren(h("div", { class: "fp-page" },
    h("h1", { class: "fp-title" }, "Podgląd formularza"),
    h("section", { class: "fp-card" },
      h("h2", { class: "fp-card-title" }, "Wczytaj Form Definition"),
      h("p", {}, "Wczytaj plik JSON z konfiguracją formularza albo wklej jego treść. Podgląd pokaże formularz tak, jak zobaczy go obywatel: z zależnościami między polami, walidacją i przechodzeniem między stronami. Nic nie jest wysyłane ani zapisywane."),
      message ? h("div", { class: "fp-error fp-load-error", role: "alert" }, message) : null,
      drop,
      h("label", { class: "fp-label", for: "load-paste" }, "albo wklej JSON"),
      paste,
      h("div", { class: "fp-actions" }, h("button", {
        type: "button", class: "fp-btn", id: "load-paste-btn", onclick: () => openText(paste.value),
      }, "Pokaż podgląd")))));
}

function readFile(file) {
  file.text().then(openText, (e) => showLoader(`Nie udało się odczytać pliku: ${e.message}`));
}

function openText(text) {
  if (!text.trim()) { showLoader("Wklej treść JSON albo wybierz plik."); return; }
  let raw;
  try {
    raw = JSON.parse(text);
  } catch (e) {
    showLoader(`To nie jest poprawny JSON: ${e.message}`);
    return;
  }
  open(raw);
}

async function fetchJob(jobId, item) {
  const headers = {};
  const token = store.get(TOKEN_KEY);
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`/api/v1/jobs/${encodeURIComponent(jobId)}`, { headers });
  if (res.status === 401 || res.status === 403) {
    throw new Error("Serwer wymaga tokenu JWT. Wpisz go na stronie głównej i otwórz podgląd ponownie z listy wyników.");
  }
  if (res.status === 404) throw new Error("Nie ma takiego zadania. Zadania są trzymane tylko w pamięci serwera i znikają po restarcie.");
  if (!res.ok) throw new Error(`Nie udało się pobrać zadania (HTTP ${res.status}).`);
  const job = await res.json();
  const it = job.items?.find((i) => String(i.index) === String(item));
  if (!it) throw new Error(`Zadanie nie ma pliku o numerze ${item}.`);
  if (!it.result) throw new Error(`Plik ${it.file?.filename ?? item} nie ma jeszcze wyniku analizy (status: ${it.status}).`);
  return it.result;
}

async function open(raw) {
  problems = new Problems();
  try {
    model = new FormModel(raw, problems);
  } catch (e) {
    showLoader(e.message);
    return;
  }
  problems.listeners.push(updateProblems);
  renderer = new Renderer(model, { onChange, onStructure });
  current = 0;
  controls.hidden = false;
  pagebar.hidden = false;
  tabJump.replaceChildren(...model.tabs.map((t, i) => h("option", { value: i }, `${i + 1}. ${t.title ?? "(bez tytułu)"}`)));
  updateProblems();
  await model.evaluate();
  renderTab(true);
}

// ---- rendering and navigation ----

function renderTab(scrollTop) {
  const tab = model.tabs[current];
  const last = current === model.tabs.length - 1;
  const left = h("div", {}, h("button", { type: "button", class: "fp-linkbtn fp-abort", onclick: abort }, "Przerwij wypełnianie"));
  const right = h("div", { class: "fp-nav-right" });
  if (current > 0) right.append(h("button", { type: "button", class: "fp-btn fp-btn-outline", onclick: () => goTo(current - 1) }, "Wstecz"));
  if (tab.type === "FINISH") {
    right.append(h("button", { type: "button", class: "fp-btn", disabled: true, title: "Podpis nie jest częścią podglądu" }, "Przejdź do podpisu"));
  } else if (!last) {
    right.append(h("button", { type: "button", class: "fp-btn", id: "next-btn", onclick: next }, "Dalej"));
  }
  const page = h("div", { class: "fp-page" },
    h("h1", { class: "fp-title" }, model.def.title || "(formularz bez tytułu)"),
    h("p", { class: "fp-step" }, `Krok ${current + 1} z ${model.tabs.length}: ${tab.title ?? ""}`),
    renderer.renderTab(tab),
    tab.type === "FINISH" ? renderer.summary((i) => goTo(i)) : null,
    h("nav", { class: "fp-nav" }, left, right));
  main.classList.remove("fp-anim"); // first paint of a tab: no expand animations
  main.replaceChildren(page);
  renderer.applyStates();
  requestAnimationFrame(() => requestAnimationFrame(() => main.classList.add("fp-anim")));
  tabJump.value = String(current);
  if (scrollTop) window.scrollTo(0, 0);
}

function goTo(i) {
  current = Math.max(0, Math.min(i, model.tabs.length - 1));
  renderTab(true);
}

async function next() {
  await refresh(); // States must reflect the latest keystrokes before validating
  if (!skip.checked) {
    const bad = renderer.validate();
    if (bad) {
      bad.el.scrollIntoView({ behavior: "smooth", block: "center" });
      bad.focus?.();
      return;
    }
  }
  goTo(current + 1);
}

function abort() {
  if (!confirm("Przerwać wypełnianie? Wszystkie odpowiedzi zostaną usunięte.")) return;
  model.reset();
  current = 0;
  model.evaluate().then(() => renderTab(true));
}

async function refresh() {
  clearTimeout(debounce);
  const seq = ++evalSeq;
  await model.evaluate();
  if (seq === evalSeq) renderer.applyStates();
}

function onChange(typing) {
  clearTimeout(debounce);
  if (typing) debounce = setTimeout(refresh, 150);
  else refresh();
}

async function onStructure() {
  const y = window.scrollY;
  await model.evaluate();
  renderTab(false);
  window.scrollTo(0, y);
}

// ---- toolbar ----

function updateProblems() {
  const list = problems?.list ?? [];
  const errors = list.filter((p) => p.level === "error").length;
  problemsBtn.textContent = list.length ? `Problemy: ${list.length}` : "Brak problemów";
  problemsBtn.disabled = !list.length; // nothing to open: a status, not a button
  if (!list.length) problemsPanel.hidden = true;
  problemsBtn.className = `fp-tb-btn ${errors ? "fp-tb-error" : list.length ? "fp-tb-warn" : "fp-tb-ok"}`;
  problemsPanel.replaceChildren(
    h("div", { class: "fp-problems-head" }, h("strong", {}, "Problemy znalezione podczas renderowania"),
      h("button", { type: "button", class: "fp-linkbtn", onclick: () => { problemsPanel.hidden = true; } }, "Zamknij")),
    list.length
      ? h("ul", {}, list.map((p) => h("li", { class: `fp-problem-${p.level}` },
        h("span", { class: "fp-problem-level" }, p.level === "error" ? "błąd" : "uwaga"), " ", p.message,
        p.where ? h("div", { class: "fp-problem-where" }, p.where) : null)))
      : h("p", {}, "Nic nie znaleziono. Pełną zgodność ze schematem sprawdza backend."));
}

tabJump.addEventListener("change", () => goTo(Number(tabJump.value)));
showTech.addEventListener("change", () => document.body.classList.toggle("fp-show-tech", showTech.checked));
skip.addEventListener("change", () => { if (skip.checked) renderer?.clearErrors(); });
problemsBtn.addEventListener("click", () => { problemsPanel.hidden = !problemsPanel.hidden; });
$("load-other").addEventListener("click", () => {
  history.replaceState(null, "", location.pathname);
  showLoader();
});

// ---- debug drawer (bottom right, remembers open/closed) ----

const DEBUG_KEY = "kodex.debugOpen";
const debugToggle = $("debug-toggle");
function setDebug(open) {
  controls.classList.toggle("fp-debug-open", open);
  debugToggle.setAttribute("aria-expanded", String(open));
  debugToggle.title = open ? "Zwiń narzędzia" : "Narzędzia podglądu";
  try { localStorage.setItem(DEBUG_KEY, open ? "1" : "0"); } catch { /* ignore */ }
  if (!open) problemsPanel.hidden = true;
}
debugToggle.addEventListener("click", () => setDebug(!controls.classList.contains("fp-debug-open")));
setDebug((() => { try { return localStorage.getItem(DEBUG_KEY) === "1"; } catch { return false; } })());

// ---- start ----

(async () => {
  const params = new URLSearchParams(location.search);
  if (params.has("job")) {
    try {
      await open(await fetchJob(params.get("job"), params.get("item") ?? "0"));
    } catch (e) {
      showLoader(e.message);
    }
  } else if (params.get("source") === "session") {
    const text = store.get(HANDOFF_KEY);
    if (text) openText(text);
    else showLoader("Brak przekazanego wyniku. Wczytaj plik JSON.");
  } else {
    showLoader();
  }
})();
