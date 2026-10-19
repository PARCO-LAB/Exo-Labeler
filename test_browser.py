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
        chrome = webdriver(endpoint + "/execute/sync", {"script":
            "let panel=document.getElementById('selectionPanel'),"
            "ruler=document.querySelector('.ruler-surface'),"
            "track=document.getElementById('annotationRows'),"
            "button=document.getElementById('previous'),"
            "camera=document.querySelector('.visibility input');"
            "let initial={closed:!panel.open,summary:document.getElementById('selectionSummary').textContent,"
            "rulerHeight:ruler.getBoundingClientRect().height,rulerColor:getComputedStyle(ruler).backgroundColor,"
            "trackColor:getComputedStyle(track).backgroundColor,buttonHeight:button.getBoundingClientRect().height,"
            "cameraVisible:camera.getBoundingClientRect().width>0,"
            "compactAbsent:!document.getElementById('compactTransport'),"
            "titles:[...document.querySelectorAll('.camera h2')].map(x=>x.textContent),"
            "titleFont:parseFloat(getComputedStyle(document.querySelector('.camera h2')).fontSize)};"
            "document.getElementById('selectionSummary').click();initial.opened=panel.open;"
            "initial.selectVisible=document.getElementById('subject').getBoundingClientRect().width>0;"
            "document.getElementById('selectionSummary').click();return initial", "args": []})
        assert chrome["closed"] and chrome["opened"] and chrome["selectVisible"] and chrome["cameraVisible"], chrome
        assert "s5" in chrome["summary"] and "curl_0" in chrome["summary"], chrome
        assert chrome["rulerHeight"] == 22 and chrome["rulerColor"] == "rgb(232, 236, 239)", chrome
        assert chrome["trackColor"] == chrome["rulerColor"], chrome
        assert chrome["compactAbsent"] and chrome["buttonHeight"] <= 28, chrome
        assert chrome["titles"] == ["Body", "Esterna", "Aria"] and chrome["titleFont"] < 13, chrome
        def label_center(level, label):
            return webdriver(endpoint + "/execute/sync", {"script":
                "document.getElementById('labelsPanel').open=true;"
                "let chip=[...document.querySelectorAll('.label-chip')].find(x=>x.textContent===arguments[1]"
                "&&x.parentElement.querySelector('strong').textContent===arguments[0]);"
                "if(!chip)return null;let r=chip.getBoundingClientRect();"
                "return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}", "args": [level, label]})
        for level, label, expected in [("Low", "move · flexion", "Free segmental movement"),
                                       ("High", "curl", "Definizione non presente nello schema")]:
            center = label_center(level, label)
            assert center, (level, label)
            webdriver(endpoint + "/actions", {"actions": [{"type": "pointer", "id": "mouse",
                "parameters": {"pointerType": "mouse"}, "actions": [
                    {"type": "pointerMove", "duration": 0, "x": center["x"], "y": center["y"]}]}]})
            tooltip = webdriver(endpoint + "/execute/sync", {"script":
                "let t=document.getElementById('labelTooltip'),r=t.getBoundingClientRect();"
                "return {visible:!t.hidden,text:t.textContent,right:r.right,bottom:r.bottom,"
                "width:innerWidth,height:innerHeight}", "args": []})
            assert tooltip["visible"] and expected in tooltip["text"], tooltip
            assert tooltip["right"] <= tooltip["width"] and tooltip["bottom"] <= tooltip["height"], tooltip
        focus_tip = webdriver(endpoint + "/execute/sync", {"script":
            "let chip=[...document.querySelectorAll('.label-chip')].find(x=>x.textContent==='sync');"
            "chip.focus();let t=document.getElementById('labelTooltip');"
            "return {visible:!t.hidden,text:t.textContent,description:chip.getAttribute('aria-description')}" , "args": []})
        assert focus_tip["visible"] and "align sensor clocks" in focus_tip["text"], focus_tip
        assert focus_tip["description"] == focus_tip["text"], focus_tip
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('labelsPanel').open=false", "args": []})
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
        assert state["readout"] == "Frame 9 / 120" and state["rulerLabels"] == ["1", "51", "101"], state
        assert state["status"] == "", state
        timeline = webdriver(endpoint + "/execute/sync", {"script":
            "let r=document.getElementById('ruler').getBoundingClientRect(),"
            "l=document.getElementById('playheadLine').getBoundingClientRect(),"
            "h=document.getElementById('playheadHead').getBoundingClientRect();"
            "return {ruler:{x:r.x,y:r.y,width:r.width},line:{x:l.x,width:l.width},"
            "head:h.x+h.width/2,offset:parseFloat(getComputedStyle(document.getElementById('playheadLine'),'::before').left)}",
            "args": []})
        assert abs(timeline["ruler"]["x"] - timeline["line"]["x"]) < 1, timeline
        assert abs(timeline["ruler"]["width"] - timeline["line"]["width"]) < 1, timeline
        assert abs(timeline["head"] - (timeline["line"]["x"] + timeline["offset"])) < 1, timeline
        x = round(timeline["ruler"]["x"] + timeline["ruler"]["width"] * .65)
        y = round(timeline["ruler"]["y"] + 11)
        webdriver(endpoint + "/actions", {"actions": [{"type": "pointer", "id": "mouse",
            "parameters": {"pointerType": "mouse"}, "actions": [
                {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerUp", "button": 0},
            ]}]})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if not state["busy"] and 75 <= state["frame"] <= 80:
                break
            time.sleep(0.1)
        assert not state["busy"] and 75 <= state["frame"] <= 80, state
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
        restored = webdriver(endpoint + "/execute/sync", {"script":
            "document.getElementById('resetViews').click();"
            "let cameras=[...document.querySelectorAll('.camera')],r=cameras[0].getBoundingClientRect();"
            "return {width:r.width,height:r.height,inline:cameras.map(x=>[x.style.width,x.style.height])}", "args": []})
        assert abs(restored["width"] - before["width"]) < 1 and abs(restored["height"] - before["height"]) < 1, (before, restored)
        assert restored["inline"] == [["", ""]] * 3, restored
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
        webdriver(endpoint + "/execute/sync", {"script":
            "document.getElementById('play').click();let s=document.getElementById('slider');"
            "s.value=9;s.dispatchEvent(new Event('input',{bubbles:true}))", "args": []})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if not state["busy"]:
                break
            time.sleep(0.1)
        assert state["frame"] == 9 and not state["busy"], state
        def click_label(level, label):
            return webdriver(endpoint + "/execute/sync", {"script":
                "let chip=[...document.querySelectorAll('.label-chip')].find(x=>x.textContent===arguments[1]"
                "&&x.parentElement.querySelector('strong').textContent===arguments[0]);"
                "if(!chip || chip.draggable) return false;chip.click();return true", "args": [level, label]})
        assert click_label("High", "curl")
        draft = webdriver(endpoint + "/execute/sync", {"script":
            "return {end:document.getElementById('endLabel').disabled,save:document.getElementById('saveLabels').disabled,"
            "row:document.querySelector('.annotation-caption')?.textContent}", "args": []})
        assert not draft["end"] and draft["save"] and "frame 10" in draft["row"], draft
        track = webdriver(endpoint + "/execute/sync", {"script":
            "let r=document.querySelector('.annotation-track').getBoundingClientRect();"
            "return {x:Math.round(r.x+r.width*.75),y:Math.round(r.y+r.height/2)}", "args": []})
        webdriver(endpoint + "/actions", {"actions": [{"type": "pointer", "id": "mouse",
            "parameters": {"pointerType": "mouse"}, "actions": [
                {"type": "pointerMove", "duration": 0, "x": track["x"], "y": track["y"]},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerUp", "button": 0},
            ]}]})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if not state["busy"] and 87 <= state["frame"] <= 92:
                break
            time.sleep(0.1)
        assert not state["busy"] and 87 <= state["frame"] <= 92, state
        webdriver(endpoint + "/execute/sync", {"script":
            "let s=document.getElementById('slider');s.value=19;s.dispatchEvent(new Event('input',{bubbles:true}))", "args": []})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if not state["busy"]:
                break
            time.sleep(0.1)
        assert state["frame"] == 19 and not state["busy"], state
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('endLabel').click()", "args": []})
        closed = webdriver(endpoint + "/execute/sync", {"script":
            "return {end:document.getElementById('endLabel').disabled,save:document.getElementById('saveLabels').disabled,"
            "row:document.querySelector('.annotation-caption')?.textContent}", "args": []})
        assert closed["end"] and not closed["save"] and "frame 10–20" in closed["row"], closed
        from_name = webdriver(endpoint + "/execute/sync", {"script":
            "document.querySelector('.track-name').click();"
            "let dialog=document.getElementById('editDialog');"
            "let result={open:dialog.open,start:document.getElementById('editStart').value,"
            "end:document.getElementById('editEnd').value};"
            "document.getElementById('cancelEdit').click();return result", "args": []})
        assert from_name == {"open": True, "start": "10", "end": "20"}, from_name
        webdriver(endpoint + "/execute/sync", {"script":
            "let h=document.querySelector('.annotation-handle.end');h.focus();"
            "h.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true,cancelable:true}));"
            "document.querySelector('.annotation-caption').click();document.getElementById('editSide').value='left';"
            "document.querySelector('#editForm button[type=submit]').click()", "args": []})
        edited = webdriver(endpoint + "/execute/sync", {"script":
            "return {row:document.querySelector('.annotation-caption').textContent,dialog:document.getElementById('editDialog').open}", "args": []})
        assert "frame 10–21" in edited["row"] and "side: left" in edited["row"] and not edited["dialog"], edited
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('saveLabels').click()", "args": []})
        for _ in range(50):
            if not webdriver(endpoint + "/execute/sync", {"script":
                    "return document.getElementById('status').textContent", "args": []}).startswith("Intervallo"):
                break
            time.sleep(0.1)
        saved = json.loads((ViewerTest.root / "s5/label/curl_0.json").read_text())
        assert saved["annotations"][0]["start_frame"] == 10 and saved["annotations"][0]["end_frame"] == 21, saved
        assert saved["annotations"][0]["t_start"] == 9 / 30 and saved["annotations"][0]["t_end"] == 21 / 30, saved
        assert saved["annotations"][0]["side"] == "left", saved
        webdriver(endpoint + "/execute/sync", {"script":
            "let s=document.getElementById('sequence');s.value='curl_1';s.dispatchEvent(new Event('change'))", "args": []})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if state["ready"] and not state["busy"] and state["status"] == "":
                break
            time.sleep(0.1)
        assert not webdriver(endpoint + "/execute/sync", {"script":
            "return document.querySelector('.annotation-caption')", "args": []})
        webdriver(endpoint + "/execute/sync", {"script":
            "let s=document.getElementById('sequence');s.value='curl_0';s.dispatchEvent(new Event('change'))", "args": []})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if state["ready"] and not state["busy"] and state["status"] == "":
                break
            time.sleep(0.1)
        restored = webdriver(endpoint + "/execute/sync", {"script":
            "return document.querySelector('.annotation-caption')?.textContent", "args": []})
        assert "frame 10–21" in restored and "side: left" in restored, restored
        handle = webdriver(endpoint + "/execute/sync", {"script":
            "let r=document.querySelector('.annotation-handle.end').getBoundingClientRect();"
            "return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}", "args": []})
        webdriver(endpoint + "/actions", {"actions": [{"type": "pointer", "id": "mouse",
            "parameters": {"pointerType": "mouse"}, "actions": [
                {"type": "pointerMove", "duration": 0, "x": handle["x"], "y": handle["y"]},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerMove", "duration": 350, "x": handle["x"] + 50, "y": handle["y"]},
            ]}]})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if not state["busy"]:
                break
            time.sleep(0.1)
        dragging = webdriver(endpoint + "/execute/sync", {"script":
            "let bar=document.querySelector('.annotation-bar'),"
            "head=document.getElementById('playheadHead').getBoundingClientRect();"
            "return {end:+bar.querySelector('.annotation-handle.end').getAttribute('aria-valuenow'),"
            "frame:+document.getElementById('slider').value+1,readout:document.getElementById('readout').textContent,"
            "barRight:bar.getBoundingClientRect().right,headX:head.x+head.width/2}", "args": []})
        assert not state["busy"] and dragging["end"] > 21, (state, dragging)
        assert dragging["frame"] == dragging["end"] and dragging["readout"] == f"Frame {dragging['end']} / 120", dragging
        assert abs(dragging["barRight"] - dragging["headX"]) < 2, dragging
        assert len(set(state["hashes"])) == 1, state
        assert all(abs(t - ((dragging["frame"] - 1) / 30 + .001)) < .001 for t in state["times"]), state
        webdriver(endpoint + "/actions", {"actions": [{"type": "pointer", "id": "mouse",
            "parameters": {"pointerType": "mouse"}, "actions": [{"type": "pointerUp", "button": 0}]}]})
        moved = webdriver(endpoint + "/execute/sync", {"script":
            "return document.querySelector('.annotation-caption').textContent", "args": []})
        assert "frame 10–21" not in moved and "frame 10–" in moved, moved

        def go_to(frame):
            webdriver(endpoint + "/execute/sync", {"script":
                "let s=document.getElementById('slider');s.value=arguments[0];"
                "s.dispatchEvent(new Event('input',{bubbles:true}))", "args": [frame - 1]})
            for _ in range(100):
                current = webdriver(endpoint + "/execute/sync", read)
                if current["frame"] == frame - 1 and not current["busy"]:
                    return
                time.sleep(0.1)
            raise AssertionError(current)

        go_to(30)
        assert click_label("Mid", "curl_cycle")
        go_to(40)
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('endLabel').click()", "args": []})
        webdriver(endpoint + "/execute/sync", {"script":
            "document.querySelectorAll('.annotation-caption')[1].click();"
            "document.getElementById('editRep').value='2';"
            "document.getElementById('editParents').options[0].selected=true;"
            "document.querySelector('#editForm button[type=submit]').click()", "args": []})
        go_to(32)
        assert click_label("Low", "move · flexion")
        go_to(34)
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('endLabel').click()", "args": []})
        webdriver(endpoint + "/execute/sync", {"script":
            "document.querySelectorAll('.annotation-caption')[2].click();"
            "document.getElementById('editLoad').value='both';"
            "document.getElementById('editContact').value='both';"
            "document.getElementById('editTarget').value='hand';"
            "document.getElementById('editParents').options[0].selected=true;"
            "document.querySelector('#editForm button[type=submit]').click()", "args": []})
        go_to(60)
        assert click_label("Aux", "sync")
        point = webdriver(endpoint + "/execute/sync", {"script":
            "return {rows:[...document.querySelectorAll('.annotation-caption')].map(x=>x.textContent),"
            "end:document.getElementById('endLabel').disabled}", "args": []})
        assert len(point["rows"]) == 4 and point["end"] and "frame 60" in point["rows"][3], point
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('saveLabels').click()", "args": []})
        for _ in range(50):
            saved = json.loads((ViewerTest.root / "s5/label/curl_0.json").read_text())
            if len(saved["annotations"]) == 4:
                break
            time.sleep(0.1)
        assert len(saved["annotations"]) == 4, saved
        assert saved["annotations"][0]["end_frame"] > 21, saved
        assert saved["annotations"][1]["rep"] == 2 and saved["annotations"][1]["parents"] == [1], saved
        assert saved["annotations"][2]["parents"] == [2] and saved["annotations"][2]["external_load"] == "both", saved
        assert saved["annotations"][2]["contact_points"] == "both" and saved["annotations"][2]["target"] == "hand", saved
        assert saved["annotations"][2]["phase"] == "flexion", saved
        assert saved["annotations"][3]["level"] == "aux" and saved["annotations"][3]["t_start"] == saved["annotations"][3]["t_end"], saved
        go_to(12)
        assert click_label("High", "curl")
        go_to(18)
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('endLabel').click()", "args": []})
        go_to(32)
        assert click_label("Low", "move · extension")
        go_to(35)
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('endLabel').click()", "args": []})
        grouped = webdriver(endpoint + "/execute/sync", {"script":
            "let rows=[...document.querySelectorAll('.annotation-row')];"
            "let lane=(row)=>[...row.querySelectorAll('.annotation-bar')].map(x=>x.getBoundingClientRect().y);"
            "let ruler=document.getElementById('ruler').getBoundingClientRect();"
            "return {rows:rows.length,names:rows.map(x=>x.querySelector('.track-name').textContent),"
            "counts:rows.map(x=>x.querySelectorAll('.annotation-bar').length),"
            "highY:lane(rows[0]),lowY:lane(rows[2]),"
            "tracksX:rows.map(x=>x.querySelector('.annotation-track').getBoundingClientRect().x),"
            "rulerX:ruler.x,inside:document.getElementById('scrub').contains(document.getElementById('annotationRows')),"
            "tickX:[...document.querySelectorAll('#ruler .tick')].map(x=>x.getBoundingClientRect().x),"
            "lines:[...document.querySelectorAll('#gridLines .grid-line')].map(x=>{let r=x.getBoundingClientRect(),s=getComputedStyle(x);"
            "return {x:r.x,y:r.y,height:r.height,major:x.classList.contains('major'),"
            "color:s.borderLeftColor,width:s.borderLeftWidth}}),"
            "trackTop:document.getElementById('annotationRows').getBoundingClientRect().y,"
            "trackHeight:document.getElementById('annotationRows').getBoundingClientRect().height}",
            "args": []})
        assert grouped["inside"] and grouped["rows"] == 4, grouped
        assert grouped["names"] == ["high · curl", "mid · curl_cycle", "low · move", "aux · sync"], grouped
        assert grouped["counts"] == [2, 1, 2, 1], grouped
        assert len(set(grouped["highY"])) == 2 and len(set(grouped["lowY"])) == 2, grouped
        assert all(abs(x - grouped["rulerX"]) < 1 for x in grouped["tracksX"]), grouped
        assert len(grouped["tickX"]) == len(grouped["lines"]) > 0, grouped
        assert len(grouped["lines"]) == 12 and [i for i, line in enumerate(grouped["lines"]) if line["major"]] == [0, 5, 10], grouped
        assert all(line["width"] == "1px" and line["color"] == ("rgb(17, 17, 17)" if line["major"] else "rgb(186, 197, 206)")
                   for line in grouped["lines"]), grouped
        assert all(abs(tick - line["x"]) < 1 and abs(line["y"] - grouped["trackTop"]) < 1
                   and abs(line["height"] - grouped["trackHeight"]) < 1
                   for tick, line in zip(grouped["tickX"], grouped["lines"])), grouped
        from_shared_name = webdriver(endpoint + "/execute/sync", {"script":
            "document.querySelector('.track-name').click();"
            "let chooser=document.getElementById('chooseDialog');"
            "let options=[...document.querySelectorAll('#chooseList button')].map(x=>x.textContent);"
            "let opened=chooser.open;document.querySelectorAll('#chooseList button')[1].click();"
            "let result={opened,options,chooserClosed:!chooser.open,"
            "editorOpen:document.getElementById('editDialog').open,"
            "start:document.getElementById('editStart').value,end:document.getElementById('editEnd').value};"
            "document.getElementById('cancelEdit').click();return result", "args": []})
        assert from_shared_name["opened"] and from_shared_name["chooserClosed"] and from_shared_name["editorOpen"], from_shared_name
        assert len(from_shared_name["options"]) == 2 and "frame 12–18" in from_shared_name["options"][1], from_shared_name
        assert from_shared_name["start"] == "12" and from_shared_name["end"] == "18", from_shared_name
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('saveLabels').click()", "args": []})
        for _ in range(50):
            saved = json.loads((ViewerTest.root / "s5/label/curl_0.json").read_text())
            if len(saved["annotations"]) == 6:
                break
            time.sleep(0.1)
        assert len(saved["annotations"]) == 6, saved
        go_to(119)
        assert click_label("Aux", "sync")
        go_to(120)
        assert click_label("Aux", "sync")
        edge = webdriver(endpoint + "/execute/sync", {"script":
            "let row=[...document.querySelectorAll('.annotation-row')].find(x=>x.querySelector('.track-name').textContent==='aux · sync');"
            "let right=row.querySelector('.annotation-track').getBoundingClientRect().right;"
            "let bars=[...row.querySelectorAll('.annotation-bar')].map(x=>x.getBoundingClientRect());"
            "return {rows:document.querySelectorAll('.annotation-row').length,count:bars.length,"
            "lastRight:bars.at(-1).right,trackRight:right,lateY:bars.slice(-2).map(x=>x.y)}", "args": []})
        assert edge["rows"] == 4 and edge["count"] == 3, edge
        assert edge["lastRight"] <= edge["trackRight"] + 1 and len(set(edge["lateY"])) == 2, edge
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('saveLabels').click()", "args": []})
        for _ in range(50):
            saved = json.loads((ViewerTest.root / "s5/label/curl_0.json").read_text())
            if len(saved["annotations"]) == 8:
                break
            time.sleep(0.1)
        assert len(saved["annotations"]) == 8, saved
        webdriver(endpoint + "/execute/sync", {"script":
            "let s=document.getElementById('sequence');s.value='static_0';s.dispatchEvent(new Event('change'))", "args": []})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if state["ready"] and not state["busy"]:
                break
            time.sleep(0.1)
        no_labels = webdriver(endpoint + "/execute/sync", {"script":
            "return {chips:document.querySelectorAll('.label-chip').length,"
            "text:document.getElementById('palette').textContent}", "args": []})
        assert no_labels["chips"] == 0 and "Nessuna label" in no_labels["text"], no_labels
        print("Video, annotazioni e riapertura nel browser: OK")
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
