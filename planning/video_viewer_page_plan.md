# Video Viewer Page — Plan

A new page in the React UI that lists every recorded clip on disk and lets the user play one in a modal. Reads from the recorder's clip directory; no changes to the recorder service itself.

---

## 1. What we're working with

Recorder (`nano/src/recorder/main.py`):

- Writes one file per armed cycle: `clip_YYYYMMDD_HHMMSS.mkv` in `CLIPS_DIR = /var/lib/turret/clips`.
- Container is **Matroska**, payload is **H.264** copied straight off the wire (no decode/encode).
- Publishes `turret/recorder/event`:
  - `{"event": "clip_started", "path": "..."}` on each new fragment
  - `{"event": "clip_closed",  "path": "..."}` when the clip finishes
- The filename's timestamp is the **start** time. Duration is not encoded anywhere we can read cheaply.

Backend (`nano/src/ui/backend/`):

- Flask + Flask-SocketIO, `async_mode="threading"`, served by Werkzeug. LAN-only, no auth.
- Routes registered in `routes.py` via `register_routes(app, bridge)`.
- Static SPA served from `nano/src/ui/static/` with SPA fallback for client-side routes.

Frontend (`nano/src/ui/frontend/src/`):

- React + React-Router. Add a route in `App.tsx` and a top-nav link.
- Existing modal/drawer pattern to mirror: `components/LogsDrawer.tsx`.
- API helpers in `api.ts`.

Permissions today: `/var/lib/turret/clips` is owned `dlinano:dlinano` 0755, files 0664. The ui-backend already runs as `dlinano`, so reads are free.

---

## 2. The one real risk: MKV in the browser

`<video src="...mkv">` is **not reliably playable** in Chrome/Firefox. The bytes inside are H.264 (which all browsers play); it's the Matroska container they refuse. We have three options, in order of preference:

### Option A — Remux on close (recommended)

When the recorder publishes `clip_closed`, the ui-backend's MQTT bridge spawns a one-shot:

```
ffmpeg -nostdin -y -i clip_X.mkv -c copy -movflags +faststart clip_X.mp4
```

No transcoding (just container swap). On a Nano this finishes in a second or two even for multi-minute clips. The `.mkv` stays as the authoritative copy; the `.mp4` is the playable view. If `.mp4` is missing for an older clip, do the same remux lazily the first time someone hits the "play" endpoint.

Pros: trivial static-file streaming with Range support; clip pages load instantly; standard `<video>` tag.
Cons: ~1.05× disk usage (MP4 is essentially the same size). Can be mitigated later by deleting `.mkv` once `.mp4` is verified.

### Option B — Stream-remux on demand

`ffmpeg -i in.mkv -c copy -f mp4 -movflags frag_keyframe+empty_moov pipe:1` piped through Flask. No new files, but: no Range support → no seeking, and one ffmpeg per concurrent viewer.

### Option C — Serve MKV as-is

Cheap. Fails in Chrome/Firefox depending on version. Not acceptable for the primary path.

**Plan adopts Option A.** Background remux on `clip_closed`, lazy remux for legacy files.

---

## 3. Duration & metadata

The recorder doesn't record duration anywhere. Two ways to derive it:

- **From the MP4 once remuxed.** `ffprobe -v error -show_entries format=duration -of csv=p=0 clip.mp4` is fast and exact.
- **From file mtime minus filename-encoded start.** Quick and avoids a subprocess, but inaccurate when writes are delayed or the clip is still open.

Cache duration in a sidecar `clip_X.json`:

```json
{
  "start_ts": "2026-04-19T17:27:10",
  "duration_s": 47.3,
  "size_bytes": 31909512,
  "mp4_ready": true
}
```

Sidecar is written when the remux finishes. For files that already exist without a sidecar, the `/api/clips` endpoint generates one on first listing (one-time cost).

---

## 4. Backend changes

### 4.1 New module: `nano/src/ui/backend/clips.py`

Pure helpers, no Flask coupling:

