import { useState } from "react";
import { setMode } from "../api";
import { useUIStore } from "../store";

const MODES = ["idle", "monitor", "trailcam", "auto-aim", "auto-sentry"];

export default function ModeSelect() {
  const current = useUIStore((s) => s.mode?.mode ?? "idle");
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function onChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const next = e.target.value;
    setErr(null);
    setPending(true);
    try {
      await setMode(next);
    } catch (e: any) {
      setErr(e?.message ?? String(e));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="mode-select">
      <label>
        <span className="muted">mode</span>
        <select value={current} onChange={onChange} disabled={pending}>
          {MODES.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
      </label>
      {err && <span className="error">{err}</span>}
    </div>
  );
}
