# Video Save — Plan

Two distinct flows feeding the same recorder service:

1. **Monitor mode** — automatic, motion-triggered: PIR fires → record until motion is quiet for a configured window.
2. **Auto-aim / auto-sentry** — manual: a Record button in the UI that toggles recording on press.

Both end up publishing the same `turret/recorder/arm` MQTT topic that the recorder already consumes. No recorder-service changes.

---

## 1. Current state of the world

What's plumbed:

- `nano/src/recorder/main.py` subscribes to **`turret/recorder/arm`** with `{"armed": bool, "trailing_s": int}`. `armed=true` opens the gate and starts a new MKV; `armed=false` schedules a close after `trailing_s`. One armed cycle = one clip file.
- The recorder publishes **`turret/recorder/event`**: `clip_started` / `clip_closed` with the file path. The hub already listens and updates `state.recorder_armed` (`nano/src/hub/main.py:175`), republishing it in `hub_state.armed`.
- The hub already subscribes to **`turret/motion`** in `_handle_motion` (`main.py:151`) and records `state.last_motion_ts`. It is mode-aware and explicitly says `"not yet wired"` for monitor — that's the line item we're filling in.
- Modes: `idle`, `monitor`, `trailcam`, `auto-aim`, `auto-sentry` (`nano/src/hub/modes.py`).

What's missing:

- **Nobody publishes `recorder/arm`.** Clips that exist on disk were created manually via `mosquitto_pub`.
- No UI control for arming the recorder. The existing `ArmToggle` is for the *firing* solenoid (`turret/fire/armed`), unrelated.

So this work has three parts:

1. Hub: auto-arm the recorder when in monitor mode and PIR motion is active.
2. UI + backend: a manual Record toggle for auto-aim / auto-sentry.
3. Conflict rules between (1) and (2) so they don't fight.

---

## 2. Monitor-mode auto-arming (hub change)

### Behavior

In `Mode.MONITOR`:

- PIR `active=true` → publish `recorder/arm armed=true`. Recorder opens gate + starts a fresh MKV.
- PIR remains `active=true` (or re-fires) → re-publish `armed=true`. The recorder's `_open_gate` is idempotent and will cancel any pending close; further activity extends the clip.
- PIR `active=false` → publish `recorder/arm armed=false trailing_s=<configured>`. The recorder closes the clip after the trailing window. If motion fires again before expiry, the recorder cancels the close (one continuous clip across rapid retriggers).

Outside monitor mode (idle, trailcam, auto-aim, auto-sentry), the hub does not auto-arm on motion.

### Trailing window — pick one source of truth

Two existing settings overlap and need disambiguation:

- `state.motion_timeout_s` on the hub (PIR active → presumed-active grace period). Default 30s, range 1–600s, set via `hub_config`.
- `TRAILING_SECONDS = 10` baked into `common/config/hosts.py`, also exposed in settings as `trailing_s`.

Two reasonable resolutions:

- **(a) Recorder owns the window.** Hub sends `armed=false` the moment PIR goes idle and passes `trailing_s` from settings. Hub's `motion_timeout_s` becomes purely about telemetry/UI ("PIR considered active for the last N seconds"), not about recording.
- **(b) Hub owns the window.** Hub holds `armed=true` for `motion_timeout_s` after the last edge, then sends `armed=false trailing_s=0`. Recorder closes immediately.

(a) is the smaller change and matches what's already there — recorder already implements the trailing close with cancel-on-rearm. **Plan adopts (a).** Settings page already exposes `trailing_s` so the user has the knob.

### Mode transitions

- Entering monitor → if PIR is currently believed active (`now - last_motion_ts < motion_timeout_s`), publish `armed=true` immediately so we don't wait for the next edge.
- Leaving monitor while recording → publish `armed=false trailing_s=0`. We don't want a clip silently continuing into another mode where a different actor might also be issuing arm commands.
- Entering monitor with a manual clip already in progress (started from auto-aim before mode switch) → see §4.

### Code locations

- `nano/src/hub/main.py:151` `_handle_motion` — replace the `"not yet wired"` log with a publish to `TOPIC_RECORDER_ARM` gated on `mode == MONITOR`.
- `_handle_mode_change` already exists; add the entry/exit publishes there.

---

## 3. Manual record button (UI + backend)

