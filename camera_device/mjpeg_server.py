"""HTTP MJPEG streamer for the simulated camera (ADD-ONLY; does not touch MQTT)."""

from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import stream_clean_to_json as stream

BOUNDARY = "parkingcamera"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8081
STREAM_SECRET = os.environ.get("CAMERA_STREAM_SECRET", "").strip()


def optional_bool(value, default=False):
    if value in {None, ""}:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_query(path: str) -> dict[str, str]:
    if "?" not in path:
        return {}
    _, _, query = path.partition("?")
    params: dict[str, str] = {}
    for part in query.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        params[key] = value
    return params


def _authorized(handler: BaseHTTPRequestHandler) -> bool:
    """Require shared secret when CAMERA_STREAM_SECRET is set."""
    if not STREAM_SECRET:
        return True
    header_key = (handler.headers.get("X-API-Key") or "").strip()
    query_token = _parse_query(handler.path).get("token", "").strip()
    return header_key == STREAM_SECRET or query_token == STREAM_SECRET


def _send_unauthorized(handler: BaseHTTPRequestHandler):
    body = b'{"detail":"Invalid or missing camera stream secret"}\n'
    handler.send_response(401)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("WWW-Authenticate", 'Header realm="camera-mjpeg"')
    handler.end_headers()
    handler.wfile.write(body)


class LatestFrame:
    def __init__(self):
        self._condition = threading.Condition()
        self._jpeg: bytes | None = None
        self._sequence = 0
        self._source_frame_id: str | None = None

    def publish(self, jpeg: bytes, source_frame_id: str):
        with self._condition:
            self._jpeg = jpeg
            self._source_frame_id = source_frame_id
            self._sequence += 1
            self._condition.notify_all()

    def wait_for_next(self, last_sequence: int, timeout: float = 30.0):
        with self._condition:
            ok = self._condition.wait_for(
                lambda: self._sequence > last_sequence and self._jpeg is not None,
                timeout=timeout,
            )
            if not ok:
                return last_sequence, None, None
            return self._sequence, self._jpeg, self._source_frame_id


latest_frame = LatestFrame()


def frame_loop(
    input_dir: Path,
    interval_seconds: float,
    loop_dataset: bool,
    start_delay_seconds: float,
):
    if start_delay_seconds > 0:
        print(f"MJPEG waiting {start_delay_seconds} seconds before streaming", flush=True)
        time.sleep(start_delay_seconds)

    cycle = 1
    while True:
        published = 0
        for _split, image_file, label_file, frame_id in stream.iter_frames(input_dir):
            if not image_file.is_file():
                continue
            # Labels are required for MQTT path; video stream still serves the JPEG.
            if not label_file.exists():
                print(f"MJPEG skipped {image_file}: missing label {label_file}", flush=True)
                continue

            jpeg = image_file.read_bytes()
            latest_frame.publish(jpeg, frame_id)
            published += 1
            print(
                f"MJPEG frame ready cycle={cycle} source_frame_id={frame_id} bytes={len(jpeg)}",
                flush=True,
            )
            if interval_seconds > 0:
                time.sleep(interval_seconds)

        if published == 0:
            print(f"MJPEG found no frames in {input_dir}; retrying in 5s", flush=True)
            time.sleep(5)
            continue
        if not loop_dataset:
            print("MJPEG finished one pass; keeping last frame available", flush=True)
            return
        print(f"MJPEG completed cycle={cycle}; restarting dataset", flush=True)
        cycle += 1


class MjpegHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        print(f"MJPEG HTTP {self.address_string()} {format % args}", flush=True)

    def do_GET(self):
        path_only = self.path.split("?", 1)[0]

        if path_only in {"/health", "/"}:
            auth_flag = "true" if STREAM_SECRET else "false"
            body = (
                f'{{"ok":true,"service":"camera-mjpeg","auth_enabled":{auth_flag}}}\n'
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path_only != "/stream/mjpeg":
            self.send_error(404, "Not Found")
            return

        if not _authorized(self):
            _send_unauthorized(self)
            return

        self.send_response(200)
        self.send_header(
            "Content-Type",
            f"multipart/x-mixed-replace; boundary={BOUNDARY}",
        )
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        last_sequence = 0
        try:
            while True:
                sequence, jpeg, source_frame_id = latest_frame.wait_for_next(last_sequence)
                if jpeg is None:
                    continue
                last_sequence = sequence
                header = (
                    f"--{BOUNDARY}\r\n"
                    f"Content-Type: image/jpeg\r\n"
                    f"Content-Length: {len(jpeg)}\r\n"
                    f"X-Source-Frame-Id: {source_frame_id or ''}\r\n"
                    f"\r\n"
                ).encode("utf-8")
                self.wfile.write(header)
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return


def start_http_server_background(
    host: str | None = None,
    port: int | None = None,
) -> ThreadingHTTPServer:
    """Serve /stream/mjpeg from latest_frame (no independent frame loop)."""
    bind_host = host or os.environ.get("CAMERA_MJPEG_HOST", DEFAULT_HOST)
    bind_port = port if port is not None else int(os.environ.get("CAMERA_MJPEG_PORT", str(DEFAULT_PORT)))
    server = ThreadingHTTPServer((bind_host, bind_port), MjpegHandler)
    thread = threading.Thread(target=server.serve_forever, name="mjpeg-http", daemon=True)
    thread.start()
    auth_state = "enabled" if STREAM_SECRET else "disabled"
    print(
        f"MJPEG listening on http://{bind_host}:{bind_port}/stream/mjpeg auth={auth_state}",
        flush=True,
    )
    return server


def main():
    """Standalone MJPEG-only mode (own frame loop). Prefer camera_to_mqtt for synced MQTT+MJPEG."""
    input_dir = stream.resolve_input_dir(
        Path(os.environ.get("CAMERA_INPUT", "data/content/dataset"))
    )
    interval = float(
        os.environ.get(
            "CAMERA_STREAM_INTERVAL",
            os.environ.get("CAMERA_PUBLISH_INTERVAL", "1"),
        )
    )
    loop_dataset = optional_bool(os.environ.get("CAMERA_LOOP_DATASET"), True)
    start_delay = float(os.environ.get("CAMERA_START_DELAY", "0"))

    worker = threading.Thread(
        target=frame_loop,
        kwargs={
            "input_dir": input_dir,
            "interval_seconds": interval,
            "loop_dataset": loop_dataset,
            "start_delay_seconds": start_delay,
        },
        name="mjpeg-frame-loop",
        daemon=True,
    )
    worker.start()
    server = start_http_server_background()
    try:
        while worker.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
