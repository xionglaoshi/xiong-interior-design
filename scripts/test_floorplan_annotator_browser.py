#!/usr/bin/env python3
"""Run browser interaction checks against a generated two-layer annotator HTML."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import time
import subprocess
import sys

from test_model_viewer_browser import DevTools, chrome_binary, wait_for


def main() -> int:
    parser = argparse.ArgumentParser(description="在隔离无头Chrome中验收平面注释器交互。")
    parser.add_argument("--html", required=True, type=Path, help="已生成的独立注释器HTML")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--chrome", help="Chrome/Chromium路径")
    args = parser.parse_args()
    html_path = args.html.expanduser().resolve()
    if not html_path.is_file() or html_path.suffix.lower() != ".html":
        parser.error("--html 必须指向已存在的HTML文件")

    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    import threading
    import subprocess
    import urllib.request

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, _format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a, **kw: QuietHandler(*a, directory=str(html_path.parent), **kw))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    with tempfile.TemporaryDirectory(prefix="floorplan-annotator-test-") as profile:
        download_dir = Path(profile) / "downloads"
        download_dir.mkdir()
        process = subprocess.Popen([
            chrome_binary(args.chrome), "--headless=new", "--enable-unsafe-swiftshader",
            "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
            "--remote-allow-origins=http://localhost", f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check", "--disable-background-networking", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cdp = None
        try:
            active_port = Path(profile) / "DevToolsActivePort"
            debug_port = wait_for(lambda: int(active_port.read_text().splitlines()[0]) if active_port.exists() else None, 10, "Chrome DevTools")
            with urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json/list", timeout=3) as response:
                page = next(t for t in json.load(response) if t.get("type") == "page")
            cdp = DevTools(page["webSocketDebuggerUrl"])
            cdp.command("Page.enable")
            cdp.command("Runtime.enable")
            cdp.command("Browser.setDownloadBehavior", {"behavior":"allow", "downloadPath":str(download_dir), "eventsEnabled":True})
            cdp.command("Emulation.setDeviceMetricsOverride", {"width":1280,"height":850,"deviceScaleFactor":1,"mobile":False})
            url = f"http://127.0.0.1:{port}/{html_path.name}"
            cdp.command("Page.navigate", {"url":url})
            wait_for(lambda: cdp.evaluate("!!document.querySelector('#view-existing') && !!document.querySelector('#view-layout')"), args.timeout, "annotator page")
            cdp.evaluate("localStorage.clear();document.querySelector('#view-existing').click()")

            def canvas_point(x_ratio, y_ratio):
                box = cdp.evaluate("(()=>{const r=document.querySelector('svg[aria-label=\"现状图\"]').getBoundingClientRect();return{x:r.left,y:r.top,w:r.width,h:r.height}})()")
                return box["x"]+box["w"]*x_ratio, box["y"]+box["h"]*y_ratio

            def draw_rect():
                cdp.evaluate("document.querySelector('[data-tool=rect]').click()")
                sx,sy=canvas_point(.2,.2); ex,ey=canvas_point(.45,.45)
                cdp.command("Input.dispatchMouseEvent",{"type":"mousePressed","x":sx,"y":sy,"button":"left","clickCount":1})
                cdp.command("Input.dispatchMouseEvent",{"type":"mouseMoved","x":ex,"y":ey,"button":"left","buttons":1})
                cdp.command("Input.dispatchMouseEvent",{"type":"mouseReleased","x":ex,"y":ey,"button":"left","buttons":0})

            def count(layer):
                return cdp.evaluate(f"(()=>{{const c=JSON.parse(document.querySelector('#drawing-config').textContent);const h=c.{'existingHash' if layer == 'existing' else 'layoutHash'};const k=`xiong-floorplan-markup:v1:${{c.projectId}}:{layer}:${{h}}`;return JSON.parse(localStorage.getItem(k)||'[]').length}})()")

            draw_rect()
            if count("existing") != 1: raise AssertionError("Mouse-drawn rectangle was not stored")
            cdp.evaluate("document.querySelector('#view-layout').click()")
            if count("layout") != 0: raise AssertionError("Markup leaked to the other layer")
            cdp.evaluate("document.querySelector('#view-existing').click();document.querySelector('#undo').click()")
            if count("existing") != 0: raise AssertionError("Undo failed")
            draw_rect()
            cdp.evaluate("window.confirm=()=>true;document.querySelector('#clear').click()")
            if count("existing") != 0: raise AssertionError("Clear failed")
            draw_rect()
            cdp.command("Page.reload", {"ignoreCache":True})
            wait_for(lambda: cdp.evaluate("!!document.querySelector('#view-existing')"), args.timeout, "page reload")
            persisted = wait_for(lambda: cdp.evaluate("document.querySelectorAll('[data-mark-layer=existing] .mark-shape').length"), 3, "saved rectangle after reload")
            if persisted < 1: raise AssertionError(f"Markup did not persist after reload: {persisted}")

            cdp.evaluate("document.querySelector('#export').click()")
            exported_path = wait_for(
                lambda: next((p for p in download_dir.glob("*.json") if p.is_file() and not p.name.endswith(".crdownload")), None),
                5, "annotator's exported JSON file")
            exported = json.loads(exported_path.read_text(encoding="utf-8"))
            config = json.loads(cdp.evaluate("document.querySelector('#drawing-config').textContent"))
            if exported.get("schema_version") != 1 or exported.get("project_id") != config["projectId"]:
                raise AssertionError("Exported JSON project/schema metadata mismatch")
            if exported.get("canvas_px") != [config["width"], config["height"]]:
                raise AssertionError("Exported JSON canvas dimensions mismatch")
            if exported.get("existing_hash") != config["existingHash"] or exported.get("layout_hash") != config["layoutHash"]:
                raise AssertionError("Exported JSON source image hashes mismatch")
            if exported.get("existing_state") != config.get("existingState"):
                raise AssertionError("Exported JSON lost the source screenshot/crop provenance")
            marks = exported.get("views", {}).get("existing", [])
            if len(marks) != 1 or marks[0].get("kind") != "rect" or not all(k in marks[0] for k in ("x", "y", "w", "h")):
                raise AssertionError(f"Unexpected exported mark geometry: {marks!r}")
            if len(exported["views"]["existing"]) != 1: raise AssertionError("Export missed the drawn mark")
            index_path = Path(profile) / "annotation-source-index.json"
            index_script = Path(__file__).with_name("summarize_floorplan_annotations.py")
            indexed = subprocess.run([
                sys.executable, str(index_script), "--input", str(exported_path),
                "--output", str(index_path), "--execute",
            ], capture_output=True, text=True, check=False)
            if indexed.returncode != 0:
                raise AssertionError(f"Annotation export could not be indexed: {indexed.stderr}")
            source_index = json.loads(index_path.read_text(encoding="utf-8"))
            expected_annotation_hash = hashlib.sha256(exported_path.read_bytes()).hexdigest()
            if source_index.get("annotation_sha256") != expected_annotation_hash:
                raise AssertionError("Source index does not bind the browser-exported annotation file hash")
            indexed_marks = source_index.get("marks", [])
            if (source_index.get("interpretation_status") != "needs_interpretation"
                    or len(indexed_marks) != 1
                    or indexed_marks[0].get("kind") != "rect"
                    or indexed_marks[0].get("geometry_px") != {key: marks[0][key] for key in ("x", "y", "w", "h")}
                    or indexed_marks[0].get("source_record", {}).get("sha256") != expected_annotation_hash):
                raise AssertionError("Browser-exported geometry/source was not preserved by the annotation index")
            file_url = "data:application/json;base64," + base64.b64encode(json.dumps(exported).encode()).decode()
            result = cdp.evaluate(f"(async()=>{{const r=await fetch('{file_url}');const f=new File([await r.blob()],'roundtrip.json',{{type:'application/json'}});window.confirm=()=>true;const i=document.querySelector('#import-file');const d=new DataTransfer();d.items.add(f);i.files=d.files;i.dispatchEvent(new Event('change',{{bubbles:true}}));await new Promise(r=>setTimeout(r,100));return document.querySelectorAll('[data-mark-layer=existing] .mark-shape').length}})()")
            if result != len(exported["views"]["existing"]): raise AssertionError(f"JSON import round-trip failed: imported={result}, exported={len(exported['views']['existing'])}")
            print(json.dumps({"status":"ANNOTATOR_INTERACTION_OK","rectangle_mouse_draw":"ok","layer_isolation":"ok","undo":"ok","clear":"ok","reload_persistence":"ok","actual_download_json":"ok","source_index_bridge":"ok","export_canvas_px":exported["canvas_px"],"export_marks":len(exported["views"]["existing"]),"import_marks":result},ensure_ascii=False,indent=2))
            return 0
        finally:
            if cdp: cdp.close()
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == "__main__":
    try: raise SystemExit(main())
    except Exception as exc: raise SystemExit(f"ANNOTATOR_TEST_FAILED: {exc}")