### UI

New component `nano/src/ui/frontend/src/components/RecordToggle.tsx`:

- Rendered on the dashboard, visible only when `mode in {auto-aim, auto-sentry}`. (Or always visible but disabled — see open question Q2.)
- Toggle button: "● Record" / "■ Stop", styled like `ArmToggle`.
- State source: `hub_state.armed` from the existing socket store. The button reflects the *actual* recorder state, not optimistic local state — so a long round-trip or a recorder restart can't desync the UI.
- Press handler: `POST /api/recorder/arm` with `{on: !armed}`. Treat the button as "request"; the button position only flips when `hub_state.armed` updates back from MQTT. (Mirrors how `ArmToggle` behaves.)
- Optional small text under the button: "Recording → clip_20260503_191027.mkv (0:42)" — name comes from `clip_started`, duration ticks locally from start time. Nice but not required for v1.

### Backend route

In `nano/src/ui/backend/routes.py`:

```
POST /api/recorder/arm
  body: { "on": bool }
  -> publishes TOPIC_RECORDER_ARM { "armed": on, "trailing_s": <effective trailing or 0 if stopping> }
  -> 200 { "ok": true, "armed": on }
  -> 409 if mode is not in {auto-aim, auto-sentry, monitor}  (see Q2)
```

API helper in `api.ts`:

```ts
export const setRecorderArm = (on: boolean) => postJson("/api/recorder/arm", { on });
```

The route does **not** retain the message. Recording is a transient action; a retained `armed=true` would re-arm on every recorder restart, which is wrong.

### Trailing-on-stop

Manual stop should mean *stop now* — the user pressed the button intending the clip to end. So the route sends `trailing_s=0` when `on=false`. PIR-driven stops in monitor mode use the configured trailing.

---

## 4. Conflict rules (the part that's easy to get wrong)

Two arming sources can race: hub-driven monitor-mode auto-arm and UI-driven manual arm. Concrete cases and the proposed rule:

| Situation | Rule |
|---|---|
| Manual record in auto-aim → user switches to monitor | On mode transition into monitor, hub publishes `armed=false trailing_s=0` to close the manual clip cleanly. Subsequent PIR motion starts a new clip. (Avoids "one giant clip across two intents" which is hard to label later.) |
| Manual record in auto-aim → user switches to idle | Same: hub closes the clip on mode exit. Idle should produce no recording. |
| User presses Record in monitor mode (if button is allowed there) | Manual press always wins — overrides the PIR-driven state until released. Once released, hub returns to PIR-driven behavior. |
| Recorder restarts mid-clip | Existing reconnect logic in `_start_pipeline` re-applies `_desired_armed`. After restart the recorder publishes nothing until the next arm message. Hub already has correct `state.recorder_armed=False` after the missing `clip_closed`. UI button reflects this. |
| User in monitor, PIR keeps firing | Single continuous clip via the recorder's cancel-pending-close behavior. |
| User in monitor, PIR fires twice with a gap > trailing_s | Two clips. Already how the recorder works. |

Implementation sketch for the hub: a small helper `self._set_recorder_arm(armed, trailing_s, source)` that publishes and logs the source for forensics. All code paths (motion handler, mode change handler, future UI-via-hub paths if any) go through it.

---

## 5. Files touched / created

Touched:

- `nano/src/hub/main.py` — `_handle_motion` (auto-arm in monitor), `_handle_mode_change` (clean transitions), helper `_set_recorder_arm`.
- `nano/src/ui/backend/routes.py` — `POST /api/recorder/arm`.
- `nano/src/ui/frontend/src/api.ts` — `setRecorderArm`.
- `nano/src/ui/frontend/src/routes/Dashboard.tsx` — slot in `<RecordToggle />`.
- `nano/src/ui/frontend/src/styles.css` — minor styling (mirror `ArmToggle`).

Created:

- `nano/src/ui/frontend/src/components/RecordToggle.tsx`.

No changes:

- Recorder service. The contract is already what we need.
- MQTT topics file. Reuses `TOPIC_RECORDER_ARM` / `TOPIC_RECORDER_EVENT`.
- Pi services.

---

## 6. Implementation order

