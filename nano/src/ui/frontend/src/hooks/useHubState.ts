import { useEffect } from "react";

import { socket } from "../socket";
import {
  AimState,
  FireFlag,
  HubConfig,
  HubState,
  ModeCurrent,
  MotionEnable,
  Telemetry,
  useUIStore,
} from "../store";

// Bind the socket.io singleton to the Zustand store. Call once at App level.
export function useSocketBinding() {
  useEffect(() => {
    const s = useUIStore.getState();

    const onConnect = () => s.setConnected(true);
    const onDisconnect = () => s.setConnected(false);
    const onHub = (p: HubState) => s.setHub(p);
    const onTelem = (p: Telemetry) => s.setTelemetry(p);
    const onMode = (p: ModeCurrent) => s.setMode(p);
    const onArmed = (p: FireFlag) => s.setFireArmed(p);
    const onEnable = (p: FireFlag) => s.setFireEnable(p);
    const onAim = (p: AimState) => s.setAim(p);
    const onMotionEnable = (p: MotionEnable) => s.setMotionEnable(p);
    const onConfig = (p: HubConfig) => s.setHubConfig(p);

    socket.on("connect", onConnect);
    socket.on("disconnect", onDisconnect);
    socket.on("hub_state", onHub);
    socket.on("telemetry", onTelem);
    socket.on("mode_current", onMode);
    socket.on("fire_armed", onArmed);
    socket.on("fire_enable", onEnable);
    socket.on("aim_state", onAim);
    socket.on("motion_enable", onMotionEnable);
    socket.on("hub_config", onConfig);

    if (socket.connected) s.setConnected(true);

    return () => {
      socket.off("connect", onConnect);
      socket.off("disconnect", onDisconnect);
      socket.off("hub_state", onHub);
      socket.off("telemetry", onTelem);
      socket.off("mode_current", onMode);
      socket.off("fire_armed", onArmed);
      socket.off("fire_enable", onEnable);
      socket.off("aim_state", onAim);
      socket.off("motion_enable", onMotionEnable);
      socket.off("hub_config", onConfig);
    };
  }, []);
}
