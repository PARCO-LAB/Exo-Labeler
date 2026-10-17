import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import ViewerHandler, discover, label_choices, proxy


class ViewerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        body = cls.root / "s5/aligned/camera_body"
        body.mkdir(parents=True)
        sample = body / "s5_curl_0_rgb.mp4"
        subprocess.run([
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x48:rate=30",
            "-frames:v", "120", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(sample),
        ], check=True)
        shutil.copyfile(sample, body / "s5_curl_0_depth.mp4")
        shutil.copyfile(sample, body / "s5_curl_1_rgb.mp4")
        for camera in ("camera_env", "camera_aria"):
            directory = cls.root / "s5/aligned" / camera
            directory.mkdir()
            shutil.copyfile(sample, directory / sample.name)
            shutil.copyfile(sample, directory / "s5_curl_1_rgb.mp4")
            shutil.copyfile(sample, directory / "s5_static_0_rgb.mp4")
        shutil.copyfile(sample, body / "s5_static_0_rgb.mp4")
        incomplete = cls.root / "s6/aligned/camera_body"
        incomplete.mkdir(parents=True)
        shutil.copyfile(sample, incomplete / "s6_curl_0_rgb.mp4")
        (cls.root / "s0/aligned/curl_0").mkdir(parents=True)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ViewerHandler)
        cls.server.root = cls.root
        cls.server.recordings = discover(cls.root)
        cls.server.label_lock = threading.Lock()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, path, headers=None, method="GET", data=None):
        payload = json.dumps(data).encode() if data is not None else None
        return urlopen(Request(self.base + path, data=payload,
                               headers={"Content-Type": "application/json", **(headers or {})}, method=method))

    def test_discovery_and_metadata(self):
        with self.request("/api/catalog") as response:
            self.assertEqual(json.load(response), {"s5": ["curl_0", "curl_1", "static_0"]})
        with self.request("/api/sequence?subject=s5&sequence=curl_0") as response:
            data = json.load(response)
        self.assertEqual(set(data["videos"]), {"body", "external", "aria"})
        self.assertEqual([data["metadata"][camera]["frames"] for camera in data["videos"]], [120] * 3)
        self.assertEqual(data["metadata"]["body"]["fps"], 30)
        with self.assertRaises(HTTPError) as error:
            self.request("/api/sequence?subject=s6&sequence=curl_0")
        self.assertEqual(error.exception.code, 404)

    def test_annotation_schema_and_save(self):
        task, choices = label_choices("drilling_vertical_0")
        self.assertEqual(task["trial_id"], "drilling_vertical")
        self.assertIn(("low", "pointing"), {(choice["level"], choice["label"]) for choice in choices})
        with self.request("/api/schema?subject=s5&sequence=static_0") as response:
            self.assertEqual(json.load(response)["choices"], [])
        with self.request("/api/schema?subject=s5&sequence=curl_0") as response:
            self.assertIn("curl", [choice["label"] for choice in json.load(response)["choices"]])
        url = "/api/labels?subject=s5&sequence=curl_0"
        with self.request(url) as response:
            current = json.load(response)
        self.assertIsNone(current["revision"])
        self.assertEqual(current["data"]["annotations"], [])
        base = {"parents": [], "rep": None, "side": None, "direction": None, "phase": None,
                "external_load": None, "contact_points": None, "target": None, "note": None}
        document = current["data"]
        document["annotations"] = [
            {**base, "id": 1, "level": "high", "label": "curl", "start_frame": 1,
             "end_frame": 30, "t_start": 0, "t_end": 1},
            {**base, "id": 2, "level": "aux", "label": "sync", "start_frame": 10,
             "end_frame": 10, "t_start": 9 / 30, "t_end": 9 / 30},
        ]
        with self.request(url, method="POST", data={"revision": None, "data": document}) as response:
            saved = json.load(response)
        path = self.root / "s5/label/curl_0.json"
        self.assertEqual(json.loads(path.read_text()), document)
        with self.request(url) as response:
            reopened = json.load(response)
        self.assertEqual(reopened["data"], document)
        self.assertEqual(reopened["revision"], saved["revision"])
        with self.assertRaises(HTTPError) as error:
            self.request(url, method="POST", data={"revision": None, "data": document})
        self.assertEqual(error.exception.code, 409)
        bad = json.loads(json.dumps(document))
        bad["annotations"][1]["end_frame"] = 11
        with self.assertRaises(HTTPError) as error:
            self.request(url, method="POST", data={"revision": saved["revision"], "data": bad})
        self.assertEqual(error.exception.code, 400)
        bad["annotations"][1]["end_frame"] = 10
        bad["annotations"][0]["end_frame"] = 121
        with self.assertRaises(HTTPError) as error:
            self.request(url, method="POST", data={"revision": saved["revision"], "data": bad})
        self.assertEqual(error.exception.code, 400)
        bad["annotations"][0]["end_frame"] = 30
        bad["annotations"][0]["t_start"] = float("nan")
        with self.assertRaises(HTTPError) as error:
            self.request(url, method="POST", data={"revision": saved["revision"], "data": bad})
        self.assertEqual(error.exception.code, 400)
        with self.assertRaises(HTTPError) as error:
            self.request(url, headers={"Content-Type": "text/plain"}, method="POST",
                         data={"revision": saved["revision"], "data": document})
        self.assertEqual(error.exception.code, 415)
        static = {**document, "sequence": "static_0"}
        with self.assertRaises(HTTPError) as error:
            self.request("/api/labels?subject=s5&sequence=static_0", method="POST",
                         data={"revision": None, "data": static})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(json.loads(path.read_text()), document)
        curl_choices = label_choices("curl_0")[1]
        self.assertEqual({item["phase"] for item in curl_choices if item["label"] == "move"},
                         {"flexion", "extension"})
        legacy = {**document, "sequence": "curl_1", "annotations": [{**document["annotations"][0]}]}
        legacy["annotations"][0].pop("phase")
        (self.root / "s5/label/curl_1.json").write_text(json.dumps(legacy))
        with self.request("/api/labels?subject=s5&sequence=curl_1") as response:
            upgraded = json.load(response)
        self.assertIsNone(upgraded["data"]["annotations"][0]["phase"])
        self.assertIsNotNone(upgraded["revision"])

    def test_media_ranges_and_path_validation(self):
        path = "/media/s5/curl_0/body"
        with self.request(path, {"Range": "bytes=0-15"}) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers["Content-Range"].split("/")[0], "bytes 0-15")
            self.assertEqual(len(response.read()), 16)
        with self.request(path, {"Range": "bytes=-8"}) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(len(response.read()), 8)
        with self.request(path, method="HEAD") as response:
            self.assertEqual(response.status, 200)
            self.assertGreater(int(response.headers["Content-Length"]), 16)
        with self.assertRaises(HTTPError) as error:
            self.request(path, {"Range": "bytes=999999999-"})
        self.assertEqual(error.exception.code, 416)
        with self.assertRaises(HTTPError) as error:
            self.request("/media/s5/curl_0/depth")
        self.assertEqual(error.exception.code, 404)

    def test_selected_sequence_is_served_from_memory(self):
        path = self.server.recordings["s5"]["curl_0"]["aria"]
        original = path.read_bytes()
        with self.request("/api/sequence?subject=s5&sequence=curl_0") as response:
            self.assertEqual(response.status, 200)
        cached = proxy(path, 120)
        self.assertNotEqual(cached, original)
        with tempfile.TemporaryDirectory() as directory:
            proxy_file = Path(directory) / "proxy.mp4"
            proxy_file.write_bytes(cached)
            result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                     "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(proxy_file)],
                                    capture_output=True, text=True, check=True)
            packets = [line.split(",") for line in result.stdout.splitlines()]
            timestamps = [float(packet[0]) for packet in packets]
            self.assertEqual(len(timestamps), 120)
            self.assertTrue(all(abs(timestamp - index / 30) < 0.001
                                for index, timestamp in enumerate(timestamps)))
            self.assertTrue(all("K" in packet[1] for packet in packets))
        try:
            path.write_bytes(bytes(len(original)))
            with self.request("/media/s5/curl_0/aria?proxy=1", {"Range": "bytes=10-39"}) as response:
                self.assertEqual(response.read(), cached[10:40])
        finally:
            path.write_bytes(original)
        with self.request("/api/sequence?subject=s5&sequence=curl_1") as response:
            self.assertEqual(response.status, 200)
        with self.request("/media/s5/curl_0/aria?proxy=1", {"Range": "bytes=10-39"}) as response:
            self.assertEqual(response.read(), cached[10:40])


if __name__ == "__main__":
    unittest.main()
