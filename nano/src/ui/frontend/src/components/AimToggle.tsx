import { useState } from "react";

import { setMotionEnable } from "../api";
import { useUIStore } from "../store";

// Manual servo PSU gate. Decoupled from the fire arm: the user wants to
// be able to look at the video without the servos buzzing. Per
// motor_control_plan.md §9 answer 3.
export default function AimToggle() {
  const enabled = useUIStore((s) => s.motionEnable?.enabled ?? false);
  const [pending, setPending] = useState(false);

  async function toggle() {
    setPending(true);
    try {
      await setMotionEnable(!enabled);
    } catch (e) {
      console.warn("[aim-enable]", e);
    } finally {
      setPending(false);
    }
  }

  return (
    <button
      className={enabled ? "btn aim-toggle on" : "btn aim-toggle off"}
      onClick={toggle}
      disabled={pending}
      title="Servo power"
    >
      {enabled ? "◉ AIM ON" : "○ AIM OFF"}
    </button>
  );
}
