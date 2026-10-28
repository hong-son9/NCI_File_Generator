"""
web_api.py - Cầu nối giữa trang web (app.js, chạy Pyodide) và datagen_core.

Mọi hàm nhận/trả chuỗi JSON. File đọc/ghi qua hệ thống file ảo của Pyodide:
/app/layouts, /app/rules, /app/codes do app.js nạp sẵn; /tmp cho file tải lên và file xuất.
"""
from __future__ import annotations

import json
import shutil
import traceback
from pathlib import Path

import datagen_core as core

ROOT = Path("/app")
LAYOUT_DIR = ROOT / "layouts"
OUT_DIR = Path("/tmp/out")
ALL_ERROR_LABELS = {**core.ERROR_LABELS, **core.ROW_ERROR_LABELS, **core.RULE_ERROR_LABELS}
REPO: dict[str, core.Layout] = {}


def _safe(fn):
    def wrap(*args):
        try:
            return json.dumps(fn(*args), ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": str(e) or type(e).__name__, "trace": traceback.format_exc()},
                              ensure_ascii=False)
    return wrap


def _layout_meta(lay: core.Layout) -> dict:
    return {
        "fields": [{"index": f.index, "name": f.name, "type": f.type, "desc": f.desc, "rule": f.rule_text(),
                    "errors": core.applicable_errors(f)} for f in lay.fields],
        "rules": [r.full_label() for r in lay.rules],
        "rulePlain": [r.label() for r in lay.rules],
    }


@_safe
def load() -> dict:
    """Nạp layouts + rules + codes, trả về mọi thông tin UI cần."""
    global REPO
    REPO, problems = core.load_layouts(LAYOUT_DIR)
    types = sorted(k for k in REPO if k not in (core.HEADER_CD, core.TRAILER_CD))
    H = REPO.get(core.HEADER_CD)
    return {
        "problems": problems,
        "types": types,
        "missing": [c for c in (core.HEADER_CD, core.TRAILER_CD) if c not in REPO],
        "hasCodes": core.find_code_file(LAYOUT_DIR) is not None,
        "header": [{"name": f.name, "desc": f.desc} for f in H.fields] if H else [],
        "headerDefaults": core.header_defaults(),
        "runId": core.new_run_id(),
        "fileErrors": core.FILE_ERROR_LABELS,
        "errorLabels": ALL_ERROR_LABELS,
        "rowErrors": core.applicable_errors(None),
        "rowLevel": core.ROW_LEVEL,
        "rulePrefix": core.RULE_PREFIX,
        "setSteps": core.SET_STEPS,
        "setStepNames": core.SET_STEP_NAMES,
        "layouts": {t: _layout_meta(REPO[t]) for t in types},
    }


@_safe
def generate(params: str) -> dict:
    """Như App._run_generate + ghi file: trả về các dòng, báo cáo lỗi và đường dẫn file đã ghi trong /tmp."""
    p = json.loads(params)
    lay = REPO.get(p["info"])
    if lay is None:
        raise ValueError("Chưa chọn loại dữ liệu.")
    missing = [c for c in (core.HEADER_CD, core.TRAILER_CD) if c not in REPO]
    if missing:
        raise ValueError(f"Thiếu layout {', '.join(missing)}.")
    csv_rows = core.read_csv(p["csvPath"]) if p.get("csvPath") else None
    res = core.generate(
        lay, REPO[core.HEADER_CD], REPO[core.TRAILER_CD], p["header"],
        n_rows=len(csv_rows) if csv_rows is not None else int(p["rows"]),
        fixed=p.get("fixed") or {}, errors=[core.ErrorSpec(**e) for e in p.get("errors", [])],
        file_errors=p.get("fileErrors", []), csv_rows=csv_rows,
        seed=p.get("seed"), customers=int(p.get("customers", 1)), lifecycle=bool(p.get("lifecycle", True)),
        file_suffix=p.get("suffix") or None, filename=p.get("filename") or None,
        run_id=p.get("runId") or None,
        cust_codes=str(p.get("custCodes", "")).replace(",", " ").split(),
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data_path = OUT_DIR / res.filename
    core.write_lines(data_path, res.lines, bool(p.get("crlf")))
    rep_path = None
    if res.report and p.get("report", True):
        rep_path = OUT_DIR / (res.filename + ".errors.csv")
        core.write_report(rep_path, res.report)
    return {"lines": res.lines, "report": res.report, "errorLines": sorted(res.error_lines),
            "filename": res.filename, "dataPath": str(data_path), "reportPath": str(rep_path) if rep_path else None}


@_safe
def generate_set(params: str) -> dict:
    """Như App.open_set_dialog: sinh bộ file liên kết, ghi vào /tmp/out/<thư mục> rồi nén thành 1 file zip."""
    p = json.loads(params)
    res = core.generate_set(
        REPO, p["header"], p.get("types", []),
        individuals=int(p.get("individuals", 1)), corporates=int(p.get("corporates", 0)),
        loans_per_cust=int(p.get("loans", 1)), cards_per_cust=int(p.get("cards", 1)),
        seed=p.get("seed"), lifecycle=bool(p.get("lifecycle", True)), file_suffix=p.get("suffix") or None,
        run_id=p.get("runId") or None,
        existing_individuals=str(p.get("existingInd", "")).replace(",", " ").split(),
        existing_corporates=str(p.get("existingCorp", "")).replace(",", " ").split(),
    )
    out = {"files": [{"step": f.step, "info": f.info_cd, "path": f.path, "lines": f.result.lines}
                     for f in res.files],
           "notes": res.notes, "manifest": core.set_manifest(res)}
    if p.get("write", True):            # write=False: chỉ xem trước
        name = f"bo_lien_ket_{p.get('suffix') or 'nice'}"
        folder = OUT_DIR / name
        if folder.exists():
            shutil.rmtree(folder)
        core.write_set(folder, res, bool(p.get("crlf")))
        out["zipPath"] = shutil.make_archive(str(OUT_DIR / name), "zip", root_dir=OUT_DIR, base_dir=name)
        out["zipName"] = name + ".zip"
    return out


@_safe
def new_run_id() -> str:
    return core.new_run_id()


@_safe
def csv_info(path: str, info: str) -> dict:
    """Đọc CSV tải lên: số dòng, cột lạ, cột thiếu (như App.load_csv)."""
    rows = core.read_csv(path)
    lay = REPO[info]
    cols = list(rows[0].keys()) if rows else []
    return {"rows": len(rows),
            "unknown": [c for c in cols if c not in lay.names],
            "missing": [n for n in lay.names if n not in cols and n != "recordType"]}


@_safe
def validate(path: str, filename: str, info: str) -> dict:
    lines = core.read_lines(path)
    return {"lines": len(lines), "issues": core.validate_lines(lines, REPO, info or None, filename=filename)}
