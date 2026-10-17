"""Local viewer and annotator for aligned EXO recordings."""

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, quote, unquote, urlsplit


CAMERAS = {"body": "camera_body", "external": "camera_env", "aria": "camera_aria"}
SUBJECT = re.compile(r"s\d+")
LABEL_SCHEMA = json.loads(Path(__file__).with_name("label_schema.json").read_text())
TASKS = {task["trial_id"]: task for task in LABEL_SCHEMA["tasks"]}
MAX_LABEL_BYTES = 4 * 1024 * 1024


def label_choices(sequence):
    trial, separator, suffix = sequence.rpartition("_")
    task = TASKS.get(trial) if separator and suffix.isdigit() else None
    if not task:
        return None, []
    choices = []
    seen = set()

    def add(level, label, definition="", phase=None):
        if (level, label, phase) not in seen:
            choices.append({"level": level, "label": label, "definition": definition, "phase": phase})
            seen.add((level, label, phase))

    def add_actions(items):
        for item in items:
            if "action" in item:
                add("low", item["action"], definitions.get(item["action"], ""), item.get("phase"))
            add_actions(item.get("group", []))

    definitions = {entry["label"]: entry["definition"] for entry in LABEL_SCHEMA["atomic_actions"]}
    for high in task["high_level"]:
        add("high", high["label"])
        for mid in high.get("mid_level", []):
            add("mid", mid["label"])
            add_actions(mid.get("atomic_sequence", []))
    for auxiliary in LABEL_SCHEMA["auxiliary_labels"]:
        add("aux", auxiliary["label"], auxiliary["definition"])
    return task, choices


def label_path(server, subject, sequence):
    if sequence not in server.recordings.get(subject, {}):
        raise LookupError("Sequenza non trovata")
    subject_dir = server.root / subject
    directory = subject_dir / "label"
    path = directory / f"{sequence}.json"
    if (subject_dir.is_symlink() or directory.is_symlink() or path.is_symlink()
            or not directory.resolve().is_relative_to(subject_dir.resolve())):
        raise ValueError("Percorso delle label non sicuro")
    return path


