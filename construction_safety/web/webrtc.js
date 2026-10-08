/* SDP uses the dashboard origin; H.264 media comes directly from Insight via ICE. */
class CameraVideo {
  constructor(video, camera, status) {
    this.video = video;
    this.camera = camera;
    this.status = status;
    this.key = null;
    this.pc = null;
    this.retryAt = 0;
    this.blocked = false;
  }

  close() {
    this.abort?.abort();
    this.abort = null;
    const pc = this.pc;
    this.pc = null;
    pc?.close();
    this.video.srcObject = null;
    this.playbackSample = null;
    this.playbackFps = null;
  }

  retry(message) {
    this.close();
    this.retryAt = Date.now() + 2000;
    this.status(message);
  }

  update(camera, hidden) {
    const key = `${camera.session_id}:${camera.routing?.channel ?? camera.channel}`;
    if (key !== this.key) {
      this.close();
      this.key = key;
      this.blocked = false;
      this.retryAt = 0;
    }
    if (hidden || camera.status !== 'running' || camera.stale) {
      this.close();
      return;
    }
    if (!window.RTCPeerConnection) {
      this.status('WEBRTC UNAVAILABLE');
      return;
    }
    if (!this.pc && !this.blocked && Date.now() >= this.retryAt) this.connect();
    if (!this.pc) return;
    // Browser playback is distinct from analytics throughput and camera source FPS.
    const quality = this.video.getVideoPlaybackQuality?.();
    if (quality) {
      const sample = { at: performance.now(), frames: quality.totalVideoFrames - quality.droppedVideoFrames };
      const previous = this.playbackSample;
      if (previous && sample.at - previous.at >= 500) {
        this.playbackFps = Math.max(0, 1000 * (sample.frames - previous.frames) / (sample.at - previous.at));
        this.playbackSample = sample;
      } else if (!previous) this.playbackSample = sample;
    }
    // A connected transport alone does not mean that the video is progressing.
    if (this.video.readyState >= 2 && this.video.currentTime !== this.lastTime) {
      this.lastTime = this.video.currentTime;
      this.lastFrame = Date.now();
      this.video.style.opacity = '1';
      this.status('LIVE');
    } else if (Date.now() - this.lastFrame > 12000) {
      this.retry('VIDEO RETRY');
    }
  }

  async connect() {
    const pc = new RTCPeerConnection({ iceServers: [] });
    this.pc = pc;
    this.lastFrame = Date.now();
    this.lastTime = -1;
    this.status('CONNECTING');
    const receiver = pc.addTransceiver('video', { direction: 'recvonly' }).receiver;
    // Annotations are burned in; no metadata synchronization buffer is needed.
    try {
      if ('jitterBufferTarget' in receiver) receiver.jitterBufferTarget = 50;
    } catch (_) { /* Browser retains its default when this hint is unsupported. */ }
    pc.ontrack = ({ track }) => {
      if (this.pc !== pc) return;
      this.video.srcObject = new MediaStream([track]);
      if (!this.paused) this.video.play().catch(() => this.status('PLAYBACK BLOCKED'));
    };
    pc.onconnectionstatechange = () => {
      if (this.pc === pc && pc.connectionState === 'failed') this.retry('CONNECTION RETRY');
    };
    const abort = new AbortController();
    this.abort = abort;
    const timer = setTimeout(() => abort.abort(), 12000);
    try {
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      // Insight supplies host candidates and supports peer-reflexive ICE discovery.
      const response = await fetch('api/webrtc', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ camera: this.camera, offer }),
        signal: abort.signal,
      });
      const answer = await response.json();
      if (this.pc !== pc) return;
      if (!response.ok) {
        this.blocked = response.status >= 400 && response.status < 500;
        throw Error(response.status === 415 ? 'UNSUPPORTED CODEC' : 'SIGNALING RETRY');
      }
      await pc.setRemoteDescription(answer);
    } catch (error) {
      if (this.pc === pc) this.retry(this.blocked ? error.message : 'CONNECTION RETRY');
    } finally {
      clearTimeout(timer);
    }
  }
}
