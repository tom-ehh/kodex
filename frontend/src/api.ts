// Thin client for the Kodex REST API (/api/v1). Same origin; JWT only when the server enables it.

export const TOKEN_KEY = "coi.token"; // shared with /preview
export const HANDOFF_KEY = "coi.previewDefinition"; // read by /preview?source=session

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
  form: Record<string, unknown>;
  route: { tier: string; confidence: number; model: string; escalated: boolean };
  attempts: { tier: string; model: string; calls: number; cost_usd: number; error: string | null }[];
  dictionary_proposals: unknown[];
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
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
  try {
    const body = await res.json();
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
  return new ApiError(res.status, detail && res.status !== 401 ? `${base} ${detail}` : base);
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
