import { useUIStore } from "../store";

// Until the real motion_ctrl service exists, the hub echoes the last `aim`
// command onto turret/motion_ctrl/state. Values are normalized [-1, 1] —
// not real degrees yet, but they prove the round-trip.

function fmt(v: number | undefined | null) {
  if (v == null) return "—";
  return (v >= 0 ? "+" : "") + v.toFixed(2);
}

export default function TurretAngle() {
  const aim = useUIStore((s) => s.aim);

  return (
    <section className="panel">
      <h3>Turret angle</h3>
      <dl className="kv">
        <dt>Pan</dt><dd>{fmt(aim?.pan)}</dd>
        <dt>Tilt</dt><dd>{fmt(aim?.tilt)}</dd>
      </dl>
    </section>
  );
}
