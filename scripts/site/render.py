#!/usr/bin/env python3
"""Regenerate every image on the site from the HTML mockups in this folder.

    python3 scripts/site/render.py [path/to/status-polling.json path/to/status-dormant.json]

Needs google-chrome and Pillow. The status screenshot is built from real `doorbell status`
output (see capture_status.py); without arguments it re-uses the JSON from $DOORBELL_SITE_TMP.
"""
import html
import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
SITE = HERE.parents[1] / "site"
IMG = SITE / "img"
TMP = Path(os.environ.get("DOORBELL_SITE_TMP", "/tmp/doorbell-site"))
CHROME = os.environ.get("CHROME", "/usr/bin/google-chrome")


def chrome(src, out, w, h, scale=2, transparent=False):
    cmd = [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
           f"--force-device-scale-factor={scale}", f"--window-size={w},{h}", "--allow-file-access-from-files",
           "--virtual-time-budget=3000", f"--screenshot={out}"]
    if transparent:
        cmd.append("--default-background-color=00000000")
    subprocess.run(cmd + [Path(src).resolve().as_uri()], check=True, capture_output=True)


def crop_quantize(path, colors=96):
    im = Image.open(path).convert("RGBA")
    bbox = im.getchannel("A").getbbox()
    if bbox:
        im = im.crop(bbox)
    q = im.quantize(colors=colors, method=Image.Quantize.FASTOCTREE)
    q.save(path, optimize=True)
    return im.size


def span(cls, text):
    return f'<span class="{cls}">{html.escape(str(text))}</span>'


def status_html(d, cmd="doorbell status"):
    """Pretty-print the real JSON, trimmed to the fields a reader needs. Trimmed parts show as ..."""
    def kv(ind, k, v, comma=True):
        val = json.dumps(v)
        cls = "s" if isinstance(v, str) else "n" if isinstance(v, (int, float)) else "g"
        if k == "state":
            cls = "g" if v == "POLLING" else "p"
        return f'{" " * ind}{span("k", json.dumps(k))}: {span(cls, val)}{"," if comma else ""}\n'
    o = f'{span("p", "$")} {span("b", cmd)}\n{{\n'
    o += kv(2, "state", d["state"]) + kv(2, "uptime_s", d["uptime_s"])
    a = d["api_calls"]
    o += f'  {span("k", chr(34) + "api_calls" + chr(34))}: {{ {span("k", chr(34) + "last_1m" + chr(34))}: {span("n", a["last_1m"])}, {span("k", chr(34) + "last_1h" + chr(34))}: {span("n", a["last_1h"])} }},\n'
    o += f'  {span("k", chr(34) + "poll_set" + chr(34))}: {span("d", json.dumps(d["poll_set"]))},\n'
    o += f'  {span("k", chr(34) + "agents" + chr(34))}: ' + ("[],\n" if not d["agents"] else "[\n")
    for i, ag in enumerate(d["agents"]):
        o += "    {\n" + kv(6, "name", ag["name"]) + kv(6, "status", ag["status"]) + kv(6, "waiters", ag["waiters"])
        o += f'      {span("k", chr(34) + "sub" + chr(34))}: {span("d", json.dumps(ag["sub"]))},\n'
        o += f'      {span("k", chr(34) + "items" + chr(34))}: {span("d", json.dumps(ag["items"]))}\n'
        o += "    }" + ("," if i < len(d["agents"]) - 1 else "") + "\n"
    if d["agents"]:
        o += "  ],\n"
    o += f'  {span("k", chr(34) + "stuck" + chr(34))}: {span("d", json.dumps(d["stuck"]))}\n}}\n'
    return o


def main():
    IMG.mkdir(parents=True, exist_ok=True)
    pol = Path(sys.argv[1] if len(sys.argv) > 2 else TMP / "status-polling.json")
    dor = Path(sys.argv[2] if len(sys.argv) > 2 else TMP / "status-dormant.json")
    p, dm = json.loads(pol.read_text()), json.loads(dor.read_text())
    dm["api_calls"] = {"last_1m": 0, "last_1h": 0}      # a settled daemon: the 3 start-up calls aged out
    dm["uptime_s"] = 4310
    body = status_html(dm, "doorbell status   # no agent is listening") + "\n" + status_html(p, "doorbell status   # two agents registered")
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><link rel="stylesheet" href="mock.css"></head><body>'
            '<div class="win" style="--w:1120px"><div class="chrome"><i></i><i></i><i></i><span>~/doorbell-agents/main</span></div>'
            f"<pre>{body}</pre></div></body></html>")
    work = HERE / ".build"
    work.mkdir(exist_ok=True)
    (work / "terminal-status.html").write_text(page)
    # the .build copy needs mock.css next to it
    (work / "mock.css").write_text((HERE / "mock.css").read_text().replace("../../site/", "../../../site/"))
    jobs = [(work / "terminal-status.html", "doorbell-status.png", 1120, 1700),
            (HERE / "terminal-setup.html", "claude-code-setup.png", 980, 800),
            (HERE / "slack-thread.html", "slack-thread.png", 900, 500),
            (HERE / "flow.html", "flow.png", 1100, 420)]
    for src, name, w, h in jobs:
        out = IMG / name
        chrome(src, out, w, h, transparent=True)
        print(name, crop_quantize(out))
    out = IMG / "social-card.png"
    chrome(HERE / "social-card.html", out, 1200, 630, scale=1)
    Image.open(out).convert("RGB").quantize(colors=48).save(out, optimize=True)
    for name, size, transparent in [("favicon-32.png", 32, True), ("apple-touch-icon-180.png", 180, False), ("img/icon-512.png", 512, False)]:
        src = SITE / "favicon.svg" if transparent else HERE / "icon.svg"
        wrap = work / "icon.html"
        wrap.write_text(f'<!doctype html><html><body style="margin:0"><img src="{src.as_uri()}" width="{size}" height="{size}" style="display:block"></body></html>')
        chrome(wrap, SITE / name, size, size, scale=1, transparent=transparent)
        print(name)


if __name__ == "__main__":
    main()
