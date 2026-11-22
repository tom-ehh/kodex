// Batch state: files picked in the browser, analysed one request each, results kept
// in sessionStorage so they survive a trip to /preview. The server stores nothing.

import { useCallback, useEffect, useRef, useState } from "react";
import { analyze, ApiError, session, type Envelope, type Limits } from "./api";

export type ItemStatus = "ready" | "queued" | "analyzing" | "done" | "review" | "failed" | "rejected";

export interface Item {
  id: string;
  name: string;
  size: number;
  status: ItemStatus;
  updatedAt: number;
  startedAt?: number;
  error?: string;
  result?: Envelope;
}

const STORE_KEY = "kodex.batch";
const CONCURRENCY = 2;

/** Client-side pre-check mirroring the server rules (GET /formats); the server re-checks. */
export function precheck(file: File, limits: Limits | null): string | null {
  if (!limits) return null;
  const dot = file.name.lastIndexOf(".");
  const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
  if (!limits.formats.some((f) => f.extensions.includes(ext)))
    return `Nieobsługiwany format${ext ? ` (${ext})` : ""}.`;
  if (file.size > limits.max_file_size_bytes)
    return `Plik jest za duży (maks. ${formatBytes(limits.max_file_size_bytes)}).`;
  if (file.name.length > limits.filename_max_length)
    return `Nazwa pliku jest za długa (maks. ${limits.filename_max_length} znaków).`;
  const bad = [...limits.filename_forbidden_chars].filter((c) => file.name.includes(c));
  if (bad.length) return `Nazwa pliku zawiera niedozwolone znaki: ${bad.join(" ")}`;
  if (file.size === 0) return "Plik jest pusty.";
  return null;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1).replace(/\.0$/, "")} MB`;
}

function load(): Item[] {
  try {
    const items: Item[] = JSON.parse(session.get(STORE_KEY) ?? "[]");
    // Files do not survive a reload: anything unfinished cannot resume.
    return items.map((i) =>
      ["ready", "queued", "analyzing"].includes(i.status)
        ? { ...i, status: "failed", error: "Przerwano (strona została przeładowana). Prześlij plik ponownie." }
        : i,
    );
  } catch {
    return [];
  }
}

export function useBatch(limits: Limits | null) {
  const [items, setItems] = useState<Item[]>(load);
  const files = useRef(new Map<string, File>());
  const running = useRef(0);
  const [authNeeded, setAuthNeeded] = useState(false);

  useEffect(() => session.set(STORE_KEY, JSON.stringify(items)), [items]);

  const patch = useCallback((id: string, p: Partial<Item>) => {
    setItems((xs) => xs.map((x) => (x.id === id ? { ...x, ...p, updatedAt: Date.now() } : x)));
  }, []);

  const add = useCallback(
    (picked: FileList | File[]) => {
      const now = Date.now();
      const next: Item[] = [...picked].map((f) => {
        const id = crypto.randomUUID();
        const error = precheck(f, limits);
        if (!error) files.current.set(id, f);
        return { id, name: f.name, size: f.size, status: error ? "rejected" : "ready", error: error ?? undefined, updatedAt: now };
      });
      setItems((xs) => [...next, ...xs]);
    },
    [limits],
  );

  const remove = useCallback((id: string) => {
    files.current.delete(id);
    setItems((xs) => xs.filter((x) => x.id !== id));
  }, []);

  const clearFinished = useCallback(() => {
    setItems((xs) => xs.filter((x) => !["done", "review", "failed", "rejected"].includes(x.status)));
  }, []);

  // Pump: start queued items up to CONCURRENCY.
  useEffect(() => {
    const queued = items.filter((i) => i.status === "queued");
    for (const item of queued.slice(0, Math.max(0, CONCURRENCY - running.current))) {
      const file = files.current.get(item.id);
      if (!file) {
        patch(item.id, { status: "failed", error: "Brak pliku. Dodaj go ponownie." });
        continue;
      }
      running.current++;
      patch(item.id, { status: "analyzing", startedAt: Date.now() });
      analyze(file)
        .then((result) => {
          files.current.delete(item.id);
          patch(item.id, { status: "done", result, error: undefined });
        })
        .catch((e: unknown) => {
          if (e instanceof ApiError && e.status === 401) setAuthNeeded(true);
          // 502 with a draft: no tier produced a valid form, Morpheus queued it for a human.
          const b = e instanceof ApiError ? e.body : undefined;
          if (b?.needs_review && b.form) {
            files.current.delete(item.id);
            patch(item.id, {
              status: "review",
              result: { form: b.form as Record<string, unknown>, form_id: b.form_id as string, needs_review: true, attempts: [], dictionary_proposals: [] },
              error: undefined,
            });
            return;
          }
          patch(item.id, { status: "failed", error: e instanceof Error ? e.message : String(e) });
        })
        .finally(() => {
          running.current--;
          setItems((xs) => [...xs]); // re-run the pump
        });
    }
  }, [items, patch]);

  const start = useCallback(() => {
    setItems((xs) => xs.map((x) => (x.status === "ready" ? { ...x, status: "queued", updatedAt: Date.now() } : x)));
  }, []);

  const retry = useCallback(
    (id: string) => {
      if (files.current.has(id)) patch(id, { status: "queued", error: undefined });
    },
    [patch],
  );

  return { items, add, remove, start, retry, clearFinished, authNeeded, setAuthNeeded, hasFile: (id: string) => files.current.has(id) };
}
