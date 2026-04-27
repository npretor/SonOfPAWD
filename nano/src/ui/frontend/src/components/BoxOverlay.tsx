import { RefObject, useEffect, useState } from "react";

import { useUIStore } from "../store";

const STALE_MS = 700;

interface VideoRect {
  containerW: number;
  containerH: number;
  videoW: number;
  videoH: number;
}

// Watches the <video> element's container size + intrinsic frame size so we
// can letterbox-correctly project normalized bboxes. Cheap: only fires on
// resize and metadata changes, not on every frame.
function useVideoRect(videoRef: RefObject<HTMLVideoElement>): VideoRect | null {
  const [rect, setRect] = useState<VideoRect | null>(null);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const update = () => {
      const r = v.getBoundingClientRect();
      if (!v.videoWidth || !v.videoHeight || !r.width || !r.height) {
        setRect(null);
        return;
      }
      setRect({
        containerW: r.width,
        containerH: r.height,
        videoW: v.videoWidth,
        videoH: v.videoHeight,
      });
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(v);
    v.addEventListener("loadedmetadata", update);
    v.addEventListener("resize", update);
    return () => {
      ro.disconnect();
      v.removeEventListener("loadedmetadata", update);
      v.removeEventListener("resize", update);
    };
  }, [videoRef]);

  return rect;
}

export default function BoxOverlay({
  videoRef,
}: {
  videoRef: RefObject<HTMLVideoElement>;
}) {
  const detections = useUIStore((s) => s.detections);
  const rect = useVideoRect(videoRef);

  // Re-render when staleness flips so old boxes disappear without traffic.
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!detections) return;
    const t = setTimeout(() => setTick((n) => n + 1), STALE_MS + 50);
    return () => clearTimeout(t);
  }, [detections]);

  if (!rect || !detections || !detections.detections.length) return null;
  if (Date.now() - detections.ts > STALE_MS) return null;

  // object-fit: contain math.
  const scale = Math.min(rect.containerW / rect.videoW, rect.containerH / rect.videoH);
  const dispW = rect.videoW * scale;
  const dispH = rect.videoH * scale;
  const offX = (rect.containerW - dispW) / 2;
  const offY = (rect.containerH - dispH) / 2;

  return (
    <svg
      className="box-overlay"
      width={rect.containerW}
      height={rect.containerH}
      viewBox={`0 0 ${rect.containerW} ${rect.containerH}`}
    >
      {detections.detections.map((d, i) => {
        const [x0, y0, x1, y1] = d.bbox;
        const left = offX + x0 * dispW;
        const top = offY + y0 * dispH;
        const w = (x1 - x0) * dispW;
        const h = (y1 - y0) * dispH;
        const label = `${d.class} ${(d.score * 100).toFixed(0)}%`;
        return (
          <g key={i}>
            <rect
              x={left} y={top} width={w} height={h}
              fill="none" stroke="#4aa3ff" strokeWidth={2}
            />
            <rect
              x={left} y={Math.max(0, top - 18)} width={Math.min(w, 110)} height={18}
              fill="rgba(74,163,255,0.85)"
            />
            <text
              x={left + 4} y={Math.max(12, top - 5)}
              fontSize={11} fill="#06121f" fontFamily="ui-monospace, Menlo, Consolas, monospace"
            >
              {label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
