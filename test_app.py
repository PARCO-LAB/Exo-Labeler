import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from app import LABEL_SCHEMA, ViewerHandler, discover, label_choices, proxy, validate_labels


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
        shutil.copyfile(sample, body / "s5_throwing_0_rgb.mp4")
        shutil.copyfile(sample, body / "s5_pouring_light_0_rgb.mp4")
        shutil.copyfile(sample, body / "s5_drilling_vertical_0_rgb.mp4")
        shutil.copyfile(sample, body / "s5_infinity_tracing_0_rgb.mp4")
        for camera in ("camera_env", "camera_aria"):
            directory = cls.root / "s5/aligned" / camera
            directory.mkdir()
            shutil.copyfile(sample, directory / sample.name)
            shutil.copyfile(sample, directory / "s5_curl_1_rgb.mp4")
            shutil.copyfile(sample, directory / "s5_throwing_0_rgb.mp4")
            shutil.copyfile(sample, directory / "s5_pouring_light_0_rgb.mp4")
            shutil.copyfile(sample, directory / "s5_drilling_vertical_0_rgb.mp4")
            shutil.copyfile(sample, directory / "s5_infinity_tracing_0_rgb.mp4")
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
        cls.server.leases = {}
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def setUp(self):
        self.server.leases.clear()
        self.lease_tokens = {}

    def request(self, path, headers=None, method="GET", data=None, lease=True):
        headers = headers or {}
        if lease and method == "POST" and urlsplit(path).path in ("/api/labels", "/api/calibration"):
            query = parse_qs(urlsplit(path).query)
            key = (query["subject"][0], query["sequence"][0])
            if key not in self.lease_tokens:
                with self.request(f"/api/lease?subject={key[0]}&sequence={key[1]}",
                                  method="POST", data={"token": None}) as response:
                    self.lease_tokens[key] = json.load(response)["token"]
            headers = {"X-Sequence-Lease": self.lease_tokens[key], **headers}
        payload = json.dumps(data).encode() if data is not None else None
        return urlopen(Request(self.base + path, data=payload,
                               headers={"Content-Type": "application/json", **headers}, method=method))

    def test_sequence_lease(self):
        lease_url = "/api/lease?subject=s5&sequence=curl_0"
        labels_url = "/api/labels?subject=s5&sequence=curl_0"
        calibration_url = "/api/calibration?subject=s5&sequence=curl_0"
        def compete():
            try:
                with self.request(lease_url, method="POST", data={"token": None}) as response:
                    return response.status, json.load(response)["token"]
            except HTTPError as error:
                return error.code, None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: compete(), range(2)))
        self.assertEqual(sorted(status for status, _ in results), [200, 423])
        first = next(token for status, token in results if status == 200)
        with self.request("/api/lease?subject=s5&sequence=throwing_0", method="POST",
                          data={"token": None}) as response:
            self.assertEqual(response.status, 200)
        with self.request(lease_url, method="POST", data={"token": first}) as response:
            self.assertEqual(json.load(response)["token"], first)
        with self.request("/api/lease/release?subject=s5&sequence=curl_0",
                          method="POST", data={"token": "wrong"}) as response:
            self.assertFalse(json.load(response)["released"])
        for url in (labels_url, calibration_url):
            with self.request(url) as response:
                current = json.load(response)
            for token in (None, "wrong"):
                headers = {"X-Sequence-Lease": token} if token else {}
                with self.assertRaises(HTTPError) as error:
                    self.request(url, headers=headers, method="POST", lease=False,
                                 data={"revision": current["revision"], "data": current["data"]})
                self.assertEqual(error.exception.code, 423)
            with self.request(url, headers={"X-Sequence-Lease": first}, method="POST", lease=False,
                              data={"revision": current["revision"], "data": current["data"]}) as response:
                self.assertTrue(json.load(response)["revision"])
        with self.request("/api/lease/release?subject=s5&sequence=curl_0", method="POST",
                          data={"token": first}) as response:
            self.assertTrue(json.load(response)["released"])
        with self.request(lease_url, method="POST", data={"token": None}) as response:
            second = json.load(response)["token"]
        self.assertNotEqual(first, second)
        with self.request(labels_url) as response:
            current = json.load(response)
        with self.assertRaises(HTTPError) as error:
            self.request(labels_url, headers={"X-Sequence-Lease": first}, method="POST", lease=False,
                         data={"revision": current["revision"], "data": current["data"]})
        self.assertEqual(error.exception.code, 423)
        self.server.leases[("s5", "curl_0")]["expires"] = time.monotonic() - 1
        with self.assertRaises(HTTPError) as error:
            self.request(labels_url, headers={"X-Sequence-Lease": second}, method="POST", lease=False,
                         data={"revision": current["revision"], "data": current["data"]})
        self.assertEqual(error.exception.code, 423)
        with self.request(lease_url, method="POST", data={"token": None}) as response:
            self.assertNotEqual(json.load(response)["token"], second)

    def test_discovery_and_metadata(self):
        with self.request("/api/catalog") as response:
            self.assertEqual(json.load(response), {"s5": ["curl_0", "curl_1", "drilling_vertical_0", "infinity_tracing_0", "pouring_light_0", "static_0", "throwing_0"]})
        with self.request("/api/sequence?subject=s5&sequence=curl_0") as response:
            data = json.load(response)
        self.assertEqual(set(data["videos"]), {"body", "external", "aria"})
        self.assertEqual([data["metadata"][camera]["frames"] for camera in data["videos"]], [120] * 3)
        self.assertEqual(data["metadata"]["body"]["fps"], 30)
        self.assertEqual((data["metadata"]["body"]["width"], data["metadata"]["body"]["height"]), (64, 48))
        with self.assertRaises(HTTPError) as error:
            self.request("/api/sequence?subject=s6&sequence=curl_0")
        self.assertEqual(error.exception.code, 404)

    def test_catalog_refreshes_after_new_recordings(self):
        subject = self.root / "s7"
        try:
            for camera in ("camera_body", "camera_env", "camera_aria"):
                directory = subject / "aligned" / camera
                directory.mkdir(parents=True)
                shutil.copyfile(self.root / "s5/aligned/camera_body/s5_curl_0_rgb.mp4",
                                directory / "s7_curl_0_rgb.mp4")
                with self.request("/api/catalog") as response:
                    catalog = json.load(response)
                self.assertEqual("s7" in catalog, camera == "camera_aria")
            self.assertEqual(catalog["s7"], ["curl_0"])
            with self.request("/api/schema?subject=s7&sequence=curl_0") as response:
                self.assertEqual(response.status, 200)
        finally:
            shutil.rmtree(subject)
            self.server.recordings = discover(self.root)

    def test_camera_calibration_save_and_validation(self):
        url = "/api/calibration?subject=s5&sequence=curl_0"
        with self.request(url) as response:
            current = json.load(response)
        self.assertIsNone(current["revision"])
        self.assertEqual(current["data"], {"subject": "s5", "sequence": "curl_0", "fps": 30,
                                           "coordinate_space": "source_pixels", "points": [], "matrix": []})
        document = {**current["data"], "points": [
            {"camera": "body", "frame": 1, "label": "Rhigh", "x": 63, "y": 47},
            {"camera": "external", "frame": 120, "label": "ball", "x": 0, "y": 0},
            {"camera": "aria", "frame": 20, "label": "Llow", "x": 12, "y": 25},
        ], "matrix": [{"camera": "body", "frame": 1,
                       "vertices": [{"x": 0, "y": 0}, {"x": 63, "y": 0}, {"x": 63, "y": 47}, {"x": 0, "y": 47}]}]}
        with self.request(url, method="POST", data={"revision": None, "data": document}) as response:
            saved = json.load(response)
        path = self.root / "s5/camera_calibration/curl_0.json"
        self.assertEqual(json.loads(path.read_text()), document)
        with self.request(url) as response:
            reopened = json.load(response)
        self.assertEqual(reopened, {"data": document, "revision": saved["revision"]})
        with self.assertRaises(HTTPError) as error:
            self.request(url, method="POST", data={"revision": None, "data": document})
        self.assertEqual(error.exception.code, 409)
        for change in ({"camera": "unknown"}, {"label": "sync"}, {"frame": 121},
                       {"x": 64}, {"y": 48}, {"x": 1.5}, {"frame": True}):
            bad = json.loads(json.dumps(document))
            bad["points"][0].update(change)
            with self.assertRaises(HTTPError) as error:
                self.request(url, method="POST", data={"revision": saved["revision"], "data": bad})
            self.assertEqual(error.exception.code, 400, change)
        bad = json.loads(json.dumps(document))
        bad["points"].append(dict(bad["points"][0]))
        with self.assertRaises(HTTPError) as error:
            self.request(url, method="POST", data={"revision": saved["revision"], "data": bad})
        self.assertEqual(error.exception.code, 400)
        for change in ({"camera": "unknown"}, {"frame": 121}, {"vertices": []},
                       {"vertices": [{"x": 0, "y": 0}, {"x": 1, "y": 1}, {"x": 2, "y": 2}]},
                       {"vertices": [{"x": 0, "y": 0}, {"x": 64, "y": 0}, {"x": 0, "y": 1}]}):
            bad = json.loads(json.dumps(document))
            bad["matrix"][0].update(change)
            with self.assertRaises(HTTPError) as error:
                self.request(url, method="POST", data={"revision": saved["revision"], "data": bad})
            self.assertEqual(error.exception.code, 400, change)
        bad = json.loads(json.dumps(document))
        bad["matrix"].append(dict(bad["matrix"][0]))
        with self.assertRaises(HTTPError) as error:
            self.request(url, method="POST", data={"revision": saved["revision"], "data": bad})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(json.loads(path.read_text()), document)
        document["points"] = []
        document["matrix"] = []
        with self.request(url, method="POST", data={"revision": saved["revision"], "data": document}):
            pass
        with self.request(url) as response:
            self.assertEqual(json.load(response)["data"]["points"], [])
        legacy = {key: value for key, value in document.items() if key != "matrix"}
        path.write_text(json.dumps(legacy))
        with self.request(url) as response:
            self.assertEqual(json.load(response)["data"], legacy)
        outside = self.root / "outside-calibration.json"
        outside.write_text("untouched")
        link = self.root / "s5/camera_calibration/curl_1.json"
        link.symlink_to(outside)
        linked_url = "/api/calibration?subject=s5&sequence=curl_1"
        with self.assertRaises(HTTPError) as error:
            self.request(linked_url)
        self.assertEqual(error.exception.code, 409)
        with self.assertRaises(HTTPError) as error:
            self.request(linked_url, method="POST", data={"revision": None,
                         "data": {**document, "sequence": "curl_1"}})
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(outside.read_text(), "untouched")
        link.unlink()

    def test_annotation_schema_and_save(self):
        task, choices = label_choices("drilling_vertical_0")
        self.assertEqual(task["trial_id"], "drilling_vertical")
        self.assertIn(("atomic", "pointing"), {(choice["level"], choice["label"]) for choice in choices})
        with self.request("/api/schema?subject=s5&sequence=static_0") as response:
            self.assertEqual(json.load(response)["choices"], [])
        with self.request("/api/schema?subject=s5&sequence=curl_0") as response:
            labels = [choice["label"] for choice in json.load(response)["choices"]]
        self.assertIn("curl", labels)
        self.assertIn("transition", labels)
        self.assertIn("invalid", labels)
        self.assertNotIn("sync", labels)
        url = "/api/labels?subject=s5&sequence=curl_0"
        with self.request(url) as response:
            current = json.load(response)
        self.assertIsNone(current["revision"])
        self.assertEqual(current["data"]["annotations"], [])
        self.assertEqual(current["workflow_stage"], "high")
        base = {"parents": [], "rep": None, "side": None, "direction": None, "phase": None,
                "external_load": None, "contact_points": None, "target": None, "location": None, "note": None}
        document = current["data"]
        document["annotations"] = [
            {**base, "id": 1, "level": "high", "label": "curl", "start_frame": 1,
             "end_frame": 30, "t_start": 0, "t_end": 1},
            {**base, "id": 2, "level": "aux", "label": "transition", "start_frame": 10,
             "end_frame": 20, "t_start": 9 / 30, "t_end": 20 / 30},
        ]
        with self.request(url, method="POST", data={"revision": None, "data": document}) as response:
            saved = json.load(response)
        path = self.root / "s5/label/curl_0.json"
        self.assertEqual(json.loads(path.read_text()), document)
        with self.request(url) as response:
            reopened = json.load(response)
        self.assertEqual(reopened["data"], document)
        self.assertEqual(reopened["revision"], saved["revision"])
        self.assertEqual(reopened["workflow_stage"], "free")
        with self.assertRaises(HTTPError) as error:
            self.request(url, method="POST", data={"revision": None, "data": document})
        self.assertEqual(error.exception.code, 409)
        bad = json.loads(json.dumps(document))
        bad["annotations"][1]["end_frame"] = 11
        with self.assertRaises(HTTPError) as error:
            self.request(url, method="POST", data={"revision": saved["revision"], "data": bad})
        self.assertEqual(error.exception.code, 400)
        bad["annotations"][1]["end_frame"] = 20
        bad["annotations"][0]["end_frame"] = 121
        with self.assertRaises(HTTPError) as error:
            self.request(url, method="POST", data={"revision": saved["revision"], "data": bad})
        self.assertEqual(error.exception.code, 400)
        bad = json.loads(json.dumps(document))
        bad["annotations"][1].update(label="sync", end_frame=10, t_end=9 / 30)
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
        self.assertEqual({item["label"] for item in curl_choices if item["level"] == "low"},
                         {"flexion", "extension"})
        self.assertTrue(next(item for item in curl_choices if item["label"] == "curl")["definition"])
        legacy_sync = {**document, "sequence": "curl_1", "annotations": [bad["annotations"][1]]}
        (self.root / "s5/label/curl_1.json").write_text(json.dumps(legacy_sync))
        with self.assertRaises(HTTPError) as error:
            self.request("/api/labels?subject=s5&sequence=curl_1")
        self.assertEqual(error.exception.code, 409)
        self.assertIn("sync", json.load(error.exception)["error"])
        self.assertEqual(json.loads((self.root / "s5/label/curl_1.json").read_text()), legacy_sync)
        legacy = {**document, "sequence": "curl_1", "annotations": [{**document["annotations"][0]}]}
        legacy["annotations"][0].pop("phase")
        (self.root / "s5/label/curl_1.json").write_text(json.dumps(legacy))
        with self.request("/api/labels?subject=s5&sequence=curl_1") as response:
            upgraded = json.load(response)
        self.assertIsNone(upgraded["data"]["annotations"][0]["phase"])
        self.assertIsNotNone(upgraded["revision"])
        old_empty = {**document, "schema_version": "0.1", "sequence": "curl_1", "annotations": []}
        (self.root / "s5/label/curl_1.json").write_text(json.dumps(old_empty))
        with self.request("/api/labels?subject=s5&sequence=curl_1") as response:
            upgraded = json.load(response)
        self.assertEqual(upgraded["data"]["schema_version"], LABEL_SCHEMA["schema_version"])
        self.assertEqual(json.loads((self.root / "s5/label/curl_1.json").read_text()), old_empty)
        old_empty["schema_version"] = "0.2"
        (self.root / "s5/label/curl_1.json").write_text(json.dumps(old_empty))
        with self.request("/api/labels?subject=s5&sequence=curl_1") as response:
            self.assertEqual(json.load(response)["data"]["schema_version"], LABEL_SCHEMA["schema_version"])
        old_empty["schema_version"] = "0.3"
        (self.root / "s5/label/curl_1.json").write_text(json.dumps(old_empty))
        with self.request("/api/labels?subject=s5&sequence=curl_1") as response:
            self.assertEqual(json.load(response)["data"]["schema_version"], LABEL_SCHEMA["schema_version"])
        old_empty["schema_version"] = "0.2"
        old_nonempty = {**old_empty, "annotations": [{k: v for k, v in document["annotations"][0].items()
                                                      if k != "location"}]}
        (self.root / "s5/label/curl_1.json").write_text(json.dumps(old_nonempty))
        with self.request("/api/labels?subject=s5&sequence=curl_1") as response:
            upgraded = json.load(response)
        self.assertEqual(upgraded["data"]["annotations"][0]["location"], None)
        self.assertEqual(upgraded["data"]["schema_version"], LABEL_SCHEMA["schema_version"])
        self.assertEqual(json.loads((self.root / "s5/label/curl_1.json").read_text()), old_nonempty)
        compatible_03 = {**old_nonempty, "schema_version": "0.3",
                         "annotations": [{**old_nonempty["annotations"][0], "location": None}]}
        (self.root / "s5/label/curl_1.json").write_text(json.dumps(compatible_03))
        with self.request("/api/labels?subject=s5&sequence=curl_1") as response:
            upgraded = json.load(response)
        self.assertEqual(upgraded["data"]["schema_version"], LABEL_SCHEMA["schema_version"])
        self.assertEqual(json.loads((self.root / "s5/label/curl_1.json").read_text()), compatible_03)
        incompatible_03 = json.loads(json.dumps(compatible_03))
        incompatible_03["annotations"][0]["external_load"] = "both"
        (self.root / "s5/label/curl_1.json").write_text(json.dumps(incompatible_03))
        with self.assertRaises(HTTPError) as error:
            self.request("/api/labels?subject=s5&sequence=curl_1")
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(json.loads((self.root / "s5/label/curl_1.json").read_text()), incompatible_03)
        incompatible = json.loads(json.dumps(old_nonempty))
        incompatible["annotations"][0]["target"] = "hand"
        (self.root / "s5/label/curl_1.json").write_text(json.dumps(incompatible))
        with self.assertRaises(HTTPError) as error:
            self.request("/api/labels?subject=s5&sequence=curl_1")
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(json.loads((self.root / "s5/label/curl_1.json").read_text()), incompatible)
        old_nonempty["schema_version"] = "0.1"
        (self.root / "s5/label/curl_1.json").write_text(json.dumps(old_nonempty))
        with self.assertRaises(HTTPError) as error:
            self.request("/api/labels?subject=s5&sequence=curl_1")
        self.assertEqual(error.exception.code, 409)

    def test_atomic_levels_and_attribute_placement(self):
        url = "/api/labels?subject=s5&sequence=throwing_0"
        with self.request("/api/schema?subject=s5&sequence=throwing_0") as response:
            schema = json.load(response)
        self.assertEqual(schema["schema_version"], "0.4")
        self.assertEqual(schema["side_values"], ["left", "right", "both"])
        self.assertEqual(schema["task_attributes"]["target"], "medicine_ball")
        self.assertEqual(schema["load_contact_level"], "atomic")
        self.assertIn(("atomic", "move", "wind_up"),
                      {(item["level"], item["label"], item["phase"]) for item in schema["choices"]})
        with self.request(url) as response:
            document = json.load(response)["data"]
        base = {"parents": [], "rep": None, "side": None, "direction": None, "phase": None,
                "external_load": None, "contact_points": None, "target": None, "location": None, "note": None}
        document["annotations"] = [
            {**base, "id": 1, "level": "high", "label": "throwing_catching", "target": "medicine_ball",
             "start_frame": 1,
             "end_frame": 60, "t_start": 0, "t_end": 2},
            {**base, "id": 2, "level": "mid", "label": "pass_cycle", "parents": [1], "rep": 1,
             "side": "both",
             "start_frame": 1, "end_frame": 60, "t_start": 0, "t_end": 2},
            {**base, "id": 3, "level": "low", "label": "ball_throw", "parents": [2], "side": "left",
             "start_frame": 5, "end_frame": 30, "t_start": 4 / 30, "t_end": 1},
            {**base, "id": 4, "level": "atomic", "label": "move", "phase": "wind_up", "parents": [3],
             "external_load": "both", "contact_points": "both", "start_frame": 5,
             "end_frame": 10, "t_start": 4 / 30, "t_end": 10 / 30},
        ]
        with self.request(url, method="POST", data={"revision": None, "data": document}) as response:
            saved = json.load(response)
        revision = saved["revision"]
        self.assertTrue(any("side" in warning for warning in saved["warnings"]))
        with self.request(url) as response:
            loaded = json.load(response)
        self.assertEqual(loaded["data"], document)
        self.assertEqual(loaded["warnings"], saved["warnings"])
        for index, changes in [(3, {"parents": [2]}), (3, {"phase": "waiting"}),
                               (3, {"phase": None}), (0, {"target": "door"}),
                               (3, {"target": "hand"}), (1, {"side": "invalid"}),
                               (0, {"side": "left"}), (0, {"external_load": "both"}),
                               (1, {"contact_points": "both"}), (2, {"external_load": "both"}),
                               (0, {"direction": "left"}), (2, {"location": "floor"})]:
            bad = json.loads(json.dumps(document))
            bad["annotations"][index].update(changes)
            with self.assertRaises(HTTPError) as error:
                self.request(url, method="POST", data={"revision": revision, "data": bad})
            self.assertEqual(error.exception.code, 400)

    def test_workflow_stage_persistence(self):
        url = "/api/labels?subject=s5&sequence=drilling_vertical_0"
        with self.request(url) as response:
            current = json.load(response)
        self.assertEqual(current["workflow_stage"], "high")
        document = current["data"]
        document["workflow_stage"] = "mid"
        with self.request(url, method="POST", data={"revision": current["revision"], "data": document}) as response:
            saved = json.load(response)
        with self.request(url) as response:
            reopened = json.load(response)
        self.assertEqual(reopened["workflow_stage"], "mid")
        self.assertEqual(reopened["data"], document)
        self.assertEqual(reopened["revision"], saved["revision"])
        document["workflow_stage"] = "free"
        with self.request(url, method="POST", data={"revision": saved["revision"], "data": document}) as response:
            self.assertEqual(response.status, 200)
        document["workflow_stage"] = "unknown"
        with self.assertRaises(HTTPError) as error:
            self.request(url, method="POST", data={"revision": None, "data": document})
        self.assertEqual(error.exception.code, 400)
        document["workflow_stage"] = ["mid"]
        with self.assertRaises(HTTPError) as error:
            self.request(url, method="POST", data={"revision": None, "data": document})
        self.assertEqual(error.exception.code, 400)

    def test_every_task_matches_new_schema(self):
        for task in LABEL_SCHEMA["tasks"]:
            with self.subTest(task=task["trial_id"]):
                choices = label_choices(task["trial_id"] + "_0")[1]
                expected = {("high", task["high"]["label"]),
                            ("mid", task["high"]["mid"]["label"])}
                for low in task["high"]["mid"].get("low", []):
                    expected.add(("low", low["label"]))
                    expected.update(("atomic", item["label"]) for item in low.get("atomic", []))
                self.assertEqual({(item["level"], item["label"]) for item in choices if item["level"] != "aux"}, expected)
                self.assertTrue(all(item["definition"] for item in choices))
                definitions = {(level, node["label"], node.get("phase")): node["definition"]
                               for level, nodes in (("high", [task["high"]]),
                                                    ("mid", [task["high"]["mid"]]),
                                                    ("low", task["high"]["mid"].get("low", [])),
                                                    ("atomic", [atomic for low in task["high"]["mid"].get("low", [])
                                                                for atomic in low.get("atomic", [])]))
                               for node in nodes}
                for choice in choices:
                    if choice["level"] != "aux":
                        self.assertEqual(choice["definition"], definitions[
                            choice["level"], choice["label"], choice["phase"]])
        files = self.server.recordings["s5"]["curl_0"]
        no_low = {"schema_version": LABEL_SCHEMA["schema_version"], "subject": "s5",
                  "sequence": "infinity_tracing_0", "fps": 30, "annotations": [{
                      "id": 1, "level": "mid", "label": "figure_eight", "phase": None,
                      "start_frame": 1, "end_frame": 30, "t_start": 0, "t_end": 1,
                      "parents": [], "rep": 1, "side": "left", "direction": None,
                      "external_load": "none", "contact_points": "none", "target": None,
                      "location": None, "note": None}]}
        self.assertEqual(validate_labels(no_low, "s5", "infinity_tracing_0", files), no_low)

    def test_location_rules_and_task_specific_attributes(self):
        files = self.server.recordings["s5"]["curl_0"]
        base = {"id": 1, "level": "low", "phase": None, "start_frame": 1, "end_frame": 30,
                "t_start": 0, "t_end": 1, "parents": [], "rep": None, "side": None,
                "direction": None, "external_load": None, "contact_points": None,
                "target": None, "location": None, "note": None}
        cases = [("pouring_light_0", "pour_glass", "g9", "g10"),
                 ("drilling_vertical_0", "drill_target", "r1", "r0"),
                 ("lifting_2hands_light_0", "lift_to_shelf", "shelf_2", "shelf_0")]
        for sequence, label, good, bad in cases:
            with self.subTest(sequence=sequence):
                choice = next(item for item in label_choices(sequence)[1] if item["level"] == "low" and item["label"] == label)
                self.assertTrue(choice["location_values"] or choice["location_pattern"])
                record = {**base, "label": label, "location": good}
                document = {"schema_version": LABEL_SCHEMA["schema_version"], "subject": "s5",
                            "sequence": sequence, "fps": 30, "annotations": [record]}
                self.assertEqual(validate_labels(document, "s5", sequence, files), document)
                record["location"] = bad
                with self.assertRaises(ValueError):
                    validate_labels(document, "s5", sequence, files)
        axial = {"schema_version": LABEL_SCHEMA["schema_version"], "subject": "s5",
                 "sequence": "axial_twist_0", "fps": 30,
                 "annotations": [{**base, "level": "mid", "label": "trunk_twist", "direction": "left"}]}
        self.assertEqual(validate_labels(axial, "s5", "axial_twist_0", files), axial)
        axial["annotations"][0]["side"] = "left"
        with self.assertRaises(ValueError):
            validate_labels(axial, "s5", "axial_twist_0", files)

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
