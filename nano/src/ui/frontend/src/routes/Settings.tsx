import { useEffect, useState } from "react";

import {
  CalibrationPayload,
  getCalibration,
  getSettings,
  putCalibration,
  putSettings,
  SettingsPayload,
} from "../api";
import { useUIStore } from "../store";

const TARGETS = ["cat", "raccoon", "rat", "deer"] as const;
const SCHEMES = ["joystick", "keyboard", "tilt"] as const;

type Status = { kind: "idle" | "saving" | "saved" | "error"; msg?: string };

const DEFAULT_CAL: CalibrationPayload = {
  pan: { min_us: 1000, max_us: 2000, center_us: 1500, invert: true },
  tilt: { min_us: 1000, max_us: 2000, center_us: 1500, invert: true },
  max_slew_us_per_tick: 40,
};

const US_MIN = 500;
const US_MAX = 2500;

function mergeCal(base: CalibrationPayload, partial: Partial<CalibrationPayload>): CalibrationPayload {
  return {
    pan: { ...base.pan, ...(partial.pan ?? {}) },
    tilt: { ...base.tilt, ...(partial.tilt ?? {}) },
    max_slew_us_per_tick: partial.max_slew_us_per_tick ?? base.max_slew_us_per_tick,
  };
}

function sameCal(a: CalibrationPayload, b: CalibrationPayload): boolean {
  return (
    a.pan.min_us === b.pan.min_us && a.pan.max_us === b.pan.max_us &&
    a.pan.center_us === b.pan.center_us && a.pan.invert === b.pan.invert &&
    a.tilt.min_us === b.tilt.min_us && a.tilt.max_us === b.tilt.max_us &&
    a.tilt.center_us === b.tilt.center_us && a.tilt.invert === b.tilt.invert &&
    a.max_slew_us_per_tick === b.max_slew_us_per_tick
  );
}

function calValid(c: CalibrationPayload): string | null {
  for (const axis of ["pan", "tilt"] as const) {
    const a = c[axis];
    if (!(US_MIN <= a.min_us && a.min_us < a.center_us && a.center_us < a.max_us && a.max_us <= US_MAX)) {
      return `${axis}: need ${US_MIN} ≤ min < center < max ≤ ${US_MAX}`;
    }
  }
  if (c.max_slew_us_per_tick < 1 || c.max_slew_us_per_tick > 500) {
    return "slew: 1..500 µs/tick";
  }
  return null;
}

export default function Settings() {
  const hubConfig = useUIStore((s) => s.hubConfig);
  const motionCal = useUIStore((s) => s.motionCalibration);
  const [form, setForm] = useState<SettingsPayload | null>(null);
  const [initial, setInitial] = useState<SettingsPayload | null>(null);
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  const [cal, setCal] = useState<CalibrationPayload>(DEFAULT_CAL);
  const [calInitial, setCalInitial] = useState<CalibrationPayload>(DEFAULT_CAL);
  const [calStatus, setCalStatus] = useState<Status>({ kind: "idle" });

  // Initial calibration load: prefer the retained MQTT value via REST so we
  // don't race the socket replay on a hard refresh.
  useEffect(() => {
    let cancelled = false;
    getCalibration()
      .then((r) => {
        if (cancelled) return;
        const merged = mergeCal(DEFAULT_CAL, r.calibration);
        setCal(merged);
        setCalInitial(merged);
      })
      .catch((e: Error) => setCalStatus({ kind: "error", msg: e.message }));
    return () => { cancelled = true; };
  }, []);

  // If another tab saves while we're open, MQTT delivers the new retained
  // value over the socket. Reflect it unless the user is mid-edit.
  useEffect(() => {
    if (!motionCal) return;
    if (calStatus.kind === "saving") return;
    const incoming: CalibrationPayload = {
      pan: motionCal.pan,
      tilt: motionCal.tilt,
      max_slew_us_per_tick: motionCal.max_slew_us_per_tick,
    };
    if (sameCal(incoming, cal)) return;
    setCal(incoming);
    setCalInitial(incoming);
  }, [motionCal]); // eslint-disable-line react-hooks/exhaustive-deps

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

  const calDirty = !sameCal(cal, calInitial);
  const calError = calValid(cal);
  function saveCal() {
    if (calError) return;
    setCalStatus({ kind: "saving" });
    putCalibration(cal)
      .then((effective) => {
        setCal(effective);
        setCalInitial(effective);
        setCalStatus({ kind: "saved" });
        setTimeout(() => setCalStatus((s) => (s.kind === "saved" ? { kind: "idle" } : s)), 2000);
      })
      .catch((e: Error) => setCalStatus({ kind: "error", msg: e.message }));
  }
  function resetCal() { setCal(calInitial); setCalStatus({ kind: "idle" }); }
  function updateAxis(axis: "pan" | "tilt", k: keyof CalibrationPayload["pan"], v: number | boolean) {
    setCal({ ...cal, [axis]: { ...cal[axis], [k]: v } });
  }

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

      <h3 className="settings-section">Motion calibration</h3>
      <p className="muted settings-hint">
        Servo pulse-width bounds in microseconds. Most hobby servos accept
        500–2500 µs; start narrow and widen until you hit a hard stop.
      </p>
      <div className="settings-form">
        {(["pan", "tilt"] as const).map((axis) => (
          <div className="cal-axis" key={axis}>
            <h4>{axis}</h4>
            {(["min_us", "center_us", "max_us"] as const).map((field) => (
              <div className="settings-row" key={field}>
                <label htmlFor={`${axis}-${field}`}>{field.replace("_", " ")}</label>
                <input
                  id={`${axis}-${field}`}
                  type="range"
                  min={US_MIN} max={US_MAX} step={5}
                  value={cal[axis][field]}
                  onChange={(e) => updateAxis(axis, field, Number(e.target.value))}
                />
                <span>{cal[axis][field]} µs</span>
              </div>
            ))}
            <div className="settings-row">
              <label htmlFor={`${axis}-invert`}>invert</label>
              <input
                id={`${axis}-invert`}
                type="checkbox"
                checked={cal[axis].invert}
                onChange={(e) => updateAxis(axis, "invert", e.target.checked)}
              />
              <span className="muted">{cal[axis].invert ? "yes" : "no"}</span>
            </div>
          </div>
        ))}

        <div className="settings-row">
          <label htmlFor="slew">Max slew (µs/tick)</label>
          <input
            id="slew"
            type="range"
            min={1} max={200} step={1}
            value={cal.max_slew_us_per_tick}
            onChange={(e) => setCal({ ...cal, max_slew_us_per_tick: Number(e.target.value) })}
          />
          <span>{cal.max_slew_us_per_tick} µs</span>
          <span className="help">Higher = faster pan/tilt but more servo strain.</span>
        </div>

        <div className="settings-actions">
          <button
            className="btn btn-primary"
            onClick={saveCal}
            disabled={!calDirty || calStatus.kind === "saving" || calError !== null}
          >
            {calStatus.kind === "saving" ? "Saving…" : "Save calibration"}
          </button>
          <button className="btn" onClick={resetCal} disabled={!calDirty}>Reset</button>
          <span className={`settings-status ${calStatus.kind}`}>
            {calError && calDirty && <span className="error">{calError}</span>}
            {!calError && calStatus.kind === "saved" && "Saved."}
            {!calError && calStatus.kind === "error" && calStatus.msg}
          </span>
        </div>
      </div>
    </main>
  );
}
