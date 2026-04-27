// Command path helpers. Backend POST endpoints publish MQTT on our behalf.

async function postJson(path: string, body: unknown): Promise<Response> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`POST ${path} ${res.status}: ${text}`);
  }
  return res;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`GET ${path} ${res.status}: ${text}`);
  }
  return res.json();
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`PUT ${path} ${res.status}: ${text}`);
  }
  return res.json();
}

export const setMode = (mode: string) => postJson("/api/mode", { mode });
export const setArmed = (on: boolean) => postJson("/api/arm", { on });
export const fire = () => postJson("/api/fire", {});
export const sendAim = (pan: number, tilt: number) =>
  postJson("/api/aim", { pan, tilt });
export const setMotionEnable = (enabled: boolean) =>
  postJson("/api/motion-ctrl/enable", { enabled });

export interface SettingsPayload {
  motion_timeout_s: number;
  trailing_s: number;
  target_class: string;
  default_control_scheme: string;
  telemetry_poll_interval_s: number;
}

export const getSettings = () => getJson<SettingsPayload>("/api/settings");
export const putSettings = (s: Partial<SettingsPayload>) =>
  putJson<SettingsPayload>("/api/settings", s);

export interface LogResponse {
  service: string;
  kind: string;
  text: string;
  lines?: number;
  error?: string;
}

export const getLogs = (service: string, lines = 200) =>
  getJson<LogResponse>(`/api/logs/${encodeURIComponent(service)}?lines=${lines}`);

export const listLogServices = () =>
  getJson<{ services: string[] }>("/api/logs");
