import { useEffect, useRef } from "react";
import { clipUrl } from "../api";

interface Props {
  clipName: string | null;
  onClose: () => void;
}

export default function VideoModal({ clipName, onClose }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    if (!clipName) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [clipName, onClose]);

  function handleClose() {
    const v = videoRef.current;
    if (v) {
      v.pause();
      v.src = "";
      v.load();
    }
    onClose();
  }

  if (!clipName) return null;

  return (
    <div className="video-modal-backdrop" onClick={handleClose}>
      <div className="video-modal" onClick={(e) => e.stopPropagation()}>
        <button className="video-modal-close btn" onClick={handleClose}>
          ✕ Close
        </button>
        <video
          ref={videoRef}
          className="video-modal-player"
          src={clipUrl(clipName)}
          controls
          autoPlay
          preload="metadata"
        />
        <div className="video-modal-name muted">{clipName}</div>
      </div>
    </div>
  );
}
