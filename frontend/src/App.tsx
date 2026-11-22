import { useLayoutEffect, type MouseEvent } from "react";
import { Workspace } from "./Workspace";
import { ease, glideTo, lerp, span, useCurtain } from "./useScrollProgress";

const BAR = 56;
const reducedMotion =
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Fixed navy layer: full-screen "Kodex" intro (COI visuals) that collapses in place into
 * the top bar as you scroll; the workspace rises with its bottom edge.
 */
/** Glide to the workspace (curtain fully collapsed) and move focus there. */
function toWorkspace(e: MouseEvent) {
  e.preventDefault();
  glideTo(window.innerHeight - BAR);
  document.getElementById("main")?.focus({ preventScroll: true });
}

function Curtain({ animated }: { animated: boolean }) {
  const c = useCurtain(BAR);
  const t = animated ? c.t : 1;
  const height = animated ? c.height : BAR;
  const big = Math.min(208, Math.max(80, c.vw * 0.16));
  const intro = 1 - span(t, 0, 0.35); // tagline, CTA
  const bar = span(t, 0.85, 1); // divider + subtitle

  return (
    <header
      className="fixed inset-x-0 top-0 z-40 overflow-hidden bg-navy-950 text-white"
      style={{ height, boxShadow: t >= 1 ? "0 8px 24px rgb(0 0 0 / 0.2)" : "none" }}
    >
      <div
        aria-hidden="true"
        className="absolute -right-48 -top-64 h-[56rem] w-[56rem] rounded-full bg-[radial-gradient(circle_at_30%_30%,#1e3a8a,#0d2a66_55%,#0a1a44)]"
        style={{ opacity: 1 - t * 0.75 }}
      />
      <div
        aria-hidden="true"
        className="absolute right-[18%] top-[60vh] h-80 w-56 bg-gradient-to-b from-[#3b2a7a]/70 via-[#860031] to-accent"
        style={{ opacity: 1 - span(t, 0.5, 0.9) }}
      />

      <div className="relative mx-auto flex h-full max-w-6xl flex-col justify-center px-6">
        {/* Only the logo takes part in layout; the intro copy hangs below it, so fading it out never moves the title. */}
        <div className="relative">
          <a href="#main" onClick={toWorkspace} className="flex items-center gap-3">
            <span className="font-black leading-none tracking-tighter" style={{ fontSize: lerp(big, 24, ease(t)) }}>
              Kodex
            </span>
            <span className="h-5 w-px bg-accent" style={{ opacity: bar }} aria-hidden="true" />
            <span className="whitespace-nowrap text-xs text-white/70" style={{ opacity: bar }}>
              Generator e-formularzy
            </span>
          </a>
          <div
            className="absolute left-0 top-full"
            style={{ opacity: intro, visibility: intro > 0 ? "visible" : "hidden" }}
          >
            <p className="mt-6 max-w-md text-xl font-light text-white/80">
              Papierowe formularze urzędowe → konfiguracje e-usług w JSON.
            </p>
            <a
              href="#main"
              onClick={toWorkspace}
              className="mt-10 inline-flex items-center gap-2 whitespace-nowrap rounded-lg border border-white/40 px-4 py-2 text-sm hover:bg-white/10"
            >
              Przejdź do narzędzia ↓
            </a>
          </div>
        </div>
      </div>

      <nav
        aria-label="Główna"
        className="absolute inset-x-0 top-0 mx-auto flex h-14 max-w-6xl items-center justify-end gap-6 px-6 text-sm text-white/80"
      >
        <a href="#main" onClick={toWorkspace} className="hover:text-white">Prześlij</a>
        <a href="/preview" className="hover:text-white">Podgląd</a>
        <a href="/docs" className="hover:text-white">API</a>
      </nav>
    </header>
  );
}

export default function App() {
  const animated = !reducedMotion;
  // Coming back from /preview (/#main): land on the workspace, no intro replay.
  useLayoutEffect(() => {
    if (location.hash !== "#main") return;
    const y = animated ? window.innerHeight - BAR : 0;
    window.scrollTo(0, y);
    requestAnimationFrame(() => window.scrollTo(0, y)); // after the browser's own fragment scroll
  }, [animated]);
  return (
    <div className="min-h-[200vh] bg-surface text-ink">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2"
      >
        Przejdź do treści
      </a>
      <Curtain animated={animated} />
      {/* Spacer under the curtain: content starts one screen down, so it rises with the curtain's edge. */}
      <div aria-hidden="true" style={{ height: animated ? "100vh" : BAR }} />
      <Workspace />
    </div>
  );
}
