// app.js - Giao diện web của NICE Test Data Generator.
// Logic sinh/kiểm tra dữ liệu chạy bằng Python (Pyodide) qua web_api.py -> datagen_core.py,
// giống hệt bản Tkinter (datagen_ui.py). File này chỉ lo phần giao diện và lưu cấu hình.
"use strict";

const $ = (id) => document.getElementById(id);
const PREVIEW_LIMIT = 1000;
const ROW_LEVEL_LABEL = "(Cả dòng)";
const STORE_PREFIX = "nice-datagen:";
const DEFAULT_INFO = "LOAN"; // loại dữ liệu khi mới mở trang

const state = {
  py: null,
  api: null,
  meta: null,
  info: "",
  fixed: {},
  errors: [],
  csvPath: null,
  csvName: "",
  errTypeCodes: [],
  headerInputs: {},
};

// ------------------------------------------------------------------ lưu cấu hình (localStorage)
function loadStore(key) {
  try {
    return JSON.parse(localStorage.getItem(STORE_PREFIX + key) || "{}") || {};
  } catch {
    return {};
  }
}

function saveStore(key, value) {
  try {
    localStorage.setItem(STORE_PREFIX + key, JSON.stringify(value));
  } catch {
    /* trình duyệt chặn lưu trữ: bỏ qua */
  }
}

// ------------------------------------------------------------------ Python
function call(name, ...args) {
  const res = JSON.parse(state.api[name](...args));
  if (res && res.error) {
    console.error(res.trace);
    throw new Error(res.error);
  }
  return res;
}

async function fetchText(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`Không tải được ${url} (${r.status})`);
  return r.text();
}

async function boot() {
  try {
    const py = await loadPyodide();
    $("loading-text").textContent = "Đang nạp layout...";
    const [bundle, core, api] = await Promise.all([
      fetchText("bundle.json").then(JSON.parse), fetchText("datagen_core.py"), fetchText("web_api.py"),
    ]);
    for (const dir of ["/app/layouts", "/app/rules", "/app/codes", "/tmp/in"]) py.FS.mkdirTree(dir);
    for (const [path, text] of Object.entries(bundle)) py.FS.writeFile(`/app/${path}`, text);
    py.FS.writeFile("/app/datagen_core.py", core);
    py.FS.writeFile("/app/web_api.py", api);
    py.runPython("import sys; sys.path.insert(0, '/app')");
    state.py = py;
    state.api = py.pyimport("web_api");
    init(call("load"));
    $("loading").classList.add("hidden");
  } catch (e) {
    $("loading-text").textContent = "Không khởi động được: " + e.message;
    console.error(e);
  }
}

// ------------------------------------------------------------------ khởi tạo
function init(meta) {
  state.meta = meta;
  if (meta.problems.length) alert("Không đọc được:\n\n" + meta.problems.join("\n"));
  const g = loadStore("global");
  $("crlf").checked = !!g.crlf;
  $("report").checked = g.report !== false;
  $("suffix").value = yymmdd(new Date());

  const sel = $("info");
  sel.replaceChildren(...meta.types.map((t) => new Option(t, t)));
  buildHeader(g.header || {});
  buildFileErrors();
  buildSetTypes();
  if (meta.types.length) selectLayout(meta.types.includes(DEFAULT_INFO) ? DEFAULT_INFO : meta.types[0]);

  let msg = `Đã nạp ${meta.types.length} loại dữ liệu`;
  if (!meta.hasCodes) msg += "   ⚠ Thiếu codes/nci_codes.csv";
  if (meta.missing.length) msg += `   ⚠ Thiếu layout: ${meta.missing.join(", ")}`;
  setStatus(msg);
}

function yymmdd(d) {
  return String(d.getFullYear()).slice(2) + String(d.getMonth() + 1).padStart(2, "0") + String(d.getDate()).padStart(2, "0");
}

function setStatus(text) {
  $("status").textContent = text;
}

function layout() {
  return state.meta.layouts[state.info];
}

