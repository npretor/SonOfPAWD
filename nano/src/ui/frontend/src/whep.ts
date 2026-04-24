// Minimal WHEP (WebRTC-HTTP Egress Protocol) client against MediaMTX.
//
// MediaMTX exposes each path at:
//   http://<host>:8889/<path>/whep
// as both a GET (built-in HTML player) and a POST (SDP offer exchange).
//
// Flow: POST offer SDP after ICE gathering finishes, receive answer SDP,
// setRemoteDescription. MediaMTX bundles its ICE candidates in the answer,
// so no trickle PATCH is needed.

export interface WhepState {
  ice: RTCIceConnectionState;
  conn: RTCPeerConnectionState;
  gather: RTCIceGathererState;
  bytesReceived: number;
  framesDecoded: number;
}

export interface WhepSession {
  pc: RTCPeerConnection;
  resourceUrl: string | null;
  close: () => Promise<void>;
  onStats?: (s: WhepState) => void;
}

export async function whepStart(
  url: string,
  video: HTMLVideoElement,
  onStats?: (s: WhepState) => void,
): Promise<WhepSession> {
  const pc = new RTCPeerConnection({
    iceServers: [],   // LAN-only; no STUN/TURN
  });

  // Pi publishes video only; asking for audio here can make MediaMTX reject
  // or leave the audio m-line inactive, neither of which helps us.
  pc.addTransceiver("video", { direction: "recvonly" });

  pc.ontrack = (e) => {
    console.log("[whep] ontrack kind=%s streams=%d", e.track.kind, e.streams.length);
    if (e.streams[0] && video.srcObject !== e.streams[0]) {
      video.srcObject = e.streams[0];
    }
  };

  pc.oniceconnectionstatechange = () =>
    console.log("[whep] iceConnectionState:", pc.iceConnectionState);
  pc.onconnectionstatechange = () =>
    console.log("[whep] connectionState:", pc.connectionState);
  pc.onicegatheringstatechange = () =>
    console.log("[whep] iceGatheringState:", pc.iceGatheringState);

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  await waitForIceGatheringComplete(pc);

  console.log("[whep] POST %s (offer SDP, %d bytes)", url, pc.localDescription!.sdp.length);
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/sdp" },
    body: pc.localDescription!.sdp,
  });
  if (!res.ok) {
    await pc.close();
    const body = await res.text().catch(() => "");
    throw new Error(`WHEP POST ${res.status}: ${body || res.statusText}`);
  }

  const answerSdp = await res.text();
  const resourceUrl = res.headers.get("Location");
  console.log("[whep] answer received (%d bytes), resource=%s", answerSdp.length, resourceUrl);

  await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });

  let statsTimer: ReturnType<typeof setInterval> | null = null;
  if (onStats) {
    statsTimer = setInterval(async () => {
      let bytes = 0, frames = 0;
      try {
        const stats = await pc.getStats();
        stats.forEach((r: any) => {
          if (r.type === "inbound-rtp" && r.kind === "video") {
            bytes = r.bytesReceived ?? bytes;
            frames = r.framesDecoded ?? frames;
          }
        });
      } catch {
        // ignore
      }
      onStats({
        ice: pc.iceConnectionState,
        conn: pc.connectionState,
        gather: pc.iceGatheringState,
        bytesReceived: bytes,
        framesDecoded: frames,
      });
    }, 1000);
  }

  return {
    pc,
    resourceUrl,
    close: async () => {
      if (statsTimer) clearInterval(statsTimer);
      if (resourceUrl) {
        try {
          await fetch(resourceUrl, { method: "DELETE" });
        } catch {
          // MediaMTX may already have torn down; ignore.
        }
      }
      pc.close();
      video.srcObject = null;
    },
  };
}

function waitForIceGatheringComplete(pc: RTCPeerConnection): Promise<void> {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const check = () => {
      if (pc.iceGatheringState === "complete") {
        pc.removeEventListener("icegatheringstatechange", check);
        resolve();
      }
    };
    pc.addEventListener("icegatheringstatechange", check);
    setTimeout(() => {
      pc.removeEventListener("icegatheringstatechange", check);
      resolve();
    }, 2000);
  });
}
