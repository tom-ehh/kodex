import { useEffect, useRef, useState, type DragEvent } from "react";
import { strToU8, zipSync } from "fflate";
import { getLimits, HANDOFF_KEY, session, TOKEN_KEY, type Limits } from "./api";
import { formatBytes, useBatch, type Item, type ItemStatus } from "./batch";

const STATUS: Record<ItemStatus, { label: string; cls: string }> = {
  ready: { label: "Gotowy do analizy", cls: "bg-surface text-muted ring-line" },
  queued: { label: "W kolejce", cls: "bg-surface text-muted ring-line" },
  analyzing: { label: "Analizowanie…", cls: "bg-blue-50 text-blue-700 ring-blue-200" },
  done: { label: "Gotowe", cls: "bg-green-50 text-ok ring-green-200" },
  failed: { label: "Błąd", cls: "bg-accent-tint text-error ring-accent/30" },
  rejected: { label: "Odrzucony", cls: "bg-accent-tint text-error ring-accent/30" },
};

const jsonName = (name: string) => name.replace(/\.[^.]+$/, "") + ".json";
const time = (t: number) => new Date(t).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function download(name: string, data: BlobPart, type: string) {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function openPreview(item: Item) {
  session.set(HANDOFF_KEY, JSON.stringify(item.result!.form));
  location.href = "/preview?source=session";
}

/** Re-render every second while something is analysing (elapsed time). */
function useTick(active: boolean) {
  const [, set] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => set((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [active]);
}

function Badge({ status }: { status: ItemStatus }) {
  const s = STATUS[status];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${s.cls}`}>
      {status === "analyzing" && <span className="size-1.5 animate-pulse rounded-full bg-current" aria-hidden="true" />}
      {s.label}
    </span>
  );
}

function TokenPrompt({ onDone }: { onDone: () => void }) {
  const [value, setValue] = useState(session.get(TOKEN_KEY) ?? "");
  return (
    <form
      className="flex flex-wrap items-end gap-3 rounded-lg border border-warn/40 bg-amber-50 p-4"
      onSubmit={(e) => {
        e.preventDefault();
        session.set(TOKEN_KEY, value.trim());
        onDone();
      }}
    >
      <label className="flex min-w-72 flex-1 flex-col gap-1 text-sm">
        <span className="font-medium">Serwer wymaga tokenu JWT</span>
        <input
          className="rounded border border-line bg-white px-3 py-2 font-mono text-xs"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="eyJhbGciOi…"
          autoComplete="off"
        />
      </label>
      <button className="rounded-lg bg-navy-950 px-4 py-2 text-sm font-medium text-white hover:bg-navy-800">Zapisz token</button>
    </form>
  );
}

export function Workspace() {
  const [limits, setLimits] = useState<Limits | null>(null);
  const [limitsError, setLimitsError] = useState<string | null>(null);
  const batch = useBatch(limits);
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    getLimits().then(setLimits, (e: Error) => setLimitsError(e.message));
  }, []);

  const { items } = batch;
  const ready = items.filter((i) => i.status === "ready");
  const active = items.filter((i) => i.status === "queued" || i.status === "analyzing");
  const done = items.filter((i) => i.status === "done");
  const tracked = items.filter((i) => i.status !== "ready" && i.status !== "rejected");
  const finished = tracked.filter((i) => i.status === "done" || i.status === "failed");
  useTick(active.length > 0);

  const exts = limits?.formats.flatMap((f) => f.extensions) ?? [];
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) batch.add(e.dataTransfer.files);
  };

  const downloadAll = () => {
    const entries: Record<string, Uint8Array> = {};
    for (const i of done) {
      let name = jsonName(i.name);
      for (let n = 2; name in entries; n++) name = jsonName(i.name).replace(/\.json$/, `-${n}.json`);
      entries[name] = strToU8(JSON.stringify(i.result!.form, null, 2));
    }
    download("kodex-formularze.zip", zipSync(entries) as Uint8Array<ArrayBuffer>, "application/zip");
  };

  return (
    <main id="main" tabIndex={-1} className="mx-auto flex max-w-6xl scroll-mt-14 flex-col gap-8 px-6 py-10 outline-none">
      {/* ---- Upload ---- */}
      <section aria-labelledby="upload-title" className="rounded-xl border border-line bg-white p-8">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="upload-title" className="text-2xl font-light">Prześlij formularze</h2>
          {limits && (
            <p className="text-sm text-muted">
              Do {limits.max_batch_files} plików naraz, maks. {formatBytes(limits.max_file_size_bytes)} każdy
            </p>
          )}
        </div>

        {limitsError && <p role="alert" className="mt-4 text-sm text-error">Nie udało się pobrać limitów: {limitsError}</p>}
        {batch.authNeeded && <div className="mt-4"><TokenPrompt onDone={() => batch.setAuthNeeded(false)} /></div>}

        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`mt-6 rounded-lg border-2 border-dashed px-6 py-16 text-center transition ${
            dragging ? "border-accent bg-accent-tint" : "border-line bg-surface"
          }`}
        >
          <p className="text-lg text-muted">Przeciągnij pliki tutaj</p>
          <p className="my-2 text-xs uppercase tracking-wide text-muted/70">lub</p>
          <button
            type="button"
            onClick={() => input.current?.click()}
            className="rounded-lg bg-accent px-5 py-2.5 font-medium text-white transition hover:bg-accent-hover active:bg-accent-active"
          >
            Wskaż pliki
          </button>
          <input
            ref={input}
            type="file"
            multiple
            hidden
            accept={exts.join(",")}
            onChange={(e) => {
              if (e.target.files?.length) batch.add(e.target.files);
              e.target.value = "";
            }}
          />
          <p className="mt-4 text-xs text-muted">
            {(limits?.formats.map((f) => f.kind.toUpperCase()) ?? ["PDF", "DOC", "DOCX", "ODS", "XLSX", "JPEG", "PNG"]).join(" · ")}
          </p>
        </div>

        {/* Picked but not yet sent */}
        {(ready.length > 0 || items.some((i) => i.status === "rejected")) && (
          <div className="mt-6">
            <ul className="divide-y divide-line rounded-lg border border-line">
              {items
                .filter((i) => i.status === "ready" || i.status === "rejected")
                .map((i) => (
                  <li key={i.id} className="flex items-center gap-4 px-4 py-3 text-sm">
                    <span className="min-w-0 flex-1 truncate font-medium" title={i.name}>{i.name}</span>
                    <span className="text-muted">{formatBytes(i.size)}</span>
                    {i.status === "rejected" ? (
                      <span role="alert" className="text-error">{i.error}</span>
                    ) : (
                      <Badge status={i.status} />
                    )}
                    <button type="button" onClick={() => batch.remove(i.id)} className="text-muted underline-offset-2 hover:text-ink hover:underline">
                      Usuń
                    </button>
                  </li>
                ))}
            </ul>
            <div className="mt-4 flex justify-end">
              <button
                type="button"
                disabled={!ready.length}
                onClick={batch.start}
                className="rounded-lg bg-navy-950 px-5 py-2.5 font-medium text-white transition hover:bg-navy-800 disabled:cursor-not-allowed disabled:opacity-40"
              >
                Analizuj {ready.length ? `(${ready.length})` : ""}
              </button>
            </div>
          </div>
        )}
      </section>

      {/* ---- Results ---- */}
      <section aria-labelledby="jobs-title" className="rounded-xl border border-line bg-white">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-line px-8 py-5">
          <h2 id="jobs-title" className="text-2xl font-light">Wyniki</h2>
          <div className="flex items-center gap-3">
            {finished.length > 0 && (
              <button type="button" onClick={batch.clearFinished} className="text-sm text-muted hover:text-ink hover:underline">
                Wyczyść zakończone
              </button>
            )}
            <button
              type="button"
              disabled={!done.length}
              onClick={downloadAll}
              className="rounded-lg border border-ink/20 px-4 py-2 text-sm font-medium hover:bg-surface disabled:cursor-not-allowed disabled:opacity-40"
            >
              Pobierz wszystko (.zip)
            </button>
          </div>
        </div>

        {tracked.length > 0 && (
          <div className="border-b border-line px-8 py-4" aria-live="polite">
            <div className="flex justify-between text-sm">
              <span>Przetworzono {finished.length} z {tracked.length}</span>
              {active.length > 0 && <span className="text-muted">Trwa analiza: {active.length}</span>}
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface" role="progressbar" aria-valuemin={0} aria-valuemax={tracked.length} aria-valuenow={finished.length}>
              <div className="h-full rounded-full bg-focus transition-all duration-500" style={{ width: `${(finished.length / tracked.length) * 100}%` }} />
            </div>
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-8 py-3 font-medium">Nazwa pliku</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Szczegóły</th>
                <th className="px-4 py-3 font-medium">Zaktualizowano</th>
                <th className="px-8 py-3 text-right font-medium">Akcje</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {tracked.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-8 py-20 text-center text-muted">Nie przeanalizowano jeszcze żadnych formularzy.</td>
                </tr>
              )}
              {tracked.map((i) => (
                <tr key={i.id} className="align-top">
                  <td className="max-w-72 truncate px-8 py-4 font-medium" title={i.name}>{i.name}</td>
                  <td className="px-4 py-4"><Badge status={i.status} /></td>
                  <td className="px-4 py-4 text-muted">
                    {i.status === "analyzing" && i.startedAt && <>{Math.round((Date.now() - i.startedAt) / 1000)} s</>}
                    {i.status === "done" && i.result && (
                      <>
                        Poziom <strong className="font-medium text-ink">{i.result.route.tier}</strong>
                        {i.result.route.escalated && " (eskalacja)"}
                        {" · "}
                        {String((i.result.form as { title?: string }).title ?? "")}
                      </>
                    )}
                    {i.status === "failed" && <span className="text-error">{i.error}</span>}
                  </td>
                  <td className="px-4 py-4 tabular-nums text-muted">{time(i.updatedAt)}</td>
                  <td className="whitespace-nowrap px-8 py-4 text-right">
                    {i.status === "done" && (
                      <span className="inline-flex gap-4">
                        <button type="button" onClick={() => openPreview(i)} className="font-medium text-focus hover:underline">
                          Podgląd e-formularza
                        </button>
                        <button
                          type="button"
                          onClick={() => download(jsonName(i.name), JSON.stringify(i.result!.form, null, 2), "application/json")}
                          className="font-medium hover:underline"
                        >
                          Pobierz JSON
                        </button>
                      </span>
                    )}
                    {i.status === "failed" && batch.hasFile(i.id) && (
                      <button type="button" onClick={() => batch.retry(i.id)} className="font-medium hover:underline">Ponów</button>
                    )}
                    {i.status !== "analyzing" && i.status !== "queued" && (
                      <button type="button" onClick={() => batch.remove(i.id)} className="ml-4 text-muted hover:text-ink hover:underline">Usuń</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
