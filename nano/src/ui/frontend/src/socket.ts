import { io, Socket } from "socket.io-client";

// Singleton. In production the page is served from Flask on :5000 so a
// same-origin connect is all we need. In dev, Vite proxies /socket.io to
// Flask, so this still works at :5173.
export const socket: Socket = io({
  path: "/socket.io",
  transports: ["websocket", "polling"],
  autoConnect: true,
  reconnection: true,
});
