#!/usr/bin/env python3
"""구글 시트 '이미지' 탭에 적힌 HTML을 PNG로 그려 이 저장소에 저장합니다.

시트 열: path | width | height | scale | html | note
  path   저장 경로 (예: 2026-10-26-spicy-number.png, linkedin/2026-10-26-slide1.png)
  width  가로(px), height 세로(px), scale 배율(기본 2)
  html   그릴 HTML 본문 (<div ...> 블록). 로고는 /_blob/<id> 또는 {{logo:dark|white|mono}}로 적습니다.
         50,000자를 넘으면 html2, html3 열에 이어서 적습니다.

같은 path라도 내용(html·크기)이 바뀌면 다시 그립니다. 내용이 같으면 건너뜁니다.
환경 변수 IMAGES_SHEET_CSV_URL 에 '이미지' 탭의 CSV 내보내기 주소가 있어야 합니다.
"""
import csv
import hashlib
import io
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import logos  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, ".rendered")
FONT_DIR = os.path.join(ROOT, "node_modules", "@fontsource")


def log(m):
    print(m, flush=True)


def font_css():
    a = os.path.join(FONT_DIR, "archivo", "files", "archivo-latin-%s-normal.woff2")
    i = os.path.join(FONT_DIR, "instrument-serif", "files", "instrument-serif-latin-400-%s.woff2")
    css = "".join("@font-face{font-family:'Archivo';font-weight:%s;src:url('file://%s')}" % (w, a % w)
                  for w in (400, 500, 600, 700, 800) if os.path.exists(a % w))
    css += ("@font-face{font-family:'Instrument Serif';font-style:normal;src:url('file://%s')}"
            "@font-face{font-family:'Instrument Serif';font-style:italic;src:url('file://%s')}"
            % (i % "normal", i % "italic"))
    return css


def fetch_rows():
    url = os.environ.get("IMAGES_SHEET_CSV_URL", "")
    if not url:
        log("[중단] IMAGES_SHEET_CSV_URL 이 없습니다.")
        sys.exit(3)
    with urllib.request.urlopen(url, timeout=60) as r:
        text = r.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def row_html(row):
    parts = [row.get("html") or ""]
    n = 2
    while row.get("html%d" % n):
        parts.append(row["html%d" % n])
        n += 1
    # 셀 맨 앞의 '~'는 수식으로 오해되는 것(+, -, =)을 막기 위한 표시이므로 떼어 냅니다
    text = "".join(p[1:] if p.startswith("~") else p for p in parts).strip()
    if text.startswith("gz:"):  # gzip 후 base64로 압축해 적은 HTML
        import base64
        import gzip
        try:
            text = gzip.decompress(base64.b64decode("".join(text[3:].split()))).decode("utf-8")
        except Exception as e:  # noqa: BLE001
            log(f"[건너뜀] gz: 내용을 풀지 못했습니다: {type(e).__name__}")
            return ""
    return text.replace("\r\n", "\n")


def safe_path(p):
    p = p.strip().replace("\\", "/").lstrip("/")
    if not p or ".." in p.split("/") or not p.lower().endswith(".png"):
        return None
    if p.startswith((".git/", ".github/", "tools/", "node_modules/", ".rendered/")):
        return None
    return p


def plan():
    jobs = []
    for n, row in enumerate(fetch_rows(), start=2):
        path = safe_path(row.get("path") or "")
        html = row_html(row).strip()
        if not path or not html:
            continue
        try:
            w = int(float(row.get("width") or 0)); h = int(float(row.get("height") or 0))
            s = float(row.get("scale") or 2)
        except ValueError:
            log(f"[건너뜀] {n}행: width/height/scale 숫자를 확인하세요."); continue
        if not (100 <= w <= 4000 and 100 <= h <= 4000 and 1 <= s <= 3):
            log(f"[건너뜀] {n}행: 크기 범위 밖 ({w}x{h} @{s})."); continue
        digest = hashlib.sha256(f"{w}x{h}@{s}\n{html}".encode("utf-8")).hexdigest()
        state_file = os.path.join(STATE, path + ".json")
        done = None
        if os.path.exists(state_file):
            try:
                done = json.load(open(state_file, encoding="utf-8")).get("sha256")
            except ValueError:
                done = None
        if done == digest and os.path.exists(os.path.join(ROOT, path)):
            continue
        jobs.append((n, path, w, h, s, html, digest))
    return jobs


def render(jobs):
    from playwright.sync_api import sync_playwright
    css = "<!doctype html><meta charset='utf-8'><style>body{margin:0;background:transparent}" + font_css() + "</style>"
    with sync_playwright() as p:
        b = p.chromium.launch()
        for n, path, w, h, s, html, digest in jobs:
            out = os.path.join(ROOT, path)
            os.makedirs(os.path.dirname(out) or ROOT, exist_ok=True)
            tmp = os.path.join(STATE, "_tmp.html")
            os.makedirs(os.path.dirname(tmp), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(css + logos.fix(html))
            pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=s)
            pg.goto("file://" + tmp)
            pg.wait_for_timeout(800)
            pg.screenshot(path=out, clip={"x": 0, "y": 0, "width": w, "height": h})
            pg.close()
            st = os.path.join(STATE, path + ".json")
            os.makedirs(os.path.dirname(st), exist_ok=True)
            with open(st, "w", encoding="utf-8") as f:
                json.dump({"sha256": digest, "size": f"{w}x{h}@{s}", "sheetRow": n}, f)
            log(f"[그림 완료] {path} ({w}x{h} @{s})")
            os.remove(tmp)
        b.close()


if __name__ == "__main__":
    jobs = plan()
    if sys.argv[1:] == ["plan"]:
        log(f"그릴 이미지 {len(jobs)}장: " + ", ".join(j[1] for j in jobs))
        with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as f:
            f.write(f"count={len(jobs)}\n")
        sys.exit(0)
    if not jobs:
        log("새로 그릴 이미지가 없습니다.")
        sys.exit(0)
    render(jobs)
