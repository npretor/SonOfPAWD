import { useUIStore } from "../store";

export default function ServiceHealth() {
  const hub = useUIStore((s) => s.hub);
  const services = hub?.services ?? {};
  const keys = Object.keys(services).sort();

  return (
    <section className="panel">
      <h3>Services</h3>
      {keys.length === 0 ? (
        <p className="muted">waiting for hub_state…</p>
      ) : (
        <ul className="service-list">
          {keys.map((k) => {
            const status = services[k];
            const cls =
              status === "up" ? "dot dot-up" :
              status === "down" ? "dot dot-down" :
              "dot dot-unknown";
            return (
              <li key={k}>
                <span className={cls} />
                <span className="svc-name">{k}</span>
                <span className="svc-status">{status}</span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
