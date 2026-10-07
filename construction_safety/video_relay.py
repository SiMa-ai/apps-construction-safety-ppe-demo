"""Optional RTP-over-SSH transport when SDK host UDP ports are unreachable."""

import argparse
import json
import os
import select
import shlex
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

HEADER = struct.Struct("!HI")


def receive(base, channels):
    import cv2
    import numpy as np

    root = Path(__file__).resolve().parents[1]
    cameras = json.loads((root / "configs/dashboard.json").read_text())["cameras"]
    sockets = {}
    last_previews = {}
    next_preview = 0.0
    try:
        for channel in range(channels):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("127.0.0.1", base + channel))
            sockets[sock] = channel
        while True:
            ready, _, _ = select.select(list(sockets), [], [], 0.05)
            for sock in ready:
                packet = sock.recv(65535)
                sys.stdout.buffer.write(HEADER.pack(sockets[sock], len(packet)) + packet)
                sys.stdout.buffer.flush()
            if time.monotonic() >= next_preview:
                next_preview = time.monotonic() + 0.1
                for index, camera in enumerate(cameras):
                    try:
                        record = json.loads((root / f"runs/{camera}.device.json").read_text())
                        path = Path(record.get("preview_dir", record["run_dir"])) / "preview.jpg"
                        with path.open("rb") as stream:
                            stamp = os.fstat(stream.fileno()).st_mtime_ns
                            key = (str(path), stamp)
                            if last_previews.get(camera) == key:
                                continue
                            image = cv2.imdecode(
                                np.frombuffer(stream.read(), np.uint8), cv2.IMREAD_COLOR
                            )
                        if image is None:
                            continue
                        if image.shape[1] > 640:
                            image = cv2.resize(
                                image, (640, round(image.shape[0] * 640 / image.shape[1]))
                            )
                        ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 65])
                        if not ok:
                            continue
                        packet = struct.pack("!d", stamp / 1e9) + jpeg.tobytes()
                        sys.stdout.buffer.write(HEADER.pack(100 + index, len(packet)) + packet)
                        sys.stdout.buffer.flush()
                        last_previews[camera] = key
                    except (OSError, ValueError, KeyError):
                        continue
    finally:
        for sock in sockets:
            sock.close()


def read_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise EOFError("Video relay connection closed")
        data.extend(chunk)
    return data


class VideoRelay:
    def __init__(self, root, runtime):
        self.root, self.runtime = root, runtime
        self.cameras = json.loads((root / "configs/dashboard.json").read_text())["cameras"]
        self.previews = {}
        self.preview_lock = threading.Lock()
        self.closed = threading.Event()
        self.process = None
        self.status = {"mode": "ssh", "connected": False, "packets": 0}
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _run(self):
        cfg = self.runtime
        command = [
            cfg["python"],
            str(self.root / "construction_safety/video_relay.py"),
            "--receive",
            "--base",
            str(cfg["video_port_base"]),
            "--channels",
            "4",
        ]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            while not self.closed.is_set():
                try:
                    self.process = subprocess.Popen(
                        [
                            "ssh",
                            "-T",
                            "-o",
                            "BatchMode=yes",
                            "-o",
                            "ConnectTimeout=5",
                            "-o",
                            "ServerAliveInterval=5",
                            "-o",
                            "ServerAliveCountMax=2",
                            cfg["ssh_target"],
                            shlex.join(command),
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                    )
                    if self.closed.is_set():
                        break
                    while not self.closed.is_set():
                        channel, size = HEADER.unpack(read_exact(self.process.stdout, HEADER.size))
                        is_preview = 100 <= channel < 100 + len(self.cameras)
                        if (not is_preview and (channel >= 4 or size > 65535)) or size > 2_000_000:
                            raise ValueError("Invalid relay packet")
                        packet = read_exact(self.process.stdout, size)
                        if is_preview:
                            if size < 10 or packet[8:10] != b"\xff\xd8":
                                raise ValueError("Invalid preview packet")
                            stamp = struct.unpack("!d", packet[:8])[0]
                            with self.preview_lock:
                                self.previews[self.cameras[channel - 100]] = (
                                    bytes(packet[8:]),
                                    f'"{stamp}"',
                                    stamp,
                                )
                            continue
                        sender.sendto(
                            packet,
                            ("127.0.0.1", cfg.get("insight_local_video_port_base", 9000) + channel),
                        )
                        self.status = {
                            "mode": "ssh",
                            "connected": True,
                            "packets": self.status["packets"] + 1,
                        }
                except (OSError, EOFError, ValueError) as exc:
                    self.status = {**self.status, "connected": False, "error": str(exc)}
                finally:
                    if self.process:
                        self.process.terminate()
                        try:
                            self.process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            self.process.kill()
                            self.process.wait()
                        self.process.stdout.close()
                self.closed.wait(2)

    def close(self):
        self.closed.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
        self.thread.join(timeout=8)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receive", action="store_true", required=True)
    parser.add_argument("--base", type=int, required=True)
    parser.add_argument("--channels", type=int, default=4)
    args = parser.parse_args()
    receive(args.base, args.channels)


if __name__ == "__main__":
    main()
