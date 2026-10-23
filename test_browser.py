"""Optional Firefox check for seeking and playback: python test_browser.py."""

import json
import shutil
import socket
import subprocess
import time
from urllib.request import Request, urlopen

from app import LABEL_SCHEMA
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
    label_dir = ViewerTest.root / "s5/label"
    label_dir.mkdir()
    for name in ("curl_0", "curl_1", "throwing_0", "pouring_light_0"):
        (label_dir / f"{name}.json").write_text(json.dumps({
            "schema_version": LABEL_SCHEMA["schema_version"], "subject": "s5", "sequence": name,
            "fps": 30, "workflow_stage": "free", "annotations": []}))
    lease_url = ViewerTest.base + "/api/lease?subject=s5&sequence=curl_0"
    with urlopen(Request(lease_url, data=json.dumps({"token": None}).encode(),
                         headers={"Content-Type": "application/json"})) as response:
        held_token = json.load(response)["token"]
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
        observer = webdriver(endpoint + "/execute/sync", {"script":
            "return {ready:!document.getElementById('slider').disabled,"
            "banner:!document.getElementById('leaseBar').hidden,"
            "text:document.getElementById('leaseText').textContent,"
            "labelDisabled:document.querySelector('.label-chip')?.disabled,"
            "playEnabled:!document.getElementById('play').disabled}", "args": []})
        assert observer["ready"] and observer["banner"] and observer["labelDisabled"] and observer["playEnabled"], observer
        assert 'in uso' in observer["text"], observer
        calibration_readonly = webdriver(endpoint + "/execute/sync", {"script":
            "document.getElementById('modeSwitch').click();"
            "return [...document.querySelectorAll('.calibration-chip')].every(x=>x.disabled)", "args": []})
        assert calibration_readonly
        labels_url = ViewerTest.base + "/api/labels?subject=s5&sequence=curl_0"
        with urlopen(labels_url) as response:
            before = json.load(response)
        with urlopen(Request(labels_url, data=json.dumps({"revision": before["revision"],
                                                         "data": before["data"]}).encode(),
                             headers={"Content-Type": "application/json",
                                      "X-Sequence-Lease": held_token})) as response:
            assert json.load(response)["revision"] != before["revision"]
        with urlopen(Request(ViewerTest.base + "/api/lease/release?subject=s5&sequence=curl_0",
                             data=json.dumps({"token": held_token}).encode(),
                             headers={"Content-Type": "application/json"})) as response:
            assert json.load(response)["released"]
        webdriver(endpoint + "/execute/sync", {"script":
            "document.getElementById('modeSwitch').click();"
            "document.getElementById('leaseRetry').click();", "args": []})
        for _ in range(100):
            owner = webdriver(endpoint + "/execute/sync", {"script":
                "return {banner:!document.getElementById('leaseBar').hidden,"
                "labelEnabled:!document.querySelector('.label-chip')?.disabled}", "args": []})
            if not owner["banner"] and owner["labelEnabled"]:
                break
            time.sleep(.1)
        assert not owner["banner"] and owner["labelEnabled"], owner
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
        calibration_mode = webdriver(endpoint + "/execute/sync", {"script":
            "document.getElementById('modeSwitch').click();"
            "return {button:document.getElementById('modeSwitch').textContent,"
            "labelerHidden:document.getElementById('labelsPanel').hidden,"
            "timelineHidden:document.getElementById('scrub').hidden,"
            "sliderVisible:!document.getElementById('calibrationScrub').hidden,"
            "endHidden:document.getElementById('endLabel').hidden,"
            "chips:[...document.querySelectorAll('.calibration-chip')].map(x=>x.textContent),"
            "readout:document.getElementById('readout').textContent}", "args": []})
        assert calibration_mode == {"button": "Labeler", "labelerHidden": True, "timelineHidden": True,
                                    "sliderVisible": True, "endHidden": True,
                                    "chips": ["Rhigh", "Rlow", "Lhigh", "Llow", "ball", "matrix"],
                                    "readout": "Frame 1 / 120"}, calibration_mode
        for _ in range(50):
            calibration_ready = webdriver(endpoint + "/execute/sync", {"script":
                "return [...document.querySelectorAll('.calibration-chip')].every(x=>!x.disabled)", "args": []})
            if calibration_ready:
                break
            time.sleep(.1)
        assert calibration_ready
        zoom_state = webdriver(endpoint + "/execute/sync", {"script":
            "let before=imageRect('body');"
            "document.querySelector('.camera[data-camera=body] [data-zoom=in]').click();"
            "document.querySelector('.camera[data-camera=body] [data-zoom=in]').click();"
            "document.querySelector('.camera[data-camera=external] [data-zoom=in]').click();"
            "let rect=imageRect('body'),picture=document.querySelector('.camera[data-camera=body] .picture');"
            "let x=rect.left+rect.width*.45,y=rect.top+rect.height*.45;"
            "let coords=calibrationCoords('body',x,y);"
            "[...document.querySelectorAll('.calibration-chip')].find(c=>c.textContent==='Rhigh').click();"
            "picture.dispatchEvent(new MouseEvent('click',{bubbles:true,clientX:x,clientY:y}));"
            "return {body:document.querySelector('.camera[data-camera=body] .zoom-value').textContent,"
            "external:document.querySelector('.camera[data-camera=external] .zoom-value').textContent,"
            "aria:document.querySelector('.camera[data-camera=aria] .zoom-value').textContent,"
            "ratio:rect.width/before.width,coords,center:{x:Math.round(picture.getBoundingClientRect().left+"
            "picture.getBoundingClientRect().width/2),y:Math.round(picture.getBoundingClientRect().top+"
            "picture.getBoundingClientRect().height/2)}}", "args": []})
        assert zoom_state["body"] == "156%" and zoom_state["external"] == "125%" and zoom_state["aria"] == "100%", zoom_state
        assert abs(zoom_state["ratio"] - 1.5625) < .001 and zoom_state["coords"] == {"x": 35, "y": 26}, zoom_state
        for _ in range(50):
            zoom_marker = webdriver(endpoint + "/execute/sync", {"script":
                "let m=document.querySelector('.calibration-overlay[data-camera=body] .calibration-marker');"
                "return m ? {x:parseFloat(m.style.left),title:m.title} : null", "args": []})
            if zoom_marker:
                break
            time.sleep(.1)
        assert zoom_marker and "(35, 26)" in zoom_marker["title"], zoom_marker
        x, y = zoom_state["center"].values()
        webdriver(endpoint + "/actions", {"actions": [
            {"type": "key", "id": "keyboard", "actions": [
                {"type": "keyDown", "value": "\ue008"}, {"type": "pause", "duration": 0},
                {"type": "pause", "duration": 250}, {"type": "pause", "duration": 0},
                {"type": "keyUp", "value": "\ue008"}]},
            {"type": "pointer", "id": "mouse", "parameters": {"pointerType": "mouse"}, "actions": [
                {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerMove", "duration": 250, "x": x + 30, "y": y},
                {"type": "pointerUp", "button": 0}, {"type": "pause", "duration": 0}]}]})
        panned = webdriver(endpoint + "/execute/sync", {"script":
            "let p=document.querySelector('.camera[data-camera=body] .picture');"
            "let m=document.querySelector('.calibration-overlay[data-camera=body] .calibration-marker');"
            "return {pan:parseFloat(p.style.getPropertyValue('--pan-x')),marker:parseFloat(m.style.left),"
            "count:document.querySelectorAll('.calibration-overlay[data-camera=body] .calibration-marker').length}", "args": []})
        assert panned["pan"] > 20 and abs(panned["marker"] - zoom_marker["x"] - panned["pan"]) < 2, panned
        assert panned["count"] == 1, panned
        wheel_zoom = webdriver(endpoint + "/execute/sync", {"script":
            "let p=document.querySelector('.camera[data-camera=body] .picture'),r=p.getBoundingClientRect();"
            "let wheel=new WheelEvent('wheel',{bubbles:true,cancelable:true,deltaY:-100,"
            "clientX:r.left+r.width/2,clientY:r.top+r.height/2});p.dispatchEvent(wheel);"
            "let after=p.parentElement.querySelector('.zoom-value').textContent;"
            "p.parentElement.querySelector('[data-zoom=out]').click();"
            "return {prevented:wheel.defaultPrevented,after,back:p.parentElement.querySelector('.zoom-value').textContent}",
            "args": []})
        assert wheel_zoom == {"prevented": True, "after": "195%", "back": "156%"}, wheel_zoom
        reset_zoom = webdriver(endpoint + "/execute/sync", {"script":
            "document.getElementById('resetViews').click();"
            "return [...document.querySelectorAll('.camera')].map(c=>({zoom:c.querySelector('.zoom-value').textContent,"
            "pan:c.querySelector('.picture').style.getPropertyValue('--pan-x')}))", "args": []})
        assert reset_zoom == [{"zoom": "100%", "pan": "0px"}] * 3, reset_zoom

        def drop_point(camera, label, u, v):
            return webdriver(endpoint + "/execute/sync", {"script":
                "let [camera,label,u,v]=arguments;"
                "let chip=[...document.querySelectorAll('.calibration-chip')].find(x=>x.textContent===label);"
                "let picture=document.querySelector(`.camera[data-camera='${camera}'] .picture`);"
                "let video=document.getElementById(camera+'Video'), box=picture.getBoundingClientRect();"
                "let scale=Math.min(box.width/video.videoWidth,box.height/video.videoHeight);"
                "let x=Math.round(box.left+(box.width-video.videoWidth*scale)/2+u*video.videoWidth*scale);"
                "let y=Math.round(box.top+(box.height-video.videoHeight*scale)/2+v*video.videoHeight*scale);"
                "let transfer=new DataTransfer();"
                "chip.dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:transfer}));"
                "let over=new DragEvent('dragover',{bubbles:true,cancelable:true,dataTransfer:transfer,clientX:x,clientY:y});"
                "picture.dispatchEvent(over);"
                "picture.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer,clientX:x,clientY:y}));"
                "return {accepted:over.defaultPrevented,data:transfer.getData('application/x-exo-calibration')}",
                "args": [camera, label, u, v]})

        assert drop_point("body", "Rhigh", .25, .25) == {"accepted": True, "data": "Rhigh"}
        for _ in range(50):
            selected_after_drop = webdriver(endpoint + "/execute/sync", {"script":
                "return document.querySelector('.calibration-chip[aria-pressed=true]')?.textContent || null", "args": []})
            if selected_after_drop == "Rlow":
                break
            time.sleep(.1)
        assert selected_after_drop == "Rlow", selected_after_drop
        assert drop_point("external", "Llow", .75, .5) == {"accepted": True, "data": "Llow"}
        for _ in range(50):
            selected_after_drop = webdriver(endpoint + "/execute/sync", {"script":
                "return document.querySelector('.calibration-chip[aria-pressed=true]')?.textContent || null", "args": []})
            if selected_after_drop == "ball":
                break
            time.sleep(.1)
        assert selected_after_drop == "ball", selected_after_drop
        for _ in range(50):
            marks = webdriver(endpoint + "/execute/sync", {"script":
                "return [...document.querySelectorAll('.calibration-marker')].map(x=>x.title)", "args": []})
            if len(marks) == 2:
                break
            time.sleep(.1)
        assert len(marks) == 2 and any("Rhigh" in mark for mark in marks) and any("Llow" in mark for mark in marks), marks
        dot_size = webdriver(endpoint + "/execute/sync", {"script":
            "return getComputedStyle(document.querySelector('.calibration-marker'),'::before').width", "args": []})
        assert dot_size == "8px", dot_size
        matrix_draft = webdriver(endpoint + "/execute/sync", {"script":
            "[...document.querySelectorAll('.calibration-chip')].find(x=>x.textContent==='matrix').click();"
            "let picture=document.querySelector('.camera[data-camera=body] .picture');"
            "let video=document.getElementById('bodyVideo'),box=picture.getBoundingClientRect();"
            "let scale=Math.min(box.width/video.videoWidth,box.height/video.videoHeight);"
            "for(let [u,v] of [[.15,.2],[.45,.2],[.4,.55],[.15,.55]]){"
            "let x=Math.round(box.left+(box.width-video.videoWidth*scale)/2+u*video.videoWidth*scale);"
            "let y=Math.round(box.top+(box.height-video.videoHeight*scale)/2+v*video.videoHeight*scale);"
            "picture.dispatchEvent(new MouseEvent('click',{bubbles:true,clientX:x,clientY:y}));}"
            "return [...document.querySelectorAll('.calibration-chip')].find(x=>x.textContent==='matrix').getAttribute('aria-pressed')", "args": []})
        assert matrix_draft == "true", matrix_draft
        for _ in range(50):
            draft_vertices = webdriver(endpoint + "/execute/sync", {"script":
                "return document.querySelectorAll('.calibration-overlay[data-camera=body] .matrix-outline circle').length", "args": []})
            if draft_vertices == 4:
                break
            time.sleep(.1)
        assert draft_vertices == 4, draft_vertices
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('saveLabels').click()", "args": []})
        unsaved_matrix = webdriver(endpoint + "/execute/sync", {"script":
            "return document.getElementById('status').textContent", "args": []})
        assert "Completa o annulla" in unsaved_matrix, unsaved_matrix
        completed_matrix = webdriver(endpoint + "/execute/sync", {"script":
            "[...document.querySelectorAll('.matrix-actions button')].find(x=>x.textContent==='Completa poligono').click();"
            "return {polygon:!!document.querySelector('.calibration-overlay[data-camera=body] .matrix-outline polygon'),"
            "delete:!!document.querySelector('.calibration-overlay[data-camera=body] .matrix-delete')}", "args": []})
        assert completed_matrix == {"polygon": True, "delete": True}, completed_matrix
        vertex_start = webdriver(endpoint + "/execute/sync", {"script":
            "let vertex=document.querySelector('.calibration-overlay[data-camera=body] .matrix-vertex');"
            "let box=vertex.getBoundingClientRect();"
            "return {x:Math.round(box.left+box.width/2),y:Math.round(box.top+box.height/2),label:vertex.getAttribute('aria-label')}",
            "args": []})
        assert "Vertice 1" in vertex_start["label"], vertex_start
        vertex_target = webdriver(endpoint + "/execute/sync", {"script":
            "let video=document.getElementById('bodyVideo'),box=video.parentElement.getBoundingClientRect();"
            "let scale=Math.min(box.width/video.videoWidth,box.height/video.videoHeight);"
            "return {x:Math.round(box.left+(box.width-video.videoWidth*scale)/2+.25*video.videoWidth*scale),"
            "y:Math.round(box.top+(box.height-video.videoHeight*scale)/2+.1*video.videoHeight*scale)}", "args": []})
        webdriver(endpoint + "/actions", {"actions": [{"type": "pointer", "id": "mouse",
            "parameters": {"pointerType": "mouse"}, "actions": [
                {"type": "pointerMove", "duration": 0, "x": vertex_start["x"], "y": vertex_start["y"]},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerMove", "duration": 250, "x": vertex_target["x"], "y": vertex_target["y"]},
                {"type": "pointerUp", "button": 0}]}]})
        moved_vertex = webdriver(endpoint + "/execute/sync", {"script":
            "let vertex=document.querySelector('.calibration-overlay[data-camera=body] .matrix-vertex');"
            "return {label:vertex.getAttribute('aria-label'),"
            "polygon:document.querySelector('.calibration-overlay[data-camera=body] polygon').getAttribute('points'),"
            "marks:document.querySelectorAll('.calibration-marker').length}", "args": []})
        assert "x 47, y 42" in moved_vertex["label"] and moved_vertex["marks"] == 2, moved_vertex
        nudged_vertex = webdriver(endpoint + "/execute/sync", {"script":
            "let vertex=document.querySelector('.calibration-overlay[data-camera=body] .matrix-vertex');"
            "vertex.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true,cancelable:true}));"
            "return vertex.getAttribute('aria-label')", "args": []})
        assert "x 48, y 42" in nudged_vertex, nudged_vertex
        kept_draft = webdriver(endpoint + "/execute/sync", {"script":
            "document.getElementById('modeSwitch').click();document.getElementById('modeSwitch').click();"
            "return {marks:document.querySelectorAll('.calibration-marker').length,"
            "saveEnabled:!document.getElementById('saveLabels').disabled}", "args": []})
        assert kept_draft == {"marks": 2, "saveEnabled": True}, kept_draft
        webdriver(endpoint + "/execute/sync", {"script":
            "let s=document.getElementById('calibrationSlider');s.value=1;s.dispatchEvent(new Event('input',{bubbles:true}))", "args": []})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if state["ready"] and not state["busy"] and state["frame"] == 1:
                break
            time.sleep(.1)
        assert state["frame"] == 1 and not state["busy"], state
        click_placed = webdriver(endpoint + "/execute/sync", {"script":
            "let chip=[...document.querySelectorAll('.calibration-chip')].find(x=>x.textContent==='ball');"
            "chip.click();"
            "let picture=document.querySelector('.camera[data-camera=aria] .picture');"
            "let video=document.getElementById('ariaVideo'),box=picture.getBoundingClientRect();"
            "let x=Math.round(box.left+box.width/2),y=Math.round(box.top+box.height/2);"
            "picture.dispatchEvent(new MouseEvent('click',{bubbles:true,clientX:x,clientY:y}));"
            "return {selected:[...document.querySelectorAll('.calibration-chip')].find(x=>x.textContent==='ball').getAttribute('aria-pressed'),"
            "placing:document.getElementById('videos').classList.contains('calibration-placing')}",
            "args": []})
        for _ in range(50):
            click_placed = webdriver(endpoint + "/execute/sync", {"script":
                "let chip=[...document.querySelectorAll('.calibration-chip')].find(x=>x.textContent==='ball');"
                "return {selected:chip.getAttribute('aria-pressed'),"
                "placing:document.getElementById('videos').classList.contains('calibration-placing')}", "args": []})
            if click_placed == {"selected": "false", "placing": False}:
                break
            time.sleep(.1)
        assert click_placed == {"selected": "false", "placing": False}, click_placed
        for _ in range(50):
            aria_marker = webdriver(endpoint + "/execute/sync", {"script":
                "let marker=document.querySelector('.calibration-overlay[data-camera=aria] .calibration-marker');"
                "if(!marker)return null;let overlay=marker.parentElement.getBoundingClientRect();"
                "return {title:marker.title,x:Math.round(overlay.left+parseFloat(marker.style.left)),"
                "y:Math.round(overlay.top+parseFloat(marker.style.top))}", "args": []})
            if aria_marker:
                break
            time.sleep(.1)
        assert aria_marker and "ball" in aria_marker["title"], aria_marker
        aria_target = webdriver(endpoint + "/execute/sync", {"script":
            "let video=document.getElementById('ariaVideo'),box=video.parentElement.getBoundingClientRect();"
            "let scale=Math.min(box.width/video.videoWidth,box.height/video.videoHeight);"
            "return {x:Math.round(box.left+(box.width-video.videoWidth*scale)/2+.75*video.videoWidth*scale),"
            "y:Math.round(box.top+(box.height-video.videoHeight*scale)/2+.25*video.videoHeight*scale)}", "args": []})
        webdriver(endpoint + "/actions", {"actions": [{"type": "pointer", "id": "mouse",
            "parameters": {"pointerType": "mouse"}, "actions": [
                {"type": "pointerMove", "duration": 0, "x": aria_marker["x"], "y": aria_marker["y"]},
                {"type": "pointerDown", "button": 0},
                {"type": "pointerMove", "duration": 250, "x": aria_target["x"], "y": aria_target["y"]},
                {"type": "pointerUp", "button": 0}]}]})
        moved_marker = webdriver(endpoint + "/execute/sync", {"script":
            "let markers=[...document.querySelectorAll('.calibration-overlay[data-camera=aria] .calibration-marker')];"
            "return {count:markers.length,title:markers[0]?.title,dialog:!!document.querySelector('dialog[open]')}", "args": []})
        assert moved_marker["count"] == 1 and "ball" in moved_marker["title"] and not moved_marker["dialog"], moved_marker
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('saveLabels').click()", "args": []})
        calibration_file = ViewerTest.root / "s5/camera_calibration/curl_0.json"
        for _ in range(50):
            if calibration_file.exists():
                calibration = json.loads(calibration_file.read_text())
                if len(calibration["points"]) == 3:
                    break
            time.sleep(.1)
        assert len(calibration["points"]) == 3, calibration
        assert len(calibration["matrix"]) == 1, calibration
        matrix = calibration["matrix"][0]
        assert matrix["camera"] == "body" and matrix["frame"] == 1 and len(matrix["vertices"]) == 4, matrix
        assert matrix["vertices"][0] == {"x": 48, "y": 42}, matrix
        points = {(item["camera"], item["frame"], item["label"]): (item["x"], item["y"])
                  for item in calibration["points"]}
        assert points[("body", 1, "Rhigh")] == (47, 35), points
        assert all(abs(a - b) <= 1 for a, b in zip(points[("external", 1, "Llow")], (47, 24))), points
        assert all(abs(a - b) <= 1 for a, b in zip(points[("aria", 2, "ball")], (47, 12))), points
        for _ in range(50):
            saved_in_browser = webdriver(endpoint + "/execute/sync", {"script":
                "return document.getElementById('saveLabels').disabled &&"
                "document.getElementById('status').textContent.includes('Calibrazione salvata')", "args": []})
            if saved_in_browser:
                break
            time.sleep(.1)
        assert saved_in_browser
        webdriver(endpoint + "/url", {"url": ViewerTest.base + "/?calibration-test=1"})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if state["ready"] and not state["busy"]:
                break
            time.sleep(.1)
        assert state["ready"] and not state["busy"], state
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('modeSwitch').click()", "args": []})
        for _ in range(50):
            restored_points = webdriver(endpoint + "/execute/sync", {"script":
                "return [...document.querySelectorAll('.calibration-marker')].map(x=>x.title)", "args": []})
            if len(restored_points) == 2:
                break
            time.sleep(.1)
        assert len(restored_points) == 2 and any("Rhigh" in title for title in restored_points), restored_points
        restored_matrix = webdriver(endpoint + "/execute/sync", {"script":
            "return !!document.querySelector('.calibration-overlay[data-camera=body] .matrix-outline polygon')", "args": []})
        assert restored_matrix
        matrix_deleted = webdriver(endpoint + "/execute/sync", {"script":
            "let remove=document.querySelector('.calibration-overlay[data-camera=body] .matrix-delete');"
            "let original=window.confirm;window.confirm=()=>true;remove.click();window.confirm=original;"
            "return !document.querySelector('.calibration-overlay[data-camera=body] .matrix-outline polygon')", "args": []})
        assert matrix_deleted
        deleted = webdriver(endpoint + "/execute/sync", {"script":
            "let marker=[...document.querySelectorAll('.calibration-marker')].find(x=>x.textContent==='Rhigh');"
            "let original=window.confirm;window.confirm=()=>true;marker.click();window.confirm=original;"
            "return {remaining:[...document.querySelectorAll('.calibration-marker')].map(x=>x.textContent),"
            "saveEnabled:!document.getElementById('saveLabels').disabled}", "args": []})
        assert deleted == {"remaining": ["Llow"], "saveEnabled": True}, deleted
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('saveLabels').click()", "args": []})
        for _ in range(50):
            calibration = json.loads(calibration_file.read_text())
            if len(calibration["points"]) == 2:
                break
            time.sleep(.1)
        assert len(calibration["points"]) == 2 and not calibration["matrix"] and not any(
            item["label"] == "Rhigh" for item in calibration["points"]), calibration
        for _ in range(50):
            if webdriver(endpoint + "/execute/sync", {"script":
                    "return document.getElementById('saveLabels').disabled", "args": []}):
                break
            time.sleep(.1)
        back_to_labeler = webdriver(endpoint + "/execute/sync", {"script":
            "document.getElementById('modeSwitch').click();"
            "return {button:document.getElementById('modeSwitch').textContent,"
            "scrub:!document.getElementById('scrub').hidden,"
            "calibrationHidden:document.getElementById('calibrationPalette').hidden,"
            "labelsVisible:!document.getElementById('labelsPanel').hidden}", "args": []})
        assert back_to_labeler == {"button": "Camera calibration", "scrub": True,
                                   "calibrationHidden": True, "labelsVisible": True}, back_to_labeler
        def label_center(level, label):
            return webdriver(endpoint + "/execute/sync", {"script":
                "document.getElementById('labelsPanel').open=true;"
                "let chip=[...document.querySelectorAll('.label-chip')].find(x=>x.textContent===arguments[1]"
                "&&x.parentElement.querySelector('strong').textContent===arguments[0]);"
                "if(!chip)return null;let r=chip.getBoundingClientRect();"
                "return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}", "args": [level, label]})
        for level, label, expected in [("Low", "flexion", "Elbow flexion lifting the dumbbell"),
                                       ("Mid", "curl_cycle", "One elbow flexion-extension"),
                                       ("High", "curl", "Alternating elbow flexion-extension")]:
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
            "let chip=[...document.querySelectorAll('.label-chip')].find(x=>x.textContent==='invalid');"
            "chip.focus();let t=document.getElementById('labelTooltip');"
            "return {visible:!t.hidden,text:t.textContent,description:chip.getAttribute('aria-description'),"
            "syncAbsent:![...document.querySelectorAll('.label-chip')].some(x=>x.textContent==='sync')}" , "args": []})
        assert focus_tip["visible"] and "Segment to exclude" in focus_tip["text"] and focus_tip["syncAbsent"], focus_tip
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
            "end:document.getElementById('editEnd').value,"
            "sideHidden:document.getElementById('editSide').closest('label').hidden,"
            "loadHidden:document.getElementById('editLoad').closest('label').hidden};"
            "document.getElementById('cancelEdit').click();return result", "args": []})
        assert from_name == {"open": True, "start": "10", "end": "20",
                             "sideHidden": True, "loadHidden": True}, from_name
        webdriver(endpoint + "/execute/sync", {"script":
            "let h=document.querySelector('.annotation-handle.end');h.focus();"
            "h.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true,cancelable:true}));"
            "document.querySelector('.annotation-caption').click();"
            "document.querySelector('#editForm button[type=submit]').click()", "args": []})
        edited = webdriver(endpoint + "/execute/sync", {"script":
            "return {row:document.querySelector('.annotation-caption').textContent,dialog:document.getElementById('editDialog').open}", "args": []})
        assert "frame 10–21" in edited["row"] and "side:" not in edited["row"] and not edited["dialog"], edited
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('saveLabels').click()", "args": []})
        for _ in range(50):
            if not webdriver(endpoint + "/execute/sync", {"script":
                    "return document.getElementById('status').textContent", "args": []}).startswith("Intervallo"):
                break
            time.sleep(0.1)
        saved = json.loads((ViewerTest.root / "s5/label/curl_0.json").read_text())
        assert saved["annotations"][0]["start_frame"] == 10 and saved["annotations"][0]["end_frame"] == 21, saved
        assert saved["annotations"][0]["t_start"] == 9 / 30 and saved["annotations"][0]["t_end"] == 21 / 30, saved
        assert saved["annotations"][0]["side"] is None, saved
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
        assert "frame 10–21" in restored, restored
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
        mid_parent = webdriver(endpoint + "/execute/sync", {"script":
            "document.querySelectorAll('.annotation-caption')[1].click();"
            "let field=document.getElementById('editParents'),choice=field.querySelector('label');"
            "let result={visible:!field.hidden,text:choice.textContent,checked:choice.querySelector('input').checked,"
            "side:!document.getElementById('editSide').closest('label').hidden,"
            "load:!document.getElementById('editLoad').closest('label').hidden};"
            "choice.click();document.getElementById('editRep').value='2';"
            "document.getElementById('editSide').value='left';"
            "document.querySelector('#editForm button[type=submit]').click();return result", "args": []})
        assert mid_parent["visible"] and "#1 curl" in mid_parent["text"] and not mid_parent["checked"], mid_parent
        assert mid_parent["side"] and not mid_parent["load"], mid_parent
        go_to(32)
        assert click_label("Low", "flexion")
        go_to(34)
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('endLabel').click()", "args": []})
        low_parent = webdriver(endpoint + "/execute/sync", {"script":
            "document.querySelectorAll('.annotation-caption')[2].click();"
            "let choice=document.querySelector('#parentChoices label');"
            "let result={text:choice.textContent,checked:choice.querySelector('input').checked,"
            "load:!document.getElementById('editLoad').closest('label').hidden};"
            "document.getElementById('editLoad').value='both';"
            "document.getElementById('editContact').value='both';"
            "document.getElementById('editTarget').value='dumbbells';"
            "choice.click();document.querySelector('#editForm button[type=submit]').click();return result", "args": []})
        assert "#2 curl_cycle" in low_parent["text"] and not low_parent["checked"], low_parent
        assert low_parent["load"], low_parent
        go_to(60)
        assert click_label("Aux", "invalid")
        go_to(62)
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('endLabel').click()", "args": []})
        interval = webdriver(endpoint + "/execute/sync", {"script":
            "return {rows:[...document.querySelectorAll('.annotation-caption')].map(x=>x.textContent),"
            "end:document.getElementById('endLabel').disabled}", "args": []})
        assert len(interval["rows"]) == 4 and interval["end"] and "frame 60–62" in interval["rows"][3], interval
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('saveLabels').click()", "args": []})
        for _ in range(50):
            saved = json.loads((ViewerTest.root / "s5/label/curl_0.json").read_text())
            if len(saved["annotations"]) == 4:
                break
            time.sleep(0.1)
        assert len(saved["annotations"]) == 4, saved
        assert saved["annotations"][0]["end_frame"] > 21, saved
        assert saved["annotations"][1]["rep"] == 2 and saved["annotations"][1]["parents"] == [1], saved
        assert saved["annotations"][1]["side"] == "left", saved
        assert saved["annotations"][2]["parents"] == [2] and saved["annotations"][2]["external_load"] == "both", saved
        assert saved["annotations"][2]["contact_points"] == "both" and saved["annotations"][2]["target"] == "dumbbells", saved
        assert saved["annotations"][2]["phase"] is None, saved
        assert saved["annotations"][3]["label"] == "invalid" and saved["annotations"][3]["t_start"] == 59 / 30, saved
        assert saved["annotations"][3]["t_end"] == 62 / 30, saved
        reopened_parent = webdriver(endpoint + "/execute/sync", {"script":
            "document.querySelector('[data-record-id=\"2\"] .annotation-caption').click();"
            "let choice=document.querySelector('#parentChoices label'),"
            "result={checked:choice.querySelector('input').checked,"
            "highlight:getComputedStyle(choice).backgroundColor};"
            "document.getElementById('cancelEdit').click();return result", "args": []})
        assert reopened_parent == {"checked": True, "highlight": "rgb(40, 67, 88)"}, reopened_parent
        go_to(12)
        assert click_label("High", "curl")
        go_to(18)
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('endLabel').click()", "args": []})
        go_to(32)
        assert click_label("Low", "flexion")
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
        assert grouped["names"] == ["high · curl", "mid · curl_cycle", "low · flexion", "aux · invalid"], grouped
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
        multiple_parents = webdriver(endpoint + "/execute/sync", {"script":
            "document.querySelector('[data-record-id=\"2\"] .annotation-caption').click();"
            "let labels=[...document.querySelectorAll('#parentChoices label')],"
            "before=labels.map(x=>x.querySelector('input').checked);"
            "labels[1].click();let after=labels.map(x=>x.querySelector('input').checked);"
            "document.querySelector('#editForm button[type=submit]').click();"
            "return {count:labels.length,before,after}", "args": []})
        assert multiple_parents == {"count": 2, "before": [True, False], "after": [True, True]}, multiple_parents
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('saveLabels').click()", "args": []})
        for _ in range(50):
            saved = json.loads((ViewerTest.root / "s5/label/curl_0.json").read_text())
            if len(saved["annotations"]) == 6:
                break
            time.sleep(0.1)
        assert len(saved["annotations"]) == 6, saved
        assert saved["annotations"][1]["parents"] == [1, 5], saved
        go_to(119)
        assert click_label("Aux", "invalid")
        go_to(120)
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('endLabel').click()", "args": []})
        assert click_label("Aux", "invalid")
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('endLabel').click()", "args": []})
        edge = webdriver(endpoint + "/execute/sync", {"script":
            "let row=[...document.querySelectorAll('.annotation-row')].find(x=>x.querySelector('.track-name').textContent==='aux · invalid');"
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
        webdriver(endpoint + "/execute/sync", {"script":
            "let s=document.getElementById('sequence');s.value='throwing_0';s.dispatchEvent(new Event('change'))", "args": []})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if state["ready"] and not state["busy"]:
                break
            time.sleep(0.1)
        assert state["ready"] and not state["busy"], state
        assert click_label("High", "throwing_catching")
        go_to(80)
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('endLabel').click()", "args": []})
        go_to(5)
        assert click_label("Mid", "pass_cycle")
        go_to(70)
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('endLabel').click()", "args": []})
        go_to(10)
        assert click_label("Low", "ball_throw")
        go_to(60)
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('endLabel').click()", "args": []})
        go_to(20)
        assert click_label("Atomic", "move · wind_up")
        go_to(25)
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('endLabel').click()", "args": []})
        atomic = webdriver(endpoint + "/execute/sync", {"script":
            "document.querySelector('[data-record-id=\"4\"] .annotation-caption').click();"
            "let parent=document.querySelector('#parentChoices label');"
            "let result={parent:parent.textContent,load:!document.getElementById('editLoad').closest('label').hidden,"
            "target:!document.getElementById('editTarget').closest('label').hidden,"
            "side:!document.getElementById('editSide').closest('label').hidden,"
            "phase:document.getElementById('editPhase').value};"
            "parent.click();document.getElementById('editLoad').value='both';"
            "document.getElementById('editContact').value='both';"
            "document.querySelector('#editForm button[type=submit]').click();return result", "args": []})
        assert "#3 ball_throw" in atomic["parent"] and atomic["load"] and atomic["target"] and atomic["side"], atomic
        assert atomic["phase"] == "wind_up", atomic
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('saveLabels').click()", "args": []})
        for _ in range(50):
            path = ViewerTest.root / "s5/label/throwing_0.json"
            if path.exists() and len(json.loads(path.read_text())["annotations"]) == 4:
                break
            time.sleep(0.1)
        records = json.loads(path.read_text())["annotations"]
        assert records[3]["level"] == "atomic" and records[3]["phase"] == "wind_up", records
        assert records[3]["parents"] == [3] and records[3]["external_load"] == "both", records
        webdriver(endpoint + "/execute/sync", {"script":
            "let s=document.getElementById('sequence');s.value='pouring_light_0';"
            "s.dispatchEvent(new Event('change'))", "args": []})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if state["ready"] and not state["busy"]:
                break
            time.sleep(0.1)
        assert state["ready"] and not state["busy"], state
        go_to(5)
        assert click_label("Low", "pour_glass")
        go_to(8)
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('endLabel').click()", "args": []})
        location = webdriver(endpoint + "/execute/sync", {"script":
            "document.querySelector('.annotation-caption').click();"
            "let field=document.getElementById('editLocationSelect');"
            "let result={visible:!field.closest('label').hidden,"
            "values:[...field.options].map(option=>option.value)};"
            "field.value='g9';document.querySelector('#editForm button[type=submit]').click();"
            "return result", "args": []})
        assert location["visible"] and location["values"] == ["", "g1", "g2", "g3", "g4", "g5", "g6", "g7", "g8", "g9"], location
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('saveLabels').click()", "args": []})
        path = ViewerTest.root / "s5/label/pouring_light_0.json"
        for _ in range(50):
            if path.exists() and json.loads(path.read_text())["annotations"]:
                break
            time.sleep(0.1)
        assert json.loads(path.read_text())["annotations"][0]["location"] == "g9"
        old_path = ViewerTest.root / "s5/label/curl_1.json"
        old = {"schema_version": "0.1", "subject": "s5", "sequence": "curl_1", "fps": 30,
               "annotations": [{**records[0], "label": "curl"}]}
        old_path.parent.mkdir(exist_ok=True)
        old_path.write_text(json.dumps(old))
        webdriver(endpoint + "/execute/sync", {"script":
            "let s=document.getElementById('sequence');s.value='curl_1';s.dispatchEvent(new Event('change'))", "args": []})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if state["ready"] and not state["busy"] and "migrazione manuale" in state["status"]:
                break
            time.sleep(0.1)
        assert state["ready"] and not state["busy"] and "migrazione manuale" in state["status"], state
        assert webdriver(endpoint + "/execute/sync", {"script":
            "return document.querySelectorAll('.label-chip').length===0 && document.getElementById('saveLabels').disabled", "args": []})
        assert json.loads(old_path.read_text()) == old
        webdriver(endpoint + "/execute/sync", {"script":
            "let s=document.getElementById('sequence');s.value='drilling_vertical_0';"
            "s.dispatchEvent(new Event('change'))", "args": []})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if state["ready"] and not state["busy"]:
                break
            time.sleep(0.1)
        assert state["ready"] and not state["busy"], state
        guide = {"script":
            "return {visible:!document.getElementById('workflow').hidden,"
            "text:document.getElementById('workflowText').textContent,"
            "groups:[...document.querySelectorAll('.palette-group strong')].map(x=>x.textContent),"
            "button:document.getElementById('workflowAdvance').textContent}", "args": []}
        initial_guide = webdriver(endpoint + "/execute/sync", guide)
        assert initial_guide["visible"] and initial_guide["groups"] == ["High"], initial_guide
        cancelled = webdriver(endpoint + "/execute/sync", {"script":
            "window.confirm=()=>false;document.getElementById('workflowAdvance').click();"
            "return document.getElementById('workflowText').textContent", "args": []})
        assert cancelled == initial_guide["text"], cancelled
        first_step = webdriver(endpoint + "/execute/sync", {"script":
            "window.confirm=message=>{window.guidePrompt=message;return true};"
            "document.getElementById('workflowAdvance').click();"
            "return {prompt:window.guidePrompt,"
            "groups:[...document.querySelectorAll('.palette-group strong')].map(x=>x.textContent)}", "args": []})
        assert "high" in first_step["prompt"] and first_step["groups"] == ["High", "Mid"], first_step
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('saveLabels').click()", "args": []})
        guide_path = ViewerTest.root / "s5/label/drilling_vertical_0.json"
        for _ in range(50):
            if (guide_path.exists() and json.loads(guide_path.read_text())["workflow_stage"] == "mid"
                    and webdriver(endpoint + "/execute/sync", {"script":
                        "return !document.getElementById('saveLabels').disabled ? false : "
                        "document.getElementById('status').textContent.startsWith('Annotazioni salvate')", "args": []})):
                break
            time.sleep(0.1)
        assert json.loads(guide_path.read_text())["workflow_stage"] == "mid"
        webdriver(endpoint + "/execute/sync", {"script":
            "window.__guideReloadMarker=true;window.location.reload()", "args": []})
        for _ in range(100):
            try:
                reloaded = webdriver(endpoint + "/execute/sync", {"script":
                    "return document.readyState==='complete' && !window.__guideReloadMarker && "
                    "!document.getElementById('slider').disabled && !document.getElementById('seekState').textContent", "args": []})
            except (OSError, KeyError):
                reloaded = False
            if reloaded:
                break
            time.sleep(0.1)
        assert reloaded, "La pagina non si è ricaricata"
        webdriver(endpoint + "/execute/sync", {"script":
            "let s=document.getElementById('sequence');s.value='drilling_vertical_0';"
            "s.dispatchEvent(new Event('change'))", "args": []})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if state["ready"] and not state["busy"] and "drilling_vertical_0" in webdriver(
                    endpoint + "/execute/sync", {"script":
                        "return document.getElementById('selectionSummary').textContent", "args": []}):
                break
            time.sleep(0.1)
        assert state["ready"] and not state["busy"], state
        resumed = webdriver(endpoint + "/execute/sync", guide)
        assert resumed["visible"] and "Mid" in resumed["text"] and resumed["groups"] == ["High", "Mid"], resumed
        steps = webdriver(endpoint + "/execute/sync", {"script":
            "window.confirm=()=>true;let button=document.getElementById('workflowAdvance'),result=[];"
            "for(let i=0;i<3;i++){button.click();result.push({"
            "groups:[...document.querySelectorAll('.palette-group strong')].map(x=>x.textContent),"
            "visible:!document.getElementById('workflow').hidden});}return result", "args": []})
        assert steps == [
            {"groups": ["High", "Mid", "Low"], "visible": True},
            {"groups": ["High", "Mid", "Low", "Atomic"], "visible": True},
            {"groups": ["High", "Mid", "Low", "Atomic", "Aux"], "visible": False}], steps
        webdriver(endpoint + "/execute/sync", {"script": "document.getElementById('saveLabels').click()", "args": []})
        for _ in range(50):
            if (json.loads(guide_path.read_text())["workflow_stage"] == "free"
                    and webdriver(endpoint + "/execute/sync", {"script":
                        "return document.getElementById('status').textContent.startsWith('Annotazioni salvate')", "args": []})):
                break
            time.sleep(0.1)
        assert json.loads(guide_path.read_text())["workflow_stage"] == "free"
        webdriver(endpoint + "/execute/sync", {"script":
            "let s=document.getElementById('sequence');s.value='infinity_tracing_0';"
            "s.dispatchEvent(new Event('change'))", "args": []})
        for _ in range(100):
            state = webdriver(endpoint + "/execute/sync", read)
            if state["ready"] and not state["busy"]:
                break
            time.sleep(0.1)
        assert state["ready"] and not state["busy"], state
        skipped = webdriver(endpoint + "/execute/sync", {"script":
            "window.confirm=()=>true;let button=document.getElementById('workflowAdvance');"
            "let before=[...document.querySelectorAll('.palette-group strong')].map(x=>x.textContent);"
            "button.click();let middle=[...document.querySelectorAll('.palette-group strong')].map(x=>x.textContent);"
            "button.click();return {before,middle,after:[...document.querySelectorAll('.palette-group strong')]"
            ".map(x=>x.textContent),hidden:document.getElementById('workflow').hidden}", "args": []})
        assert skipped == {"before": ["High"], "middle": ["High", "Mid"],
                           "after": ["High", "Mid", "Aux"], "hidden": True}, skipped
        print("Video, annotazioni e guida nel browser: OK")
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
