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

export interface FireFlag {
  on: boolean;
  ts: number;
}

export interface AimState {
  pan: number;
  tilt: number;
  enabled?: boolean;
  pan_us?: number;
  tilt_us?: number;
  ts: number;
}

export interface MotionEnable {
  enabled: boolean;
  ts: number;
}

export interface Detection {
  class: string;
  score: number;
  bbox: [number, number, number, number]; // [x0, y0, x1, y1] normalized [0,1]
}

export interface DetectionsPayload {
  detections: Detection[];
  ts: number;
}

export interface AxisCalibration {
  min_us: number;
  max_us: number;
  center_us: number;
  invert: boolean;
}

export interface MotionCalibration {
  pan: AxisCalibration;
  tilt: AxisCalibration;
  max_slew_us_per_tick: number;
  ts?: number;
}

export interface HubConfig {
  motion_timeout_s: number;
  trailing_s: number;
  target_class: string;
  ts: number;
}

export type ControlScheme = "joystick" | "keyboard" | "tilt";

interface UIStore {
  connected: boolean;
  hub: HubState | null;
  telemetry: Telemetry | null;
  mode: ModeCurrent | null;
  fireArmed: FireFlag | null;
  fireEnable: FireFlag | null;
  aim: AimState | null;
  motionEnable: MotionEnable | null;
  motionCalibration: MotionCalibration | null;
  detections: DetectionsPayload | null;
  hubConfig: HubConfig | null;
  scheme: ControlScheme;
  setConnected: (v: boolean) => void;
  setHub: (v: HubState) => void;
  setTelemetry: (v: Telemetry) => void;
  setMode: (v: ModeCurrent) => void;
  setFireArmed: (v: FireFlag) => void;
  setFireEnable: (v: FireFlag) => void;
  setAim: (v: AimState) => void;
  setMotionEnable: (v: MotionEnable) => void;
  setMotionCalibration: (v: MotionCalibration) => void;
  setDetections: (v: DetectionsPayload) => void;
  setHubConfig: (v: HubConfig) => void;
  setScheme: (s: ControlScheme) => void;
}

const SCHEME_KEY = "sonofpawd.scheme";
function loadScheme(): ControlScheme {
  const v = localStorage.getItem(SCHEME_KEY);
  if (v === "keyboard" || v === "tilt" || v === "joystick") return v;
  return "joystick";
}

export const useUIStore = create<UIStore>((set) => ({
  connected: false,
  hub: null,
  telemetry: null,
  mode: null,
  fireArmed: null,
  fireEnable: null,
  aim: null,
  motionEnable: null,
  motionCalibration: null,
  detections: null,
  hubConfig: null,
  scheme: loadScheme(),
  setConnected: (v) => set({ connected: v }),
  setHub: (v) => set({ hub: v }),
  setTelemetry: (v) => set({ telemetry: v }),
  setMode: (v) => set({ mode: v }),
  setFireArmed: (v) => set({ fireArmed: v }),
  setFireEnable: (v) => set({ fireEnable: v }),
  setAim: (v) => set({ aim: v }),
  setMotionEnable: (v) => set({ motionEnable: v }),
  setMotionCalibration: (v) => set({ motionCalibration: v }),
  setDetections: (v) => set({ detections: v }),
  setHubConfig: (v) => set({ hubConfig: v }),
  setScheme: (s) => {
    localStorage.setItem(SCHEME_KEY, s);
    set({ scheme: s });
  },
}));
