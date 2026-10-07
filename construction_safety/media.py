import subprocess
import threading
import time

import cv2
import numpy as np


class Source:
    """Keep only the latest RTSP frame; never build an inference-latency backlog."""

    def __init__(self, source):
        self.live = source.startswith(("rtsp://", "rtsps://"))
        self.cap = cv2.VideoCapture(
            source,
            cv2.CAP_FFMPEG,
            [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 10000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000],
        )
        if not self.cap.isOpened():
            raise RuntimeError(f"Cannot open source: {source}")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.closed = False
        self.condition = threading.Condition()
        self.latest = None
        self.sequence = 0
        self.consumed = -1
        self.index = 0
        self.thread = None
        if self.live:
            self.thread = threading.Thread(target=self._read_loop, daemon=True)
            self.thread.start()

    def _read_loop(self):
        try:
            while not self.closed:
                ok, frame = self.cap.read()
                if not ok:
                    break
                with self.condition:
                    self.latest = (frame, time.monotonic())
                    self.sequence += 1
                    self.condition.notify_all()
        finally:
            with self.condition:
                self.closed = True
                self.condition.notify_all()
            self.cap.release()

    def read(self):
        if not self.live:
            ok, frame = self.cap.read()
            if not ok:
                return None
            timestamp = self.index / self.fps
            self.index += 1
            return frame, timestamp
        with self.condition:
            self.condition.wait_for(
                lambda: self.closed or self.sequence != self.consumed and self.latest is not None,
                10,
            )
            if self.latest is None or self.sequence == self.consumed:
                raise RuntimeError("RTSP source stopped or timed out")
            self.consumed = self.sequence
            return self.latest

    def close(self):
        self.closed = True
        if self.thread:
            self.thread.join(timeout=6)
        else:
            self.cap.release()


class FFmpegSender:
    def __init__(self, width, height, fps, host, port, logfile):
        self.log = open(logfile, "w")
        self.process = subprocess.Popen(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "warning",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
                "-s",
                f"{width}x{height}",
                "-r",
                str(fps),
                "-i",
                "pipe:0",
                "-an",
                "-c:v",
                "libx264",
                "-threads",
                "2",
                "-preset",
                "ultrafast",
                "-tune",
                "zerolatency",
                "-pix_fmt",
                "yuv420p",
                "-profile:v",
                "baseline",
                "-g",
                str(max(1, round(fps))),
                "-bf",
                "0",
                "-x264-params",
                "repeat-headers=1:scenecut=0",
                "-payload_type",
                "96",
                "-rtpflags",
                "skip_rtcp",
                "-f",
                "rtp",
                f"rtp://{host}:{port}?pkt_size=1200",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=self.log,
        )

    def push(self, frame, index):
        if self.process.poll() is not None:
            raise RuntimeError("Insight encoder stopped; check encoder.log")
        self.process.stdin.write(np.ascontiguousarray(frame).tobytes())

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=10)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        finally:
            self.log.close()


class NeatSender:
    def __init__(self, width, height, fps, host, port):
        import pyneat as p

        self.p, self.fps = p, fps
        opt = p.InputOptions()
        opt.payload_type = p.PayloadType.Image
        opt.format = p.Format.RGB
        opt.width, opt.height, opt.depth = width, height, 3
        opt.fps_n, opt.fps_d = round(fps), 1
        opt.memory_policy = p.InputMemoryPolicy.Ev74
        output = p.VideoSenderOptions.h264_rtp_udp_from_raw(width, height, round(fps))
        output.host = host
        output.channel = 0
        output.video_port_base = port
        output.encoder.bitrate_kbps = 3000
        graph = p.Graph("construction_preview")
        graph.add(p.nodes.input(opt))
        graph.add(p.groups.video_sender(output))
        seed = p.Tensor.from_numpy(
            np.zeros((height, width, 3), np.uint8),
            copy=True,
            image_format=p.PixelFormat.RGB,
            memory=p.TensorMemory.EV74,
        )
        self.run = graph.build([seed])

    def push(self, frame, index):
        p = self.p
        tensor = p.Tensor.from_numpy(
            cv2.cvtColor(frame, cv2.COLOR_BGR2RGB),
            copy=True,
            image_format=p.PixelFormat.RGB,
            memory=p.TensorMemory.EV74,
        )
        sample = p.make_tensor_sample("", tensor)
        sample.pts_ns = int(index * 1e9 / self.fps)
        sample.duration_ns = int(1e9 / self.fps)
        sample.frame_id = index
        self.run.push([sample])

    def close(self):
        self.run.close()
