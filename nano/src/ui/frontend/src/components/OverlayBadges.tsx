import { useUIStore } from "../store";

interface Props {
  resolution: string | null;
  fps: number | null;
}

export default function OverlayBadges({ resolution, fps }: Props) {
  const mode = useUIStore((s) => s.mode?.mode ?? "—");

  return (
    <div className="overlay-badges">
      <span className="badge">mode: {mode}</span>
      <span className="badge">{resolution ?? "—"}</span>
      <span className="badge">{fps != null ? `${fps.toFixed(0)} fps` : "— fps"}</span>
    </div>
  );
}
