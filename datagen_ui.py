"""
datagen_ui.py - Giao diện tạo file dữ liệu test NICE (Header + Detail + Trailer).

Chạy:  python datagen_ui.py
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import tkinter as tk
from dataclasses import asdict
from datetime import date
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

import datagen_core as core

APP_DIR = Path(__file__).resolve().parent
LAYOUT_DIR = APP_DIR / "layouts"
DEFAULT_INFO = "LOAN"   # loại dữ liệu khi mới mở app
CONFIG_DIR = APP_DIR / "configs"
GLOBAL_CFG = CONFIG_DIR / "_app.json"
ROW_LEVEL_LABEL = "(Cả dòng)"
MODES = [("random", "Ngẫu nhiên N dòng"), ("rows", "Dòng chỉ định")]
ALL_ERROR_LABELS = {**core.ERROR_LABELS, **core.ROW_ERROR_LABELS, **core.RULE_ERROR_LABELS}
RULE_LABEL_PREFIX = "(Quy tắc "
PREVIEW_LIMIT = 1000


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("NICE Test Data Generator")
        self.geometry("1280x820")
        self.minsize(980, 620)
        try:
            ttk.Style(self).theme_use("clam")
        except tk.TclError:
            pass

        g = _read_json(GLOBAL_CFG)
        self.repo: dict[str, core.Layout] = {}
        self.layout: core.Layout | None = None
        self.fixed: dict[str, str] = {}
        self.errors: list[core.ErrorSpec] = []
        self.csv_rows: list[dict] | None = None
        self.csv_name = ""
        self.header_vars: dict[str, tk.StringVar] = {}
        self._err_type_codes: list[str] = []

        self.out_dir = tk.StringVar(value=g.get("out_dir", str(APP_DIR / "output")))
        self.info_var = tk.StringVar()
        self.rows_var = tk.StringVar(value="10")
        self.seed_var = tk.StringVar()
        self.cust_var = tk.StringVar(value="1")
        self.lifecycle_var = tk.BooleanVar(value=True)
        self.suffix_var = tk.StringVar(value=date.today().strftime("%y%m%d"))
        self.filename_var = tk.StringVar()
        self.crlf_var = tk.BooleanVar(value=g.get("crlf", False))
        self.report_var = tk.BooleanVar(value=g.get("report", True))
        self.file_err_vars = {k: tk.BooleanVar() for k in core.FILE_ERROR_LABELS}
        self.csv_label = tk.StringVar(value="(tự sinh dữ liệu)")
        self.desc_var = tk.StringVar()
        self.err_hint = tk.StringVar()
        self.err_rows_var = tk.StringVar(value="1")
        self.err_custom_var = tk.StringVar()
        self.status = tk.StringVar(value="Sẵn sàng")
        self.set_cfg: dict = g.get("set", {})
        self.set_pending: tuple[core.SetResult, str] | None = None   # bộ file đang xem trước, chờ ghi

        self._build()
        self.reload_layouts()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ build
    def _build(self):
        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        r1 = ttk.Frame(top)
        r1.pack(fill="x")
        ttk.Label(r1, text="Loại dữ liệu:").pack(side="left")
        self.info_cb = ttk.Combobox(r1, textvariable=self.info_var, state="readonly", width=12)
        self.info_cb.pack(side="left", padx=(6, 4))
        self.info_cb.bind("<<ComboboxSelected>>", lambda e: self.select_layout(self.info_var.get()))
        ttk.Button(r1, text="Tải lại layout", command=self.reload_layouts).pack(side="left", padx=(0, 16))
        ttk.Label(r1, text="Số dòng:").pack(side="left")
        self.rows_spin = ttk.Spinbox(r1, from_=1, to=1_000_000, textvariable=self.rows_var, width=8)
        self.rows_spin.pack(side="left", padx=(6, 16))
        ttk.Label(r1, text="Số khách hàng:").pack(side="left")
        self.cust_spin = ttk.Spinbox(r1, from_=0, to=1_000_000, textvariable=self.cust_var, width=6)
        self.cust_spin.pack(side="left", padx=(6, 16))
        ttk.Label(r1, text="Seed:").pack(side="left")
        ttk.Entry(r1, textvariable=self.seed_var, width=8).pack(side="left", padx=(6, 16))
        self.lifecycle_cb = ttk.Checkbutton(r1, text="Vòng đời hợp đồng", variable=self.lifecycle_var)
        self.lifecycle_cb.pack(side="left")

        r2 = ttk.Frame(top)
        r2.pack(fill="x", pady=(6, 0))
        ttk.Button(r2, text="Dùng file CSV...", command=self.load_csv).pack(side="left")
        ttk.Button(r2, text="Bỏ CSV", command=self.clear_csv).pack(side="left", padx=4)
        ttk.Label(r2, textvariable=self.csv_label, foreground="#555").pack(side="left", padx=6)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=4)
        self.header_tab = ttk.Frame(self.nb, padding=10)
        self.cols_tab = ttk.Frame(self.nb, padding=10)
        self.err_tab = ttk.Frame(self.nb, padding=10)
        self.prev_tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(self.header_tab, text="  1. Header & file  ")
        self.nb.add(self.cols_tab, text="  2. Giá trị cột  ")
        self.nb.add(self.err_tab, text="  3. Lỗi dữ liệu  ")
        self.nb.add(self.prev_tab, text="  4. Xem trước  ")
        self._build_cols_tab()
        self._build_err_tab()
        self._build_preview_tab()

        bot = ttk.Frame(self, padding=(10, 4, 10, 8))
        bot.pack(fill="x")
        ttk.Label(bot, text="Thư mục xuất:").pack(side="left")
        ttk.Entry(bot, textvariable=self.out_dir, width=45).pack(side="left", padx=6)
        ttk.Button(bot, text="Chọn...", command=self.choose_out_dir).pack(side="left")
        ttk.Button(bot, text="Mở thư mục", command=self.open_out_dir).pack(side="left", padx=4)
        ttk.Button(bot, text="Kiểm tra file...", command=self.validate_file).pack(side="right")
        self.gen_btn = ttk.Button(bot, text="★ Tạo file", command=self.generate_file)
        self.gen_btn.pack(side="right", padx=6)
        ttk.Button(bot, text="Xem trước", command=self.preview).pack(side="right")
        ttk.Button(bot, text="Tạo bộ file liên kết...", command=self.open_set_dialog).pack(side="right", padx=6)
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w", padding=(8, 2)).pack(fill="x")

    def _build_header_tab(self):
        for w in self.header_tab.winfo_children():
            w.destroy()
        H = self.repo.get(core.HEADER_CD)
        box = ttk.LabelFrame(self.header_tab, text="Dòng Header (H)", padding=10)
        box.pack(fill="x")
        old = {k: v.get() for k, v in self.header_vars.items()}
        saved = _read_json(GLOBAL_CFG).get("header", {})
        defaults = core.header_defaults()
        self.header_vars = {}
        if not H:
            ttk.Label(box, text="⚠ Không có layouts/HEADER.xml", foreground="#b00").pack()
        else:
            for i, f in enumerate(H.fields):
                var = tk.StringVar(value=old.get(f.name, saved.get(f.name, defaults.get(f.name, ""))))
                state = "normal"
                if f.name == "recordType":
                    var.set("H")
                    state = "disabled"
                elif f.name == "informationType":
                    var.set(self.info_var.get())
                    state = "disabled"
                ttk.Label(box, text=f.name).grid(row=i, column=0, sticky="w", pady=2)
                ttk.Entry(box, textvariable=var, width=32, state=state).grid(row=i, column=1, sticky="w", padx=8)
                self.header_vars[f.name] = var

        fbox = ttk.LabelFrame(self.header_tab, text="File xuất", padding=10)
        fbox.pack(fill="x", pady=(12, 0))
        ttk.Label(fbox, text="Đuôi tên file (yymmdd)").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(fbox, textvariable=self.suffix_var, width=12).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(fbox, text="Tên file (trống = tự đặt)").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(fbox, textvariable=self.filename_var, width=40).grid(row=1, column=1, sticky="w", padx=8)
        ttk.Checkbutton(fbox, text="Xuống dòng CRLF", variable=self.crlf_var).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(fbox, text="Xuất kèm .errors.csv", variable=self.report_var).grid(
            row=3, column=0, columnspan=2, sticky="w")

    def _build_cols_tab(self):
        cols = ("idx", "name", "type", "rule", "value")
        frame = ttk.Frame(self.cols_tab)
        frame.pack(fill="both", expand=True)
        self.col_tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        for c, text, w, st in [("idx", "#", 40, False), ("name", "Cột", 170, False), ("type", "Kiểu", 90, False),
                               ("rule", "Quy tắc", 300, True),
                               ("value", "Giá trị (double-click để sửa)", 380, True)]:
            self.col_tree.heading(c, text=text, anchor="w")
            self.col_tree.column(c, width=w, stretch=st, anchor="w")
        self.col_tree.tag_configure("set", background="#e3f4e1")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.col_tree.yview)
        self.col_tree.configure(yscrollcommand=sb.set)
        self.col_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.col_tree.bind("<Double-1>", self._edit_value)
        self.col_tree.bind("<<TreeviewSelect>>", self._on_col_select)

        ttk.Label(self.cols_tab, textvariable=self.desc_var, foreground="#335", wraplength=1150,
                  justify="left").pack(fill="x", pady=(6, 0))
        btns = ttk.Frame(self.cols_tab)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="Xóa giá trị", command=self._clear_value).pack(side="left")
        ttk.Button(btns, text="Xóa tất cả", command=self._clear_all_values).pack(side="left", padx=4)

    def _build_err_tab(self):
        form = ttk.LabelFrame(self.err_tab, text="Thêm lỗi cho dòng detail", padding=10)
        form.pack(fill="x")
        ttk.Label(form, text="Cột / quy tắc").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(form, text="Loại lỗi").grid(row=0, column=1, columnspan=3, sticky="w", padx=(0, 8))
        self.err_field_cb = ttk.Combobox(form, state="readonly", width=62, height=25)
        self.err_field_cb.grid(row=1, column=0, sticky="w", padx=(0, 8))
        self.err_field_cb.bind("<<ComboboxSelected>>", self._on_err_field)
        self.err_type_cb = ttk.Combobox(form, state="readonly", width=34)
        self.err_type_cb.grid(row=1, column=1, columnspan=3, sticky="w", padx=(0, 8))
        r3 = ttk.Frame(form)
        r3.grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(r3, text="Chọn dòng:").pack(side="left", padx=(0, 4))
        self.err_mode_cb = ttk.Combobox(r3, state="readonly", width=18, values=[m[1] for m in MODES])
        self.err_mode_cb.current(0)
        self.err_mode_cb.pack(side="left")
        ttk.Label(r3, text="  Số dòng / danh sách:").pack(side="left")
        ttk.Entry(r3, textvariable=self.err_rows_var, width=12).pack(side="left", padx=(4, 12))
        ttk.Label(r3, text="Giá trị tự nhập:").pack(side="left")
        ttk.Entry(r3, textvariable=self.err_custom_var, width=18).pack(side="left", padx=(4, 12))
        ttk.Button(r3, text="+ Thêm lỗi", command=self.add_error).pack(side="left")
        ttk.Label(form, textvariable=self.err_hint, foreground="#335").grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(6, 0))

        lst = ttk.LabelFrame(self.err_tab, text="Danh sách lỗi sẽ chèn", padding=10)
        lst.pack(fill="both", expand=True, pady=(10, 0))
        cols = ("field", "etype", "mode", "rows", "custom")
        self.err_tree = ttk.Treeview(lst, columns=cols, show="headings", height=8)
        for c, t, w in [("field", "Cột / quy tắc", 380), ("etype", "Loại lỗi", 230), ("mode", "Chọn dòng", 140),
                        ("rows", "Dòng", 90), ("custom", "Giá trị tự nhập", 140)]:
            self.err_tree.heading(c, text=t, anchor="w")
            self.err_tree.column(c, width=w, anchor="w")
        self.err_tree.pack(side="left", fill="both", expand=True)
        self.err_tree.bind("<Delete>", lambda e: self.remove_error())
        side = ttk.Frame(lst)
        side.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(side, text="Xóa dòng chọn", command=self.remove_error).pack(fill="x")
        ttk.Button(side, text="Xóa hết", command=self.clear_errors).pack(fill="x", pady=4)

        fbox = ttk.LabelFrame(self.err_tab, text="Lỗi cấp file", padding=10)
        fbox.pack(fill="x", pady=(10, 0))
        for i, (k, label) in enumerate(core.FILE_ERROR_LABELS.items()):
            ttk.Checkbutton(fbox, text=label, variable=self.file_err_vars[k]).grid(
                row=0, column=i, sticky="w", padx=(0, 20))

    def _build_preview_tab(self):
        pw = ttk.Panedwindow(self.prev_tab, orient="vertical")
        pw.pack(fill="both", expand=True)
        tf = ttk.Frame(pw)
        self.preview_text = tk.Text(tf, wrap="none", font="TkFixedFont", height=18, undo=False)
        ys = ttk.Scrollbar(tf, orient="vertical", command=self.preview_text.yview)
        xs = ttk.Scrollbar(tf, orient="horizontal", command=self.preview_text.xview)
        self.preview_text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.preview_text.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.preview_text.tag_configure("err", background="#ffdede")
        self.preview_text.tag_configure("ht", foreground="#1f5fbf")
        self.preview_text.tag_configure("ln", foreground="#999")
        pw.add(tf, weight=3)

        rf = ttk.LabelFrame(pw, text="Lỗi đã chèn", padding=6)
        cols = ("line", "field", "code", "label", "old", "new", "note")
        self.rep_tree = ttk.Treeview(rf, columns=cols, show="headings", height=6)
        for c, t, w in [("line", "Dòng", 50), ("field", "Cột", 150), ("code", "Mã NICE", 70), ("label", "Lỗi", 260),
                        ("old", "Giá trị gốc", 140), ("new", "Giá trị lỗi", 140), ("note", "Ghi chú", 260)]:
            self.rep_tree.heading(c, text=t, anchor="w")
            self.rep_tree.column(c, width=w, anchor="w")
        rs = ttk.Scrollbar(rf, orient="vertical", command=self.rep_tree.yview)
        self.rep_tree.configure(yscrollcommand=rs.set)
        self.rep_tree.pack(side="left", fill="both", expand=True)
        rs.pack(side="left", fill="y")
        self.rep_tree.bind("<Double-1>", self._jump_to_line)
        pw.add(rf, weight=1)

    # ------------------------------------------------------------------ layouts
    def reload_layouts(self):
        if self.layout:
            self._save_config()
        repo, problems = core.load_layouts(LAYOUT_DIR)
        self.repo = repo
        if problems:
            messagebox.showwarning("Layout lỗi", "Không đọc được:\n\n" + "\n".join(problems))
        self._build_header_tab()
        types = sorted(k for k in repo if k not in (core.HEADER_CD, core.TRAILER_CD))
        self.info_cb["values"] = types
        missing = [c for c in (core.HEADER_CD, core.TRAILER_CD) if c not in repo]
        cur = self.info_var.get() or DEFAULT_INFO
        self.layout = None
        if types:
            self.select_layout(cur if cur in types else types[0])
        else:
            self.info_var.set("")
            self._refresh_columns()
            self._refresh_err_fields()
        msg = f"Đã nạp {len(types)} loại dữ liệu"
        if not core.find_code_file(LAYOUT_DIR):
            msg += "   ⚠ Thiếu codes/nci_codes.csv"
        if missing:
            msg += f"   ⚠ Thiếu layout: {', '.join(missing)}"
        self.status.set(msg)

    def select_layout(self, info: str):
        self._set_mode(None)
        if self.layout and self.layout.info_cd != info:
            self._save_config()
        self.layout = self.repo[info]
        self.info_var.set(info)
        if "informationType" in self.header_vars:
            self.header_vars["informationType"].set(info)
        self.clear_csv(silent=True)
        self._load_config(info)
        self._refresh_columns()
        self._refresh_err_fields()
        self._refresh_errors()
        self.status.set(f"{info}: {len(self.layout.fields)} cột")

    # ------------------------------------------------------------------ cột
    def _refresh_columns(self):
        self.col_tree.delete(*self.col_tree.get_children())
        if not self.layout:
            return
        for f in self.layout.fields:
            val = "D (cố định)" if f.name == "recordType" else self.fixed.get(f.name, "")
            self.col_tree.insert("", "end", iid=f.name, values=(f.index, f.name, f.type, f.rule_text(), val),
                                 tags=("set",) if self.fixed.get(f.name) else ())

    def _on_col_select(self, _=None):
        sel = self.col_tree.selection()
        if sel and self.layout:
            f = self.layout.get(sel[0])
            self.desc_var.set(f"{f.name}: {f.desc}" if f.desc else f.name)

    def _edit_value(self, event):
        tree = self.col_tree
        item = tree.identify_row(event.y) if event else (tree.selection() or [None])[0]
        if not item or item == "recordType":
            return
        tree.selection_set(item)
        tree.see(item)
        self.update_idletasks()
        bbox = tree.bbox(item, "value")
        if not bbox:
            return
        x, y, w, h = bbox
        entry = ttk.Entry(tree)
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, self.fixed.get(item, ""))
        entry.select_range(0, "end")
        entry.focus_set()
        done = {"v": False}

        def finish(save: bool):
            if done["v"]:
                return
            done["v"] = True
            if save:
                v = entry.get()
                if v.strip():
                    self.fixed[item] = v
                else:
                    self.fixed.pop(item, None)
                tree.set(item, "value", self.fixed.get(item, ""))
                tree.item(item, tags=("set",) if self.fixed.get(item) else ())
            entry.destroy()

        entry.bind("<Return>", lambda e: finish(True))
        entry.bind("<KP_Enter>", lambda e: finish(True))
        entry.bind("<Escape>", lambda e: finish(False))
        entry.bind("<FocusOut>", lambda e: finish(True))

    def _clear_value(self):
        for item in self.col_tree.selection():
            self.fixed.pop(item, None)
        self._refresh_columns()

    def _clear_all_values(self):
        if self.fixed and messagebox.askyesno("Xóa", "Xóa toàn bộ giá trị đã đặt cho các cột?"):
            self.fixed.clear()
            self._refresh_columns()

    # ------------------------------------------------------------------ lỗi
    def _refresh_err_fields(self):
        names = [f.name for f in self.layout.fields if f.name != "recordType"] if self.layout else []
        rules = [self._rule_label(i) for i in range(len(self.layout.rules))] if self.layout else []
        self.err_field_cb["values"] = [ROW_LEVEL_LABEL] + names + rules
        self.err_field_cb.set("")
        self.err_type_cb.set("")
        self.err_type_cb["values"] = []
        self.err_hint.set("")

    def _rule_label(self, i: int) -> str:
        return f"{RULE_LABEL_PREFIX}{i + 1}) {self.layout.rules[i].full_label()}"

    def _rule_index(self, label: str) -> int | None:
        if label.startswith(RULE_LABEL_PREFIX):
            return int(label[len(RULE_LABEL_PREFIX):].split(")")[0]) - 1
        return None

    def _on_err_field(self, _=None):
        name = self.err_field_cb.get()
        ri = self._rule_index(name)
        if ri is not None:
            self._err_type_codes = ["RULE"]
            self.err_type_cb["values"] = [ALL_ERROR_LABELS["RULE"]]
            self.err_type_cb.current(0)
            r = self.layout.rules[ri]
            self.err_hint.set(f"Làm sai quy tắc: {r.label()}")
            return
        f = None if name == ROW_LEVEL_LABEL else self.layout.get(name)
        self._err_type_codes = core.applicable_errors(f)
        self.err_type_cb["values"] = [ALL_ERROR_LABELS[c] for c in self._err_type_codes]
        # mặc định chọn loại lỗi "đặc trưng" nhất của cột (vd: ngoài danh sách, sai độ dài)
        self.err_type_cb.current(1 if f and len(self._err_type_codes) > 2 else 0)
        if f is None:
            self.err_hint.set("Lỗi cấu trúc dòng: thiếu/thừa cột, sai recordType")
        else:
            self.err_hint.set(f"{f.name}: {f.rule_text() or 'không có quy tắc, nên dùng Giá trị tự nhập'}")

    def add_error(self):
        if not self.layout:
            return
        name = self.err_field_cb.get()
        idx = self.err_type_cb.current()
        if not name or idx < 0:
            messagebox.showwarning("Thiếu thông tin", "Chọn cột và loại lỗi trước.")
            return
        code = self._err_type_codes[idx]
        mode = MODES[max(self.err_mode_cb.current(), 0)][0]
        rows = self.err_rows_var.get().strip() or "1"
        try:
            if mode == "random":
                if int(rows) < 1:
                    raise ValueError
            else:
                core.parse_rows(rows, 10 ** 9)
        except ValueError:
            messagebox.showwarning("Dòng không hợp lệ",
                                   "Ngẫu nhiên: nhập 1 số (vd 3).\nDòng chỉ định: vd 1,4,7-9")
            return
        custom = self.err_custom_var.get()
        if code == "CUSTOM" and custom == "":
            messagebox.showwarning("Thiếu giá trị", "Loại 'Giá trị tự nhập' cần nhập giá trị lỗi.")
            return
        ri = self._rule_index(name)
        fld = core.ROW_LEVEL if name == ROW_LEVEL_LABEL else f"{core.RULE_PREFIX}{ri}" if ri is not None else name
        self.errors.append(core.ErrorSpec(fld, code, mode, rows, custom if code == "CUSTOM" else ""))
        self._refresh_errors()

    def _refresh_errors(self):
        self.err_tree.delete(*self.err_tree.get_children())
        modes = dict(MODES)
        for i, s in enumerate(self.errors):
            if s.field.startswith(core.RULE_PREFIX):
                shown = self._rule_label(int(s.field[len(core.RULE_PREFIX):]))
            else:
                shown = ROW_LEVEL_LABEL if s.field == core.ROW_LEVEL else s.field
            self.err_tree.insert("", "end", iid=str(i), values=(
                shown,
                ALL_ERROR_LABELS.get(s.etype, s.etype), modes.get(s.mode, s.mode), s.rows, s.custom))

    def remove_error(self):
        for iid in sorted((int(i) for i in self.err_tree.selection()), reverse=True):
            del self.errors[iid]
        self._refresh_errors()

    def clear_errors(self):
        self.errors.clear()
        for v in self.file_err_vars.values():
            v.set(False)
        self._refresh_errors()

    # ------------------------------------------------------------------ CSV
    def load_csv(self):
        if not self.layout:
            return
        path = filedialog.askopenfilename(title="Chọn file CSV dữ liệu (dòng đầu = tên cột trong layout)",
                                          filetypes=[("CSV", "*.csv"), ("Tất cả", "*.*")])
        if not path:
            return
        try:
            rows = core.read_csv(path)
        except Exception as e:
            messagebox.showerror("Lỗi đọc CSV", str(e))
            return
        if not rows:
            messagebox.showwarning("CSV rỗng", "File CSV không có dòng dữ liệu.")
            return
        cols = list(rows[0].keys())
        unknown = [c for c in cols if c not in self.layout.names]
        missing = [n for n in self.layout.names if n not in cols and n != "recordType"]
        self.csv_rows, self.csv_name = rows, Path(path).name
        self.csv_label.set(f"CSV: {self.csv_name} ({len(rows)} dòng)")
        self.rows_spin.configure(state="disabled")
        self.cust_spin.configure(state="disabled")
        self.lifecycle_cb.configure(state="disabled")
        msg = []
        if unknown:
            msg.append("Cột không có trong layout (bỏ qua): " + ", ".join(unknown))
        if missing:
            msg.append("Cột thiếu trong CSV (sẽ tự sinh): " + ", ".join(missing))
        if msg:
            messagebox.showinfo("Lưu ý CSV", "\n\n".join(msg))

    def clear_csv(self, silent=False):
        self.csv_rows, self.csv_name = None, ""
        self.csv_label.set("(tự sinh dữ liệu)")
        self.rows_spin.configure(state="normal")
        self.cust_spin.configure(state="normal")
        self.lifecycle_cb.configure(state="normal")

    # ------------------------------------------------------------------ chạy
    def _run_generate(self) -> core.GenResult:
        if not self.layout:
            raise ValueError("Chưa chọn loại dữ liệu.")
        missing = [c for c in (core.HEADER_CD, core.TRAILER_CD) if c not in self.repo]
        if missing:
            raise ValueError(f"Thiếu layout {', '.join(missing)} trong thư mục layout.")
        if self.csv_rows is None:
            try:
                n = int(self.rows_var.get())
                assert n >= 1
            except Exception:
                raise ValueError("Số dòng phải là số nguyên ≥ 1.")
        else:
            n = len(self.csv_rows)
        header = {k: v.get() for k, v in self.header_vars.items()}
        return core.generate(
            self.layout, self.repo[core.HEADER_CD], self.repo[core.TRAILER_CD], header,
            n_rows=n, fixed=self.fixed, errors=self.errors,
            file_errors=[k for k, v in self.file_err_vars.items() if v.get()],
            csv_rows=self.csv_rows, seed=self._seed(),
            customers=self._customers(), lifecycle=self.lifecycle_var.get(),
            file_suffix=self.suffix_var.get().strip() or None,
            filename=self.filename_var.get().strip() or None,
        )

    def _seed(self) -> int | None:
        seed_txt = self.seed_var.get().strip()
        try:
            return int(seed_txt) if seed_txt else None
        except ValueError:
            raise ValueError("Seed phải là số nguyên (hoặc để trống).")

    def _customers(self) -> int:
        try:
            n = int(self.cust_var.get())
            assert n >= 0
            return n
        except Exception:
            raise ValueError("Số khách hàng phải là số nguyên ≥ 0.")

    def preview(self):
        self._set_mode(None)
        try:
            res = self._run_generate()
        except Exception as e:
            messagebox.showerror("Không tạo được dữ liệu", str(e))
            return
        self._show_result(res)
        self.nb.select(self.prev_tab)
        self.status.set(f"Xem trước {res.filename}: {len(res.lines)} dòng, "
                        f"{len(res.report)} lỗi đã chèn (chưa ghi file)")

    def generate_file(self):
        if self.set_pending:
            self._write_set(*self.set_pending)
            return
        try:
            res = self._run_generate()
            out = Path(self.out_dir.get())
            out.mkdir(parents=True, exist_ok=True)
            path = out / res.filename
            if path.exists() and not messagebox.askyesno("Ghi đè?", f"{path.name} đã tồn tại. Ghi đè?"):
                return
            core.write_lines(path, res.lines, self.crlf_var.get())
            rep_path = None
            if res.report and self.report_var.get():
                rep_path = path.with_name(path.name + ".errors.csv")
                core.write_report(rep_path, res.report)
        except Exception as e:
            messagebox.showerror("Không tạo được file", str(e))
            return
        self._save_config()
        self._show_result(res)
        self.nb.select(self.prev_tab)
        msg = f"Đã tạo {path}  ({len(res.lines) - 2 if len(res.lines) >= 2 else 0} dòng detail"
        msg += f", {len(res.report)} lỗi)" if res.report else ")"
        self.status.set(msg)
        detail = f"Đã tạo file:\n{path}"
        if rep_path:
            detail += f"\n\nBáo cáo lỗi:\n{rep_path}"
        messagebox.showinfo("Hoàn tất", detail)

    def _show_result(self, res: core.GenResult):
        t = self.preview_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        width = len(str(len(res.lines)))
        for i, line in enumerate(res.lines[:PREVIEW_LIMIT], 1):
            t.insert("end", f"{i:>{width}}  ", ("ln",))
            tags = ["err"] if i in res.error_lines else []
            if line[:1] in ("H", "T") and i in (1, len(res.lines)):
                tags.append("ht")
            t.insert("end", line + "\n", tuple(tags))
        if len(res.lines) > PREVIEW_LIMIT:
            t.insert("end", f"... còn {len(res.lines) - PREVIEW_LIMIT} dòng (chỉ hiển thị {PREVIEW_LIMIT} dòng đầu)\n",
                     ("ln",))
        t.configure(state="disabled")
        self.rep_tree.delete(*self.rep_tree.get_children())
        for r in res.report:
            self.rep_tree.insert("", "end", values=(r["line"], r["field"], r.get("code", ""), r["label"],
                                                    r["old"], r["new"], r["note"]))

    # ------------------------------------------------------------------ bộ file liên kết
    def open_set_dialog(self):
        missing = [c for c in (core.HEADER_CD, core.TRAILER_CD) if c not in self.repo]
        if missing:
            messagebox.showerror("Thiếu layout", f"Thiếu layout {', '.join(missing)} trong thư mục layout.")
            return
        cfg = self.set_cfg
        auto_seed = random.randrange(2 ** 31)   # seed trống: xem trước và tạo file ra cùng 1 bộ
        win = tk.Toplevel(self)
        win.title("Tạo bộ file liên kết")
        win.transient(self)
        win.resizable(False, False)
        body = ttk.Frame(win, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, foreground="#335", justify="left", wraplength=520,
                  text="Sinh một lần cả bộ file theo thứ tự gửi: khách hàng → hợp đồng → thông tin gắn với hợp đồng. "
                       "Mọi custCd, ctrtNo, debtorCustCd, debtorCtrtNo đều khớp nhau.").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        nums = {}
        for i, (key, label, default) in enumerate([
                ("individuals", "Khách hàng cá nhân (INID, CUST000001...)", 1),
                ("corporates", "Khách hàng doanh nghiệp (COID, CORP000001...)", 0),
                ("loans", "Hợp đồng vay mỗi khách hàng (LOAN)", 1),
                ("cards", "Thẻ mỗi khách hàng (CARD)", 1)], 1):
            ttk.Label(body, text=label).grid(row=i, column=0, sticky="w", pady=2)
            nums[key] = tk.StringVar(value=str(cfg.get(key, default)))
            ttk.Spinbox(body, from_=0, to=100_000, textvariable=nums[key], width=8).grid(
                row=i, column=1, sticky="w", padx=8)

        box = ttk.LabelFrame(body, text="Loại cần tạo", padding=8)
        box.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        chosen = set(cfg.get("types", core.SET_TYPES))
        type_vars: dict[str, tk.BooleanVar] = {}
        for s, (step, name) in enumerate(zip(core.SET_STEPS, core.SET_STEP_NAMES)):
            ttk.Label(box, text=f"{s + 1}. {name}").grid(row=s, column=0, sticky="w", pady=2)
            if s == 0:      # khách hàng: luôn đăng ký, theo số khách hàng ở trên
                ttk.Label(box, text="INID / COID (theo số khách hàng)", foreground="#555").grid(
                    row=s, column=1, columnspan=5, sticky="w", padx=(8, 0))
                continue
            for j, t in enumerate(step):
                type_vars[t] = tk.BooleanVar(value=t in chosen and t in self.repo)
                ttk.Checkbutton(box, text=t, variable=type_vars[t],
                                state="normal" if t in self.repo else "disabled").grid(
                    row=s, column=j + 1, sticky="w", padx=(8, 0))
        ttk.Label(body, foreground="#555", justify="left", wraplength=520,
                  text="Chọn LRPY / DELQ / GUAR / COLL thì tự kèm LOAN, CRPY thì kèm CARD. Seed, vòng đời, Header, "
                       "đuôi tên file và CRLF lấy theo màn hình chính. Bộ file không chèn lỗi.").grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))

        def build() -> tuple[core.SetResult, str] | None:
            try:
                vals = {k: int(v.get()) for k, v in nums.items()}
            except ValueError:
                messagebox.showwarning("Số không hợp lệ", "Các ô số phải là số nguyên.", parent=win)
                return None
            types = [t for t, v in type_vars.items() if v.get()]
            self.set_cfg = {**vals, "types": types}
            suffix = self.suffix_var.get().strip() or date.today().strftime("%y%m%d")
            try:
                return core.generate_set(
                    self.repo, {k: v.get() for k, v in self.header_vars.items()}, types,
                    individuals=vals["individuals"], corporates=vals["corporates"],
                    loans_per_cust=vals["loans"], cards_per_cust=vals["cards"],
                    seed=self._seed() if self.seed_var.get().strip() else auto_seed,
                    lifecycle=self.lifecycle_var.get(), file_suffix=suffix), suffix
            except Exception as e:
                messagebox.showerror("Không tạo được bộ file", str(e), parent=win)
                return None

        def preview():
            built = build()
            if not built:
                return
            res = built[0]
            self._show_set(res)
            self.nb.select(self.prev_tab)
            self._set_mode(built)
            self.status.set(f"Xem trước bộ {len(res.files)} file: "
                            + ", ".join(f"{f.info_cd} {len(f.result.lines) - 2}" for f in res.files)
                            + " dòng — bấm “★ Tạo bộ file” để ghi")

        def run():
            built = build()
            if built and self._write_set(*built, parent=win) and win.winfo_exists():
                win.destroy()

        btns = ttk.Frame(body)
        btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="Đóng", command=win.destroy).pack(side="right")
        ttk.Button(btns, text="★ Tạo bộ file", command=run).pack(side="right", padx=6)
        ttk.Button(btns, text="Xem trước", command=preview).pack(side="right")

    def _set_mode(self, pending: tuple[core.SetResult, str] | None):
        """Đang xem trước bộ file liên kết: nút chính ghi cả bộ đã xem thay vì 1 file."""
        self.set_pending = pending
        self.gen_btn.configure(text="★ Tạo bộ file" if pending else "★ Tạo file")

    def _write_set(self, res: core.SetResult, suffix: str, parent=None) -> bool:
        folder = Path(self.out_dir.get()) / f"bo_lien_ket_{suffix}"
        try:
            if folder.exists() and any(folder.iterdir()) and not messagebox.askyesno(
                    "Ghi đè?", f"{folder} đã có dữ liệu. Ghi đè các file trùng tên?", parent=parent or self):
                return False
            manifest = core.write_set(folder, res, self.crlf_var.get())
        except Exception as e:
            messagebox.showerror("Không tạo được bộ file", str(e), parent=parent or self)
            return False
        self._set_mode(None)
        self._save_config()
        self._show_set(res)
        self.nb.select(self.prev_tab)
        self.status.set(f"Đã tạo bộ {len(res.files)} file trong {folder}")
        messagebox.showinfo("Hoàn tất", f"Đã tạo bộ file trong:\n{folder}\n\n{manifest.read_text(encoding='utf-8')}")
        return True

    def _show_set(self, res: core.SetResult):
        """Xem trước cả bộ: từng file theo thứ tự gửi, có dòng tiêu đề tên file."""
        t = self.preview_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        shown = 0
        for f in res.files:
            t.insert("end", f"### Bước {f.step}: {f.path}\n", ("ln",))
            for line in f.result.lines[:max(PREVIEW_LIMIT - shown, 0)]:
                t.insert("end", line + "\n", ("ht",) if line[:1] in ("H", "T") else ())
            shown += len(f.result.lines)
            if shown > PREVIEW_LIMIT:
                t.insert("end", f"... (chỉ hiển thị {PREVIEW_LIMIT} dòng đầu của cả bộ)\n", ("ln",))
                break
        for note in res.notes:
            t.insert("end", f"### {note}\n", ("ln",))
        t.configure(state="disabled")
        self.rep_tree.delete(*self.rep_tree.get_children())

    def _jump_to_line(self, _=None):
        sel = self.rep_tree.selection()
        if sel:
            line = self.rep_tree.item(sel[0], "values")[0]
            if str(line).isdigit():
                self.preview_text.see(f"{line}.0")

    # ------------------------------------------------------------------ kiểm tra file
    def validate_file(self):
        path = filedialog.askopenfilename(initialdir=self.out_dir.get(), title="Chọn file cần kiểm tra")
        if not path:
            return
        try:
            lines = core.read_lines(path)
            issues = core.validate_lines(lines, self.repo, self.info_var.get() or None, filename=path)
        except Exception as e:
            messagebox.showerror("Không kiểm tra được", str(e))
            return
        win = tk.Toplevel(self)
        win.title(f"Kiểm tra: {Path(path).name}")
        win.geometry("1100x560")
        n_err = sum(1 for i in issues if i["level"] == "Lỗi")
        n_warn = len(issues) - n_err
        summary = (f"✔ Không phát hiện lỗi ({len(lines)} dòng)" if not issues
                   else f"✖ {n_err} lỗi, {n_warn} cảnh báo nghiệp vụ trong {len(lines)} dòng")
        ttk.Label(win, text=summary, padding=8, foreground="#1a7f37" if not n_err else "#b00020",
                  font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        frame = ttk.Frame(win, padding=(8, 0, 8, 8))
        frame.pack(fill="both", expand=True)
        cols = ("line", "code", "level", "field", "issue", "value")
        tree = ttk.Treeview(frame, columns=cols, show="headings")
        tree.tag_configure("warn", foreground="#8a5a00")
        for c, t, w in [("line", "Dòng", 55), ("code", "Mã NICE", 70), ("level", "Mức", 75), ("field", "Cột", 190),
                        ("issue", "Lỗi", 420), ("value", "Giá trị", 180)]:
            tree.heading(c, text=t, anchor="w")
            tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        for it in issues:
            tree.insert("", "end", values=(it["line"], it["code"], it["level"], it["field"], it["issue"], it["value"]),
                        tags=("warn",) if it["level"] != "Lỗi" else ())

    # ------------------------------------------------------------------ thư mục / cấu hình
    def choose_out_dir(self):
        d = filedialog.askdirectory(initialdir=self.out_dir.get(), title="Chọn thư mục xuất file")
        if d:
            self.out_dir.set(d)

    def open_out_dir(self):
        d = Path(self.out_dir.get())
        d.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(d)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(d)])
            else:
                subprocess.Popen(["xdg-open", str(d)])
        except Exception as e:
            messagebox.showerror("Không mở được thư mục", str(e))

    def _load_config(self, info: str):
        data = _read_json(CONFIG_DIR / f"{info}.json")
        names = set(self.layout.names)
        self.fixed = {k: v for k, v in data.get("fixed", {}).items() if k in names and v}
        self.errors = []
        for e in data.get("errors", []):
            try:
                spec = core.ErrorSpec(**e)
                ok_rule = (spec.field.startswith(core.RULE_PREFIX)
                           and int(spec.field[len(core.RULE_PREFIX):]) < len(self.layout.rules))
                if spec.field == core.ROW_LEVEL or spec.field in names or ok_rule:
                    self.errors.append(spec)
            except TypeError:
                pass
        fe = set(data.get("file_errors", []))
        for k, v in self.file_err_vars.items():
            v.set(k in fe)
        self.rows_var.set(str(data.get("rows", self.rows_var.get() or "10")))
        self.cust_var.set(str(data.get("customers", "1")))
        self.lifecycle_var.set(bool(data.get("lifecycle", True)))

    def _save_config(self):
        try:
            if self.layout:
                _write_json(CONFIG_DIR / f"{self.layout.info_cd}.json", {
                    "rows": self.rows_var.get(),
                    "customers": self.cust_var.get(),
                    "lifecycle": self.lifecycle_var.get(),
                    "fixed": {k: v for k, v in self.fixed.items() if v},
                    "errors": [asdict(e) for e in self.errors],
                    "file_errors": [k for k, v in self.file_err_vars.items() if v.get()],
                })
            _write_json(GLOBAL_CFG, {
                "out_dir": self.out_dir.get(),
                "crlf": self.crlf_var.get(), "report": self.report_var.get(), "set": self.set_cfg,
                "header": {k: v.get() for k, v in self.header_vars.items()
                           if k not in ("recordType", "informationType")},
            })
        except Exception as e:
            self.status.set(f"⚠ Không lưu được cấu hình: {e}")

    def _on_close(self):
        self._save_config()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
