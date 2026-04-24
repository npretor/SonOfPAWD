import { useUIStore } from "../store";
import ServiceHealth from "./ServiceHealth";
import TelemetryPanel from "./TelemetryPanel";
import TurretAngle from "./TurretAngle";

export default function Sidebar() {
  const connected = useUIStore((s) => s.connected);
  const mode = useUIStore((s) => s.mode?.mode ?? "—");

  return (
    <aside className="sidebar">
      <section className="panel panel-status">
        <div className="status-row">
          <span className={connected ? "dot dot-up" : "dot dot-down"} />
          <span>{connected ? "connected" : "disconnected"}</span>
        </div>
        <div className="status-row">
          <span className="muted">mode</span>
          <strong>{mode}</strong>
        </div>
      </section>
      <ServiceHealth />
      <TurretAngle />
      <TelemetryPanel />
    </aside>
  );
}
