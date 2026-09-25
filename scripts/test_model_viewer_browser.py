#!/usr/bin/env python3
"""Headless Chrome integration test for the generated local 3D viewer.

Uses the installed Chrome executable and the Python websocket-client package.
The test serves an existing generated output directory on loopback and controls
Chrome through its local DevTools WebSocket; it never opens the user's profile.
"""
from __future__ import annotations

import argparse
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def read_exact(sock: socket.socket, count: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        part = sock.recv(count - len(chunks))
        if not part:
            raise ConnectionError("Chrome DevTools WebSocket closed unexpectedly")
        chunks.extend(part)
    return bytes(chunks)


class DevTools:
    """Minimal CDP wrapper using the already installed websocket-client."""

    def __init__(self, websocket_url: str, timeout: float = 8.0):
        from urllib.parse import urlparse
        import websocket

        parsed = urlparse(websocket_url)
        if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError(f"Refusing non-loopback DevTools endpoint: {websocket_url}")
        self.ws = websocket.create_connection(
            websocket_url, timeout=timeout, origin="http://localhost",
            suppress_origin=False, enable_multithread=True,
        )
        self.next_id = 0

    def command(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        command_id = self.next_id
        self.ws.send(json.dumps({"id": command_id, "method": method, "params": params or {}}))
        while True:
            reply = json.loads(self.ws.recv())
            if reply.get("id") != command_id:
                continue
            if "error" in reply:
                raise RuntimeError(f"Chrome DevTools {method} failed: {reply['error']}")
            return reply.get("result", {})

    def evaluate(self, expression: str):
        result = self.command("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"].get("exception", {}).get("description") or result["exceptionDetails"].get("text", "JavaScript evaluation failed")
            raise RuntimeError(detail)
        remote = result.get("result", {})
        return remote.get("value")

    def close(self):
        self.ws.close()


def chrome_binary(requested: str | None) -> str:
    candidates = [
        requested,
        os.environ.get("CHROME_BIN"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        DEFAULT_CHROME,
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise FileNotFoundError("Chrome/Chromium executable not found; pass --chrome")


def wait_for(predicate, timeout: float, description: str):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (ConnectionError, TimeoutError, OSError):
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Timed out waiting for {description}; last value={last!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="在隔离的无头Chrome中实测本地3D查看页加载和交互。")
    parser.add_argument("--directory", required=True, type=Path, help="包含index.html、GLB及本地查看器脚本的输出目录")
    parser.add_argument("--chrome", help="Chrome/Chromium可执行文件路径")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--assert-wall-toggle", action="store_true",
                        help="检查墙体材质显隐，并比较浏览器画布像素")
    parser.add_argument("--expect-model-error", action="store_true",
                        help="期待无效GLB触发页面的明确加载失败提示")
    args = parser.parse_args()

    directory = args.directory.expanduser().resolve()
    required = ["index.html", "interior-scene.glb", "model-viewer-4.3.1.min.js"]
    missing = [name for name in required if not (directory / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Viewer output directory is missing required files: {missing}")

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, _format, *args):
            pass

    handler = lambda *items, **kwargs: QuietHandler(*items, directory=str(directory), **kwargs)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    port = server.server_address[1]

    with tempfile.TemporaryDirectory(prefix="interior-model-viewer-test-") as profile:
        executable = chrome_binary(args.chrome)
        process = subprocess.Popen(
            [executable, "--headless=new", "--enable-unsafe-swiftshader",
             "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
             "--remote-allow-origins=http://localhost",
                          f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
             "--disable-background-networking", "--allow-file-access-from-files", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        devtools = None
        try:
            active_port = Path(profile) / "DevToolsActivePort"

            def find_debug_port():
                if process.poll() is not None:
                    raise RuntimeError(f"Headless Chrome exited with code {process.returncode}")
                return int(active_port.read_text().splitlines()[0]) if active_port.is_file() else None

            debug_port = wait_for(find_debug_port, 10, "Chrome DevTools port")
            with urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json/list", timeout=3) as response:
                targets = json.load(response)
            page = next(target for target in targets if target.get("type") == "page")
            devtools = DevTools(page["webSocketDebuggerUrl"])
            devtools.command("Page.enable")
            devtools.command("Runtime.enable")
            devtools.command("Emulation.setDeviceMetricsOverride", {
                "width": 1280, "height": 850, "deviceScaleFactor": 1, "mobile": False,
            })
            devtools.command("Page.navigate", {"url": f"http://127.0.0.1:{port}/index.html"})

            if args.expect_model_error:
                error_text = wait_for(
                    lambda: (text if "模型加载失败" in (text := devtools.evaluate(
                        "document.querySelector('#status')?.textContent || ''")) else None),
                    args.timeout, "explicit invalid-GLB error status",
                )
                external = devtools.evaluate("performance.getEntriesByType('resource').map(x=>x.name).filter(u=>/^https?:/.test(u)&&new URL(u).origin!==location.origin)")
                if external:
                    raise RuntimeError(f"Viewer requested external resources while reporting load failure: {external}")
                print(json.dumps({"status": "MODEL_VIEWER_FAILURE_STATE_OK", "message": error_text,
                                  "external_resources": external}, ensure_ascii=False, indent=2))
                return 0

            loaded = wait_for(
                lambda: devtools.evaluate("(() => { const m=document.querySelector('#model'); return !!m && m.loaded ? m.getAttribute('alt') : false; })()"),
                args.timeout, "GLB and model-viewer load",
            )
            if not loaded:
                raise RuntimeError("The model-viewer element did not finish loading")

            controls = devtools.evaluate("""(() => {
              const model=document.querySelector('#model');
              const room=document.querySelector('#room-select');
              const mode=document.querySelector('#view-mode');
              const loadedResources=performance.getEntriesByType('resource').map(x=>x.name);
              room.value=room.options[2]?.value || '';
              room.dispatchEvent(new Event('change',{bubbles:true}));
              const roomResult={selected:room.value,target:model.getAttribute('camera-target'),orbit:model.getAttribute('camera-orbit')};
              mode.value='open';
              mode.dispatchEvent(new Event('change',{bubbles:true}));
              const openOrbit=model.getAttribute('camera-orbit');
              mode.value='layout';
              mode.dispatchEvent(new Event('change',{bubbles:true}));
              const layoutOrbit=model.getAttribute('camera-orbit');
              document.querySelector('#overview').click();
              const overview={selected:room.value,target:model.getAttribute('camera-target'),orbit:model.getAttribute('camera-orbit')};
              const rect=model.getBoundingClientRect();
              const center={x:rect.left+rect.width/2,y:rect.top+rect.height/2};
              const canvas=!!model.shadowRoot?.querySelector('canvas');
              const orbit=model.getCameraOrbit();
              return {roomResult,openOrbit,layoutOrbit,overview,center,canvas,
                orbit:{theta:orbit.theta,phi:orbit.phi,radius:orbit.radius},
                externalResources:loadedResources.filter(u=>/^https?:/.test(u)&&new URL(u).origin!==location.origin),
              };
            })()""")
            if controls["roomResult"]["selected"] != "R02" or not controls["roomResult"]["target"]:
                raise RuntimeError(f"Room focus control failed: {controls['roomResult']}")
            if "42deg" not in controls["openOrbit"] or "5deg" not in controls["layoutOrbit"]:
                raise RuntimeError(f"View mode control failed: {controls['openOrbit']!r}, {controls['layoutOrbit']!r}")
            if controls["overview"]["selected"] or controls["overview"]["target"] != "auto auto auto":
                raise RuntimeError(f"Overview reset failed: {controls['overview']}")
            if controls["externalResources"]:
                raise RuntimeError(f"Viewer loaded external web resources: {controls['externalResources']}")

            point = controls["center"]
            start_orbit = controls["orbit"]
            devtools.command("Input.dispatchMouseEvent", {
                "type": "mousePressed", "x": point["x"], "y": point["y"], "button": "left", "clickCount": 1,
            })
            devtools.command("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": point["x"] + 125, "y": point["y"] + 70,
                "button": "left", "buttons": 1,
            })
            devtools.command("Input.dispatchMouseEvent", {
                "type": "mouseReleased", "x": point["x"] + 125, "y": point["y"] + 70,
                "button": "left", "buttons": 0,
            })
            time.sleep(0.35)
            drag_orbit = devtools.evaluate("(() => {const o=document.querySelector('#model').getCameraOrbit();return {theta:o.theta,phi:o.phi,radius:o.radius};})()")
            if abs(drag_orbit["theta"] - start_orbit["theta"]) + abs(drag_orbit["phi"] - start_orbit["phi"]) < 0.01:
                raise RuntimeError(f"Mouse drag did not rotate the model: before={start_orbit}, after={drag_orbit}")

            devtools.command("Input.dispatchMouseEvent", {
                "type": "mouseWheel", "x": point["x"] + 125, "y": point["y"] + 70,
                "deltaY": -160, "deltaX": 0,
            })
            time.sleep(0.35)
            zoom_orbit = devtools.evaluate("(() => {const o=document.querySelector('#model').getCameraOrbit();return {theta:o.theta,phi:o.phi,radius:o.radius};})()")
            if abs(zoom_orbit["radius"] - drag_orbit["radius"]) < 0.001:
                raise RuntimeError(f"Mouse wheel did not zoom the model: before={drag_orbit}, after={zoom_orbit}")

            wall_toggle_report = None
            if args.assert_wall_toggle:
                from PIL import Image, ImageChops, ImageStat

                devtools.evaluate("""(() => {
                  const mode=document.querySelector('#view-mode');
                  const room=document.querySelector('#room-select');
                  room.value='R01'; room.dispatchEvent(new Event('change',{bubbles:true}));
                  mode.value='open'; mode.dispatchEvent(new Event('change',{bubbles:true}));
                  return true;
                })()""")
                devtools.evaluate("new Promise(resolve=>setTimeout(()=>requestAnimationFrame(()=>requestAnimationFrame(resolve)),1000))")

                def capture_canvas():
                    rect = devtools.evaluate("""(() => {
                      const r=document.querySelector('#model').getBoundingClientRect();
                      return {x:r.x,y:r.y,width:r.width,height:r.height};
                    })()""")
                    result = devtools.command("Page.captureScreenshot", {
                        "format": "png", "clip": {**rect, "scale": 1},
                    })
                    return Image.open(BytesIO(base64.b64decode(result["data"]))).convert("RGB")

                before_image = capture_canvas()
                hidden_state = devtools.evaluate("""(() => {
                  const button=document.querySelector('#toggle-walls');
                  if (!button || button.disabled) return {available:false};
                  button.click();
                  const wall=document.querySelector('#model').model?.getMaterialByName('Walls - soft white');
                  return {available:true,pressed:button.getAttribute('aria-pressed'),label:button.textContent,
                    alphaMode:wall?.getAlphaMode(),rgba:Array.from(wall?.pbrMetallicRoughness?.baseColorFactor || [])};
                })()""")
                if (not hidden_state.get("available") or hidden_state.get("pressed") != "true" or
                        hidden_state.get("alphaMode") != "BLEND" or hidden_state.get("rgba", [0, 0, 0, 1])[3] != 0):
                    raise RuntimeError(f"Wall hide toggle failed to update its GLB material: {hidden_state}")
                devtools.evaluate("new Promise(resolve=>setTimeout(()=>requestAnimationFrame(()=>requestAnimationFrame(resolve)),250))")
                hidden_image = capture_canvas()
                visible_delta = sum(ImageStat.Stat(ImageChops.difference(before_image, hidden_image)).sum)
                pixel_channels = before_image.width * before_image.height * 3
                visible_mean_delta = visible_delta / pixel_channels
                if visible_mean_delta < 1.0:
                    raise RuntimeError(f"Wall toggle caused no visible canvas change: mean pixel delta={visible_mean_delta}")

                restored_state = devtools.evaluate("""(() => {
                  const button=document.querySelector('#toggle-walls'); button.click();
                  const wall=document.querySelector('#model').model?.getMaterialByName('Walls - soft white');
                  return {pressed:button.getAttribute('aria-pressed'),label:button.textContent,
                    alphaMode:wall?.getAlphaMode(),rgba:Array.from(wall?.pbrMetallicRoughness?.baseColorFactor || [])};
                })()""")
                if (restored_state.get("pressed") != "false" or restored_state.get("alphaMode") != "OPAQUE" or
                        restored_state.get("rgba", [0, 0, 0, 0])[3] < 0.99):
                    raise RuntimeError(f"Wall show toggle did not restore the original GLB material: {restored_state}")
                devtools.evaluate("new Promise(resolve=>setTimeout(()=>requestAnimationFrame(()=>requestAnimationFrame(resolve)),250))")
                restored_image = capture_canvas()
                restored_delta = sum(ImageStat.Stat(ImageChops.difference(before_image, restored_image)).sum)
                restored_mean_delta = restored_delta / pixel_channels
                if restored_mean_delta > 0.2:
                    raise RuntimeError(f"Wall restoration changed the original view: mean pixel delta={restored_mean_delta}; state={restored_state}")
                wall_toggle_report = {"hidden_state": hidden_state, "restored_state": restored_state,
                                      "visible_mean_pixel_delta": round(visible_mean_delta, 4),
                                      "restored_mean_pixel_delta": round(restored_mean_delta, 4)}

            print(json.dumps({
                "status": "MODEL_VIEWER_INTERACTION_OK",
                "model_alt": loaded,
                "room_focus": controls["roomResult"],
                "open_view_orbit": controls["openOrbit"],
                "layout_view_orbit": controls["layoutOrbit"],
                "overview": controls["overview"],
                "drag_orbit": drag_orbit,
                "zoom_orbit": zoom_orbit,
                "wall_toggle": wall_toggle_report,
                "external_resources": controls["externalResources"],
            }, ensure_ascii=False, indent=2))
            return 0
        finally:
            if devtools:
                devtools.close()
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        raise SystemExit(f"MODEL_VIEWER_TEST_FAILED: {exc}")
