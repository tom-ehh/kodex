// A deliberately small, safe Markdown subset for field descriptions. Descriptions
// may come from LLM output, so all HTML is escaped first; only paragraphs, line
// breaks, "- " lists, **bold**, *italic* and [links](http/https/mailto/#) survive.

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escape = (s) => s.replace(/[&<>"']/g, (c) => ESC[c]);

function inline(text) {
  let s = escape(text);
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, url) => {
    if (!/^(https?:|mailto:|#)/i.test(url)) return label;
    const external = /^https?:/i.test(url);
    const target = external ? ' target="_blank" rel="noopener noreferrer"' : "";
    return `<a class="fp-link" href="${url}"${target}>${label}<span class="fp-ext" aria-hidden="true"></span></a>`;
  });
  s = s.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  return s;
}

// ---- HTML descriptions (the organisers' exports use a rich-text editor's HTML) ----

const KEEP = new Set(["P", "BR", "STRONG", "B", "EM", "I", "U", "UL", "OL", "LI"]);
const DROP = new Set(["SCRIPT", "STYLE", "IMG", "IFRAME", "OBJECT", "EMBED", "SVG", "MATH", "TEMPLATE", "HEAD", "TITLE", "META", "LINK"]);
const looksLikeHtml = (s) => /<\/?(html|body|p|br|span|strong|b|em|i|u|a|ul|ol|li|div)\b[^>]*>/i.test(s);

function cleanNode(node, out) {
  for (const child of [...node.childNodes]) {
    if (child.nodeType === Node.TEXT_NODE) {
      out.append(child.textContent);
    } else if (child.nodeType === Node.ELEMENT_NODE) {
      const tag = child.tagName;
      if (DROP.has(tag)) continue;
      if (tag === "A") {
        const text = child.textContent;
        if (!text.trim()) { if (text) out.append(" "); continue; } // icon-only or spacer links
        const href = (child.getAttribute("href") ?? "").trim();
        const a = document.createElement("a");
        a.className = "fp-link";
        // Only safe schemes keep their target; anything else (e.g. a bare label) becomes inert.
        a.href = /^(https?:|mailto:|#)/i.test(href) ? href : "#";
        if (/^https?:/i.test(href)) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
        a.append(text);
        const icon = document.createElement("span");
        icon.className = "fp-ext";
        icon.setAttribute("aria-hidden", "true");
        a.append(icon);
        out.append(a);
      } else if (KEEP.has(tag)) {
        const el = document.createElement(tag.toLowerCase());
        cleanNode(child, el);
        out.append(el);
      } else {
        cleanNode(child, out); // unwrap span, div, font, … keeping their text
      }
    }
  }
}

/** Sanitise editor HTML down to a small allowlist; returns "" when nothing visible remains. */
export function sanitizeHtml(html) {
  const doc = new DOMParser().parseFromString(String(html), "text/html");
  const out = document.createElement("div");
  cleanNode(doc.body, out);
  return out.textContent.trim() ? out.innerHTML : "";
}

/** Description text (HTML from the editor, or the Markdown subset) to safe HTML. */
export function richText(text) {
  if (!text) return "";
  return looksLikeHtml(String(text)) ? sanitizeHtml(text) : markdown(text);
}

/** Markdown subset to an HTML string (safe to assign to innerHTML). */
export function markdown(text) {
  if (!text) return "";
  return String(text)
    .replace(/\r\n?/g, "\n")
    .split(/\n{2,}/)
    .map((block) => {
      const lines = block.split("\n");
      if (lines.every((l) => /^\s*[-*]\s+/.test(l))) {
        return `<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*[-*]\s+/, ""))}</li>`).join("")}</ul>`;
      }
      return `<p>${lines.map(inline).join("<br>")}</p>`;
    })
    .join("");
}
