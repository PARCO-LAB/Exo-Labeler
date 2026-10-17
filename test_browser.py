"""Optional Firefox check for seeking and playback: python test_browser.py."""

import json
import shutil
import socket
import subprocess
import time
from urllib.request import Request, urlopen

from test_app import ViewerTest


def webdriver(url, data=None, method="POST"):
    request = Request(url, data=json.dumps(data).encode() if data is not None else None,
                      headers={"Content-Type": "application/json"}, method=method)
    with urlopen(request, timeout=30) as response:
        return json.load(response)["value"]


def main():
    if not shutil.which("geckodriver") or not shutil.which("firefox"):
        raise SystemExit("Firefox e geckodriver sono necessari per questo test")
    ViewerTest.setUpClass()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    driver = None
    session = None
    try:
        driver = subprocess.Popen(["geckodriver", "--port", str(port), "--log", "error"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        endpoint = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                session = webdriver(endpoint + "/session", {"capabilities": {"alwaysMatch": {
                    "browserName": "firefox", "moz:firefoxOptions": {"args": ["-headless"]}}}})["sessionId"]
                break
            except OSError:
                time.sleep(0.2)
        assert session, "Firefox non si è avviato"
        endpoint += "/session/" + session
        webdriver(endpoint + "/url", {"url": ViewerTest.base + "/"})
        read = {"script": "return {ready:!document.getElementById('slider').disabled,"
                "busy:document.getElementById('seekState').textContent,"
                "frame:+document.getElementById('slider').value,"
                "header:!!document.querySelector('header'),"
                "layout:getComputedStyle(document.getElementById('videos')).display,"
                "resize:getComputedStyle(document.querySelector('.camera')).resize,"
                "rowTop:[...document.querySelectorAll('.camera')].map(c=>c.getBoundingClientRect().top),"
                "navIconSizes:[...document.querySelectorAll('.nav-icon')].map(e=>parseFloat(getComputedStyle(e).fontSize)),"
                "readout:document.getElementById('readout').textContent,"
                "rulerLabels:[...document.querySelectorAll('#ruler .tick span')].map(e=>e.textContent),"
                "status:document.getElementById('status').textContent,"
                "ruler:document.getElementById('ruler').childElementCount,"
                "times:[...document.querySelectorAll('video')].map(v=>v.currentTime),"
                "hashes:[...document.querySelectorAll('canvas')].map(c=>{let d=c.getContext('2d')"
                ".getImageData(0,0,c.width,c.height).data,h=0;for(let i=0;i<d.length;i+=16)"
                "h=(h*33+d[i]+d[i+1]+d[i+2])>>>0;return h})}", "args": []}
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if state["ready"] and not state["busy"]:
                break
            time.sleep(0.1)
        assert state["ready"] and not state["busy"], state
        initial = state["hashes"][0]
        webdriver(endpoint + "/execute/sync", {"script":
            "let s=document.getElementById('slider');for(const n of [2,7,4,8])"
            "{s.value=n;s.dispatchEvent(new Event('input',{bubbles:true}))}", "args": []})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if not state["busy"]:
                break
            time.sleep(0.1)
        assert state["frame"] == 8 and not state["busy"], state
        assert all(abs(t - (8 / 30 + 0.001)) < 0.001 for t in state["times"]), state
        assert state["hashes"][0] != initial and len(set(state["hashes"])) == 1, state
        assert not state["header"] and state["layout"] == "flex" and len(set(state["rowTop"])) == 1, state
        assert state["resize"] == "both" and len(state["navIconSizes"]) == 3 and max(state["navIconSizes"]) < 16, state
        assert state["readout"] == "Frame 9 / 120" and state["rulerLabels"] == ["1", "21", "41", "61", "81", "101"], state
        assert state["status"] == "", state
        before = webdriver(endpoint + "/execute/sync", {"script":
            "let r=document.querySelector('.camera').getBoundingClientRect();"
            "return {x:r.x,y:r.y,width:r.width,height:r.height}", "args": []})
        x, y = round(before["x"] + before["width"] - 5), round(before["y"] + before["height"] - 5)
        webdriver(endpoint + "/actions", {"actions": [{"type": "pointer", "id": "mouse",
            "parameters": {"pointerType": "mouse"}, "actions": [
                {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerMove", "duration": 500, "x": x + 80, "y": y + 50},
                {"type": "pointerUp", "button": 0},
            ]}]})
        resized = webdriver(endpoint + "/execute/sync", {"script":
            "let r=document.querySelector('.camera').getBoundingClientRect();"
            "return {width:r.width,height:r.height,"
            "tops:[...document.querySelectorAll('.camera')].map(x=>x.getBoundingClientRect().top)}", "args": []})
        assert resized["width"] >= before["width"] + 60 and resized["height"] >= before["height"] + 30, (before, resized)
        assert len(set(resized["tops"])) == 1, resized
        x, y = round(before["x"] + resized["width"] - 5), round(before["y"] + resized["height"] - 5)
        webdriver(endpoint + "/actions", {"actions": [{"type": "pointer", "id": "mouse",
            "parameters": {"pointerType": "mouse"}, "actions": [
                {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerMove", "duration": 500, "x": x - 50, "y": y - 30},
                {"type": "pointerUp", "button": 0},
            ]}]})
        smaller = webdriver(endpoint + "/execute/sync", {"script":
            "let r=document.querySelector('.camera').getBoundingClientRect();"
            "return {width:r.width,height:r.height}", "args": []})
        assert smaller["width"] <= resized["width"] - 35 and smaller["height"] <= resized["height"] - 20, (resized, smaller)
        webdriver(endpoint + "/execute/sync", {"script":
            "document.querySelector('.visibility input[data-camera=aria]').click();"
            "let s=document.getElementById('slider');s.value=10;"
            "s.dispatchEvent(new Event('input',{bubbles:true}))", "args": []})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if not state["busy"]:
                break
            time.sleep(0.1)
        assert state["frame"] == 10 and not state["busy"] and state["readout"] == "Frame 11 / 120", state
        webdriver(endpoint + "/execute/sync", {"script":
            "document.querySelector('.visibility input[data-camera=aria]').click()", "args": []})
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('help').click()", "args": []})
        help_state = webdriver(endpoint + "/execute/sync", {"script":
            "return {open:document.getElementById('helpDialog').open,"
            "text:document.getElementById('helpDialog').textContent}", "args": []})
        assert help_state["open"] and "120 frame a 30 fps" in help_state["text"] and "Frecce" in help_state["text"]
        webdriver(endpoint + "/execute/sync", {"script":
            "document.getElementById('closeHelp').click();window.playSeeks=0;"
            "document.querySelectorAll('video').forEach(v=>v.addEventListener('seeking',()=>playSeeks++));"
            "document.getElementById('play').click()", "args": []})
        time.sleep(2)
        playback = webdriver(endpoint + "/execute/sync", {"script":
            "return {times:[...document.querySelectorAll('video')].map(v=>v.currentTime),"
            "ready:[...document.querySelectorAll('video')].map(v=>v.readyState),seeks:playSeeks,"
            "label:document.getElementById('readout').textContent}", "args": []})
        assert min(playback["times"]) > 1.5 and max(playback["times"]) - min(playback["times"]) < 0.05, playback
        assert all(ready >= 3 for ready in playback["ready"]) and playback["seeks"] == 0, playback
        assert playback["label"].startswith("≈ Frame"), playback
        webdriver(endpoint + "/execute/sync", {"script":
            "document.getElementById('ariaVideo').pause()", "args": []})
        time.sleep(0.3)
        held = webdriver(endpoint + "/execute/sync", {"script":
            "return [...document.querySelectorAll('video')].map(v=>v.paused)", "args": []})
        assert held == [True, True, True], held
        webdriver(endpoint + "/execute/sync", {"script":
            "document.getElementById('ariaVideo').play()", "args": []})
        time.sleep(0.5)
        resumed = webdriver(endpoint + "/execute/sync", {"script":
            "return [...document.querySelectorAll('video')].map(v=>({paused:v.paused,time:v.currentTime}))", "args": []})
        assert not any(item["paused"] for item in resumed), resumed
        assert max(item["time"] for item in resumed) - min(item["time"] for item in resumed) < 0.1, resumed
        print("Scrub e riproduzione sincronizzati nel browser: OK")
    finally:
        if session:
            try:
                webdriver(f"http://127.0.0.1:{port}/session/{session}", method="DELETE")
            except OSError:
                pass
        if driver:
            driver.terminate()
            driver.wait(timeout=10)
        ViewerTest.tearDownClass()


if __name__ == "__main__":
    main()
