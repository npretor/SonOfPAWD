import { useEffect, useState } from "react";
import { ClipMeta, ClipsResponse, deleteClips, listClips } from "../api";
import VideoModal from "../components/VideoModal";
import { socket } from "../socket";

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "numeric",
      minute: "2-digit", second: "2-digit",
    });
  } catch {
    return iso;
  }
}

function fmtDuration(s: number | null): string {
  if (s == null) return "—";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
}

function fmtSize(bytes: number): string {
  if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + " GB";
  if (bytes >= 1e6) return (bytes / 1e6).toFixed(1) + " MB";
  if (bytes >= 1e3) return (bytes / 1e3).toFixed(0) + " KB";
  return bytes + " B";
}

export default function Clips() {
  const [resp, setResp] = useState<ClipsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [playing, setPlaying] = useState<string | null>(null);
  const [deleteErr, setDeleteErr] = useState<string | null>(null);

  function fetch() {
    setLoading(true);
    listClips()
      .then((r) => { setResp(r); setErr(null); })
      .catch((e) => setErr((e as Error).message))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    fetch();
    socket.on("clip_changed", fetch);
    return () => { socket.off("clip_changed", fetch); };
  }, []);

  // Fallback poll while any clip is still processing, in case clip_changed
  // was emitted before this component mounted and the event was missed.
  useEffect(() => {
    if (!resp?.clips.some((c) => !c.mp4_ready)) return;
    const t = setTimeout(fetch, 3000);
    return () => clearTimeout(t);
  }, [resp]);

  function toggleSelect(name: string, e: React.MouseEvent) {
    e.stopPropagation();
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
  }

  function selectAll() {
    if (!resp) return;
    if (selected.size === resp.clips.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(resp.clips.map((c) => c.name)));
    }
  }

  async function handleDelete() {
    if (selected.size === 0) return;
    if (playing && selected.has(playing)) {
      setDeleteErr("Close the player before deleting the clip that's playing.");
      return;
    }
    if (!window.confirm(`Delete ${selected.size} clip(s)? This cannot be undone.`)) return;
    setDeleteErr(null);
    try {
      const result = await deleteClips([...selected]);
      setSelected(new Set());
      if (result.failed.length > 0) {
        setDeleteErr(
          "Some clips could not be deleted: " +
          result.failed.map((f) => `${f.name} (${f.reason})`).join(", ")
        );
      }
      // Optimistically remove deleted clips from local state
      setResp((prev) => prev
        ? {
            ...prev,
            clips: prev.clips.filter((c) => !result.deleted.includes(c.name)),
            total_bytes: prev.total_bytes - prev.clips
              .filter((c) => result.deleted.includes(c.name))
              .reduce((sum, c) => sum + c.size_bytes, 0),
          }
        : prev
      );
    } catch (e) {
      setDeleteErr((e as Error).message);
    }
  }

  const clips = resp?.clips ?? [];
  const allSelected = clips.length > 0 && selected.size === clips.length;

  return (
    <div className="clips-page">
      <div className="clips-toolbar">
        <span className="muted">
          {clips.length} clip{clips.length !== 1 ? "s" : ""}
          {resp ? ` · ${fmtSize(resp.total_bytes)} total` : ""}
        </span>
        <div className="clips-toolbar-actions">
          <button className="btn" onClick={selectAll} disabled={clips.length === 0}>
            {allSelected ? "Clear" : "Select all"}
          </button>
          <button
            className="btn"
            onClick={handleDelete}
            disabled={selected.size === 0}
          >
            Delete selected ({selected.size})
          </button>
          <button className="btn" onClick={fetch} disabled={loading}>
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>
      </div>

      {deleteErr && <div className="error clips-delete-err">{deleteErr}</div>}
      {err && <div className="error clips-err">{err}</div>}

      {!loading && clips.length === 0 && !err && (
        <div className="clips-empty muted">No clips recorded yet.</div>
      )}

      <div className="clip-grid">
        {clips.map((clip) => (
          <ClipTile
            key={clip.name}
            clip={clip}
            isSelected={selected.has(clip.name)}
            onToggleSelect={(e) => toggleSelect(clip.name, e)}
            onPlay={() => setPlaying(clip.name)}
          />
        ))}
      </div>

      <VideoModal clipName={playing} onClose={() => setPlaying(null)} />
    </div>
  );
}

interface TileProps {
  clip: ClipMeta;
  isSelected: boolean;
  onToggleSelect: (e: React.MouseEvent) => void;
  onPlay: () => void;
}

function ClipTile({ clip, isSelected, onToggleSelect, onPlay }: TileProps) {
  return (
    <div
      className={`clip-tile${isSelected ? " selected" : ""}`}
      onClick={clip.mp4_ready ? onPlay : undefined}
      style={{ cursor: clip.mp4_ready ? "pointer" : "default" }}
    >
      <input
        type="checkbox"
        className="clip-tile-check"
        checked={isSelected}
        onChange={() => {}}
        onClick={onToggleSelect}
      />
      <div className="clip-tile-icon">🎬</div>
      <div className="clip-tile-meta">
        <div className="clip-tile-time">{fmtDate(clip.start_ts)}</div>
        <div className="clip-tile-stats muted">
          {fmtDuration(clip.duration_s)} · {fmtSize(clip.size_bytes)}
        </div>
      </div>
      <button
        className="btn clip-tile-play"
        onClick={(e) => { e.stopPropagation(); if (clip.mp4_ready) onPlay(); }}
        disabled={!clip.mp4_ready}
        title={clip.mp4_ready ? "Play" : "Processing…"}
      >
        {clip.mp4_ready ? "▶ Play" : "Processing…"}
      </button>
    </div>
  );
}