- `list_clips() -> list[ClipMeta]` — scans `CLIPS_DIR`, returns sorted-by-start-desc list. For each `.mkv`:
  - read sidecar `.json` if present; otherwise compute (ffprobe on the `.mp4` if it exists, else estimate from mtime) and write the sidecar
  - flag `mp4_ready` = whether `.mp4` sibling exists
- `clip_path(name: str) -> Path | None` — resolves a clip name to its `.mp4` path, returning `None` if the name is not a valid clip filename or not in the clips dir. Used by the playback endpoint and must reject path traversal.
- `remux_async(mkv_path: Path) -> None` — spawns `ffmpeg -c copy -movflags +faststart` in a background thread; writes sidecar on success.
- `delete_clip(name: str) -> bool` — removes `.mkv`, `.mp4`, `.json` together. (Optional for v1; nice for cleanup.)

Use `Path.resolve()` + check that the resolved path is inside `CLIPS_DIR` for every name coming from the client. Filename allow-list regex: `^clip_\d{8}_\d{6}\.(mkv|mp4)$`.

### 4.2 New routes in `routes.py`

```
GET  /api/clips
  -> { "clips": [ {name, start_ts, duration_s, size_bytes, mp4_ready}, ... ] }

GET  /api/clips/<name>           # streams the .mp4 with HTTP Range support
  -> 200 + video/mp4 stream
  -> 404 if missing
  -> 202 + remux-in-progress if .mkv exists but .mp4 not ready (kicks off remux if not running)

DELETE /api/clips/<name>         # removes .mkv, .mp4, and .json sidecar together
  -> 204 on success
  -> 404 if the name doesn't resolve to an existing clip
  -> 409 if a remux is currently in flight for this clip (retry after clip_changed)

POST /api/clips/delete           # bulk delete; body: { "names": ["clip_X.mkv", ...] }
  -> 200 + { "deleted": [...], "failed": [{name, reason}, ...] }
```

The bulk endpoint is what the UI's "Delete selected" button actually calls. Keeping the single-resource `DELETE` around too is cheap and useful for `curl` poking. Both go through the same `clips.delete_clip(name)` helper, which:
- validates the name against the allow-list regex,
- refuses if the path is currently being remuxed (in-flight set check),
- removes `.mkv`, `.mp4`, and `.json` and tolerates any of the three already being absent.

After a bulk delete, the route emits the same `clip_changed` Socket.IO event so any other browser tabs refresh.

For Range support: `flask.send_file(path, mimetype="video/mp4", conditional=True)`. Werkzeug ≥ 2.0 honors `Range` headers when `conditional=True`. Verify the Nano's installed Flask/Werkzeug versions before relying on this — fallback is a small manual Range handler.

### 4.3 Background remux trigger in `mqtt_bridge.py`

The bridge already subscribes to `turret/recorder/event` and emits to Socket.IO. Extend it: when an event with `event=="clip_closed"` arrives, call `clips.remux_async(path)`. Emit a follow-up Socket.IO event `clip_ready` (or just `clip_changed`) once the sidecar is written so the frontend can refresh without polling.

Keep the existing Socket.IO emit in place — the dashboard's "last clip" indicator still wants the raw event.

### 4.4 No changes to the recorder service itself

The recorder owns the MKV writing path; the viewer is read-only. This keeps the recorder simple and means a viewer bug can't break recording.

---

## 5. Frontend changes

### 5.1 New API helpers (`api.ts`)

```ts
export interface ClipMeta {
  name: string;          // "clip_20260419_172710.mkv"
  start_ts: string;      // ISO 8601
  duration_s: number;
  size_bytes: number;
  mp4_ready: boolean;
}

export const listClips = () => getJson<{ clips: ClipMeta[] }>("/api/clips");
export const clipUrl = (name: string) => `/api/clips/${encodeURIComponent(name)}`;

export interface BulkDeleteResponse {
  deleted: string[];
  failed: { name: string; reason: string }[];
}
export const deleteClips = (names: string[]) =>
  postJson("/api/clips/delete", { names }).then(r => r.json() as Promise<BulkDeleteResponse>);
```

