import { useEffect, useState } from "react";

import { setRecorderArm } from "../api";
import { useUIStore } from "../store";

const VISIBLE_MODES = new Set(["auto-aim", "auto-sentry"]);

function fmtElapsed(seconds: number): string {
  const mm = Math.floor(seconds / 60).toString();
  const ss = (seconds % 60).toString().padStart(2, "0");
  return `${mm}:${ss}`;
}

export default function RecordToggle() {
  const mode = useUIStore((s) => s.mode?.mode);
  const armed = useUIStore((s) => s.hub?.armed ?? false);
  // recorder_start_ts is a Unix epoch (seconds) published by the hub when a
  // clip_started event arrives. Using the backend timestamp means the counter
  // survives page reloads correctly — we don't reset to zero on reconnect.
  const startedAtSec = useUIStore((s) => s.hub?.recorder_start_ts ?? null);
  const [pending, setPending] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!armed) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [armed]);

  // Show whenever recording is active so the user can always stop it,
  // even after a mode change. Otherwise only show in modes that allow start.
  const visible = armed || (mode != null && VISIBLE_MODES.has(mode));
  if (!visible) return null;

  async function toggle() {
    setPending(true);
    try {
      await setRecorderArm(!armed);
    } catch (e) {
      console.warn("[record]", e);
    } finally {
      setPending(false);
    }
  }

  const elapsed =
    armed && startedAtSec != null
      ? Math.max(0, Math.floor((now - startedAtSec * 1000) / 1000))
      : 0;

  return (
    <button
      className={armed ? "btn record-toggle recording" : "btn record-toggle stopped"}
      onClick={toggle}
      disabled={pending}
      title={armed ? "Stop recording" : "Start recording"}
    >
      {armed ? (
        <>
          <span className="rec-dot" /> REC {fmtElapsed(elapsed)}
        </>
      ) : (
        "● Record"
      )}
    </button>
  );
}
