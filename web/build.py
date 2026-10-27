"""
build.py - Gom bản web vào web/dist để deploy lên Cloudflare Pages.

    python web/build.py

dist/ gồm trang web (index.html, app.js, style.css), code Python (datagen_core.py, web_api.py)
và bundle.json chứa nội dung layouts/, rules/, codes/ để nạp vào Pyodide.
Không cần cài thêm gì: chỉ dùng thư viện chuẩn.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent
APP = WEB.parent
DIST = WEB / "dist"
STATIC = ["index.html", "app.js", "style.css", "web_api.py", "_headers"]
DATA_DIRS = [("layouts", "*.xml"), ("rules", "*.xml"), ("codes", "*.csv")]


def build() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    for name in STATIC:
        shutil.copy2(WEB / name, DIST / name)
    shutil.copy2(APP / "datagen_core.py", DIST / "datagen_core.py")
    bundle = {}
    for folder, pattern in DATA_DIRS:
        for p in sorted((APP / folder).glob(pattern)):
            bundle[f"{folder}/{p.name}"] = p.read_text(encoding="utf-8-sig")
    (DIST / "bundle.json").write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    print(f"Đã build {DIST} ({len(bundle)} file dữ liệu)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")    # console Windows (cp1252) không in được tiếng Việt
    build()
