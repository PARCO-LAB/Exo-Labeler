"""Local, read-only viewer for aligned EXO recordings."""

import argparse
import json
import re
import subprocess
import tempfile
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit


CAMERAS = {"body": "camera_body", "external": "camera_env", "aria": "camera_aria"}
SUBJECT = re.compile(r"s\d+")


def discover(root):
    """Return complete RGB triplets, keyed by subject and sequence."""
    recordings = {}
    for subject in sorted(root.iterdir(), key=lambda p: p.name):
        if not SUBJECT.fullmatch(subject.name) or not subject.is_dir():
            continue
        by_camera = {}
        for camera, folder in CAMERAS.items():
            directory = subject / "aligned" / folder
            if not directory.is_dir():
                by_camera[camera] = {}
                continue
            prefix = subject.name + "_"
            by_camera[camera] = {
                file.name[len(prefix) : -len("_rgb.mp4")]: file
                for file in directory.iterdir()
                if file.name.startswith(prefix)
                and file.name.endswith("_rgb.mp4")
                and file.is_file()
                and not file.is_symlink()
            }
        shared = set.intersection(*(set(files) for files in by_camera.values()))
        if shared:
            recordings[subject.name] = {
                sequence: {camera: files[sequence] for camera, files in by_camera.items()}
                for sequence in sorted(shared)
            }
    return recordings


@lru_cache(maxsize=128)
def probe(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name,r_frame_rate,nb_frames,start_time:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    numerator, denominator = map(int, stream["r_frame_rate"].split("/"))
    return {
        "codec": stream["codec_name"],
        "fps": numerator / denominator,
        "frames": int(stream["nb_frames"]),
        "duration": float(data["format"]["duration"]),
        "start": float(stream.get("start_time", 0)),
    }


@lru_cache(maxsize=12)
def proxy(path, expected_frames):
    """Build a small, all-intra MP4 for fast browser playback and frame seeking."""
    with tempfile.TemporaryDirectory(prefix="exo-viewer-") as directory:
        output = Path(directory) / "video.mp4"
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(path), "-vf", "scale=min(1280\\,iw):-2,setpts=N/(30*TB)",
             "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-g", "1",
             "-bf", "0", "-pix_fmt", "yuv420p", "-vsync", "0",
             "-movflags", "+faststart", str(output)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode:
            raise ValueError(result.stderr.strip() or "FFmpeg non ha creato il video")
        metadata = probe(output)
        if (metadata["frames"] != expected_frames or abs(metadata["fps"] - 30) > 0.01
                or abs(metadata["start"]) > 0.001 or abs(metadata["duration"] - expected_frames / 30) > 0.02):
            raise ValueError(f"Il proxy di {path.name} non conserva gli indici dei frame a 30 Hz")
        packets = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "packet=pts_time", "-of", "csv=p=0", str(output)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        timestamps = [float(line) for line in packets.stdout.splitlines()]
        if len(timestamps) != expected_frames or any(
                abs(timestamp - frame / 30) > 0.001 for frame, timestamp in enumerate(timestamps)):
            raise ValueError(f"Il proxy di {path.name} ha timestamp dei frame non regolari")
        return output.read_bytes()


class ViewerHandler(BaseHTTPRequestHandler):
    def send_json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.handle_request()

    def do_GET(self):
        self.handle_request()

    def handle_request(self):
        parsed = urlsplit(self.path)
        if parsed.path == "/":
            body = (Path(__file__).with_name("index.html")).read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return
        if parsed.path == "/api/catalog":
            self.send_json({subject: sorted(sequences) for subject, sequences in self.server.recordings.items()})
            return
        if parsed.path == "/api/sequence":
            query = parse_qs(parsed.query)
            subject = query.get("subject", [""])[0]
            sequence = query.get("sequence", [""])[0]
            files = self.server.recordings.get(subject, {}).get(sequence)
            if not files:
                self.send_json({"error": "Sequenza non trovata"}, HTTPStatus.NOT_FOUND)
                return
            try:
                metadata = {camera: probe(file) for camera, file in files.items()}
                for camera, file in files.items():
                    proxy(file, metadata[camera]["frames"])
            except (OSError, MemoryError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as exc:
                self.send_json({"error": f"Impossibile caricare la sequenza: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self.send_json({
                "videos": {camera: "/media/" + "/".join(map(quote, (subject, sequence, camera))) + "?proxy=1"
                           for camera in CAMERAS},
                "metadata": metadata,
            })
            return
        if parsed.path.startswith("/media/"):
            parts = [unquote(part) for part in parsed.path[len("/media/"):].split("/")]
            if len(parts) != 3:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            subject, sequence, camera = parts
            file = self.server.recordings.get(subject, {}).get(sequence, {}).get(camera)
            if not file or not file.resolve().is_relative_to(self.server.root):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_media(file, parse_qs(parsed.query).get("proxy") == ["1"])
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def send_media(self, path, use_proxy=False):
        try:
            data = proxy(path, probe(path)["frames"]) if use_proxy else None
        except (OSError, MemoryError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Proxy non disponibile")
            return
        try:
            size = len(data) if data is not None else path.stat().st_size
            byte_range = self.headers.get("Range")
            if byte_range:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", byte_range)
                if not match or not any(match.groups()):
                    raise ValueError("Invalid range")
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                else:
                    length = int(last)
                    if not length:
                        raise ValueError("Invalid range")
                    start, end = max(size - length, 0), size - 1
                if start >= size or end < start:
                    raise ValueError("Invalid range")
                status = HTTPStatus.PARTIAL_CONTENT
            else:
                start, end, status = 0, size - 1, HTTPStatus.OK
        except ValueError:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(end - start + 1))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            if data is not None:
                view = memoryview(data)
                for offset in range(start, end + 1, 1024 * 1024):
                    self.wfile.write(view[offset:min(offset + 1024 * 1024, end + 1)])
            else:
                with path.open("rb") as video:
                    video.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        block = video.read(min(1024 * 1024, remaining))
                        if not block:
                            break
                        self.wfile.write(block)
                        remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / "nas/MAEVE/EXO_RECORD")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"Cartella non accessibile: {root}")
    # shortcut: the catalog is fixed at startup; restart the server when new recordings arrive.
    recordings = discover(root)
    server = ThreadingHTTPServer((args.host, args.port), ViewerHandler)
    server.root = root
    server.recordings = recordings
    print(f"EXO viewer: http://{args.host}:{server.server_port} ({sum(map(len, recordings.values()))} sequenze)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
