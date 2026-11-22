// Thin client for the Kodex REST API (/api/v1). Same origin; JWT only when the server enables it.

export const TOKEN_KEY = "coi.token"; // shared with /preview
export const HANDOFF_KEY = "coi.previewDefinition"; // read by /preview?source=session
export const FORM_META_KEY = "coi.previewFormMeta"; // {form_id, needs_review}: enables corrections in /preview

export const session = {
  get(k: string): string | null {
    try {
      return sessionStorage.getItem(k);
    } catch {
      return null;
    }
  },
  set(k: string, v: string) {
    try {
      sessionStorage.setItem(k, v);
    } catch {
      /* storage unavailable or full: the app still works for this page view */
    }
  },
};

export interface Limits {
  formats: { kind: string; mime: string; extensions: string[] }[];
  max_file_size_bytes: number;
  max_batch_files: number;
  filename_max_length: number;
  filename_forbidden_chars: string;
}

export interface Envelope {
  form_id?: string;
  form: Record<string, unknown>;
  /** True for a 502 draft Morpheus queued for a human: not importable as is. */
  needs_review?: boolean;
  route?: { tier: string; confidence: number; model: string; escalated: boolean };
  attempts: { tier: string; model: string; calls: number; cost_usd: number; error: string | null }[];
  dictionary_proposals: unknown[];
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body?: Record<string, unknown>,
  ) {
    super(message);
  }
}

function headers(): HeadersInit {
  const token = session.get(TOKEN_KEY);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Polish, user-facing message for a failed response. */
async function failure(res: Response): Promise<ApiError> {
  let detail = "";
  let raw: Record<string, unknown> | undefined;
  try {
    const body = await res.json();
    raw = body;
    detail = typeof body.detail === "string" ? body.detail : "";
    const issues: { message?: string }[] | undefined = body.file?.issues;
    if (issues?.length) detail = issues.map((i) => i.message).filter(Boolean).join("; ");
  } catch {
    /* not JSON */
  }
  const byStatus: Record<number, string> = {
    401: "Wymagane uwierzytelnienie (token JWT).",
    403: "Brak uprawnień.",
    413: "Plik jest za duży.",
    422: "Plik nie przeszedł walidacji.",
    502: "Żaden model nie wygenerował poprawnego formularza.",
    503: "Usługa analizy jest niedostępna (brak skonfigurowanego modelu).",
  };
  const base = byStatus[res.status] ?? `Błąd serwera (${res.status}).`;
  return new ApiError(res.status, detail && res.status !== 401 ? `${base} ${detail}` : base, raw);
}

export async function getLimits(): Promise<Limits> {
  const res = await fetch("/api/v1/formats", { headers: headers() });
  if (!res.ok) throw await failure(res);
  return res.json();
}

export async function analyze(file: File, signal?: AbortSignal): Promise<Envelope> {
  const body = new FormData();
  body.append("file", file, file.name);
  const res = await fetch("/api/v1/forms/analyze?envelope=true", { method: "POST", body, headers: headers(), signal });
  if (!res.ok) throw await failure(res);
  return res.json();
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    ...init,
    headers: { ...headers(), ...(init.body ? { "Content-Type": "application/json" } : {}) },
  });
  if (!res.ok) throw await failure(res);
  return res.json();
}

// ---- Morpheus ----

export interface ReviewItem {
  id: string;
  kind: "proposed_skill" | "needs_human_episode" | "needs_review_form" | "recurring_failure";
  ref_id: string;
  diff: Record<string, unknown> | null;
  replay: Record<string, unknown> | null;
  status: string;
  created_at: string;
}

export interface SkillOut {
  id: string;
  name: string;
  layer: "seed" | "episodic" | "general";
  status: string;
  description: string;
  hits: number;
  wins: number;
  losses: number;
}

export const morpheus = {
  review: () => call<ReviewItem[]>("/morpheus/review"),
  decide: (id: string, decision: "approve" | "reject") =>
    call(`/morpheus/review/${encodeURIComponent(id)}`, { method: "POST", body: JSON.stringify({ decision }) }),
  skills: () => call<SkillOut[]>("/morpheus/skills?status=active"),
  metrics: () => call<{ current_version: number; cycles: Record<string, unknown>[] }>("/morpheus/metrics"),
  sleep: () => call<{ status: string }>("/morpheus/sleep", { method: "POST" }),
};