### 5.2 New route: `routes/Clips.tsx`

Component responsibilities:

- On mount: `listClips()`, store in local `useState`. Show spinner until loaded.
- Subscribe to the `clip_changed` Socket.IO event in a `useEffect` and refetch.
- Local state `selected: Set<string>` (clip names). Adding/removing items is constant-time and easy to feed into the toolbar's "N selected" counter.
- Render a CSS grid of tiles (8–12 per row depending on viewport), each tile:
  - **selection checkbox** in the top-left corner; clicking it toggles membership in `selected` (does not open the modal — stop propagation)
  - clicking elsewhere on the tile opens the play modal (so casual play stays one click)
  - thumbnail placeholder (just an icon for v1; thumbnail generation is out of scope)
  - start time, formatted `Apr 19, 5:27:10 PM`
  - duration `0:47`
  - size `30.4 MB`
  - "Play" button (disabled with tooltip "Processing…" if `mp4_ready === false`)
  - selected tiles get a visible outline / tint via a `.clip-tile.selected` class
- Sort newest first.
- **Toolbar above the grid**:
  - total count, total disk usage, refresh button (existing)
  - "Select all" / "Clear" toggle that flips between selecting every visible clip and clearing
  - "Delete selected (N)" button — disabled when `selected.size === 0`. On click:
    1. Confirm dialog: "Delete N clip(s)? This cannot be undone."
    2. Call `deleteClips([...selected])`.
    3. On success: optimistically drop the deleted names from local state and clear `selected`. The `clip_changed` Socket.IO event will trigger a refetch shortly after for the authoritative state.
    4. If the response contains `failed` entries, show a small inline notice listing the names + reasons (e.g. "still processing — try again in a moment").

### 5.3 New component: `components/VideoModal.tsx`

- Renders `<dialog>` (or a div with backdrop) when `clipName` is set.
- Body is a single `<video src={clipUrl(clipName)} controls autoPlay preload="metadata" />`.
- Close on backdrop click, Escape key, or close button. Pause + null the source on close so the connection drops cleanly.
- Mirror the styling pattern in `LogsDrawer.tsx` so it feels native.

### 5.4 Wire into `App.tsx` and the topbar

```tsx
<Link to="/clips">Clips</Link>
...
<Route path="/clips" element={<Clips />} />
```

### 5.5 Styling

Add a `.clip-grid` + `.clip-tile` block to `styles.css`. Match the existing card aesthetic (the dashboard panels are a good reference).

---

## 6. Edge cases worth thinking through up front

- **Clip currently being recorded.** `clip_X.mkv` exists but `clip_closed` hasn't fired. The listing should either skip in-progress clips or surface them with `duration_s: null` and a disabled play button. Easiest detector: check if the file's mtime is within the last few seconds AND no sidecar exists. Cleaner: have the bridge track which paths are currently open from `clip_started` events not yet matched by `clip_closed`.
- **Concurrent remux requests.** A user mashing play on a fresh clip shouldn't spawn N ffmpegs. Track in-flight remuxes in a set keyed by path; second caller gets `202` and waits for the Socket.IO notification.
- **ffmpeg missing.** Check at startup, log loudly, and the `/api/clips` endpoint returns rows with `mp4_ready: false` permanently. The page should show a banner explaining ffmpeg isn't installed.
- **Disk full from MP4 doubling.** Add a `total_bytes` field to the listing response so the UI shows it. Future enhancement: a "delete MKV after MP4 verified" toggle.
- **Path traversal.** `name` from the client must match `^clip_\d{8}_\d{6}\.(mkv|mp4)$` and resolve inside `CLIPS_DIR`. Reject otherwise. Bulk delete validates every name in the list before deleting any of them — partial-success is acceptable for "currently being remuxed" but not for "this name looks like an attack".
- **Deleting a clip that's currently being remuxed.** `delete_clip` checks the in-flight remux set and refuses with `409` / `"remux in progress"` rather than racing the ffmpeg subprocess. UI surfaces this as a `failed` row.
- **Deleting the clip currently playing in the modal.** The modal holds an open HTTP connection to the MP4. Closing the modal first is the cleanest UX — to enforce, the toolbar's "Delete selected" can refuse (with a small toast) when the currently-playing clip is in the selection. Alternative: just let the browser's stream 404 mid-playback; ugly but harmless.
- **Accidental bulk delete.** The confirm dialog is the only safety net in v1 — clips aren't recoverable. If this turns out to bite, add a "Trash" folder later (move instead of unlink).
- **Werkzeug threading + long downloads.** With `async_mode="threading"`, each in-flight video stream pins one worker thread. For LAN-only single-user, fine. If multiple viewers ever happen, revisit.
- **Big libraries.** No pagination in v1 (clips are short and few). Add cursor-based paging later if the count crosses a few hundred.

