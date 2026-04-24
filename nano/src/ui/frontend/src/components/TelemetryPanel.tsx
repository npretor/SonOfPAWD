import { useUIStore } from "../store";

function fmtPct(v: number | null | undefined) {
  return v == null ? "—" : `${v.toFixed(0)}%`;
}

function fmtTemp(v: number | null | undefined) {
  return v == null ? "—" : `${v.toFixed(1)} °C`;
}

function fmtGB(bytes: number | null | undefined) {
  if (bytes == null) return "—";
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

export default function TelemetryPanel() {
  const t = useUIStore((s) => s.telemetry);

  return (
    <section className="panel">
      <h3>Telemetry</h3>
      <dl className="kv">
        <dt>CPU</dt><dd>{fmtPct(t?.cpu_pct)}</dd>
        <dt>RAM</dt><dd>{fmtPct(t?.ram_pct)}</dd>
        <dt>Temp</dt><dd>{fmtTemp(t?.temp_c)}</dd>
        <dt>Disk free</dt><dd>{fmtGB(t?.disk_free_bytes)}</dd>
        <dt>Power</dt><dd className="muted">—</dd>
      </dl>
    </section>
  );
}
