/**
 * Summary: The reduced-motion hook the UI uses to honour the OS "Reduce motion"
 * setting (WCAG / DESIGN.md accessibility): animated surfaces (the fraud gauge, the
 * streaming SAR caret, progress transitions) read this and render their final state
 * instantly instead of animating. It subscribes to the `prefers-reduced-motion: reduce`
 * media query and re-renders on change, and is defensive about environments where
 * `matchMedia` is absent (older jsdom / SSR) so it never throws.
 *
 * Key classes:
 * - (none)
 *
 * Key functions:
 * - usePrefersReducedMotion: true when the user has requested reduced motion.
 *
 * Notes:
 * - When `matchMedia` is unavailable the hook reports false (animate) — animation is the
 *   richer default and the absence of the API means we cannot detect a preference.
 */
import { useEffect, useState } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      return;
    }
    const media = window.matchMedia(QUERY);
    setReduced(media.matches);
    const onChange = (event: MediaQueryListEvent): void => setReduced(event.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  return reduced;
}
