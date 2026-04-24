import { create } from "zustand";

// Shape of the payloads we care about, mirrored from the MQTT contract.
// `ts` is always present (MqttClient injects it).

export type ServiceStatus = "up" | "down" | "degraded" | string;

export interface HubState {
  mode: string;
  services: Record<string, ServiceStatus>;
  armed: boolean;
  ir_on: boolean;
  ts: number;
}

export interface Telemetry {
  temp_c: number | null;
  cpu_pct: number | null;
  ram_pct: number | null;
  disk_free_bytes: number | null;
  disk_total_bytes: number | null;
  ts: number;
}

export interface ModeCurrent {
  mode: string;
  ts: number;
}

interface UIStore {
  connected: boolean;
  hub: HubState | null;
  telemetry: Telemetry | null;
  mode: ModeCurrent | null;
  setConnected: (v: boolean) => void;
  setHub: (v: HubState) => void;
  setTelemetry: (v: Telemetry) => void;
  setMode: (v: ModeCurrent) => void;
}

export const useUIStore = create<UIStore>((set) => ({
  connected: false,
  hub: null,
  telemetry: null,
  mode: null,
  setConnected: (v) => set({ connected: v }),
  setHub: (v) => set({ hub: v }),
  setTelemetry: (v) => set({ telemetry: v }),
  setMode: (v) => set({ mode: v }),
}));
