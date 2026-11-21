// Field validators named in Form Definitions (`validation[]`). Each returns an error
// message in Polish, or null. REQUIRED is handled by the caller, since emptiness
// depends on the component.

const digits = (v) => String(v).replace(/[\s-]/g, "");
const weighted = (d, w) => w.reduce((sum, x, i) => sum + x * Number(d[i]), 0);

export function isEmpty(value) {
  if (value == null) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

export function validPesel(v) {
  const d = digits(v);
  if (!/^\d{11}$/.test(d)) return false;
  return (10 - (weighted(d, [1, 3, 7, 9, 1, 3, 7, 9, 1, 3]) % 10)) % 10 === Number(d[10]);
}

export function validNip(v) {
  const d = digits(v);
  if (!/^\d{10}$/.test(d)) return false;
  const c = weighted(d, [6, 5, 7, 2, 3, 4, 5, 6, 7]) % 11;
  return c !== 10 && c === Number(d[9]);
}

export function validRegon(v) {
  const d = digits(v);
  let w;
  if (/^\d{9}$/.test(d)) w = [8, 9, 2, 3, 4, 5, 6, 7];
  else if (/^\d{14}$/.test(d)) w = [2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8];
  else return false;
  return (weighted(d, w) % 11) % 10 === Number(d[d.length - 1]);
}

export function validIdCard(v) {
  const s = String(v).replace(/\s/g, "").toUpperCase();
  if (!/^[A-Z]{3}\d{6}$/.test(s)) return false;
  const val = (c) => (/[A-Z]/.test(c) ? c.charCodeAt(0) - 55 : Number(c));
  const w = [7, 3, 1, 0, 7, 3, 1, 7, 3];
  let sum = 0;
  for (let i = 0; i < 9; i++) if (i !== 3) sum += w[i] * val(s[i]);
  return sum % 10 === Number(s[3]);
}

const CHECKS = {
  EMAIL: [(v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(v).trim()), "Wpisz poprawny adres e-mail."],
  PESEL: [validPesel, "Wpisz poprawny numer PESEL (11 cyfr)."],
  NIP: [validNip, "Wpisz poprawny numer NIP (10 cyfr)."],
  REGON: [validRegon, "Wpisz poprawny numer REGON (9 lub 14 cyfr)."],
  ZIP_CODE: [(v) => /^\d{2}-\d{3}$/.test(String(v).trim()), "Wpisz kod pocztowy w formacie 00-000."],
  EPUAP: [(v) => /^\/[^/\s]+\/[^/\s]+$/.test(String(v).trim()), "Wpisz adres skrytki ePUAP, np. /nazwa/skrytka."],
  ADE: [(v) => /^AE:PL-[0-9A-Z]{5}-[0-9A-Z]{5}-[0-9A-Z]{5}-[0-9A-Z]{2}$/i.test(String(v).trim()),
    "Wpisz adres do doręczeń elektronicznych, np. AE:PL-12345-67890-ABCDE-12."],
  SERIAL_NUMBER_ID_CARD: [validIdCard, "Wpisz poprawną serię i numer dowodu osobistego."],
};
const PATTERN = /^pattern\s*[:=]\s*(.+)$/i;

/**
 * A pattern validator as {regex, message}, or null. Accepts the object form
 * {"name": "PATTERN", "regex": …, "message": …} (the design standards' Pattern with
 * regex and message; no document fixes its JSON) and the string "Pattern:<regex>".
 */
function patternOf(v) {
  if (v && typeof v === "object" && /^pattern$/i.test(v.name ?? v.type ?? "")) {
    return { regex: v.regex ?? v.value ?? "", message: v.message };
  }
  const m = typeof v === "string" ? PATTERN.exec(v) : null;
  return m ? { regex: m[1] } : null;
}

/** Readable name of a validator entry (string or object), for messages. */
export function validatorName(v) {
  return typeof v === "string" ? v : JSON.stringify(v);
}

/** True when the validator is one the preview can run. */
export function knownValidator(v) {
  return v === "REQUIRED" || (typeof v === "string" && v in CHECKS) || Boolean(patternOf(v)?.regex);
}

/** Run the non-REQUIRED validators of a field on a non-empty string value. */
export function check(validation, value) {
  for (const v of validation) {
    if (typeof v === "string" && v in CHECKS) {
      const [ok, msg] = CHECKS[v];
      if (!ok(value)) return msg;
      continue;
    }
    const p = patternOf(v);
    if (!p) continue;
    let re;
    try { re = new RegExp(p.regex); } catch { continue; }
    if (!re.test(String(value).trim())) return p.message || "Wpisz wartość w poprawnym formacie.";
  }
  return null;
}