---

## 7. Implementation order

1. **Backend, no UI yet.**
   1. Add `nano/src/ui/backend/clips.py` with `list_clips`, `clip_path`, `delete_clip`, sidecar write.
   2. Add `GET /api/clips`, `GET /api/clips/<name>`, `DELETE /api/clips/<name>`, and `POST /api/clips/delete` to `routes.py`.
   3. Smoke test with `curl http://localhost:5000/api/clips`, a browser hitting one of the existing clips, and `curl -X POST .../api/clips/delete` against a throwaway clip.
2. **Background remux.**
   1. Add `remux_async` + the in-flight set to `clips.py`.
   2. Wire `clip_closed` MQTT events in `mqtt_bridge.py` to call it; add `clip_changed` Socket.IO emit on completion. Bulk-delete handler also emits `clip_changed`.
   3. One-shot backfill: on ui-backend startup, kick off remux for any `.mkv` lacking a `.mp4` sibling. Throttle to one at a time to keep the Nano responsive.
3. **Frontend.**
   1. Add `listClips`, `clipUrl`, `deleteClips` to `api.ts`.
   2. Add `VideoModal.tsx`.
   3. Add `Clips.tsx` — grid of tiles with selection checkboxes, toolbar with select-all + delete-selected, opens modal on tile click.
   4. Add the route + nav link in `App.tsx`.
   5. Style (`.clip-grid`, `.clip-tile`, `.clip-tile.selected`, toolbar).
4. **Verify in browser.** Per CLAUDE.md: don't claim this works until a real browser plays a real clip end-to-end with seeking and modal close cleanup verified, AND a multi-select bulk delete round-trips correctly (UI reflects deletion, files actually gone, other tabs refresh on `clip_changed`).

Each numbered group is a separate commit.

---

## 8. Out of scope (call out, don't build)

- Thumbnails. Cheap to add later (`ffmpeg -ss N -frames:v 1 -vf scale=160:-1`) but not needed for v1.
- Tagging / search / filters. Filenames carry start time; the grid is sorted; that's enough for now.
- Auth. LAN-only, mirrors the rest of the app.
- Long-term retention policy (auto-delete old clips). Worth doing eventually but separate.
- Mobile layout. The dashboard isn't mobile-tuned either; match its breakpoints, no special handling.
- Live "currently recording" preview. The dashboard already has the WebRTC stream for that.

---

## 9. Files to be created or touched

Created:
- `nano/src/ui/backend/clips.py`
- `nano/src/ui/frontend/src/routes/Clips.tsx`
- `nano/src/ui/frontend/src/components/VideoModal.tsx`

Touched:
- `nano/src/ui/backend/routes.py` — add `/api/clips` and `/api/clips/<name>`.
- `nano/src/ui/backend/mqtt_bridge.py` — call `remux_async` on `clip_closed`, emit `clip_changed`.
- `nano/src/ui/frontend/src/api.ts` — `listClips`, `clipUrl`, `ClipMeta` interface.
- `nano/src/ui/frontend/src/App.tsx` — register `/clips` route + nav link.
- `nano/src/ui/frontend/src/styles.css` — grid + tile styles.

No changes to: recorder service, systemd units, hub, motion-ctrl, MQTT topics list (the `clip_changed` Socket.IO event is UI-internal, not an MQTT topic).
