"""Capture a reproducible LinkedIn demo of the local Streamlit application."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

import websocket
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "demo"
PROFILE_DIR = ROOT / ".demo-browser-profile"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
APP_URL = "http://127.0.0.1:8501"
CHANNEL_ID = "UCxgAuX3XZROujMmGphN_scA"
WIDTH, HEIGHT = 1280, 720


class CDP:
    def __init__(self, url: str):
        self.socket = websocket.create_connection(url, timeout=30)
        self.request_id = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.request_id += 1
        request_id = self.request_id
        self.socket.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        while True:
            response = json.loads(self.socket.recv())
            if response.get("id") == request_id:
                if "error" in response:
                    raise RuntimeError(f"CDP {method} failed: {response['error']}")
                return response.get("result", {})

    def evaluate(self, expression: str):
        result = self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        return result.get("result", {}).get("value")

    def close(self):
        self.socket.close()


def wait_for_debugger(timeout: int = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=2):
                return
        except Exception:
            time.sleep(0.5)
    raise TimeoutError("Chrome DevTools endpoint did not start")


def create_tab() -> dict:
    encoded = urllib.parse.quote(APP_URL, safe="")
    request = urllib.request.Request(
        f"http://127.0.0.1:9222/json/new?{encoded}", method="PUT"
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def wait_until(cdp: CDP, expression: str, timeout: int, label: str) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cdp.evaluate(expression):
            return
        time.sleep(1)
    body = cdp.evaluate("document.body ? document.body.innerText.slice(0, 2000) : ''")
    raise TimeoutError(f"Timed out waiting for {label}. Visible text: {body}")


def screenshot(cdp: CDP, filename: str) -> Path:
    data = cdp.call(
        "Page.captureScreenshot",
        {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
    )["data"]
    path = OUTPUT_DIR / filename
    path.write_bytes(base64.b64decode(data))
    return path


def submit_question(cdp: CDP, question: str, expected_answer_count: int) -> None:
    focused = cdp.evaluate(
        """
        (() => {
          const input = document.querySelector('textarea[placeholder*="Ask a question"]');
          if (!input) return false;
          input.focus();
          return true;
        })()
        """
    )
    if not focused:
        raise RuntimeError("Could not find the Streamlit chat input")

    cdp.call("Input.insertText", {"text": question})
    cdp.call(
        "Input.dispatchKeyEvent",
        {"type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
    )
    cdp.call(
        "Input.dispatchKeyEvent",
        {"type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
    )
    wait_until(
        cdp,
        f"(document.body.innerText.match(/Tokens:/g) || []).length >= {expected_answer_count} && "
        "!document.body.innerText.includes('Searching video context')",
        120,
        f"answer {expected_answer_count}",
    )
    cdp.evaluate(
        """
        (() => {
          const main = document.querySelector('[data-testid="stMain"]');
          if (main) main.scrollTop = main.scrollHeight;
          window.scrollTo(0, document.body.scrollHeight);
        })()
        """
    )
    time.sleep(1)


def font(size: int, bold: bool = False):
    candidates = [
        Path(r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def title_card() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#0e1117")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((72, 72, WIDTH - 72, HEIGHT - 72), radius=30, fill="#161b22", outline="#30363d", width=2)
    draw.text((120, 150), "YOUTUBE RAG", font=font(26, True), fill="#ff6b5f")
    draw.text((120, 215), "Ask an entire channel.", font=font(54, True), fill="#f0f6fc")
    draw.text((120, 285), "Jump to the exact answer.", font=font(54, True), fill="#f0f6fc")
    draw.text((120, 395), "3 videos  •  32 transcript chunks  •  timestamp citations", font=font(25), fill="#9da7b3")
    draw.rounded_rectangle((120, 490, 555, 555), radius=32, fill="#238636")
    draw.text((153, 505), "LIVE MARK TILBURY DEMO", font=font(25, True), fill="white")
    return image


def make_gif(paths: list[Path]) -> Path:
    scenes = [title_card()] + [Image.open(path).convert("RGB").resize((WIDTH, HEIGHT)) for path in paths]
    frames: list[Image.Image] = []
    for index, scene in enumerate(scenes):
        frames.extend([scene.copy()] * (22 if index == 0 else 28))
        if index + 1 < len(scenes):
            next_scene = scenes[index + 1]
            frames.extend(Image.blend(scene, next_scene, alpha / 8) for alpha in range(1, 8))

    output = OUTPUT_DIR / "youtube_rag_linkedin_demo.gif"
    frames[0].save(
        output,
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
        optimize=True,
    )
    return output


def make_mp4(gif: Path) -> Path:
    output = OUTPUT_DIR / "youtube_rag_linkedin_demo.mp4"
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-i",
            str(gif),
            "-vf",
            f"scale={WIDTH}:{HEIGHT}:flags=lanczos",
            "-r",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return output


def main() -> None:
    OUTPUT_DIR.mkdir(exist_ok=True)
    if not CHROME.exists():
        raise FileNotFoundError(f"Chrome not found at {CHROME}")

    if not PROFILE_DIR.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError("Refusing to manage a browser profile outside the project")
    if PROFILE_DIR.exists():
        shutil.rmtree(PROFILE_DIR, ignore_errors=True)
    PROFILE_DIR.mkdir(exist_ok=True)

    process = subprocess.Popen(
        [
            str(CHROME),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-allow-origins=*",
            "--remote-debugging-port=9222",
            f"--user-data-dir={PROFILE_DIR}",
            f"--window-size={WIDTH},{HEIGHT}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    cdp = None
    try:
        wait_for_debugger()
        tab = create_tab()
        cdp = CDP(tab["webSocketDebuggerUrl"])
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": WIDTH, "height": HEIGHT, "deviceScaleFactor": 1, "mobile": False},
        )

        wait_until(
            cdp,
            f"document.body && document.body.innerText.includes('{CHANNEL_ID}') && "
            "[...document.querySelectorAll('button')].filter(button => button.innerText.trim() === 'Load').length >= 2",
            30,
            "cached channel list",
        )
        initial = screenshot(cdp, "01_home.png")

        clicked = cdp.evaluate(
            f"""
            (() => {{
              const loadButtons = [...document.querySelectorAll('button')]
                .filter(button => button.innerText.trim() === 'Load');
              const target = loadButtons.find(button => {{
                let node = button;
                for (let depth = 0; depth < 8 && node; depth++, node = node.parentElement) {{
                  if ((node.innerText || '').includes('{CHANNEL_ID}')) return true;
                }}
                return false;
              }}) || loadButtons[loadButtons.length - 1];
              if (!target) return false;
              target.click();
              return true;
            }})()
            """
        )
        if not clicked:
            raise RuntimeError("Could not click the Mark Tilbury cache entry")

        wait_until(cdp, "document.body.innerText.includes('Knowledge Base Ready')", 180, "knowledge base")
        ready = screenshot(cdp, "02_ready.png")

        submit_question(cdp, "What tax strategy does Mark explain?", 1)
        answer_one = screenshot(cdp, "03_tax_answer.png")

        submit_question(cdp, "What are the main practical lessons across these videos?", 2)
        cdp.evaluate(
            """
            (() => {
              const summaries = [...document.querySelectorAll('summary')]
                .filter(summary => summary.innerText.includes('Verified Timestamp Sources'));
              if (summaries.length) summaries[summaries.length - 1].click();
            })()
            """
        )
        time.sleep(1)
        answer_two = screenshot(cdp, "04_cross_video_answer.png")

        gif = make_gif([initial, ready, answer_one, answer_two])
        mp4 = make_mp4(gif)
        print(
            json.dumps(
                {
                    "screenshots": [str(initial), str(ready), str(answer_one), str(answer_two)],
                    "gif": str(gif),
                    "mp4": str(mp4),
                }
            )
        )
    finally:
        if cdp:
            cdp.close()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()
