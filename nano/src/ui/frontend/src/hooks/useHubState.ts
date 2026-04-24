import { useEffect } from "react";

import { socket } from "../socket";
import { HubState, ModeCurrent, Telemetry, useUIStore } from "../store";

// Bind the socket.io singleton to the Zustand store. Call once at App level.
export function useSocketBinding() {
  useEffect(() => {
    const { setConnected, setHub, setTelemetry, setMode } =
      useUIStore.getState();

    const onConnect = () => setConnected(true);
    const onDisconnect = () => setConnected(false);
    const onHub = (p: HubState) => setHub(p);
    const onTelem = (p: Telemetry) => setTelemetry(p);
    const onMode = (p: ModeCurrent) => setMode(p);

    socket.on("connect", onConnect);
    socket.on("disconnect", onDisconnect);
    socket.on("hub_state", onHub);
    socket.on("telemetry", onTelem);
    socket.on("mode_current", onMode);

    if (socket.connected) setConnected(true);

    return () => {
      socket.off("connect", onConnect);
      socket.off("disconnect", onDisconnect);
      socket.off("hub_state", onHub);
      socket.off("telemetry", onTelem);
      socket.off("mode_current", onMode);
    };
  }, []);
}
