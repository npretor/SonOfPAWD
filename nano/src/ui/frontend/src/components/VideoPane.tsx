import { useEffect, useRef, useState } from "react";

import BoxOverlay from "./BoxOverlay";
import OverlayBadges from "./OverlayBadges";
import { WhepSession, WhepState, whepStart } from "../whep";

// MediaMTX listens on :8889 for WebRTC/WHEP by default. The UI is served from
// Flask on :5000 same host, so we reuse the page's hostname.
function whepUrl(): string {
  const host = window.location.hostname || "localhost";
  return `http://${host}:8889/live/whep`;
}

export default function VideoPane() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const sessionRef = useRef<WhepSession | null>(null);

  const [playing, setPlaying] = useState(false);
  const [starting, setStarting] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [resolution, setResolution] = useState<string | null>(null);
  const [fps, setFps] = useState<number | null>(null);
  const [stats, setStats] = useState<WhepState | null>(null);

  useEffect(() => {
    return () => {
      sessionRef.current?.close();
      sessionRef.current = null;
    };
  }, []);

  async function start() {
    if (!videoRef.current || starting || playing) return;
    setErr(null);
    setStats(null);
    setStarting(true);
    try {
      const session = await whepStart(whepUrl(), videoRef.current, setStats);
      sessionRef.current = session;
      setPlaying(true);
    } catch (e: any) {
      setErr(e?.message ?? String(e));
    } finally {
      setStarting(false);
    }
  }

  async function stop() {
    const s = sessionRef.current;
    sessionRef.current = null;
    setPlaying(false);
    setResolution(null);
    setFps(null);
    setStats(null);
    if (s) await s.close();
  }

  useEffect(() => {
    const v = videoRef.current;
    if (!v || !playing) return;

    const onMeta = () => {
      if (v.videoWidth && v.videoHeight) {
        setResolution(`${v.videoWidth}×${v.videoHeight}`);
      }
    };
    v.addEventListener("loadedmetadata", onMeta);
    v.addEventListener("resize", onMeta);

    let cancelled = false;
    let lastTs = 0;
    let frames = 0;
    let acc = 0;

    const rvfc = (v as any).requestVideoFrameCallback?.bind(v);
    function onFrame(_now: number, meta: any) {
      if (cancelled) return;
      const ts = meta.mediaTime ?? meta.presentationTime ?? performance.now() / 1000;
      if (lastTs > 0) {
        const dt = ts - lastTs;
        if (dt > 0) {
          acc += dt;
          frames += 1;
          if (frames >= 15) {
            setFps(frames / acc);
            frames = 0;
            acc = 0;
          }
        }
      }
      lastTs = ts;
      if (rvfc) rvfc(onFrame);
    }
    if (rvfc) rvfc(onFrame);

    return () => {
      cancelled = true;
      v.removeEventListener("loadedmetadata", onMeta);
      v.removeEventListener("resize", onMeta);
    };
  }, [playing]);

  return (
    <div className="video-pane">
      <div className="video-shell">
        <video
          ref={videoRef}
          autoPlay
          playsInline
          muted
          controls={false}
        />
        {playing && <BoxOverlay videoRef={videoRef} />}
        {playing && <OverlayBadges resolution={resolution} fps={fps} />}
        {playing && stats && (
          <div className="whep-stats">
            <span>ice: {stats.ice}</span>
            <span>conn: {stats.conn}</span>
            <span>bytes: {stats.bytesReceived}</span>
            <span>frames: {stats.framesDecoded}</span>
          </div>
        )}
        {!playing && (
          <div className="video-idle">
            <button className="btn btn-primary" onClick={start} disabled={starting}>
              {starting ? "Connecting…" : "▶ Play"}
            </button>
            {err && <div className="error">{err}</div>}
          </div>
        )}
      </div>
      <div className="video-controls">
        {playing ? (
          <button className="btn" onClick={stop}>■ Stop</button>
        ) : (
          <span className="muted">WebRTC via MediaMTX · :8889/live/whep</span>
        )}
      </div>
    </div>
  );
}
