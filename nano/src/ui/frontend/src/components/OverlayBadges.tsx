import { useUIStore } from "../store";

interface Props {
  resolution: string | null;
  fps: number | null;
  quality?: "high" | "low";
}

export default function OverlayBadges({ resolution, fps, quality }: Props) {
  const mode = useUIStore((s) => s.mode?.mode ?? "—");

  return (
    <div className="overlay-badges">
      <span className="badge">mode: {mode}</span>
      <span className="badge">{resolution ?? "—"}</span>
      <span className="badge">{fps != null ? `${fps.toFixed(0)} fps` : "— fps"}</span>
      {quality && (
        <span className={`badge${quality === "low" ? " badge-warn" : ""}`}>
          {quality === "high" ? "HD" : "Low"}
        </span>
      )}
    </div>
  );
}
