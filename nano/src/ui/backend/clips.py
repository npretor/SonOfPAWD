"""Clip management helpers for the UI backend.

Handles listing, serving, remuxing (MKV -> MP4), and deleting recorded clips.
"""
import json
import logging
import re
import subprocess
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

log = logging.getLogger("ui.clips")

CLIPS_DIR = Path("/var/lib/turret/clips")
_FILENAME_RE = re.compile(r"^clip_\d{8}_\d{6}\.(mkv|mp4)$")
# splitmuxsink always opens a new fragment after split-now, even when the valve
# is closed. Those orphan files contain only a container header (~200 bytes).
# Any real clip at 6 Mbps would be >>4 KB even for under one second of video.
_MIN_CLIP_BYTES = 4096

# Tracks in-flight remux jobs: set of mkv Path objects
_remux_lock = threading.Lock()
_remux_inflight: Set[Path] = set()

# Notify listeners (typically routes.py) when a remux completes.
# Populated by routes.py after bridge is available.
_on_remux_done = None


def set_remux_done_callback(cb):
    global _on_remux_done
    _on_remux_done = cb


def _valid_name(name: str) -> bool:
    return bool(_FILENAME_RE.match(name))


def _sidecar_path(mkv: Path) -> Path:
    return mkv.with_suffix(".json")


def _mp4_path(mkv: Path) -> Path:
    return mkv.with_suffix(".mp4")


def _estimate_duration(mkv: Path) -> Optional[float]:
    """Try ffprobe on the mp4, fall back to mtime-based estimate."""
    mp4 = _mp4_path(mkv)
    if mp4.exists():
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(mp4)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
            )
            val = result.stdout.decode(errors="replace").strip()
            if val:
                return float(val)
        except Exception:
            pass
    # Fall back: mtime - filename start time
    try:
        stem = mkv.stem  # clip_YYYYMMDD_HHMMSS
        parts = stem.split("_")
        date_s = parts[1] + parts[2]
        from datetime import datetime
        start = datetime.strptime(date_s, "%Y%m%d%H%M%S")
        mtime = datetime.fromtimestamp(mkv.stat().st_mtime)
        return max(0.0, (mtime - start).total_seconds())
    except Exception:
        return None


def _write_sidecar(mkv: Path, mp4_ready: bool) -> dict:
    duration = _estimate_duration(mkv) if mp4_ready else None
    try:
        size = mkv.stat().st_size
    except OSError:
        size = 0
    stem = mkv.stem
    parts = stem.split("_")
    start_ts = f"{parts[1][:4]}-{parts[1][4:6]}-{parts[1][6:8]}T{parts[2][:2]}:{parts[2][2:4]}:{parts[2][4:6]}"
    data = {
        "start_ts": start_ts,
        "duration_s": duration,
        "size_bytes": size,
        "mp4_ready": mp4_ready,
    }
    try:
        _sidecar_path(mkv).write_text(json.dumps(data))
    except OSError as e:
        log.warning("could not write sidecar for %s: %s", mkv, e)
    return data


def list_clips() -> List[Dict]:
    """Return all clips sorted newest-first, generating sidecars as needed."""
    if not CLIPS_DIR.exists():
        return []
    clips = []
    for mkv in sorted(CLIPS_DIR.glob("clip_*.mkv"), reverse=True):
        try:
            if mkv.stat().st_size < _MIN_CLIP_BYTES:
                continue
        except OSError:
            continue
        sidecar = _sidecar_path(mkv)
        mp4 = _mp4_path(mkv)
        if sidecar.exists():
            try:
                data = json.loads(sidecar.read_text())
                # Refresh mp4_ready if it changed since sidecar was written
                if data.get("mp4_ready") != mp4.exists():
                    data["mp4_ready"] = mp4.exists()
                    sidecar.write_text(json.dumps(data))
            except Exception:
                data = _write_sidecar(mkv, mp4.exists())
        else:
            data = _write_sidecar(mkv, mp4.exists())

        clips.append({
            "name": mkv.name,
            "start_ts": data.get("start_ts", ""),
            "duration_s": data.get("duration_s"),
            "size_bytes": data.get("size_bytes", 0),
            "mp4_ready": data.get("mp4_ready", False),
        })
    return clips


def clip_path(name: str) -> Optional[Path]:
    """Resolve clip name to its .mp4 path, guarding against path traversal."""
    if not _valid_name(name):
        return None
    # Normalise to mkv name for the base check, then find the mp4
    stem = Path(name).stem
    target = (CLIPS_DIR / (stem + ".mp4")).resolve()
    try:
        target.relative_to(CLIPS_DIR.resolve())
    except ValueError:
        return None
    return target if target.exists() else None


def is_remuxing(name: str) -> bool:
    mkv_name = Path(name).with_suffix(".mkv").name
    mkv = CLIPS_DIR / mkv_name
    with _remux_lock:
        return mkv in _remux_inflight


def remux_async(mkv_path: Path) -> bool:
    """Kick off a background ffmpeg remux. Returns False if already in flight."""
    with _remux_lock:
        if mkv_path in _remux_inflight:
            return False
        _remux_inflight.add(mkv_path)

    def _do_remux():
        mp4 = _mp4_path(mkv_path)
        try:
            log.info("remuxing %s -> %s", mkv_path.name, mp4.name)
            result = subprocess.run(
                ["ffmpeg", "-nostdin", "-y", "-i", str(mkv_path),
                 "-c", "copy", "-movflags", "+faststart", str(mp4)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300,
            )
            if result.returncode == 0:
                _write_sidecar(mkv_path, True)
                log.info("remux complete: %s", mp4.name)
                if _on_remux_done:
                    _on_remux_done(mkv_path.name)
            else:
                log.error("ffmpeg failed for %s: %s", mkv_path.name,
                          result.stderr.decode(errors="replace")[-500:])
        except subprocess.TimeoutExpired:
            log.error("ffmpeg timed out for %s", mkv_path.name)
        except FileNotFoundError:
            log.error("ffmpeg not found — install ffmpeg on this host")
        except Exception as e:
            log.exception("remux error for %s: %s", mkv_path.name, e)
        finally:
            with _remux_lock:
                _remux_inflight.discard(mkv_path)

    threading.Thread(target=_do_remux, daemon=True, name=f"remux-{mkv_path.stem}").start()
    return True


def delete_clip(name: str) -> Tuple[bool, str]:
    """Delete .mkv, .mp4, and .json for a clip. Returns (success, reason)."""
    if not _valid_name(name):
        return False, "invalid name"
    stem = Path(name).stem
    mkv = (CLIPS_DIR / (stem + ".mkv")).resolve()
    try:
        mkv.relative_to(CLIPS_DIR.resolve())
    except ValueError:
        return False, "path traversal rejected"
    with _remux_lock:
        if mkv in _remux_inflight:
            return False, "remux in progress"
    for ext in (".mkv", ".mp4", ".json"):
        p = mkv.with_suffix(ext)
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning("delete failed for %s: %s", p, e)
    return True, ""


def backfill_remux() -> None:
    """On startup, queue remux for any .mkv missing a .mp4 sibling."""
    if not CLIPS_DIR.exists():
        return
    for mkv in CLIPS_DIR.glob("clip_*.mkv"):
        try:
            if mkv.stat().st_size < _MIN_CLIP_BYTES:
                continue
        except OSError:
            continue
        if not _mp4_path(mkv).exists():
            remux_async(mkv)
