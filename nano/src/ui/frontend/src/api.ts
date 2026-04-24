// Command path helpers. Backend POST endpoints publish MQTT on our behalf.

export async function setMode(mode: string): Promise<void> {
  const res = await fetch("/api/mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`POST /api/mode ${res.status}: ${body}`);
  }
}