// ------------------------------------------------------------------ header
function buildHeader(saved) {
  const box = $("header-fields");
  box.replaceChildren();
  state.headerInputs = {};
  if (!state.meta.header.length) {
    box.textContent = "⚠ Không có layouts/HEADER.xml";
    return;
  }
  for (const f of state.meta.header) {
    const label = document.createElement("label");
    label.textContent = f.name;
    label.htmlFor = "h-" + f.name;
    label.title = f.desc;
    const input = document.createElement("input");
    input.id = "h-" + f.name;
    input.size = 32;
    input.value = saved[f.name] ?? state.meta.headerDefaults[f.name] ?? "";
    if (f.name === "recordType") {
      input.value = "H";
      input.disabled = true;
    } else if (f.name === "informationType") {
      input.disabled = true;
    }
    box.append(label, input);
    state.headerInputs[f.name] = input;
  }
}

function headerValues() {
  return Object.fromEntries(Object.entries(state.headerInputs).map(([k, el]) => [k, el.value]));
}

// ------------------------------------------------------------------ chọn loại dữ liệu
function selectLayout(info) {
  if (state.info && state.info !== info) saveConfig();
  state.info = info;
  $("info").value = info;
  if (state.headerInputs.informationType) state.headerInputs.informationType.value = info;
  clearCsv();
  loadConfig(info);
  renderCols();
  renderErrFields();
  renderErrors();
  setStatus(`${info}: ${layout().fields.length} cột`);
}

function loadConfig(info) {
  const data = loadStore("cfg:" + info);
  const lay = layout();
  const names = new Set(lay.fields.map((f) => f.name));
  state.fixed = Object.fromEntries(Object.entries(data.fixed || {}).filter(([k, v]) => names.has(k) && v));
  state.errors = (data.errors || []).filter((e) => {
    if (e.field === state.meta.rowLevel || names.has(e.field)) return true;
    if (String(e.field).startsWith(state.meta.rulePrefix)) {
      return Number(e.field.slice(state.meta.rulePrefix.length)) < lay.rules.length;
    }
    return false;
  });
  const fe = new Set(data.fileErrors || []);
  for (const box of document.querySelectorAll("#file-errors input")) box.checked = fe.has(box.value);
  $("rows").value = data.rows || $("rows").value || "10";
  $("customers").value = data.customers ?? "1";
  $("lifecycle").checked = data.lifecycle !== false;
}

function saveConfig() {
  if (state.info) {
    saveStore("cfg:" + state.info, {
      rows: $("rows").value,
      customers: $("customers").value,
      lifecycle: $("lifecycle").checked,
      fixed: state.fixed,
      errors: state.errors,
      fileErrors: checkedFileErrors(),
    });
  }
  const header = headerValues();
  delete header.recordType;
  delete header.informationType;
  saveStore("global", { info: state.info, header, crlf: $("crlf").checked, report: $("report").checked });
}

// ------------------------------------------------------------------ tab 2: giá trị cột
function renderCols() {
  const body = $("cols-table").tBodies[0];
  body.replaceChildren();
  $("col-desc").textContent = "";
  for (const f of layout().fields) {
    const tr = document.createElement("tr");
    const input = document.createElement("input");
    if (f.name === "recordType") {
      input.value = "D (cố định)";
      input.disabled = true;
    } else {
      input.value = state.fixed[f.name] || "";
      input.addEventListener("input", () => {
        if (input.value.trim()) state.fixed[f.name] = input.value;
        else delete state.fixed[f.name];
        tr.classList.toggle("set", !!state.fixed[f.name]);
      });
    }
    tr.classList.toggle("set", !!state.fixed[f.name]);
    for (const text of [f.index, f.name, f.type, f.rule]) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.append(td);
    }
    const td = document.createElement("td");
    td.append(input);
    tr.append(td);
    tr.addEventListener("click", () => {
      for (const r of body.rows) r.classList.remove("sel");
      tr.classList.add("sel");
      $("col-desc").textContent = f.desc ? `${f.name}: ${f.desc}` : f.name;
    });
    body.append(tr);
  }
}

// ------------------------------------------------------------------ tab 3: lỗi dữ liệu
function ruleLabel(i) {
  return `(Quy tắc ${i + 1}) ${layout().rules[i]}`;
}

function errFieldLabel(field) {
  if (field === state.meta.rowLevel) return ROW_LEVEL_LABEL;
  if (field.startsWith(state.meta.rulePrefix)) return ruleLabel(Number(field.slice(state.meta.rulePrefix.length)));
  return field;
}

