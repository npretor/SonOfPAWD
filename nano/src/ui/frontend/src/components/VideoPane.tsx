import { useEffect, useRef, useState } from "react";

import BoxOverlay from "./BoxOverlay";
import OverlayBadges from "./OverlayBadges";
import { WhepSession, WhepState, whepStart } from "../whep";

// Quality mode chosen by the user. "auto" = adaptive switching enabled.
type QualityMode = "auto" | "high" | "low";
// Actual stream quality in use (derived from mode + adaptive logic).
type Quality = "high" | "low";

// Auto-mode thresholds
const LOSS_DEGRADE_THRESH = 0.05;   // >5% packet loss → try to degrade
const LOSS_RECOVER_THRESH = 0.01;   // <1% packet loss → eligible to recover
const FPS_DEGRADE_THRESH  = 15;     // <15 fps → try to degrade
const FPS_RECOVER_THRESH  = 25;     // >25 fps sustained → eligible to recover
const DEGRADE_HOLD_MS     = 5_000;  // bad conditions must persist this long before degrading
const RECOVER_HOLD_MS     = 10_000; // good conditions must persist this long before recovering

function whepUrl(q: Quality): string {
  const host = window.location.hostname || "localhost";
  return `http://${host}:8889/${q === "high" ? "live" : "live_low"}/whep`;
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

  // Quality mode chosen by the user (default: auto = start HD, adapt down)
  const [qualityMode, setQualityMode] = useState<QualityMode>("auto");
  // Actual quality of the current stream
  const [quality, setQuality] = useState<Quality>("high");

  // Refs used inside async callbacks / effects to avoid stale closures
  const qualityRef      = useRef<Quality>("high");
  const qualityModeRef  = useRef<QualityMode>("auto");
  const switchingRef    = useRef(false);
  const badSinceRef     = useRef<number | null>(null);
  const goodSinceRef    = useRef<number | null>(null);
  const prevStatsRef    = useRef<{ packetsLost: number; packetsReceived: number } | null>(null);

  useEffect(() => {
    return () => {
      sessionRef.current?.close();
      sessionRef.current = null;
    };
  }, []);

  async function startSession(q: Quality) {
    if (!videoRef.current) return;
    try {
      const session = await whepStart(whepUrl(q), videoRef.current, setStats);
      sessionRef.current = session;
      qualityRef.current = q;
      setQuality(q);
    } catch (e: any) {
      setErr(e?.message ?? String(e));
      setPlaying(false);
    }
  }

  async function start() {
    if (!videoRef.current || starting || playing) return;
    setErr(null);
    setStats(null);
    setStarting(true);
    resetAdaptiveState();
    const initialQuality: Quality = "high";
    qualityRef.current = initialQuality;
    setQuality(initialQuality);
    try {
      const session = await whepStart(whepUrl(initialQuality), videoRef.current, setStats);
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
    setQuality("high");
    qualityRef.current = "high";
    resetAdaptiveState();
    if (s) await s.close();
  }

  function resetAdaptiveState() {
    switchingRef.current = false;
    badSinceRef.current = null;
    goodSinceRef.current = null;
    prevStatsRef.current = null;
  }

  async function switchQuality(newQ: Quality) {
    if (switchingRef.current || qualityRef.current === newQ) return;
    switchingRef.current = true;
    resetAdaptiveState();
    const old = sessionRef.current;
    sessionRef.current = null;
    if (old) old.close().catch(() => {});
    await startSession(newQ);
    switchingRef.current = false;
  }

  // Called when the user clicks a quality mode button
  async function handleModeSelect(mode: QualityMode) {
    qualityModeRef.current = mode;
    setQualityMode(mode);
    if (!playing) return;
    if (mode === "high") await switchQuality("high");
    else if (mode === "low") await switchQuality("low");
    // "auto" just re-enables adaptive logic; stream stays at current quality
  }

  // Resolution + FPS tracking
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

  // Adaptive quality logic — only active in "auto" mode
  useEffect(() => {
    if (!stats || !playing || qualityModeRef.current !== "auto") return;

    let lossRate = 0;
    const prev = prevStatsRef.current;
    if (prev) {
      const dLost = stats.packetsLost - prev.packetsLost;
      const dRecv = stats.packetsReceived - prev.packetsReceived;
      const dTotal = dLost + dRecv;
      if (dTotal > 0) lossRate = Math.max(0, dLost) / dTotal;
    }
    prevStatsRef.current = { packetsLost: stats.packetsLost, packetsReceived: stats.packetsReceived };

    const fpsOk  = fps === null || fps >= FPS_RECOVER_THRESH;
    const lossOk = lossRate <= LOSS_RECOVER_THRESH;
    const fpsBad = fps !== null && fps < FPS_DEGRADE_THRESH;
    const lossBad = lossRate > LOSS_DEGRADE_THRESH;
    const condGood = fpsOk && lossOk;
    const condBad  = fpsBad || lossBad;

    const now = Date.now();

    if (qualityRef.current === "high" && condBad) {
      badSinceRef.current ??= now;
      goodSinceRef.current = null;
      if (now - badSinceRef.current >= DEGRADE_HOLD_MS) switchQuality("low");
    } else if (qualityRef.current === "low" && condGood) {
      goodSinceRef.current ??= now;
      badSinceRef.current = null;
      if (now - goodSinceRef.current >= RECOVER_HOLD_MS) switchQuality("high");
    } else {
      badSinceRef.current = null;
      goodSinceRef.current = null;
    }
  }, [stats, fps]);  // eslint-disable-line react-hooks/exhaustive-deps

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
        {playing && <OverlayBadges resolution={resolution} fps={fps} quality={quality} />}
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
          <>
            <button className="btn" onClick={stop}>■ Stop</button>
            <div className="quality-selector">
              {(["auto", "high", "low"] as QualityMode[]).map((m) => (
                <button
                  key={m}
                  className={`btn btn-sm${qualityMode === m ? " active" : ""}`}
                  onClick={() => handleModeSelect(m)}
                >
                  {m === "auto" ? "Auto" : m === "high" ? "HD" : "Low"}
                </button>
              ))}
            </div>
          </>
        ) : (
          <span className="muted">WebRTC via MediaMTX · :8889/live/whep</span>
        )}
      </div>
    </div>
  );
}
