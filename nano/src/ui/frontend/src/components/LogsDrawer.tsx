import { useEffect, useRef, useState } from "react";

import { getLogs, listLogServices, LogResponse } from "../api";

const REFRESH_MS = 3000;
const STORAGE_KEY = "sonofpawd.logs.service";

export default function LogsDrawer() {
  const [open, setOpen] = useState(false);
  const [services, setServices] = useState<string[]>([]);
  const [service, setService] = useState<string>(
    () => localStorage.getItem(STORAGE_KEY) || "ui-backend"
  );
  const [resp, setResp] = useState<LogResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const outRef = useRef<HTMLPreElement | null>(null);
  const stickToBottomRef = useRef(true);

  useEffect(() => {
    listLogServices()
      .then((r) => setServices(r.services))
      .catch(() => { /* fall back to default-only */ });
  }, []);

  useEffect(() => { localStorage.setItem(STORAGE_KEY, service); }, [service]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    let timer: number | undefined;

    async function tick() {
      setLoading(true);
      try {
        const r = await getLogs(service, 300);
        if (cancelled) return;
        setResp(r);
        setErr(null);
      } catch (e) {
        if (!cancelled) setErr((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
      if (!cancelled) timer = window.setTimeout(tick, REFRESH_MS);
    }
    tick();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [open, service]);

  // Keep the user pinned to the tail unless they've scrolled up.
  useEffect(() => {
    const el = outRef.current;
    if (!el || !resp) return;
    if (stickToBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [resp]);

  function onScroll() {
    const el = outRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 16;
    stickToBottomRef.current = atBottom;
  }

  return (
    <div className="logs-drawer" style={{ maxHeight: open ? "50vh" : undefined }}>
      <div
        className="logs-drawer-toggle"
        onClick={() => setOpen((v) => !v)}
        role="button"
      >
        <span>{open ? "▼" : "▲"}</span>
        <strong>Logs</strong>
        <span className="muted">
          {open ? `(${service}${loading ? " · refreshing…" : ""})` : "(click to open)"}
        </span>
      </div>
      {open && (
        <div className="logs-drawer-body">
          <div className="logs-controls">
            <label htmlFor="log-service">Service</label>
            <select
              id="log-service"
              value={service}
              onChange={(e) => setService(e.target.value)}
            >
              {(services.length ? services : [service]).map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            {resp?.kind && <span className="muted">[{resp.kind}]</span>}
            {err && <span className="error">{err}</span>}
            {resp?.error && <span className="error">{resp.error}</span>}
          </div>
          <pre className="logs-output" ref={outRef} onScroll={onScroll}>
            {resp?.text || (loading ? "loading…" : "")}
          </pre>
        </div>
      )}
    </div>
  );
}
