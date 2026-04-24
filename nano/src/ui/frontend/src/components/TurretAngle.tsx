// Placeholder until motion_ctrl publishes turret/motion_ctrl/state. Plan
// §7 calls for the hub to echo the last `aim` command onto that topic so
// the sidebar has something to render once the control pad arrives in H.5.

export default function TurretAngle() {
  return (
    <section className="panel">
      <h3>Turret angle</h3>
      <dl className="kv">
        <dt>Pan</dt><dd className="muted">—</dd>
        <dt>Tilt</dt><dd className="muted">—</dd>
      </dl>
    </section>
  );
}
