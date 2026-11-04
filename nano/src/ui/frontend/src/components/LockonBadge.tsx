import { useUIStore } from "../store";

export default function LockonBadge() {
  const lockon = useUIStore((s) => s.hub?.lockon);
  if (!lockon || lockon.state === "IDLE") return null;

  if (lockon.state === "ACQUIRING") {
    return (
      <span className="lockon-badge acquiring">
        ACQUIRING {lockon.count}/{lockon.of}
      </span>
    );
  }
  if (lockon.state === "LOCKED") {
    return <span className="lockon-badge locked">● LOCKED</span>;
  }
  // COOLDOWN
  const remaining = lockon.cooldown_remaining ?? 0;
  return (
    <span className="lockon-badge cooldown">
      cooldown {remaining.toFixed(1)}s
    </span>
  );
}
