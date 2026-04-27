import { useEffect, useState } from "react";

import { getSettings, putSettings, SettingsPayload } from "../api";
import { useUIStore } from "../store";

const TARGETS = ["cat", "raccoon", "rat", "deer"] as const;
const SCHEMES = ["joystick", "keyboard", "tilt"] as const;

type Status = { kind: "idle" | "saving" | "saved" | "error"; msg?: string };

export default function Settings() {
  const hubConfig = useUIStore((s) => s.hubConfig);
  const [form, setForm] = useState<SettingsPayload | null>(null);
  const [initial, setInitial] = useState<SettingsPayload | null>(null);
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  useEffect(() => {
    let cancelled = false;
    getSettings()
      .then((s) => {
        if (cancelled) return;
        setForm(s);
        setInitial(s);
      })
      .catch((e: Error) => setStatus({ kind: "error", msg: e.message }));
    return () => { cancelled = true; };
  }, []);

  // The hub authoritatively normalizes system fields. If retained hub_config
  // arrives with values that differ from what we have on screen (e.g. another
  // tab saved while this one was open), reflect the truth.
  useEffect(() => {
    if (!hubConfig || !form) return;
    const drift =
      hubConfig.motion_timeout_s !== form.motion_timeout_s ||
      hubConfig.trailing_s !== form.trailing_s ||
      hubConfig.target_class !== form.target_class;
    if (drift && status.kind !== "saving") {
      setForm({ ...form,
        motion_timeout_s: hubConfig.motion_timeout_s,
        trailing_s: hubConfig.trailing_s,
        target_class: hubConfig.target_class,
      });
      setInitial((prev) => prev && ({ ...prev,
        motion_timeout_s: hubConfig.motion_timeout_s,
        trailing_s: hubConfig.trailing_s,
        target_class: hubConfig.target_class,
      }));
    }
  }, [hubConfig]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!form || !initial) {
    return (
      <main className="main settings">
        <h2>Settings</h2>
        <p className="muted">{status.kind === "error" ? status.msg : "Loading…"}</p>
      </main>
    );
  }

  const dirty =
    form.motion_timeout_s !== initial.motion_timeout_s ||
    form.trailing_s !== initial.trailing_s ||
    form.target_class !== initial.target_class ||
    form.default_control_scheme !== initial.default_control_scheme ||
    form.telemetry_poll_interval_s !== initial.telemetry_poll_interval_s;

  function save() {
    if (!form) return;
    setStatus({ kind: "saving" });
    putSettings(form)
      .then((effective) => {
        setForm(effective);
        setInitial(effective);
        setStatus({ kind: "saved" });
        setTimeout(() => setStatus((s) => (s.kind === "saved" ? { kind: "idle" } : s)), 2000);
      })
      .catch((e: Error) => setStatus({ kind: "error", msg: e.message }));
  }

  function reset() {
    setForm(initial);
    setStatus({ kind: "idle" });
  }

  const update = <K extends keyof SettingsPayload>(k: K, v: SettingsPayload[K]) =>
    setForm({ ...form, [k]: v });

  return (
    <main className="main settings">
      <h2>Settings</h2>
      <div className="settings-form">

        <div className="settings-row">
          <label htmlFor="motion-timeout">Motion timeout (s)</label>
          <input
            id="motion-timeout"
            type="range"
            min={1} max={30} step={1}
            value={form.motion_timeout_s}
            onChange={(e) => update("motion_timeout_s", Number(e.target.value))}
          />
          <span>{form.motion_timeout_s}s</span>
          <span className="help">Stop tracking this many seconds after the last motion event.</span>
        </div>

        <div className="settings-row">
          <label htmlFor="trailing-s">Trailing record (s)</label>
          <input
            id="trailing-s"
            type="range"
            min={3} max={60} step={1}
            value={form.trailing_s}
            onChange={(e) => update("trailing_s", Number(e.target.value))}
          />
          <span>{form.trailing_s}s</span>
          <span className="help">Keep recording for this many seconds after motion ends.</span>
        </div>

        <div className="settings-row">
          <label htmlFor="target-class">Target class</label>
          <select
            id="target-class"
            value={form.target_class}
            onChange={(e) => update("target_class", e.target.value)}
          >
            {TARGETS.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <span></span>
          <span className="help">Inference will only fire on this class.</span>
        </div>

        <div className="settings-row">
          <label htmlFor="scheme">Default control scheme</label>
          <select
            id="scheme"
            value={form.default_control_scheme}
            onChange={(e) => update("default_control_scheme", e.target.value)}
          >
            {SCHEMES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <span></span>
          <span className="help">UI-only; what the dashboard loads with by default.</span>
        </div>

        <div className="settings-row">
          <label htmlFor="poll">Telemetry poll (s)</label>
          <input
            id="poll"
            type="number"
            min={1} max={60}
            value={form.telemetry_poll_interval_s}
            onChange={(e) => update("telemetry_poll_interval_s", Number(e.target.value))}
          />
          <span></span>
          <span className="help">UI-only; reserved for future polling fallback when the socket is down.</span>
        </div>

        <div className="settings-row">
          <label>Motion control speed</label>
          <input type="range" disabled min={0} max={100} value={50} readOnly />
          <span className="muted">—</span>
          <span className="help">Reserved for the motion-ctrl service (Step G).</span>
        </div>

        <div className="settings-actions">
          <button className="btn btn-primary" onClick={save} disabled={!dirty || status.kind === "saving"}>
            {status.kind === "saving" ? "Saving…" : "Save"}
          </button>
          <button className="btn" onClick={reset} disabled={!dirty}>Reset</button>
          <span className={`settings-status ${status.kind}`}>
            {status.kind === "saved" && "Saved."}
            {status.kind === "error" && status.msg}
          </span>
        </div>
      </div>
    </main>
  );
}
