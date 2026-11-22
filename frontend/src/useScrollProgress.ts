import { useEffect, useState } from "react";

export interface Curtain {
  /** Current height of the fixed intro layer, px. */
  height: number;
  /** 0 at the top of the page → 1 once the layer has collapsed into the bar. */
  t: number;
  /** Viewport width, px (for type scaling). */
  vw: number;
}

/**
 * A fixed layer that starts full-screen and shrinks to `barHeight` as the page scrolls.
 * With a 100vh spacer under it, the content below rises exactly with its bottom edge.
 */
export function useCurtain(barHeight: number): Curtain {
  const read = (): Curtain => {
    const vh = window.innerHeight;
    const height = Math.max(barHeight, vh - window.scrollY);
    return { height, t: Math.min(1, window.scrollY / (vh - barHeight)), vw: window.innerWidth };
  };
  const [state, setState] = useState(read);

  useEffect(() => {
    let frame = 0;
    const onScroll = () => {
      if (!frame)
        frame = requestAnimationFrame(() => {
          frame = 0;
          setState(read());
        });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [barHeight]);

  return state;
}

/** Maps t from [a, b] onto [0, 1], clamped. */
export const span = (t: number, a: number, b: number) => Math.min(1, Math.max(0, (t - a) / (b - a)));
/** Sine ease-in-out: slow start, slow finish. */
export const ease = (t: number) => -(Math.cos(Math.PI * t) - 1) / 2;
export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

/** Eased scroll to `y` over `ms` (native smooth scroll is too quick for the intro). */
export function glideTo(y: number, ms = 1000) {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    window.scrollTo(0, y);
    return;
  }
  const from = window.scrollY;
  const start = performance.now();
  const step = (now: number) => {
    const t = Math.min(1, (now - start) / ms);
    window.scrollTo(0, from + (y - from) * ease(t));
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
