import { useCallback, useEffect, useRef } from "react";

import { sendAim } from "../api";

// Coalesce arbitrarily-frequent aim() calls down to ≤ rateHz POSTs.
// Always sends the most recent value; drops intermediates. Sends a final
// (0, 0) when the user releases — the caller just calls send(0, 0).
export function useThrottledAim(rateHz = 20) {
  const lastSent = useRef(0);
  const pending = useRef<{ pan: number; tilt: number } | null>(null);
  const timer = useRef<number | null>(null);
  const minInterval = 1000 / rateHz;

  const flush = useCallback(() => {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    const v = pending.current;
    if (!v) return;
    pending.current = null;
    lastSent.current = performance.now();
    sendAim(v.pan, v.tilt).catch((e) => console.warn("[aim]", e));
  }, []);

  const send = useCallback(
    (pan: number, tilt: number) => {
      pending.current = { pan, tilt };
      const now = performance.now();
      const elapsed = now - lastSent.current;
      if (elapsed >= minInterval) {
        flush();
      } else if (timer.current === null) {
        timer.current = window.setTimeout(flush, minInterval - elapsed);
      }
    },
    [flush, minInterval],
  );

  useEffect(() => {
    return () => {
      if (timer.current !== null) clearTimeout(timer.current);
    };
  }, []);

  return send;
}
