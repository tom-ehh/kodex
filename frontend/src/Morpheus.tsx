// Morpheus panel: the human side of the learning loop. Review queue (approve/reject),
// active skills, skillset version and last sleep cycle, and a manual "sleep now".
// Wake and the scheduled sleep run on the server without this page.

import { useCallback, useEffect, useState } from "react";
import { ApiError, morpheus, type ReviewItem, type SkillOut } from "./api";

const KIND: Record<ReviewItem["kind"], string> = {
  proposed_skill: "Propozycja umiejętności",
  needs_human_episode: "Poprawka do potwierdzenia",
  needs_review_form: "Formularz do weryfikacji",
  recurring_failure: "Powtarzający się błąd",
};

const HELP: Record<ReviewItem["kind"], string> = {
  proposed_skill: "Morpheus napisał regułę, ale nie przeszła testu powtórki. Zatwierdzenie ją aktywuje.",
  needs_human_episode: "Pierwsza taka poprawka. Zatwierdzenie pozwala Morpheusowi nauczyć się z niej (bramka jakości nadal działa).",
  needs_review_form: "Żaden model nie wygenerował poprawnego formularza. Popraw szkic w podglądzie; poprawka zamknie ten element.",
  recurring_failure: "Te same pola wielokrotnie nie przechodzą walidacji. Popraw jeden taki formularz, aby Morpheus nauczył się wzorca.",
};

function detail(i: ReviewItem): string {
  const d = i.diff ?? {};
  switch (i.kind) {
    case "needs_review_form":
      return `Próbowane poziomy: ${((d.tiers as string[]) ?? []).join(" → ")}${d.has_draft ? " · jest szkic" : ""}`;
    case "recurring_failure":
      return [d.validator && d.validator !== "-" ? d.validator : null, `miejsce ${d.where ?? d.family ?? "?"}`,
        `błąd ${d.error_class ?? "?"}`, `${d.count ?? "?"} razy`].filter(Boolean).join(" · ");
    case "needs_human_episode":
      return `Etap: ${d.stage ?? "?"}`;
    case "proposed_skill": {
      const sk = (d.skill ?? {}) as { name?: string; description?: string };
      return [sk.name, sk.description].filter(Boolean).join(": ") || (d.source === "sleep" ? "Uogólnienie ze snu" : "");
    }
  }
}

export function MorpheusPanel() {
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [skills, setSkills] = useState<SkillOut[]>([]);
  const [version, setVersion] = useState<number | null>(null);
  const [lastCycle, setLastCycle] = useState<Record<string, unknown> | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "off" | "auth" | "error">("loading");
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [r, s, m] = await Promise.all([morpheus.review(), morpheus.skills(), morpheus.metrics()]);
      setItems(r);
      setSkills(s);
      setVersion(m.current_version);
      setLastCycle(m.cycles.at(-1) ?? null);
      setState("ok");
    } catch (e) {
      setState(e instanceof ApiError && e.status === 503 ? "off" : e instanceof ApiError && e.status === 401 ? "auth" : "error");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  const decide = async (id: string, decision: "approve" | "reject") => {
    setBusy(true);
    try {
      await morpheus.decide(id, decision);
      setNote(decision === "approve" ? "Zatwierdzono." : "Odrzucono.");
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      load();
    }
  };

  const sleep = async () => {
    setBusy(true);
    try {
      await morpheus.sleep();
      setNote("Cykl snu uruchomiony: grupowanie, uogólnianie i test powtórki działają w tle.");
    } catch (e) {
      setNote(e instanceof ApiError && e.status === 409 ? "Sen jest teraz niedostępny (trwa inny cykl albo zestaw umiejętności jest przypięty)." : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setTimeout(load, 1500);
    }
  };

  if (state === "off") return null; // Morpheus disabled on this server

  const byLayer = (l: SkillOut["layer"]) => skills.filter((s) => s.layer === l).length;

  return (
    <section aria-labelledby="morpheus-title" className="rounded-xl border border-line bg-white">
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-line px-8 py-5">
        <div>
          <h2 id="morpheus-title" className="text-2xl font-light">Morpheus</h2>
          <p className="text-sm text-muted">Uczy się z poprawek na bieżąco, a w czasie snu (harmonogram) uogólnia reguły i sprawdza je testem powtórki.</p>
        </div>
        <div className="flex items-center gap-3">
          <button type="button" onClick={load} className="text-sm text-muted hover:text-ink hover:underline">Odśwież</button>
          <button
            type="button"
            disabled={busy || state !== "ok"}
            onClick={sleep}
            className="rounded-lg bg-navy-950 px-4 py-2 text-sm font-medium text-white hover:bg-navy-800 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Uśpij teraz
          </button>
        </div>
      </div>

      {state === "auth" && <p className="px-8 py-4 text-sm text-error">Wymagany token JWT.</p>}
      {state === "error" && <p className="px-8 py-4 text-sm text-error">Nie udało się pobrać stanu Morpheusa.</p>}

      {state === "ok" && (
        <>
          <dl className="grid grid-cols-2 gap-4 border-b border-line px-8 py-4 text-sm sm:grid-cols-4">
            <div><dt className="text-muted">Wersja zestawu</dt><dd className="text-lg tabular-nums">{version ?? "–"}</dd></div>
            <div><dt className="text-muted">Umiejętności bazowe</dt><dd className="text-lg tabular-nums">{byLayer("seed")}</dd></div>
            <div><dt className="text-muted">Wyuczone (epizodyczne / ogólne)</dt><dd className="text-lg tabular-nums">{byLayer("episodic")} / {byLayer("general")}</dd></div>
            <div>
              <dt className="text-muted">Ostatni sen</dt>
              <dd className="tabular-nums">{lastCycle ? `${String(lastCycle.started_at ?? "").slice(0, 16).replace("T", " ")} · ${String(lastCycle.trigger ?? "")}` : "jeszcze nie spał"}</dd>
            </div>
          </dl>
          {note && <p role="status" className="border-b border-line px-8 py-3 text-sm">{note}</p>}
          <h3 className="px-8 pt-4 text-sm font-medium uppercase tracking-wide text-muted">Kolejka do przeglądu ({items.length})</h3>
          {items.length === 0 ? (
            <p className="px-8 pb-6 pt-2 text-sm text-muted">Nic nie czeka na decyzję.</p>
          ) : (
            <ul className="divide-y divide-line">
              {items.map((i) => (
                <li key={i.id} className="flex flex-wrap items-start gap-4 px-8 py-4 text-sm">
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{KIND[i.kind] ?? i.kind}</p>
                    <p className="text-muted">{detail(i)}</p>
                    <p className="mt-1 text-xs text-muted/80">
                      {i.kind === "needs_review_form" && !i.diff?.has_draft
                        ? "Żaden model nie doszedł nawet do szkicu, więc nie ma czego poprawiać w podglądzie. Sprawdź plik (jakość skanu) albo przeanalizuj go ponownie."
                        : HELP[i.kind]}
                    </p>
                  </div>
                  <span className="inline-flex gap-3">
                    <button type="button" disabled={busy} onClick={() => decide(i.id, "approve")} className="font-medium text-focus hover:underline disabled:opacity-40">
                      {i.kind === "needs_review_form" || i.kind === "recurring_failure" ? "Przyjmij do wiadomości" : "Zatwierdź"}
                    </button>
                    <button type="button" disabled={busy} onClick={() => decide(i.id, "reject")} className="text-muted hover:text-ink hover:underline disabled:opacity-40">
                      Odrzuć
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
