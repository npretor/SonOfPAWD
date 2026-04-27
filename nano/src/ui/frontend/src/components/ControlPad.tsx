import { useEffect, useRef } from "react";
import nipplejs, { JoystickManager } from "nipplejs";

import { useThrottledAim } from "../hooks/useThrottledAim";
import { ControlScheme, useUIStore } from "../store";

const SCHEMES: ControlScheme[] = ["joystick", "keyboard", "tilt"];

// --- Joystick (nipplejs) ---------------------------------------------------

function Joystick({ send }: { send: (p: number, t: number) => void }) {
  const zoneRef = useRef<HTMLDivElement>(null);
  const managerRef = useRef<JoystickManager | null>(null);

  useEffect(() => {
    if (!zoneRef.current) return;
    const m = nipplejs.create({
      zone: zoneRef.current,
      mode: "static",
      position: { right: "60px", bottom: "60px" },
      color: "#4aa3ff",
      size: 120,
      restJoystick: true,
    });
    managerRef.current = m;

    m.on("move", (_e, data) => {
      // vector.x: -1..1 (left..right), vector.y: -1..1 (down..up in nipple's
      // axis convention). Map directly to (pan, tilt). Scale by force so a
      // light touch is gentler than a hard one (nipplejs caps force at ~1).
      const x = data.vector?.x ?? 0;
      const y = data.vector?.y ?? 0;
      const f = Math.min(1, data.force ?? 1);
      send(x * f, y * f);
    });
    m.on("end", () => send(0, 0));

    return () => {
      m.destroy();
      managerRef.current = null;
    };
  }, [send]);

  return <div ref={zoneRef} className="joystick-zone" />;
}

// --- Keyboard --------------------------------------------------------------

function KeyboardControls({ send }: { send: (p: number, t: number) => void }) {
  useEffect(() => {
    const held = new Set<string>();
    const KEYS = ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"];

    function compute(): [number, number] {
      let pan = 0, tilt = 0;
      if (held.has("ArrowLeft")) pan -= 1;
      if (held.has("ArrowRight")) pan += 1;
      if (held.has("ArrowUp")) tilt += 1;
      if (held.has("ArrowDown")) tilt -= 1;
      return [pan, tilt];
    }

    const onKey = (down: boolean) => (e: KeyboardEvent) => {
      if (!KEYS.includes(e.key)) return;
      e.preventDefault();
      if (down) held.add(e.key);
      else held.delete(e.key);
      const [p, t] = compute();
      send(p, t);
    };
    const onDown = onKey(true);
    const onUp = onKey(false);

    window.addEventListener("keydown", onDown);
    window.addEventListener("keyup", onUp);
    return () => {
      window.removeEventListener("keydown", onDown);
      window.removeEventListener("keyup", onUp);
      send(0, 0);
    };
  }, [send]);

  return (
    <div className="ctrl-hint">
      <span>arrow keys: ↑ ↓ ← →</span>
    </div>
  );
}

// --- Device tilt (iOS-friendly) -------------------------------------------

function TiltControls({ send }: { send: (p: number, t: number) => void }) {
  const grantedRef = useRef(false);

  async function requestPermission() {
    const ev: any = (window as any).DeviceOrientationEvent;
    if (ev && typeof ev.requestPermission === "function") {
      try {
        const r = await ev.requestPermission();
        grantedRef.current = r === "granted";
      } catch {
        grantedRef.current = false;
      }
    } else {
      grantedRef.current = true;
    }
    if (grantedRef.current) attach();
  }

  function attach() {
    const handler = (e: DeviceOrientationEvent) => {
      // gamma: -90..90 (left/right tilt). beta: -180..180 (front/back).
      // Use ±30° as full-stick deflection.
      const pan = clamp((e.gamma ?? 0) / 30, -1, 1);
      const tilt = clamp((e.beta ?? 0) / 30, -1, 1);
      send(pan, tilt);
    };
    window.addEventListener("deviceorientation", handler);
    return () => {
      window.removeEventListener("deviceorientation", handler);
      send(0, 0);
    };
  }

  useEffect(() => {
    // Non-iOS browsers: just attach. iOS 13+: needs the permission gesture.
    const ev: any = (window as any).DeviceOrientationEvent;
    if (ev && typeof ev.requestPermission === "function") return; // wait for click
    grantedRef.current = true;
    return attach();
  }, [send]);

  if (!grantedRef.current) {
    return (
      <div className="ctrl-hint">
        <button className="btn" onClick={requestPermission}>Enable tilt</button>
      </div>
    );
  }
  return <div className="ctrl-hint"><span>tilt your device</span></div>;
}

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}

// --- Top-level pad ---------------------------------------------------------

export default function ControlPad() {
  const scheme = useUIStore((s) => s.scheme);
  const setScheme = useUIStore((s) => s.setScheme);
  const send = useThrottledAim(20);

  return (
    <div className="control-pad">
      <div className="scheme-row">
        <span className="muted">control</span>
        {SCHEMES.map((s) => (
          <label key={s} className={s === scheme ? "scheme-pill on" : "scheme-pill"}>
            <input
              type="radio"
              name="scheme"
              value={s}
              checked={s === scheme}
              onChange={() => setScheme(s)}
            />
            {s}
          </label>
        ))}
      </div>
      {scheme === "joystick" && <Joystick send={send} />}
      {scheme === "keyboard" && <KeyboardControls send={send} />}
      {scheme === "tilt" && <TiltControls send={send} />}
    </div>
  );
}