def label_revision(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def validate_labels(document, subject, sequence, files):
    task, choices = label_choices(sequence)
    if not isinstance(document, dict) or set(document) != {"schema_version", "subject", "sequence", "fps", "annotations"}:
        raise ValueError("Formato del documento di label non valido")
    if (document["schema_version"] != LABEL_SCHEMA["schema_version"] or document["subject"] != subject
            or document["sequence"] != sequence or type(document["fps"]) is not int or document["fps"] != 30):
        raise ValueError("Schema, soggetto, sequenza o frequenza non corrispondono")
    records = document["annotations"]
    if not isinstance(records, list) or len(records) > 10000 or (not task and records):
        raise ValueError("Annotazioni non valide per questa sequenza")
    allowed = {(choice["level"], choice["label"]) for choice in choices}
    phases = {(choice["level"], choice["label"], choice["phase"]) for choice in choices}
    max_frame = min(probe(path)["frames"] for path in files.values())
    fields = {"id", "level", "label", "phase", "start_frame", "end_frame", "t_start", "t_end", "parents",
              "rep", "side", "direction", "external_load", "contact_points", "target", "note"}
    ids = {}
    for record in records:
        if isinstance(record, dict) and "phase" not in record:
            record["phase"] = None
        if not isinstance(record, dict) or set(record) != fields:
            raise ValueError("Campi di annotazione non validi")
        identifier, start, end = record["id"], record["start_frame"], record["end_frame"]
        if (type(identifier) is not int or identifier < 1 or identifier in ids
                or type(start) is not int or type(end) is not int or not 1 <= start <= end <= max_frame):
            raise ValueError("ID o intervallo di frame non valido")
        if (record["level"], record["label"]) not in allowed:
            raise ValueError("Label non prevista per questa sequenza")
        if record["phase"] is not None and (record["level"], record["label"], record["phase"]) not in phases:
            raise ValueError("Fase non prevista per questa label")
        if record["label"] == "sync" and (record["level"] != "aux" or start != end):
            raise ValueError("sync deve indicare un solo frame")
        t_start = (start - 1) / 30
        t_end = t_start if record["label"] == "sync" and record["level"] == "aux" else end / 30
        if (type(record["t_start"]) not in (int, float) or type(record["t_end"]) not in (int, float)
                or not math.isfinite(record["t_start"]) or not math.isfinite(record["t_end"])
                or abs(record["t_start"] - t_start) > 0.000001 or abs(record["t_end"] - t_end) > 0.000001):
            raise ValueError("I tempi non corrispondono ai frame")
        if (not isinstance(record["parents"], list) or any(type(parent) is not int for parent in record["parents"])
                or len(set(record["parents"])) != len(record["parents"])):
            raise ValueError("Genitori non validi")
        if record["rep"] is not None and (type(record["rep"]) is not int or record["rep"] < 1):
            raise ValueError("Numero di ripetizione non valido")
        for name in ("side", "direction", "external_load", "contact_points"):
            if record[name] is not None and record[name] not in LABEL_SCHEMA["attribute_values"][name]:
                raise ValueError(f"Valore non valido per {name}")
        for name, limit in (("target", 200), ("note", 5000)):
            if record[name] is not None and (not isinstance(record[name], str) or len(record[name]) > limit):
                raise ValueError(f"Valore non valido per {name}")
        if (record["level"] in ("high", "aux") and (record["parents"] or record["rep"] is not None)
                or record["level"] != "low" and any(record[name] is not None for name in ("external_load", "contact_points", "target"))
                or task and "side" not in task["annotated_attributes"] and record["side"] is not None
                or task and "direction" not in task["annotated_attributes"] and record["direction"] is not None):
            raise ValueError("Attributi non consentiti per questa label")
        ids[identifier] = record
    for record in records:
        parent_level = {"high": None, "mid": "high", "low": "mid", "aux": None}[record["level"]]
        if any(parent not in ids or ids[parent]["level"] != parent_level for parent in record["parents"]):
            raise ValueError("Una label fa riferimento a un genitore non valido")
    return document


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

    def do_POST(self):
        parsed = urlsplit(self.path)
        if parsed.path != "/api/labels":
            self.send_json({"error": "Percorso non trovato"}, HTTPStatus.NOT_FOUND)
            return
        query = parse_qs(parsed.query)
        subject, sequence = query.get("subject", [""])[0], query.get("sequence", [""])[0]
        files = self.server.recordings.get(subject, {}).get(sequence)
        if not files:
            self.send_json({"error": "Sequenza non trovata"}, HTTPStatus.NOT_FOUND)
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self.send_json({"error": "Content-Type deve essere application/json"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        try:
            size = int(self.headers.get("Content-Length", ""))
            if not 0 < size <= MAX_LABEL_BYTES:
                raise ValueError("Dimensione del JSON non valida")
            request = json.loads(self.rfile.read(size))
            if not isinstance(request, dict) or set(request) != {"revision", "data"}:
                raise ValueError("Richiesta di salvataggio non valida")
            if request["revision"] is not None and not isinstance(request["revision"], str):
                raise ValueError("Revisione non valida")
            document = validate_labels(request["data"], subject, sequence, files)
            path = label_path(self.server, subject, sequence)
            payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            with self.server.label_lock:
                current = path.read_bytes() if path.exists() else None
                if label_revision(current) != request["revision"]:
                    self.send_json({"error": "Il file è cambiato dopo l'apertura. Ricarica la sequenza prima di salvare."},
                                   HTTPStatus.CONFLICT)
                    return
                path.parent.mkdir(exist_ok=True)
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{sequence}.", suffix=".tmp",
                                                     delete=False) as file:
                        temporary = Path(file.name)
                        file.write(payload)
                        file.flush()
                        os.fsync(file.fileno())
                    os.replace(temporary, path)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            self.send_json({"revision": label_revision(payload)})
        except (ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError) as exc:
            self.send_json({"error": f"Salvataggio non riuscito: {exc}"}, HTTPStatus.BAD_REQUEST)
        except (OSError, subprocess.SubprocessError) as exc:
            self.send_json({"error": f"Salvataggio non riuscito: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

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
        if parsed.path in ("/api/schema", "/api/labels"):
            query = parse_qs(parsed.query)
            subject, sequence = query.get("subject", [""])[0], query.get("sequence", [""])[0]
            if sequence not in self.server.recordings.get(subject, {}):
                self.send_json({"error": "Sequenza non trovata"}, HTTPStatus.NOT_FOUND)
                return
            if parsed.path == "/api/schema":
                task, choices = label_choices(sequence)
                self.send_json({"schema_version": LABEL_SCHEMA["schema_version"],
                                "task": task["trial_id"] if task else None,
                                "annotated_attributes": task["annotated_attributes"] if task else [],
                                "choices": choices, "attribute_values": LABEL_SCHEMA["attribute_values"]})
                return
            try:
                path = label_path(self.server, subject, sequence)
                content = path.read_bytes() if path.exists() else None
                if content is not None and len(content) > MAX_LABEL_BYTES:
                    raise ValueError("File di label troppo grande")
                document = (validate_labels(json.loads(content), subject, sequence,
                                            self.server.recordings[subject][sequence]) if content is not None
                            else {"schema_version": LABEL_SCHEMA["schema_version"], "subject": subject,
                                  "sequence": sequence, "fps": 30, "annotations": []})
                self.send_json({"data": document, "revision": label_revision(content)})
            except (OSError, ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError,
                    subprocess.SubprocessError) as exc:
                self.send_json({"error": f"Label non caricabili: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
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
    server.label_lock = Lock()
    print(f"EXO viewer: http://{args.host}:{server.server_port} ({sum(map(len, recordings.values()))} sequenze)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