1. **Hub: monitor auto-arm.**
   1. Add `_set_recorder_arm(armed, trailing_s, source)` helper that publishes `TOPIC_RECORDER_ARM`.
   2. In `_handle_motion`, gate on `mode == MONITOR` and call the helper with `state.trailing_s`.
   3. In `_handle_mode_change`, publish `armed=false trailing_s=0` when leaving monitor or any auto mode if `state.recorder_armed`.
   4. On entering monitor, if `now - state.last_motion_ts < state.motion_timeout_s`, fire `armed=true` immediately.
   5. Test by switching to monitor and triggering PIR; verify `clip_started`/`clip_closed` and an MKV on disk.
2. **Backend route.**
   1. Add `POST /api/recorder/arm` to `routes.py`. Decide on Q2 first (whether it's gated by mode).
   2. Smoke test with `curl` from the Nano.
3. **Frontend.**
   1. `setRecorderArm` in `api.ts`.
   2. `RecordToggle.tsx`. Reads `armed` from the hub-state store, posts on click.
   3. Mount in `Dashboard.tsx`, visibility based on current mode.
4. **Verify in browser.** Per CLAUDE.md: real browser, all three modes, both trigger paths, mode transitions during recording, clip files appear and play in the viewer (which we built in `video_viewer_page_plan.md`).

---

## 7. Open questions

These are decisions I'd rather you make than guess at:

**Q1. Trailing window source of truth.** Plan currently has the recorder owning it via `trailing_s` from settings. Acceptable, or do you want the hub to control the close timing instead?

**Q2. Should the manual Record button work outside auto-aim and auto-sentry?**
   - **Just auto modes** (current plan): cleanest mental model — monitor's recording is "automatic", auto modes have a manual override, idle/trailcam never record.
   - **Always available**: useful for ad-hoc capture from idle (e.g., calibrating something on the bench). Adds a "manual recording from idle" code path that needs its own mode-transition rule.
   - **Auto modes + monitor**: lets the user force-record in monitor before motion happens. Manual-vs-PIR conflict rule per §4.

**Q3. Behavior on mode change while recording.** Plan closes the clip with `trailing_s=0` on mode exit. Alternative: keep recording until the natural close, accepting that a single clip can span modes. Closing is safer for organizing footage; keeping is friendlier if you switch modes a lot.

**Q4. Trailcam mode.** Currently no special behavior here. Should trailcam also auto-record on PIR? It sounds like it should (the name implies it). If yes, I'd extend §2's rules to `mode in {monitor, trailcam}` — but I don't know what differentiates trailcam from monitor today.

**Q5. UI feedback when a recording is actively going.**
   - Just the button state ("Stop" instead of "Record")? Or…
   - A red REC dot + elapsed time + current filename?
   - A toast on `clip_closed` with a "View clip" link?

**Q6. Detection-event triggering for auto modes.** Should auto-aim / auto-sentry *automatically* record while a target is being tracked (independent of the manual button)? Plan says no — auto modes are manual-only — but it's a reasonable feature.

**Q7. What about audio?** The current pipeline is video-only (`rtspsrc → rtph264depay → h264parse → splitmuxsink`). Confirming this stays video-only since the camera doesn't publish an audio track.

**Q8. Disk-full guard.** None today. Do we want the hub to refuse to publish `armed=true` if `CLIPS_DIR`'s filesystem is below some free-space threshold, or is that out of scope?

---

## 8. Out of scope (call out, don't build)

- Auto-deletion / retention of old clips. Disk fills eventually; separate problem.
- Tagging clips with the mode they were recorded in. Filename is just a timestamp today; could append `_monitor` / `_manual` later if useful.
- Per-clip metadata (target class, lock-on result, fire events). All available via MQTT, but joining them to clips is a separate sidecar effort.
- Pre-roll (recording the N seconds *before* a trigger). Would require keeping a ring buffer in the recorder; non-trivial.
- Rate-limiting auto-arm to avoid pathological short clips when PIR chatters. Trailing window already covers this in the common case.


## Answers to questions 
1. Recorder should own the trailing window source of truth. 
2. No, manual record button should not work outside of auto-aim and auto-sentry
3. Keep recording till the natural close
4. Trailcam is photos only, not video. monitor saves video
5. Add a red REC dot and elapsed time
6. No, keep manual
7. No audio. 
8. Not for now, i have the disk usage displayed and plan to save clips to an external device with enough storage 