function renderErrFields() {
  const lay = layout();
  const opts = [new Option("— chọn —", "")];
  opts[0].disabled = true;
  opts.push(new Option(ROW_LEVEL_LABEL, state.meta.rowLevel));
  for (const f of lay.fields) if (f.name !== "recordType") opts.push(new Option(f.name, f.name));
  lay.rules.forEach((_, i) => opts.push(new Option(ruleLabel(i), state.meta.rulePrefix + i)));
  $("err-field").replaceChildren(...opts);
  $("err-field").value = "";
  $("err-type").replaceChildren();
  state.errTypeCodes = [];
  $("err-hint").textContent = "";
}

function onErrField() {
  const value = $("err-field").value;
  const labels = state.meta.errorLabels;
  let codes;
  let selected = 0;
  if (value.startsWith(state.meta.rulePrefix)) {
    const i = Number(value.slice(state.meta.rulePrefix.length));
    codes = ["RULE"];
    $("err-hint").textContent = `Làm sai quy tắc: ${layout().rulePlain[i]}`;
  } else if (value === state.meta.rowLevel) {
    codes = state.meta.rowErrors;
    $("err-hint").textContent = "Lỗi cấu trúc dòng: thiếu/thừa cột, sai recordType";
  } else {
    const f = layout().fields.find((x) => x.name === value);
    codes = f.errors;
    // mặc định chọn loại lỗi "đặc trưng" nhất của cột (vd: ngoài danh sách, sai độ dài)
    selected = codes.length > 2 ? 1 : 0;
    $("err-hint").textContent = `${f.name}: ${f.rule || "không có quy tắc, nên dùng Giá trị tự nhập"}`;
  }
  state.errTypeCodes = codes;
  $("err-type").replaceChildren(...codes.map((c) => new Option(labels[c] || c, c)));
  $("err-type").selectedIndex = selected;
}

function parseRowsSpec(text) {
  // giống core.parse_rows: "1,4,7-9"
  for (const part of text.split(",")) {
    const p = part.trim();
    if (!p) continue;
    if (!/^\d+(\s*-\s*\d+)?$/.test(p)) return false;
  }
  return true;
}

function addError() {
  const field = $("err-field").value;
  const etype = $("err-type").value;
  if (!field || !etype) {
    alert("Chọn cột và loại lỗi trước.");
    return;
  }
  const mode = $("err-mode").value;
  const rows = $("err-rows").value.trim() || "1";
  const ok = mode === "random" ? /^\d+$/.test(rows) && Number(rows) >= 1 : parseRowsSpec(rows);
  if (!ok) {
    alert("Ngẫu nhiên: nhập 1 số (vd 3).\nDòng chỉ định: vd 1,4,7-9");
    return;
  }
  const custom = $("err-custom").value;
  if (etype === "CUSTOM" && custom === "") {
    alert("Loại 'Giá trị tự nhập' cần nhập giá trị lỗi.");
    return;
  }
  state.errors.push({ field, etype, mode, rows, custom: etype === "CUSTOM" ? custom : "" });
  renderErrors();
}

function renderErrors() {
  const body = $("err-table").tBodies[0];
  body.replaceChildren();
  const modes = { random: "Ngẫu nhiên N dòng", rows: "Dòng chỉ định" };
  state.errors.forEach((e, i) => {
    const tr = document.createElement("tr");
    for (const text of [errFieldLabel(e.field), state.meta.errorLabels[e.etype] || e.etype, modes[e.mode] || e.mode, e.rows, e.custom]) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.append(td);
    }
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn ghost";
    del.textContent = "Xóa";
    del.addEventListener("click", () => {
      state.errors.splice(i, 1);
      renderErrors();
    });
    const td = document.createElement("td");
    td.append(del);
    tr.append(td);
    body.append(tr);
  });
}

function buildFileErrors() {
  const box = $("file-errors");
  box.replaceChildren();
  for (const [code, label] of Object.entries(state.meta.fileErrors)) {
    const l = document.createElement("label");
    l.className = "check";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = code;
    l.append(input, " " + label);
    box.append(l);
  }
}

function checkedFileErrors() {
  return [...document.querySelectorAll("#file-errors input:checked")].map((x) => x.value);
}

// ------------------------------------------------------------------ CSV
async function writeUpload(file, path) {
  state.py.FS.writeFile(path, new Uint8Array(await file.arrayBuffer()));
  return path;
}

