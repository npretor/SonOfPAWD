import { useEffect, useRef, useState } from "react";

import { fire } from "../api";
import { useUIStore } from "../store";

const COOLDOWN_MS = 500;

export default function FireButton() {
  const armed = useUIStore((s) => s.fireArmed?.on ?? false);
  const autoEnabled = useUIStore((s) => s.fireEnable?.on ?? false);
  const enabled = armed || autoEnabled;

  const [cooling, setCooling] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => () => {
    if (timer.current !== null) clearTimeout(timer.current);
  }, []);

  async function onClick() {
    if (!enabled || cooling) return;
    setErr(null);
    setCooling(true);
    timer.current = window.setTimeout(() => setCooling(false), COOLDOWN_MS);
    try {
      await fire();
    } catch (e: any) {
      const msg = e?.message ?? String(e);
      setErr(msg);
      console.warn("[fire]", msg);
    }
  }

  return (
    <div className="fire-wrap">
      <button
        className="btn fire-button"
        onClick={onClick}
        disabled={!enabled || cooling}
        title={!enabled ? "arm the turret first" : "fire"}
      >
        FIRE
      </button>
      {err && <span className="error">{err}</span>}
    </div>
  );
}
