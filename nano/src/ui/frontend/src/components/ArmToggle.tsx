import { useState } from "react";

import { setArmed } from "../api";
import { useUIStore } from "../store";

export default function ArmToggle() {
  const armed = useUIStore((s) => s.fireArmed?.on ?? false);
  const [pending, setPending] = useState(false);

  async function toggle() {
    setPending(true);
    try {
      await setArmed(!armed);
    } catch (e) {
      console.warn("[arm]", e);
    } finally {
      setPending(false);
    }
  }

  return (
    <button
      className={armed ? "btn arm-toggle armed" : "btn arm-toggle disarmed"}
      onClick={toggle}
      disabled={pending}
    >
      {armed ? "● ARMED" : "○ DISARMED"}
    </button>
  );
}