async function loadCsv(file) {
  if (!file || !state.info) return;
  try {
    const path = await writeUpload(file, "/tmp/in/input.csv");
    const res = call("csv_info", path, state.info);
    if (!res.rows) {
      alert("File CSV không có dòng dữ liệu.");
      return;
    }
    state.csvPath = path;
    state.csvName = file.name;
    $("csv-label").textContent = `CSV: ${file.name} (${res.rows} dòng)`;
    for (const id of ["rows", "customers", "lifecycle"]) $(id).disabled = true;
    const msg = [];
    if (res.unknown.length) msg.push("Cột không có trong layout (bỏ qua): " + res.unknown.join(", "));
    if (res.missing.length) msg.push("Cột thiếu trong CSV (sẽ tự sinh): " + res.missing.join(", "));
    if (msg.length) alert(msg.join("\n\n"));
  } catch (e) {
    alert("Lỗi đọc CSV: " + e.message);
  }
}

function clearCsv() {
  state.csvPath = null;
  state.csvName = "";
  $("csv-file").value = "";
  $("csv-label").textContent = "(tự sinh dữ liệu)";
  for (const id of ["rows", "customers", "lifecycle"]) $(id).disabled = false;
}

// ------------------------------------------------------------------ chạy
function generateParams() {
  if (!state.info) throw new Error("Chưa chọn loại dữ liệu.");
  const rows = Number($("rows").value);
  if (!state.csvPath && !(Number.isInteger(rows) && rows >= 1)) throw new Error("Số dòng phải là số nguyên ≥ 1.");
  const seedText = $("seed").value.trim();
  if (seedText && !/^-?\d+$/.test(seedText)) throw new Error("Seed phải là số nguyên (hoặc để trống).");
  const customers = Number($("customers").value);
  if (!(Number.isInteger(customers) && customers >= 0)) throw new Error("Số khách hàng phải là số nguyên ≥ 0.");
  return {
    info: state.info,
    header: headerValues(),
    rows,
    customers,
    lifecycle: $("lifecycle").checked,
    seed: seedText ? Number(seedText) : null,
    fixed: state.fixed,
    errors: state.errors,
    fileErrors: checkedFileErrors(),
    csvPath: state.csvPath,
    suffix: $("suffix").value.trim(),
    filename: $("filename").value.trim(),
    crlf: $("crlf").checked,
    report: $("report").checked,
  };
}

async function runGenerate() {
  const params = generateParams();
  setStatus("Đang sinh dữ liệu...");
  await new Promise((r) => setTimeout(r, 20)); // cho trình duyệt vẽ lại trước khi Python chạy
  return call("generate", JSON.stringify(params));
}

async function preview() {
  try {
    const res = await runGenerate();
    showResult(res);
    switchTab("preview");
    setStatus(`Xem trước ${res.filename}: ${res.lines.length} dòng, ${res.report.length} lỗi đã chèn (chưa tải file)`);
  } catch (e) {
    setStatus("Không tạo được dữ liệu");
    alert("Không tạo được dữ liệu: " + e.message);
  }
}

function download(path, name, type) {
  const bytes = state.py.FS.readFile(path);
  const url = URL.createObjectURL(new Blob([bytes], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function generateFile() {
  let res;
  try {
    res = await runGenerate();
    download(res.dataPath, res.filename, "text/plain");
    if (res.reportPath) download(res.reportPath, res.filename + ".errors.csv", "text/csv");
  } catch (e) {
    setStatus("Không tạo được file");
    alert("Không tạo được file: " + e.message);
    return;
  }
  saveConfig();
  showResult(res);
  switchTab("preview");
  const detail = res.lines.length >= 2 ? res.lines.length - 2 : 0;
  setStatus(`Đã tạo ${res.filename}  (${detail} dòng detail${res.report.length ? `, ${res.report.length} lỗi` : ""})`);
}

function showResult(res) {
  const pre = $("preview-text");
  const errLines = new Set(res.errorLines);
  const width = String(res.lines.length).length;
  const frag = document.createDocumentFragment();
  res.lines.slice(0, PREVIEW_LIMIT).forEach((line, idx) => {
    const i = idx + 1;
    const div = document.createElement("div");
    div.id = "line-" + i;
    const ln = document.createElement("span");
    ln.className = "ln";
    ln.textContent = String(i).padStart(width) + "  ";
    const text = document.createElement("span");
    line.split("|:").forEach((part, j) => {     // làm mờ dấu phân cách để dễ đọc từng giá trị
      if (j) {
        const sep = document.createElement("span");
        sep.className = "sep";
        sep.textContent = "|:";
        text.append(sep);
      }
      text.append(part);
    });
    if (errLines.has(i)) text.classList.add("err");
    if ("HT".includes(line[0]) && (i === 1 || i === res.lines.length)) text.classList.add("ht");
    div.append(ln, text);
    frag.append(div);
  });
  if (res.lines.length > PREVIEW_LIMIT) {
    const more = document.createElement("div");
    more.className = "ln";
    more.textContent = `... còn ${res.lines.length - PREVIEW_LIMIT} dòng (chỉ hiển thị ${PREVIEW_LIMIT} dòng đầu)`;
    frag.append(more);
  }
  pre.replaceChildren(frag);

  const body = $("report-table").tBodies[0];
  body.replaceChildren();
  for (const r of res.report) {
    const tr = document.createElement("tr");
    for (const text of [r.line, r.field, r.code || "", r.label, r.old, r.new, r.note]) {
      const td = document.createElement("td");
      td.textContent = text ?? "";
      tr.append(td);
    }
    tr.addEventListener("click", () => {
      const el = document.getElementById("line-" + r.line);
      if (el) el.scrollIntoView({ block: "center" });
    });
    body.append(tr);
  }
}

// ------------------------------------------------------------------ bộ file liên kết
function buildSetTypes() {
  const box = $("set-types");
  box.replaceChildren();
  state.meta.setSteps.forEach((step, s) => {
    const name = document.createElement("span");
    name.textContent = `${s + 1}. ${state.meta.setStepNames[s]}`;
    const cell = document.createElement("div");
    cell.className = "checks";
    if (s === 0) {
      // khách hàng: luôn đăng ký, theo số khách hàng ở trên
      cell.classList.add("muted");
      cell.textContent = "INID / COID (theo số khách hàng)";
    } else {
      for (const t of step) {
        const l = document.createElement("label");
        l.className = "check";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.value = t;
        input.disabled = !state.meta.types.includes(t);
        l.append(input, " " + t);
        cell.append(l);
      }
    }
    box.append(name, cell);
  });
}

function openSetDialog() {
  const cfg = loadStore("set");
  const chosen = new Set(cfg.types || state.meta.setSteps.flat());
  for (const [id, def] of [["individuals", 1], ["corporates", 0], ["loans", 1], ["cards", 1]]) {
    $("set-" + id).value = cfg[id] ?? def;
  }
  for (const box of document.querySelectorAll("#set-types input")) box.checked = !box.disabled && chosen.has(box.value);
  // seed trống: xem trước rồi mở lại hộp thoại để tạo file vẫn ra đúng bộ đã xem
  state.setSeed ??= Math.floor(Math.random() * 2 ** 31);
  $("set-dialog").showModal();
}

async function runSet(ev, write = true) {
  ev.preventDefault();
  const params = {};
  for (const id of ["individuals", "corporates", "loans", "cards"]) {
    const n = Number($("set-" + id).value);
    if (!Number.isInteger(n) || n < 0) {
      alert("Các ô số phải là số nguyên ≥ 0.");
      return;
    }
    params[id] = n;
  }
  params.types = [...document.querySelectorAll("#set-types input:checked")].map((x) => x.value);
  saveStore("set", params);
  let res;
  try {
    const seedText = $("seed").value.trim();
    if (seedText && !/^-?\d+$/.test(seedText)) throw new Error("Seed phải là số nguyên (hoặc để trống).");
    setStatus("Đang sinh bộ file...");
    await new Promise((r) => setTimeout(r, 20));
    res = call("generate_set", JSON.stringify({
      ...params,
      header: headerValues(),
      seed: seedText ? Number(seedText) : state.setSeed,
      lifecycle: $("lifecycle").checked,
      suffix: $("suffix").value.trim(),
      crlf: $("crlf").checked,
      write,
    }));
    if (write) download(res.zipPath, res.zipName, "application/zip");
  } catch (e) {
    setStatus("Không tạo được bộ file");
    alert("Không tạo được bộ file: " + e.message);
    return;
  }
  $("set-dialog").close();
  showSet(res);
  switchTab("preview");
  if (!write) {
    const counts = res.files.map((f) => `${f.info} ${f.lines.length - 2}`).join(", ");
    setStatus(`Xem trước bộ ${res.files.length} file: ${counts} dòng (chưa tải file)`);
    return;
  }
  saveConfig();
  state.setSeed = null; // lần tạo sau: bộ dữ liệu mới
  setStatus(`Đã tạo bộ ${res.files.length} file: ${res.zipName}`);
  alert(res.manifest);
}

function showSet(res) {
  // xem trước cả bộ: từng file theo thứ tự gửi, có dòng tiêu đề tên file
  const frag = document.createDocumentFragment();
  const add = (text, cls) => {
    const div = document.createElement("div");
    div.textContent = text;
    if (cls) div.className = cls;
    frag.append(div);
  };
  let shown = 0;
  for (const f of res.files) {
    add(`### Bước ${f.step}: ${f.path}`, "file-title");
    for (const line of f.lines.slice(0, Math.max(PREVIEW_LIMIT - shown, 0))) add(line, "HT".includes(line[0]) ? "ht" : "");
    shown += f.lines.length;
    if (shown > PREVIEW_LIMIT) {
      add(`... (chỉ hiển thị ${PREVIEW_LIMIT} dòng đầu của cả bộ)`, "ln");
      break;
    }
  }
  for (const note of res.notes) add("### " + note, "ln");
  $("preview-text").replaceChildren(frag);
  $("report-table").tBodies[0].replaceChildren();
}

// ------------------------------------------------------------------ kiểm tra file
async function checkFile(file) {
  if (!file) return;
  let res;
  try {
    const path = await writeUpload(file, "/tmp/in/check");
    res = call("validate", path, file.name, state.info);
  } catch (e) {
    alert("Không kiểm tra được: " + e.message);
    return;
  } finally {
    $("check-file").value = "";
  }
  const nErr = res.issues.filter((i) => i.level === "Lỗi").length;
  const nWarn = res.issues.length - nErr;
  $("check-title").textContent = `Kiểm tra: ${file.name}`;
  const summary = $("check-summary");
  summary.textContent = res.issues.length
    ? `✖ ${nErr} lỗi, ${nWarn} cảnh báo nghiệp vụ trong ${res.lines} dòng`
    : `✔ Không phát hiện lỗi (${res.lines} dòng)`;
  summary.className = nErr ? "bad" : "ok";
  const body = $("check-table").tBodies[0];
  body.replaceChildren();
  for (const it of res.issues) {
    const tr = document.createElement("tr");
    if (it.level !== "Lỗi") tr.className = "warn";
    for (const text of [it.line, it.code, it.level, it.field, it.issue, it.value]) {
      const td = document.createElement("td");
      td.textContent = text ?? "";
      tr.append(td);
    }
    body.append(tr);
  }
  $("check-dialog").showModal();
}

// ------------------------------------------------------------------ tab
function switchTab(name) {
  for (const b of document.querySelectorAll(".tabs button")) b.classList.toggle("active", b.dataset.tab === name);
  for (const s of document.querySelectorAll(".tab")) s.classList.toggle("active", s.id === "tab-" + name);
}

// ------------------------------------------------------------------ sự kiện
for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => switchTab(b.dataset.tab));
$("info").addEventListener("change", () => selectLayout($("info").value));
$("csv-file").addEventListener("change", (e) => loadCsv(e.target.files[0]));
$("csv-clear").addEventListener("click", clearCsv);
$("clear-values").addEventListener("click", () => {
  if (Object.keys(state.fixed).length && confirm("Xóa toàn bộ giá trị đã đặt cho các cột?")) {
    state.fixed = {};
    renderCols();
  }
});
$("err-field").addEventListener("change", onErrField);
$("err-add").addEventListener("click", addError);
$("err-clear").addEventListener("click", () => {
  state.errors = [];
  for (const box of document.querySelectorAll("#file-errors input")) box.checked = false;
  renderErrors();
});
$("btn-preview").addEventListener("click", preview);
$("btn-generate").addEventListener("click", generateFile);
$("check-file").addEventListener("change", (e) => checkFile(e.target.files[0]));
$("check-close").addEventListener("click", () => $("check-dialog").close());
$("btn-set").addEventListener("click", openSetDialog);
$("set-form").addEventListener("submit", runSet);
$("set-preview").addEventListener("click", (e) => runSet(e, false));
for (const id of ["set-close", "set-cancel"]) $(id).addEventListener("click", () => $("set-dialog").close());
window.addEventListener("beforeunload", () => state.info && saveConfig());

boot();
