"""
datagen_core.py - Lõi sinh file dữ liệu test NICE (Header + Detail + Trailer).

1. Layout XML: mỗi loại dữ liệu 1 file (LOAN.xml, COID.xml, ...) + HEADER.xml + TRAILER.xml.
2. Quy tắc từng cột đọc từ desc, ví dụ "(Y, String, 1)", "1:신규등록, 2:해지", "12자리", "yyyymmdd",
   "(NCI Code Master C17 참조)". Thuộc tính XML (nếu có) luôn được ưu tiên:
       required="Y|N|M"  maxLen  length  format="yyyymmdd|yyyymm"  codeRef="C17"  values="1,3,7"
       pattern="^\\d{12}$"  notFuture="Y"  errCode="E132"  errCodeMissing="E131"
       --- chỉ dùng khi sinh dữ liệu ---
       default="LN{seq:08d}"  gen="personName"  genRange="1000000-500000000"
       weights="1:80,3:15,2:5"  fillRate="0.3"
3. Quy tắc liên cột: thẻ <rule> trong layout:
       <rule type="dateOrder"   a="occrDt" b="matuDt" strict="Y" gap="180-3650" errCode="E306"/>
       <rule type="requiredWhen" field="chgClsDt" when="evtCd" in="2,3" errCode="E146"/>
       <rule type="requiredWhen" field="idCardType" when="idNumber" notEmpty="Y"/>
       <rule type="atLeastOne"  fields="cccdNo,cmndNo,idNumber" weights="85,10,5" errCode="E102"/>
       <rule type="lte"         small="dlqAmt" big="bal" ratio="0.02-0.3"/>
       <rule type="valueMap"    field="eventReasonCode" when="evtCd" map="1:11;3:12-14;2:21-33,99" level="warn"/>
   level="warn" = quy tắc hợp lý về nghiệp vụ nhưng không có mã lỗi NICE tương ứng (chỉ cảnh báo).
4. Bảng mã NCI Code Master: codes/nci_codes.csv (group,code,desc_vi,desc_en).
"""
from __future__ import annotations

import calendar
import csv
import random
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

HEADER_CD = "HEADER"
TRAILER_CD = "TRAILER"
ROW_LEVEL = "*"          # ErrorSpec.field: lỗi cấu trúc dòng
RULE_PREFIX = "rule:"    # ErrorSpec.field: vi phạm quy tắc liên cột, vd "rule:2"
NUMERIC_TYPES = {"Integer", "Long", "BigDecimal", "Double", "Float", "Number"}
INTEGER_TYPES = {"Integer", "Long"}
EVT_FIELDS = ("evtCd", "registrationEvent")


class LayoutError(Exception):
    pass


# =========================================================================== code master

def load_codes(path) -> dict[str, dict[str, str]]:
    codes: dict[str, dict[str, str]] = {}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            g, c = (r.get("group") or "").strip(), (r.get("code") or "").strip()
            if g and c:
                codes.setdefault(g, {})[c] = (r.get("desc_vi") or r.get("desc_en") or "").strip()
    return codes


def find_code_file(layout_dir) -> Path | None:
    d = Path(layout_dir)
    for p in (d / "nci_codes.csv", d.parent / "codes" / "nci_codes.csv"):
        if p.exists():
            return p
    return None


# =========================================================================== layout

@dataclass
class Field:
    name: str
    type: str
    index: int
    desc: str = ""
    required: str = "N"                 # Y | N | M (bắt buộc có điều kiện)
    max_len: int | None = None
    fixed_len: int | None = None
    allowed: list[str] | None = None
    code_ref: str | None = None
    code_desc: dict = dc_field(default_factory=dict)
    fmt: str | None = None              # yyyymmdd | yyyymm
    pattern: str | None = None
    not_future: bool = False
    err_code: str = ""
    err_code_missing: str = ""
    default: str = ""                   # mẫu sinh dữ liệu
    gen: str = ""
    gen_range: tuple | None = None
    weights: dict | None = None
    fill_rate: float | None = None

    @property
    def is_numeric(self) -> bool:
        return self.type in NUMERIC_TYPES

    @property
    def is_date(self) -> bool:
        return self.fmt is not None

    @property
    def is_required(self) -> bool:
        return self.required == "Y"

    @property
    def is_cond(self) -> bool:
        return self.required.startswith("M")

    def rule_text(self) -> str:
        parts = []
        if self.is_required:
            parts.append("bắt buộc")
        elif self.is_cond:
            parts.append("bắt buộc có điều kiện")
        if self.allowed and not self.code_ref:
            vals = ",".join(self.allowed)
            parts.append(f"chỉ nhận: {vals if len(vals) <= 40 else vals[:40] + '…'}")
        if self.fmt:
            parts.append(self.fmt + (" ≤ hôm nay" if self.not_future else ""))
        elif self.fixed_len:
            parts.append(f"đúng {self.fixed_len} ký tự")
        elif self.max_len:
            parts.append(f"tối đa {self.max_len}")
        if self.pattern:
            parts.append(f"định dạng {self.pattern}")
        if self.is_numeric:
            parts.append("số")
        return "; ".join(parts)


@dataclass
class Rule:
    type: str
    a: dict
    err_code: str = ""
    level: str = "error"   # error | warn

    def g(self, k, default=""):
        return self.a.get(k, default)

    def label(self) -> str:
        if self.g("desc"):
            return self.g("desc")
        t = self.type
        if t == "dateOrder":
            return f"{self.g('a')} {'<' if self.g('strict') == 'Y' else '≤'} {self.g('b')}"
        if t == "requiredWhen":
            cond = f"có {self.g('when')}" if self.g("notEmpty") == "Y" else f"{self.g('when')} ∈ {{{self.g('in')}}}"
            return f"{self.g('field')} bắt buộc khi {cond}"
        if t == "atLeastOne":
            return "Phải có ít nhất 1 trong: " + self.g("fields")
        if t == "lte":
            return f"{self.g('small')} ≤ {self.g('big')}"
        if t == "valueMap":
            return f"{self.g('field')} phải phù hợp với {self.g('when')}"
        if t == "repayCount":
            return f"{self.g('field')} = thời hạn {self.g('a')}→{self.g('b')} / chu kỳ {self.g('cycle')}"
        return t

    def full_label(self) -> str:
        tag = "[cảnh báo]" if self.level == "warn" else ""
        return f"{self.label()} {tag}".strip()


@dataclass
class Layout:
    info_cd: str
    delimiter: str
    fields: list[Field]
    rules: list[Rule] = dc_field(default_factory=list)
    path: str = ""

    def get(self, name: str) -> Field | None:
        return next((f for f in self.fields if f.name == name), None)

    @property
    def names(self) -> list[str]:
        return [f.name for f in self.fields]


_SPEC_RE = re.compile(r"\(\s*(Y|N|O|M\d*|C\d*)\s*,\s*([A-Za-z]+)\s*,\s*(\d+)\s*\)")
_FIXLEN_RE = re.compile(r"(\d+)\s*(?:자리|digits?\b|chữ số)", re.I)
_CODE_RE = re.compile(r"(?<![\w(/.:])([0-9A-Za-z]{1,4})\s*:\s*(?=[^\s/:])")
_FIXVAL_RE = re.compile(r"(?<!\w)(\w{1,4})\s*\([^)]*\)\s*고정")
_CODEREF_RE = re.compile(r"Code Master\s+(C\d+)", re.I)


def expand_values(spec: str) -> list[str]:
    """'1,3,7' / '21-33,99' / '410-417' -> danh sách mã (giữ độ dài: 01-05 -> 01..05)."""
    out = []
    for part in [p.strip() for p in str(spec).split(",") if p.strip()]:
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            a, b = m.groups()
            out.extend(str(i).zfill(len(a)) for i in range(int(a), int(b) + 1))
        else:
            out.append(part)
    return list(dict.fromkeys(out))


def _parse_weights(spec: str) -> dict:
    w = {}
    for part in str(spec).split(","):
        if ":" in part:
            k, v = part.rsplit(":", 1)
            w[k.strip()] = float(v)
    return w


def _apply_rules(f: Field, el: ET.Element, codes: dict, parse_desc: bool = True) -> None:
    desc = f.desc if parse_desc else ""
    m = _SPEC_RE.search(desc)
    if m:
        flag = m.group(1)
        f.required = "Y" if flag == "Y" else "M" if flag.startswith("M") else "N"
        f.max_len = int(m.group(3))
    detail = desc.split(" - ", 1)[1] if " - " in desc else ""
    low = desc.lower()

    if "yyyymmdd" in low:
        f.fmt = "yyyymmdd"
    elif "yyyymm" in low:
        f.fmt = "yyyymm"
    elif f.type == "String" and re.search(r"(Dt|Date)$", f.name) and f.max_len in (None, 8):
        f.fmt = "yyyymmdd"

    m = _FIXLEN_RE.search(detail)
    if m and not f.fmt:
        f.fixed_len = int(m.group(1))

    m = _CODEREF_RE.search(desc)
    if m:
        f.code_ref = m.group(1)
    is_example = "예:" in detail or "ví dụ" in detail.lower()
    codes_in_desc = _CODE_RE.findall(detail)
    if len(codes_in_desc) >= 2 and not is_example:
        f.allowed = list(dict.fromkeys(codes_in_desc))
    else:
        m = _FIXVAL_RE.search(detail)
        if m:
            f.allowed = [m.group(1)]

    a = el.attrib
    if "required" in a:
        v = a["required"].strip().upper()
        f.required = "Y" if v in ("Y", "YES", "TRUE", "1") else "M" if v.startswith("M") else "N"
    if a.get("maxLen"):
        f.max_len = int(a["maxLen"])
    if a.get("length"):
        f.fixed_len = int(a["length"])
    if a.get("format"):
        f.fmt = a["format"].strip().lower() or None
    if a.get("codeRef"):
        f.code_ref = a["codeRef"].strip()
    if f.code_ref and f.code_ref in codes:
        f.code_desc = codes[f.code_ref]
        if not f.allowed:
            f.allowed = list(codes[f.code_ref])
    if a.get("values"):
        f.allowed = expand_values(a["values"])
    if f.fmt:
        f.fixed_len = len(f.fmt)
    f.pattern = a.get("pattern") or None
    f.not_future = a.get("notFuture", "").upper() == "Y"
    f.err_code = a.get("errCode", "")
    f.err_code_missing = a.get("errCodeMissing", "")
    f.default = a.get("default", "")
    f.gen = a.get("gen", "")
    if a.get("genRange"):
        lo, hi = a["genRange"].split("-")
        f.gen_range = (Decimal(lo), Decimal(hi))
    if a.get("weights"):
        f.weights = _parse_weights(a["weights"])
    if a.get("fillRate"):
        f.fill_rate = float(a["fillRate"])


def rules_path(layout_path) -> Path:
    """layouts/LOAN.xml -> rules/LOAN.xml (quy tắc sinh/kiểm tra, tách khỏi layout chuẩn)."""
    p = Path(layout_path)
    return p.parent.parent / "rules" / p.name


def load_layout(path, codes: dict | None = None) -> Layout:
    """Layout chỉ có <field name type index desc>. Quy tắc từng cột (thuộc tính) và <rule> liên cột
    đọc từ rules/<tên>.xml; cột có trong file quy tắc thì không suy quy tắc từ desc nữa."""
    path = Path(path)
    codes = codes or {}
    root = ET.parse(path).getroot()
    rp = rules_path(path)
    rroot = ET.parse(rp).getroot() if rp.exists() else ET.Element("rules")
    extra = {el.get("name"): el for el in rroot.findall("field")}
    fields = []
    for el in root.findall("field"):
        f = Field(el.get("name"), el.get("type", "String"), int(el.get("index")), el.get("desc", ""))
        r = extra.get(f.name)
        _apply_rules(f, ET.Element("field", {**el.attrib, **r.attrib}) if r is not None else el, codes,
                     parse_desc=r is None)
        fields.append(f)
    fields.sort(key=lambda f: f.index)
    expected = int(root.get("fieldCount", len(fields)))
    if len(fields) != expected:
        raise LayoutError(f"có {len(fields)} field nhưng fieldCount={expected}")
    for i, f in enumerate(fields):
        if f.index != i:
            raise LayoutError(f"index bị thiếu/trùng tại vị trí {i} (field {f.name})")
    names = {f.name for f in fields}
    rules = []
    unknown = [n for n in extra if n not in names]
    if unknown:
        raise LayoutError(f"{rp.name} trong rules/ có cột không có trong layout: {', '.join(unknown)}")
    for el in root.findall("rule") + rroot.findall("rule"):
        r = Rule(el.get("type", ""), dict(el.attrib), el.get("errCode", ""), el.get("level", "error"))
        refs = [r.g(k) for k in ("a", "b", "field", "when", "big", "cycle") if r.g(k)]
        refs += [x for k in ("fields", "small") for x in re.split(r"[,+]", r.g(k)) if x]
        bad = [x for x in refs if x not in names]
        if bad:
            raise LayoutError(f"rule {r.type} tham chiếu cột không có: {', '.join(bad)}")
        rules.append(r)
    return Layout(root.get("infoCd") or path.stem, root.get("delimiter", "|:"), fields, rules, str(path))


def load_layouts(folder, codes: dict | None = None) -> tuple[dict[str, Layout], list[str]]:
    repo, problems = {}, []
    if codes is None:
        cf = find_code_file(folder)
        codes = load_codes(cf) if cf else {}
    for p in sorted(Path(folder).glob("*.xml")):
        try:
            lay = load_layout(p, codes)
            repo[lay.info_cd] = lay
        except Exception as e:
            problems.append(f"{p.name}: {e}")
    return repo, problems


# =========================================================================== dữ liệu tiếng Việt

# (mã tỉnh C32, tên, đầu mã bưu chính, mã vùng điện thoại, mã biển số xe)
CITIES = [
    ("01", "Hà Nội", "10", "024", "30"), ("79", "Hồ Chí Minh", "70", "028", "51"),
    ("48", "Đà Nẵng", "50", "0236", "43"), ("31", "Hải Phòng", "04", "0225", "15"),
    ("92", "Cần Thơ", "94", "0292", "65"), ("22", "Quảng Ninh", "01", "0203", "14"),
    ("40", "Nghệ An", "43", "0238", "37"), ("56", "Khánh Hòa", "57", "0258", "79"),
]
CITY_WEIGHTS = [34, 30, 8, 7, 6, 5, 5, 5]
# Mã tỉnh -> tên chi nhánh 01, 02, 03 (branchCd = CN + mã tỉnh + số thứ tự chi nhánh)
BRANCHES = {
    "01": ["Hoàn Kiếm", "Ba Đình", "Cầu Giấy"], "79": ["Sài Gòn", "Bến Thành", "Tân Bình"],
    "48": ["Hải Châu", "Sơn Trà", "Thanh Khê"], "31": ["Hồng Bàng", "Ngô Quyền", "Lê Chân"],
    "92": ["Ninh Kiều", "Cái Răng", "Bình Thủy"], "22": ["Hạ Long", "Cẩm Phả", "Uông Bí"],
    "40": ["Vinh", "Cửa Lò", "Hoàng Mai"], "56": ["Nha Trang", "Cam Ranh", "Ninh Hòa"],
}
WARDS = ["Phường 1", "Phường Bến Thành", "Phường Hoàn Kiếm", "Phường Cầu Giấy", "Phường Hải Châu",
         "Phường Ninh Kiều", "Phường Ngô Quyền", "Phường Hồng Gai", "Phường Vinh", "Phường Nha Trang"]
STREETS = ["Láng Hạ", "Trần Hưng Đạo", "Nguyễn Trãi", "Lê Lợi", "Hai Bà Trưng", "Lý Thường Kiệt",
           "Điện Biên Phủ", "Hùng Vương", "Nguyễn Huệ", "Phan Chu Trinh", "Lê Duẩn", "Võ Văn Kiệt"]
SURNAMES = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan", "Vũ", "Võ", "Đặng", "Bùi", "Đỗ"]
SURNAME_W = [38, 11, 9.5, 7, 5, 5, 4.5, 4, 4, 2, 2, 1.5]
MIDDLE = {"M": ["Văn", "Đức", "Minh", "Quốc", "Hữu", "Thành", "Gia"],
          "F": ["Thị", "Ngọc", "Thu", "Thanh", "Mai", "Hồng", "Bảo"]}
GIVEN = {"M": ["An", "Bình", "Cường", "Dũng", "Hải", "Hùng", "Khoa", "Long", "Nam", "Phong", "Quân", "Sơn",
               "Thắng", "Tuấn", "Việt", "Huy"],
         "F": ["Anh", "Chi", "Hà", "Hạnh", "Hương", "Lan", "Linh", "Mai", "Ngân", "Nhung", "Phương", "Thảo",
               "Trang", "Uyên", "Vy", "Yến"]}
CORP_KIND = {"12": "Công ty Cổ phần", "13": "Công ty TNHH", "14": "Công ty TNHH MTV", "15": "Doanh nghiệp tư nhân",
             "17": "Công ty TNHH", "18": "Hợp tác xã"}
CORP_KIND_W = {"12": 30, "13": 30, "14": 25, "15": 8, "17": 4, "18": 3}
CORP_FIELD = ["Thương mại", "Dịch vụ", "Xây dựng", "Công nghệ", "Sản xuất", "Vận tải", "Đầu tư", "Thực phẩm"]
CORP_BRAND = ["An Phát", "Minh Long", "Hòa Bình", "Thành Công", "Phú Gia", "Đại Việt", "Sao Mai", "Hưng Thịnh",
              "Tân Phú", "Hoàng Long", "Việt Tiến", "Nam Á"]
DEPARTMENTS = ["Phòng Kế toán", "Phòng Kinh doanh", "Phòng Nhân sự", "Phòng Kỹ thuật", "Phòng Hành chính",
               "Phòng Công nghệ thông tin"]
POSITIONS = ["Nhân viên", "Chuyên viên", "Trưởng nhóm", "Phó phòng", "Trưởng phòng", "Giám đốc"]
BRANDS = ["Toyota", "Honda", "Hyundai", "Kia", "VinFast", "Mazda", "Ford", "Mitsubishi"]
MOBILE_PREFIX = ["090", "091", "093", "094", "096", "097", "098", "086", "032", "033", "035", "038", "070",
                 "077", "081", "085", "088"]


def _ascii(s: str) -> str:
    s = s.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


@dataclass
class _Persona:
    city: tuple
    gender: str
    birth: date
    name: str
    corp_type: str
    company: str
    street: str
    house: int
    ward: str


def _make_persona(rnd: random.Random) -> _Persona:
    city = rnd.choices(CITIES, CITY_WEIGHTS)[0]
    g = rnd.choice("MF")
    today = date.today()
    birth = today - timedelta(days=rnd.randint(22 * 365, 65 * 365))
    name = f"{rnd.choices(SURNAMES, SURNAME_W)[0]} {rnd.choice(MIDDLE[g])} {rnd.choice(GIVEN[g])}"
    ct = rnd.choices(list(CORP_KIND_W), list(CORP_KIND_W.values()))[0]
    company = f"{CORP_KIND[ct]} {rnd.choice(CORP_FIELD)} {rnd.choice(CORP_BRAND)}"
    return _Persona(city, g, birth, name, ct, company, rnd.choice(STREETS), rnd.randint(1, 350), rnd.choice(WARDS))


@dataclass
class _Ctx:
    rnd: random.Random
    seq: int
    header: dict
    persona: _Persona
    row: dict = dc_field(default_factory=dict)
    locked: set = dc_field(default_factory=set)
    cust: int = 1        # số thứ tự khách hàng  -> {cust}
    row_no: int = 1      # số thứ tự dòng        -> {row}


def _digits(rnd, n: int) -> str:
    return "".join(rnd.choice("0123456789") for _ in range(n))


def _gen_keyword(f: Field) -> str:
    if f.gen:
        return f.gen
    nm = f.name.lower()
    for keys, kw in [
        (("cccd",), "cccd"), (("cmnd",), "cmnd"), (("birth",), "birthDt"), (("gender",), "gender"),
        (("branchnm", "branchname"), "branchName"), (("branchcd", "branchcode"), "branchCode"),
        (("officenm", "companynm"), "companyName"), (("membnm", "custnm", "customername"), "personName"),
        (("department",), "department"), (("position",), "position"),
        (("email",), "email"), (("web", "url"), "web"), (("mobile",), "mobile"),
        (("tel", "fax", "phone"), "tel"), (("zip",), "zip"), (("provincial", "province"), "province"),
        (("adr", "addr"), "address"), (("taxcode",), "taxCorp"), (("brand",), "brand"),
        (("regno",), "plate"), (("custcd", "customercode"), "custCode"),
    ]:
        if any(k in nm for k in keys):
            return kw
    return ""


def _gen_by_keyword(kw: str, f: Field, ctx: _Ctx) -> str | None:
    rnd, p = ctx.rnd, ctx.persona
    city = p.city
    if kw == "personName":
        return p.name
    if kw == "companyName":
        return p.company
    if kw == "corpType":
        return p.corp_type
    if kw == "branchName":            # tên đi theo branchCd đã sinh ở cột trước
        code = ctx.row.get("branchCd") or ctx.row.get("branchCode") or ""
        names = BRANCHES.get(city[0], [])
        no = code[4:] if code.startswith(f"CN{city[0]}") else ""
        if no.isdigit() and 1 <= int(no) <= len(names):
            return f"Chi nhánh {names[int(no) - 1]} - {city[1]}"
        return f"Chi nhánh {city[1]}"
    if kw == "branchCode":
        return f"CN{city[0]}{rnd.randint(1, len(BRANCHES.get(city[0], [])) or 3):02d}"
    if kw == "address":
        if "office" in f.name.lower():          # nơi làm việc: khác địa chỉ nhà, cùng tỉnh
            return f"Số {rnd.randint(1, 500)} {rnd.choice(STREETS)}, {rnd.choice(WARDS)}, {city[1]}"
        return f"Số {p.house} {p.street}, {p.ward}, {city[1]}"
    if kw == "zip":
        return city[2] + _digits(rnd, 3)
    if kw == "province":
        return city[0]
    if kw == "mobile":
        return rnd.choice(MOBILE_PREFIX) + _digits(rnd, 7)
    if kw == "tel":
        return city[3] + _digits(rnd, 11 - len(city[3]))
    if kw == "email":
        base = _ascii(p.name).lower().split()
        return f"{base[-1]}.{''.join(x[0] for x in base[:-1])}{rnd.randint(1, 999)}@gmail.com"
    if kw == "web":
        return "www." + _ascii(p.company.split()[-1] + p.company.split()[-2]).lower() + ".vn"
    if kw == "cccd":
        century = (0 if p.birth.year < 2000 else 2) + (0 if p.gender == "M" else 1)
        return f"0{city[0]}{century}{p.birth.year % 100:02d}" + _digits(rnd, 6)
    if kw == "cmnd":
        return "0" + _digits(rnd, 8)
    if kw == "idNumber":
        return rnd.choice(["C", "N", "B"]) + _digits(rnd, 7)
    if kw == "taxCorp":
        return "0" + _digits(rnd, 9)
    if kw == "taxPerson":
        return "8" + _digits(rnd, 9)
    if kw == "birthDt":
        return p.birth.strftime("%Y%m%d")
    if kw == "gender":
        return p.gender
    if kw == "department":
        return rnd.choice(DEPARTMENTS)
    if kw == "position":
        return rnd.choice(POSITIONS)
    if kw == "brand":
        return rnd.choice(BRANDS)
    if kw == "plate":
        return f"{city[4]}{rnd.choice('ABCDEFGHK')}-{_digits(rnd, 3)}.{_digits(rnd, 2)}"
    if kw == "custCode":
        return f"CUST{ctx.seq:06d}"
    return None


# ---- ngày
def _parse_date(v: str, fmt: str | None) -> date | None:
    try:
        if fmt == "yyyymm" and len(v) == 6 and v.isdigit():
            return datetime.strptime(v, "%Y%m").date()
        if len(v) == 8 and v.isdigit():
            return datetime.strptime(v, "%Y%m%d").date()
    except ValueError:
        pass
    return None


def _fmt_date(d: date, fmt: str | None) -> str:
    return d.strftime("%Y%m") if fmt == "yyyymm" else d.strftime("%Y%m%d")


def _month_end(offset: int) -> date:
    t = date.today()
    y, m = divmod(t.month - 1 + offset, 12)
    y, m = t.year + y, m + 1
    return date(y, m, calendar.monthrange(y, m)[1])


def _rand_past(rnd, days_back=5 * 365, min_back=0) -> date:
    return date.today() - timedelta(days=rnd.randint(min_back, days_back))


# ---- mẫu
_TPL_RE = re.compile(r"\{([^{}]*)\}")
_SEQ_RE = re.compile(r"(seq|cust|row)\s*([+-]\s*\d+)?$")


def render_template(tpl: str, ctx: _Ctx) -> str:
    """
    {seq} {seq:4} {seq+40:4}  số thứ tự hợp đồng   {cust} số thứ tự khách hàng   {row} số thứ tự dòng
    {rand:6}  6 chữ số ngẫu nhiên
    {today}  {monthend:-1} (cuối tháng trước)      {date:20250101-20261231}  ngày ngẫu nhiên
    {dateadd:baseDt:-20:-5}  baseDt cộng ngẫu nhiên -20..-5 ngày
    {1|2|3} chọn ngẫu nhiên   {H.institutionCode} từ header   {ctrtNo} cột đã sinh   {empty} rỗng
    """
    def rep(m):
        expr = m.group(1).strip()
        if "|" in expr:
            return ctx.rnd.choice(expr.split("|"))
        key, _, arg = expr.partition(":")
        key = key.strip()
        sm = _SEQ_RE.match(key)
        if sm:
            base = {"seq": ctx.seq, "cust": ctx.cust, "row": ctx.row_no}[sm.group(1)]
            n = base + int((sm.group(2) or "0").replace(" ", ""))
            arg = arg.lstrip("0d") if arg.endswith("d") else arg
            width = int(re.sub(r"\D", "", arg) or 0)
            return str(n).zfill(width)
        if key == "rand":
            return _digits(ctx.rnd, int(arg or 6))
        if key == "today":
            return date.today().strftime("%Y%m%d")
        if key == "empty":
            return ""
        if key == "monthend":
            return _month_end(int(arg or 0)).strftime("%Y%m%d")
        if key == "date":
            a, b = arg.split("-") if arg else ("20240101", date.today().strftime("%Y%m%d"))
            da, db = datetime.strptime(a, "%Y%m%d").date(), datetime.strptime(b, "%Y%m%d").date()
            return (da + timedelta(days=ctx.rnd.randint(0, max((db - da).days, 0)))).strftime("%Y%m%d")
        if key == "dateadd":
            ref, lo, hi = arg.split(":")
            base = _parse_date(ctx.row.get(ref, ""), None) or date.today()
            return (base + timedelta(days=ctx.rnd.randint(int(lo), int(hi)))).strftime("%Y%m%d")
        if key.startswith("H."):
            return ctx.header.get(key[2:], "")
        if key in ctx.row:
            return ctx.row[key]
        return m.group(0)

    return _TPL_RE.sub(rep, tpl)


def _round_amount(v: Decimal) -> Decimal:
    if v >= 10_000_000:
        return (v / 100_000).quantize(Decimal(1)) * 100_000
    if v >= 100_000:
        return (v / 1_000).quantize(Decimal(1)) * 1_000
    return v.quantize(Decimal(1))


def _num_str(v) -> str:
    return format(Decimal(v).quantize(Decimal(1)), "f")


def default_value(f: Field, ctx: _Ctx, force: bool = False) -> str:
    """Sinh 1 giá trị HỢP LỆ cho cột. Cột bắt buộc có điều kiện (M) để trống, trừ khi force/fillRate."""
    rnd, nm = ctx.rnd, f.name.lower()
    if nm == "recordtype":
        return "D"
    if not force:
        if f.is_cond and f.fill_rate is None:
            return ""
        if f.fill_rate is not None and rnd.random() > f.fill_rate:
            return ""
    if nm in ("instcd", "institutioncode") and ctx.header.get("institutionCode"):
        return ctx.header["institutionCode"]
    if f.default:
        return render_template(f.default, ctx)
    kw = _gen_keyword(f)
    if kw in ("gender", "corpType", "province") and f.allowed:
        v = _gen_by_keyword(kw, f, ctx)
        if v in f.allowed:
            return v
    if f.allowed:
        if f.weights:
            keys = [k for k in f.weights if k in f.allowed] or f.allowed
            return rnd.choices(keys, [f.weights.get(k, 1) for k in keys])[0]
        return rnd.choice(f.allowed)
    if f.is_date:
        if kw == "birthDt":
            return _fmt_date(ctx.persona.birth, f.fmt)
        return _fmt_date(_rand_past(rnd), f.fmt)
    if f.is_numeric:
        if f.gen_range:
            lo, hi = f.gen_range
            v = Decimal(rnd.uniform(float(lo), float(hi))) if hi - lo > 1000 else Decimal(rnd.randint(int(lo), int(hi)))
            return _num_str(_round_amount(v))
        if f.type in INTEGER_TYPES or (f.max_len and f.max_len <= 5):
            return str(rnd.randint(1, 60))
        return _num_str(_round_amount(Decimal(rnd.uniform(10_000_000, 2_000_000_000))))
    v = _gen_by_keyword(kw, f, ctx) if kw else None
    if v is None:
        if f.fixed_len:
            v = _digits(rnd, f.fixed_len)
        elif nm.endswith(("cd", "code")):
            v = _digits(rnd, min(f.max_len or 2, 3))
        else:
            v = "T" + _digits(rnd, 5)
    lim = f.fixed_len or f.max_len
    return v[:lim] if lim else v


# =========================================================================== quy tắc liên cột

def _num(v: str) -> Decimal | None:
    try:
        return Decimal(v) if v not in ("", None) else None
    except InvalidOperation:
        return None


def _gap(rule: Rule, default=(0, 1500)) -> tuple[int, int]:
    if rule.g("gap"):
        lo, hi = rule.g("gap").split("-")
        return int(lo), int(hi)
    return default


def _cmp_dates(da: date, fa: Field, db: date, fb: Field) -> int:
    if "yyyymm" in (fa.fmt, fb.fmt):          # có cột yyyymm -> so theo tháng
        ka, kb = da.year * 12 + da.month, db.year * 12 + db.month
    else:
        ka, kb = da.toordinal(), db.toordinal()
    return (ka > kb) - (ka < kb)


def _cond(rule: Rule, row: dict) -> bool:
    w = row.get(rule.g("when"), "")
    if rule.g("notEmpty") == "Y":
        return w != ""
    return w in expand_values(rule.g("in"))


def _map_lookup(rule: Rule, key: str) -> list[str] | None:
    default = None
    for part in rule.g("map").split(";"):
        if ":" not in part:
            continue
        ks, vs = part.split(":", 1)
        if ks.strip() == "*":
            default = expand_values(vs)
        elif key in expand_values(ks):
            return expand_values(vs)
    return default


# Chu kỳ trả nợ C19 -> số ngày mỗi kỳ (21 = trả 1 lần cuối kỳ; 22/99 = không tính)
CYCLE_DAYS = {"11": 1, "12": 7, "13": 14, "14": 30.4, "15": 60.9, "16": 91.3, "17": 121.7, "18": 152.2,
              "19": 182.6, "20": 365.25}
IRREGULAR_CYCLES = ("22", "99")


def _expected_count(rule: Rule, row: dict, layout: Layout) -> int | None:
    cyc = row.get(rule.g("cycle"), "")
    if cyc == "21":
        return 1
    fa, fb = layout.get(rule.g("a")), layout.get(rule.g("b"))
    da, db = _parse_date(row.get(fa.name, ""), fa.fmt), _parse_date(row.get(fb.name, ""), fb.fmt)
    if cyc not in CYCLE_DAYS or not da or not db or db <= da:
        return None
    return max(1, round((db - da).days / CYCLE_DAYS[cyc]))


def check_rule(rule: Rule, row: dict, layout: Layout) -> str | None:
    t = rule.type
    if t == "repayCount":
        exp, v = _expected_count(rule, row, layout), _num(row.get(rule.g("field"), ""))
        if exp is not None and v is not None and abs(v - exp) > max(1, exp // 10):
            return f"{rule.g('field')}={v} không khớp thời hạn {rule.g('a')}→{rule.g('b')} / chu kỳ {row.get(rule.g('cycle'))} (≈{exp})"
        return None
    if t == "dateOrder":
        fa, fb = layout.get(rule.g("a")), layout.get(rule.g("b"))
        da, db = _parse_date(row.get(fa.name, ""), fa.fmt), _parse_date(row.get(fb.name, ""), fb.fmt)
        if da and db:
            c = _cmp_dates(da, fa, db, fb)
            if c > 0 or (c == 0 and rule.g("strict") == "Y"):
                return f"{fb.name}={row[fb.name]} phải {'sau' if rule.g('strict') == 'Y' else 'không trước'} {fa.name}={row[fa.name]}"
    elif t == "requiredWhen":
        if _cond(rule, row) and row.get(rule.g("field"), "") == "":
            return f"Thiếu {rule.g('field')} ({rule.label()})"
    elif t == "atLeastOne":
        names = rule.g("fields").split(",")
        if all(row.get(n, "") == "" for n in names):
            return f"Phải nhập ít nhất 1 trong: {', '.join(names)}"
    elif t == "lte":
        smalls = rule.g("small").split("+")
        big = _num(row.get(rule.g("big"), ""))
        vals = [_num(row.get(s, "")) for s in smalls]
        if big is not None and any(v is not None for v in vals):
            total = sum(v for v in vals if v is not None)
            if total > big:
                return f"{rule.g('small')}={_num_str(total)} lớn hơn {rule.g('big')}={_num_str(big)}"
    elif t == "valueMap":
        vals = _map_lookup(rule, row.get(rule.g("when"), ""))
        v = row.get(rule.g("field"), "")
        if vals and v != "" and v not in vals:
            return f"{rule.g('field')}={v} không phù hợp với {rule.g('when')}={row.get(rule.g('when'))} (hợp lệ: {','.join(vals[:12])})"
    return None


def _set(ctx: _Ctx, name: str, value: str) -> bool:
    if name in ctx.locked:
        return False
    ctx.row[name] = value
    return True


def repair_rule(rule: Rule, ctx: _Ctx, layout: Layout) -> None:
    """Chỉnh dòng cho thỏa quy tắc (không đụng vào cột lấy từ CSV / giá trị cố định)."""
    rnd, row, t = ctx.rnd, ctx.row, rule.type
    today = date.today()
    if t == "repayCount":
        if row.get(rule.g("cycle"), "") in IRREGULAR_CYCLES:     # trả không đều -> không có số kỳ cố định
            _set(ctx, rule.g("field"), "")
            return
        exp = _expected_count(rule, row, layout)
        if exp is not None and row.get(rule.g("field"), "") != "":
            _set(ctx, rule.g("field"), str(exp))
        return
    if t == "dateOrder":
        fa, fb = layout.get(rule.g("a")), layout.get(rule.g("b"))
        if row.get(fb.name, "") == "" or row.get(fa.name, "") == "":
            return
        lo, hi = _gap(rule)
        if rule.g("strict") == "Y":
            lo = max(lo, 32 if fb.fmt == "yyyymm" else 1)
        da = _parse_date(row[fa.name], fa.fmt) or _rand_past(rnd)
        db = _parse_date(row[fb.name], fb.fmt) or today
        if rule.g("adjust") == "a" or fb.name in ctx.locked:     # chỉnh cột a theo b
            if fa.name in ctx.locked:
                return
            na = db - timedelta(days=rnd.randint(lo, hi))
            _set(ctx, fa.name, _fmt_date(na, fa.fmt))
            return
        cap = today if fb.not_future else None
        if cap and (cap - da).days < lo:
            if fa.name in ctx.locked:
                return
            da = cap - timedelta(days=rnd.randint(lo, lo + 720))
            _set(ctx, fa.name, _fmt_date(da, fa.fmt))
        hi2 = min(hi, (cap - da).days) if cap else hi
        nb = da + timedelta(days=rnd.randint(lo, max(lo, hi2)))
        _set(ctx, fb.name, _fmt_date(nb, fb.fmt))
    elif t == "requiredWhen":
        f = layout.get(rule.g("field"))
        if _cond(rule, row):
            if row.get(f.name, "") == "":
                _set(ctx, f.name, default_value(f, ctx, force=True))
        elif f.is_cond and row.get(f.name, "") != "" and not any(
                r is not rule and r.type == "requiredWhen" and r.g("field") == f.name and _cond(r, row)
                for r in layout.rules):
            _set(ctx, f.name, "")
    elif t == "atLeastOne":
        names = rule.g("fields").split(",")
        if all(row.get(n, "") == "" for n in names):
            w = [float(x) for x in rule.g("weights").split(",")] if rule.g("weights") else [1] * len(names)
            n = rnd.choices(names, w)[0]
            _set(ctx, n, default_value(layout.get(n), ctx, force=True))
    elif t == "lte":
        smalls = rule.g("small").split("+")
        big = _num(row.get(rule.g("big"), ""))
        if big is None:
            return
        if rule.g("ratio") and len(smalls) == 1 and row.get(smalls[0], "") != "":
            lo, hi = (float(x) for x in rule.g("ratio").split("-"))
            _set(ctx, smalls[0], _num_str(_round_amount(big * Decimal(rnd.uniform(lo, hi)))))
            return
        vals = {s: _num(row.get(s, "")) for s in smalls}
        total = sum(v for v in vals.values() if v is not None)
        if total > big:
            share = big * Decimal(rnd.uniform(0.6, 1.0))
            for s, v in vals.items():
                if v is not None:
                    _set(ctx, s, _num_str(_round_amount(share * v / total)))
    elif t == "valueMap":
        f = layout.get(rule.g("field"))
        vals = _map_lookup(rule, row.get(rule.g("when"), ""))
        v = row.get(f.name, "")
        if vals and v != "" and v not in vals:
            ok = [x for x in vals if not f.allowed or x in f.allowed] or vals
            _set(ctx, f.name, rnd.choice(ok))


def violate_rule(rule: Rule, ctx: _Ctx, layout: Layout) -> tuple[str, str, str] | None:
    """Cố ý làm sai quy tắc. Trả về (cột, giá trị cũ, giá trị mới)."""
    rnd, row, t = ctx.rnd, ctx.row, rule.type
    if t == "repayCount":
        exp = _expected_count(rule, row, layout)
        if exp is None:
            return None
        old = row.get(rule.g("field"), "")
        row[rule.g("field")] = str(exp * 2 + 5)
        return rule.g("field"), old, row[rule.g("field")]
    if t == "dateOrder":
        fa, fb = layout.get(rule.g("a")), layout.get(rule.g("b"))
        if row.get(fa.name, "") == "":
            row[fa.name] = default_value(fa, ctx, force=True)
        da = _parse_date(row[fa.name], fa.fmt) or _rand_past(rnd)
        old = row.get(fb.name, "")
        row[fb.name] = _fmt_date(da - timedelta(days=rnd.randint(40, 400)), fb.fmt)
        return fb.name, old, row[fb.name]
    if t == "requiredWhen":
        if not _cond(rule, row):
            w = layout.get(rule.g("when"))
            if rule.g("notEmpty") == "Y":
                row[w.name] = default_value(w, ctx, force=True)
            else:
                row[w.name] = expand_values(rule.g("in"))[0]
        old = row.get(rule.g("field"), "")
        row[rule.g("field")] = ""
        return rule.g("field"), old, ""
    if t == "atLeastOne":
        names = rule.g("fields").split(",")
        old = "/".join(row.get(n, "") for n in names)
        for n in names:
            row[n] = ""
        return ",".join(names), old, ""
    if t == "lte":
        s = rule.g("small").split("+")[0]
        big = _num(row.get(rule.g("big"), ""))
        if big is None:
            big = Decimal(rnd.randint(10, 500)) * 1_000_000
            row[rule.g("big")] = _num_str(big)
        old = row.get(s, "")
        row[s] = _num_str(_round_amount(big * Decimal(rnd.uniform(1.1, 1.6)) + 100_000))
        return s, old, row[s]
    if t == "valueMap":
        f = layout.get(rule.g("field"))
        vals = _map_lookup(rule, row.get(rule.g("when"), ""))
        if vals is None:      # giá trị 'when' hiện tại không có trong map -> đổi sang 1 khóa có trong map
            keys = [k for part in rule.g("map").split(";") if ":" in part
                    for k in expand_values(part.split(":", 1)[0]) if k != "*"]
            w = layout.get(rule.g("when"))
            keys = [k for k in keys if not w.allowed or k in w.allowed]
            if not keys:
                return None
            row[w.name] = rnd.choice(keys)
            vals = _map_lookup(rule, row[w.name]) or []
        bad = [x for x in (f.allowed or []) if x not in vals]
        if not bad:
            return None
        old = row.get(f.name, "")
        row[f.name] = rnd.choice(bad)
        return f.name, old, row[f.name]
    return None


# =========================================================================== lỗi

ERROR_LABELS = {
    "EMPTY": "Để trống",
    "INVALID_CODE": "Mã không có trong danh sách",
    "WRONG_LENGTH": "Sai độ dài cố định",
    "TOO_LONG": "Vượt độ dài tối đa",
    "BAD_DATE": "Ngày sai / sai định dạng",
    "FUTURE_DATE": "Ngày trong tương lai",
    "BAD_PATTERN": "Sai định dạng (pattern)",
    "NOT_NUMERIC": "Không phải số",
    "CUSTOM": "Giá trị tự nhập",
}
ROW_ERROR_LABELS = {
    "MISSING_FIELD": "Thiếu 1 cột",
    "EXTRA_FIELD": "Thừa 1 cột",
    "BAD_RECORD_TYPE": "Sai recordType (khác D)",
    "DUP_NEW": "Trùng đăng ký mới (evtCd=1 lặp lại cho cùng hợp đồng)",
}
RULE_ERROR_LABELS = {"RULE": "Vi phạm quy tắc"}
FILE_ERROR_LABELS = {
    "NO_HEADER": "Không có dòng Header",
    "NO_TRAILER": "Không có dòng Trailer",
    "TRAILER_COUNT": "Trailer đếm sai số dòng",
    "BAD_INFO_TYPE": "Header ghi sai loại dữ liệu",
    "FILENAME_INST": "Tên file khác mã tổ chức ở Header",
}
ROW_ERROR_CODES = {"MISSING_FIELD": "F023", "EXTRA_FIELD": "F023", "BAD_RECORD_TYPE": "F002", "DUP_NEW": "E142"}
FILE_ERROR_CODES = {"NO_HEADER": "F006", "NO_TRAILER": "F006", "TRAILER_COUNT": "F004",
                    "BAD_INFO_TYPE": "F021", "FILENAME_INST": "F022"}


@dataclass
class ErrorSpec:
    field: str            # tên cột | "*" (cả dòng) | "rule:<số thứ tự>"
    etype: str
    mode: str = "random"  # random: rows = số dòng | rows: rows = "1,3,5-7"
    rows: str = "1"
    custom: str = ""


def applicable_errors(f: Field | None) -> list[str]:
    if f is None:
        return list(ROW_ERROR_LABELS)
    out = ["EMPTY"]
    if f.allowed:
        out.append("INVALID_CODE")
    if f.is_date:
        out.append("BAD_DATE")
        if f.not_future:
            out.append("FUTURE_DATE")
    elif f.fixed_len:
        out.append("WRONG_LENGTH")
    elif f.max_len:
        out.append("TOO_LONG")
    if f.pattern:
        out.append("BAD_PATTERN")
    if f.is_numeric:
        out.append("NOT_NUMERIC")
    out.append("CUSTOM")
    return out


def field_error_code(f: Field, etype: str) -> str:
    if etype == "EMPTY":
        return f.err_code_missing or f.err_code
    return f.err_code


def make_error_value(f: Field, etype: str, good: str, rnd: random.Random, custom: str = "") -> str:
    if etype == "EMPTY":
        return ""
    if etype == "CUSTOM":
        return custom
    if etype == "INVALID_CODE":
        allowed = set(f.allowed or [])
        width = len(next(iter(allowed))) if allowed else 1
        cands = ["9", "0", "X", "Z", "99", "00", "98", "XX", "999", "000", "998", "ZZZ", "9999", "XXXX"]
        cands = [c for c in cands if c not in allowed]
        same = [c for c in cands if len(c) == width]
        return rnd.choice(same or cands)
    if etype == "WRONG_LENGTH":
        n = f.fixed_len or 1
        base = good[:n] if len(good) >= n else _digits(rnd, n)
        return base[:n - 1] if (rnd.random() < 0.5 and n > 1) else base + _digits(rnd, 1)
    if etype == "TOO_LONG":
        n = f.max_len or 1
        return (good + "9" * (n + 1))[: n + 1] if f.is_numeric else (good + "X" * (n + 1))[: n + 1]
    if etype == "BAD_DATE":
        return rnd.choice(["202613", "2026AB"] if f.fmt == "yyyymm" else
                          ["20261340", "2026-10-01", "20250230", "2026101", "ABCDEFGH"])
    if etype == "FUTURE_DATE":
        return _fmt_date(date.today() + timedelta(days=rnd.randint(40, 400)), f.fmt)
    if etype == "BAD_PATTERN":
        if "@" in good:
            return good.replace("@", ".at.")
        if good and good[0].isdigit():
            return good[:-2] + "AB" if len(good) > 2 else "AB"
        return "X" + good
    if etype == "NOT_NUMERIC":
        return rnd.choice(["12A3", "ABC", "1,000", "--"])
    raise ValueError(f"Loại lỗi không hỗ trợ: {etype}")


def parse_rows(text: str, n: int) -> list[int]:
    out = []
    for part in re.split(r"[,\s;]+", text.strip()):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    bad = [r for r in out if r < 1 or r > n]
    if bad:
        raise ValueError(f"Dòng {bad} nằm ngoài khoảng 1..{n}")
    return list(dict.fromkeys(out))


def _pick_rows(spec: ErrorSpec, n: int, rnd, used: set) -> list[int]:
    if spec.mode == "rows":
        return parse_rows(spec.rows, n)
    k = min(max(int(spec.rows or 1), 0), n)
    free = [r for r in range(1, n + 1) if r not in used]
    if len(free) >= k:
        return sorted(rnd.sample(free, k))
    taken = [r for r in range(1, n + 1) if r in used]
    return sorted(free + rnd.sample(taken, k - len(free)))


# =========================================================================== build file

@dataclass
class GenResult:
    lines: list[str]
    report: list[dict]
    error_lines: set[int]
    filename: str


def header_defaults() -> dict[str, str]:
    t = date.today().strftime("%Y%m%d")
    return {
        "recordType": "H", "institutionCode": "N001002", "institutionName": "NCIInformation",
        "baseDateFrom": t, "baseDateTo": t, "reportingDate": t, "fileType": "REG",
        "informationType": "", "registrationType": "INF", "userId": "ncireg1", "responseCode": "",
    }


def default_filename(header: dict, info_cd: str, suffix: str | None = None) -> str:
    inst = header.get("institutionCode") or "INST"
    suffix = suffix or date.today().strftime("%y%m%d")
    return f"{inst}_{info_cd}INF_NICE.{suffix}"


def read_csv(path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


CUST_FIELDS = ("custCd", "customerCode")
CHANGE_DATE_FIELDS = ("chgClsDt", "custLstUpdtDt")


@dataclass
class Lifecycle:
    evt: str | None          # cột sự kiện (evtCd / registrationEvent)
    cust: str | None         # cột mã khách hàng
    key: str | None          # khóa của 1 chuỗi sự kiện: ctrtNo, hoặc custCd nếu không có ctrtNo
    change_date: str | None  # ngày thay đổi/tất toán, tăng dần theo sự kiện
    history: bool            # thông tin lịch sử theo kỳ (LRPY/CRPY): mỗi tháng 1 dòng evtCd=1
    can_close: bool


def lifecycle_info(layout: Layout) -> Lifecycle:
    evt = next((n for n in EVT_FIELDS if layout.get(n)), None)
    cust = next((n for n in CUST_FIELDS if layout.get(n)), None)
    allowed = (layout.get(evt).allowed or []) if evt else []
    return Lifecycle(evt, cust, "ctrtNo" if layout.get("ctrtNo") else cust,
                     next((n for n in CHANGE_DATE_FIELDS if layout.get(n)), None),
                     bool(layout.get("baseDt")) and "2" not in allowed, "2" in allowed)


def _plan_rows(n: int, lc: Lifecycle, customers: int, lifecycle: bool, mst: bool, rnd) -> list[tuple]:
    """Danh sách (nhóm, khách hàng, thứ tự sự kiện, số sự kiện của nhóm, evt) cho từng dòng."""
    plan, g = [], 0
    by_customer = lc.key == lc.cust
    seq_ok = lifecycle and not mst and lc.evt is not None
    while len(plan) < n:
        g += 1
        remain = n - len(plan)
        if not seq_ok:
            k = 1
        elif by_customer:                                   # INID/COID/EWST: mỗi khách hàng 1 chuỗi
            left = max(customers - g + 1, 1) if customers else remain
            k = -(-remain // left)
        else:
            k = min(remain, rnd.choices([1, 2, 3, 4], [40, 25, 20, 15])[0])
        if by_customer and (not seq_ok or not customers):
            cust = g                                        # tránh trùng đăng ký mới cùng 1 khách hàng
        else:
            cust = ((g - 1) % customers) + 1 if customers else g
        if lc.history or not seq_ok:
            evts = ["1"] * k
        else:
            evts = ["1"] + ["3"] * (k - 1)
            if k >= 2 and lc.can_close and rnd.random() < (0.2 if by_customer else 0.5):
                evts[-1] = "2"
        plan.extend((g, cust, j, k, e) for j, e in enumerate(evts))
    return plan[:n]


MATURITY_FIELDS = ("matuDt", "expirationMonth")
AMOUNT_CHANGE_FIELDS = ("loanAmt", "lmtAmt")      # số tiền vay / hạn mức: đổi -> lý do 12
INFO_CHANGE_FIELDS = ("mobileNo", "email", "homeAdr", "officeAdr", "officeNm", "positionNm", "annualIncome",
                      "corpAdr", "corpTelNo", "corpWebAdr")   # INID/COID: cập nhật thông tin khách hàng
GOOD_CLOSE = {"21": 88, "29": 4, "30": 4, "31": 4}  # tất toán bình thường (C37)
BAD_CLOSE = ("23", "24", "26", "32")                 # xóa nợ, bán nợ, ghi nhận tổn thất, phá sản


@dataclass
class _Step:
    """1 sự kiện sau đăng ký mới: lý do (C37), việc thay đổi (amount/rate/grade/info) và cột bị đổi."""
    reason: str = ""
    action: str = ""
    field: str = ""
    grade: str = ""


def _reason_field(layout: Layout) -> str | None:
    return next((f.name for f in layout.fields if "reasoncode" in f.name.lower()), None)


def _maturity_field(layout: Layout) -> Field | None:
    return next((layout.get(n) for n in MATURITY_FIELDS if layout.get(n)), None)


def _rule_gap(layout: Layout, a: str, b: str | None, default=(1, 1500)) -> tuple[int, int]:
    for r in layout.rules:
        if r.type == "dateOrder" and r.g("a") == a and r.g("b") == b:
            return _gap(r, default)
    return default


def _lte_smalls(layout: Layout, big: str) -> set[str]:
    """Các cột phải ≤ cột big (quy tắc lte) -> chỉnh lại khi big thay đổi."""
    return {s for r in layout.rules if r.type == "lte" and r.g("big") == big for s in r.g("small").split("+")}


def _can_increase(layout: Layout, name: str, row: dict) -> bool:
    return not any(r.type == "lte" and name in r.g("small").split("+") and row.get(r.g("big"), "")
                   for r in layout.rules)


def _months(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def _fit_contract(layout: Layout, lc: Lifecycle, ctx: _Ctx, evts: list[str]) -> None:
    """Dòng đăng ký mới: nhóm nợ ban đầu, ngày mở đủ sớm cho các sự kiện sau, hợp đồng chưa tất toán thì
    chưa đến hạn, ân hạn nằm trong thời hạn vay."""
    rnd, row, today = ctx.rnd, ctx.row, date.today()
    grp = layout.get("debtGrpCd")
    if grp and grp.allowed and grp.weights:             # mới đăng ký -> nhóm nợ phổ biến nhất (LOAN 1, DELQ 2)
        _set(ctx, grp.name, max((k for k in grp.weights if k in grp.allowed), key=grp.weights.get))
    occ, mat = layout.get("occrDt"), _maturity_field(layout)
    if occ and row.get(occ.name) and occ.name not in ctx.locked:
        n = len(evts) - 1
        need = (_rule_gap(layout, occ.name, lc.change_date)[0] + 20 * n) if n else 0
        mat_hi = _rule_gap(layout, occ.name, mat.name, (180, 3650))[1] if mat else 0
        open_end = evts[-1] != "2"
        for _ in range(30):
            d_occ = _parse_date(row.get(occ.name, ""), occ.fmt)
            d_mat = _parse_date(row.get(mat.name, ""), mat.fmt) if mat else None
            if d_occ and (today - d_occ).days >= need and (not mat or not open_end or (d_mat and d_mat > today)):
                break
            back_hi = mat_hi - 60 if mat and open_end and mat_hi - 60 > need else need + 1500
            _set(ctx, occ.name, _fmt_date(today - timedelta(days=rnd.randint(need, back_hi)), occ.fmt))
            _repair(layout, ctx)
    elif lc.change_date and len(evts) > 1 and row.get(lc.change_date) and lc.change_date not in ctx.locked:
        cd, need = layout.get(lc.change_date), 20 * (len(evts) - 1)    # INID/COID: chừa chỗ cho các lần cập nhật
        d = _parse_date(row[lc.change_date], cd.fmt)
        if not d or (today - d).days < need:
            _set(ctx, cd.name, _fmt_date(today - timedelta(days=rnd.randint(need, need + 720)), cd.fmt))
            ctx.locked.add(cd.name)
            _repair(layout, ctx)
    gs, gp, mt = layout.get("graceStrtDt"), layout.get("gracePeriod"), layout.get("matuDt")
    if gs and gp and mt and occ and row.get(gs.name):
        d_gs, d_mt = _parse_date(row[gs.name], gs.fmt), _parse_date(row.get(mt.name, ""), mt.fmt)
        d_oc = _parse_date(row.get(occ.name, ""), occ.fmt)
        cap = min(_months(d_gs, d_mt) - 1, _months(d_oc, d_mt) // 3) if d_gs and d_mt and d_oc else 0
        if cap < 1:                                     # kỳ hạn quá ngắn -> không có ân hạn
            _set(ctx, gs.name, "")
            _set(ctx, gp.name, "")
        elif not row.get(gp.name, "").isdigit() or not 1 <= int(row[gp.name]) <= cap:
            _set(ctx, gp.name, str(rnd.randint(1, cap)))


def _plan_story(layout: Layout, lc: Lifecycle, row: dict, evts: list[str], rnd) -> list[_Step]:
    """Kịch bản các sự kiện sau đăng ký mới. Thay đổi: đổi số tiền (12), lãi suất / nhóm nợ / thông tin (13).
    Tất toán: bình thường (21...), hoặc nợ xấu dần lên nhóm 4-5 rồi xóa nợ / bán nợ / xử lý tài sản (23-28)."""
    reason, grp = _reason_field(layout), layout.get("debtGrpCd")
    amounts = [f.name for f in layout.fields if f.is_numeric and f.gen_range and f.gen_range[1] > 1000
               and "rate" not in f.name.lower() and row.get(f.name)
               and (f.name in AMOUNT_CHANGE_FIELDS or any(f.name in r.g("small").split("+")
                                                          for r in layout.rules if r.type == "lte"))]
    if layout.get("bal"):                               # DELQ/GUAR: dư nợ, số tiền quá hạn biến động
        amounts = [n for n in amounts if n not in AMOUNT_CHANGE_FIELDS]
    else:                                               # LOAN/CARD: số tiền vay / hạn mức
        amounts = [n for n in amounts if n in AMOUNT_CHANGE_FIELDS] or amounts
    rate = next((f.name for f in layout.fields if f.name.lower() == "interestrate" and row.get(f.name)), None)
    info = [n for n in INFO_CHANGE_FIELDS if layout.get(n)]
    n_chg = evts.count("3")
    grade = int(row["debtGrpCd"]) if grp and row.get("debtGrpCd", "").isdigit() else 0
    bad = bool(grade) and evts[-1] == "2" and n_chg >= 1 and rnd.random() < 0.25
    target = rnd.choice([4, 5]) if bad else grade
    steps = []
    for i, e in enumerate(evts[1:]):
        if e == "2":
            if not reason:
                steps.append(_Step())
            elif bad:
                pool = list(BAD_CLOSE) + (["25", "28"] if row.get("collStatFg") == "1" else []) \
                    + (["27"] if row.get("guarStatFg") == "1" else [])
                steps.append(_Step(rnd.choice(pool)))
            else:
                steps.append(_Step(rnd.choices(list(GOOD_CLOSE), list(GOOD_CLOSE.values()))[0]))
            continue
        if bad:                                         # nợ xấu dần: nhóm nợ tăng tới 4-5 trước khi xử lý
            grade = min(5, grade + -(-(target - grade) // (n_chg - i)))
            steps.append(_Step("13", "grade", "debtGrpCd", str(grade)))
            continue
        opts = ([("amount", 45)] if amounts else []) + ([("rate", 30)] if rate else []) \
            + ([("grade", 15)] if grade else []) + ([("info", 40)] if info and not reason else [])
        act = rnd.choices([a for a, _ in opts], [w for _, w in opts])[0] if opts else ""
        if act == "amount":
            steps.append(_Step("12", act, rnd.choice(amounts)))
        elif act == "rate":
            steps.append(_Step("13", act, rate))
        elif act == "grade":
            grade = grade + 1 if grade == 1 else grade - 1
            steps.append(_Step("13", act, "debtGrpCd", str(grade)))
        else:
            steps.append(_Step("13" if reason else "", act, rnd.choice(info) if act == "info" else ""))
    return steps


def _event_dates(layout: Layout, lc: Lifecycle, base: dict, rnd, evts: list[str], story: list[_Step]) -> list[str]:
    """Ngày các sự kiện sau đăng ký mới: tăng dần, cách ngày mở ≥ gap của quy tắc, không quá hôm nay.
    Thay đổi xảy ra trước ngày đáo hạn; tất toán bình thường của hợp đồng đã đến hạn rơi vào ngày đáo hạn."""
    count = len(evts) - 1
    if not lc.change_date or count <= 0:
        return []
    f, today = layout.get(lc.change_date), date.today()
    occ = _parse_date(base.get("occrDt", ""), "yyyymmdd")
    start = occ or _parse_date(base.get(lc.change_date, ""), f.fmt) or today - timedelta(days=365)
    lo = start + timedelta(days=_rule_gap(layout, "occrDt", lc.change_date)[0] if occ else 1)
    mat_f = _maturity_field(layout)
    mat = _parse_date(base.get(mat_f.name, ""), mat_f.fmt) if mat_f else None
    close = None
    if evts[-1] == "2" and story and story[-1].reason in ("", "21") and mat and mat <= today:
        close = max(mat - timedelta(days=rnd.randint(0, 10)), lo + timedelta(days=count - 1))
    m = count - (1 if close else 0)
    hi = close - timedelta(days=1) if close else today
    room = (hi - lo).days + 1
    offs = sorted(rnd.sample(range(room), m)) if room >= m else list(range(m))
    days = [lo + timedelta(days=o) for o in offs]
    if close:
        days.append(min(today, max(close, (days[-1] if days else lo) + timedelta(days=1))))
    return [_fmt_date(d, f.fmt) for d in days]


def _repair(layout: Layout, ctx: _Ctx) -> None:
    for _ in range(2):
        for rule in layout.rules:
            repair_rule(rule, ctx, layout)


def _build_row(layout: Layout, ctx: _Ctx, src: dict | None, fixed: dict, mst: bool) -> list[str]:
    for f in layout.fields:
        if f.name == "recordType":
            v = "D"
        elif src is not None and f.name in src:
            v = (src[f.name] or "").strip()
            ctx.locked.add(f.name)
        elif fixed.get(f.name):
            v = render_template(fixed[f.name], ctx)
            ctx.locked.add(f.name)
        elif mst and f.name in EVT_FIELDS:
            v = "1"          # F009: file MST chỉ được có sự kiện 1
            ctx.locked.add(f.name)
        else:
            v = default_value(f, ctx)
        ctx.row[f.name] = v
    _repair(layout, ctx)
    return [ctx.row[f.name] for f in layout.fields]


HISTORY_CARRY = ("branchCd", "branchNm", "debtGrpCd", "ctrtStatCd", "lmtAmt", "cadvLmtAmt",
                 "principalRepayAmt", "nextPrincipalRepayAmt", "interestRepayAmt", "nextInterestRepayAmt")


def _fit_history(layout: Layout, ctx: _Ctx, months: int) -> None:
    """Tháng đầu LRPY: dư nợ đủ lớn để trả gốc hết các tháng sau mà vẫn ≥ số gốc mỗi kỳ."""
    bal, paid = _num(ctx.row.get("bal", "")), _num(ctx.row.get("principalRepayAmt", ""))
    if bal is not None and paid is not None and bal < paid * (months + 1):
        _set(ctx, "bal", _num_str(_round_amount(paid * (months + 1) * Decimal(ctx.rnd.uniform(1.5, 20)))))


def _next_month(layout: Layout, lc: Lifecycle, prev: dict, fixed: dict) -> dict:
    """LRPY/CRPY tháng sau của cùng hợp đồng: giữ nhóm nợ, trạng thái, hạn mức, số tiền trả mỗi kỳ;
    dư nợ giảm đúng số gốc đã trả, số kỳ còn lại giảm 1. Ngày và chi tiêu trong tháng sinh mới."""
    src = {n: prev[n] for n in (lc.key, lc.cust) + HISTORY_CARRY if n and prev.get(n, "") != "" and n not in fixed}
    bal, paid = _num(prev.get("bal", "")), _num(prev.get("principalRepayAmt", ""))
    if bal is not None and paid is not None and "bal" not in fixed:
        paid = min(paid, bal)                           # kỳ cuối: trả nốt phần còn lại
        src["principalRepayAmt"], src["bal"] = _num_str(paid), _num_str(bal - paid)
    for n in ("remPrincipalRepayCnt", "remInterestRepayCnt"):
        if prev.get(n, "").isdigit() and n not in fixed:
            src[n] = str(max(int(prev[n]) - 1, 0))
    return src


def _change_event(layout: Layout, lc: Lifecycle, ctx: _Ctx, prev: dict, evt: str, chg_date: str | None,
                  step: _Step, fixed: dict) -> list[str]:
    """Dòng thay đổi/tất toán: giữ nguyên thông tin hợp đồng, chỉ đổi sự kiện, ngày, lý do và đúng 1 thứ
    theo kịch bản (số tiền / lãi suất / nhóm nợ / thông tin KH). Tất toán không đổi giá trị nào."""
    rnd, row = ctx.rnd, dict(prev)
    ctx.row = row
    ctx.locked = {f.name for f in layout.fields}
    row[lc.evt] = evt
    if lc.change_date and chg_date:
        row[lc.change_date] = chg_date
    reason = _reason_field(layout)
    if reason and step.reason:
        row[reason] = step.reason
    f = layout.get(step.field) if step.field and step.field not in fixed else None
    if f and step.action == "amount":
        lo, hi = (0.8, 0.95) if not _can_increase(layout, f.name, row) or rnd.random() < 0.5 else (1.1, 1.3)
        row[f.name] = _num_str(_round_amount(Decimal(row[f.name]) * Decimal(rnd.uniform(lo, hi))))
    elif f and step.action == "rate":
        lo, hi = f.gen_range or (Decimal(0), Decimal(10 ** 5))
        new = Decimal(row[f.name]) + rnd.choice([-1, 1]) * 25 * rnd.randint(1, 8)
        row[f.name] = _num_str(min(max(new, lo), hi))
    elif f and step.action == "grade":
        row[f.name] = step.grade
    elif f and step.action == "info":
        row[f.name] = default_value(f, ctx, force=True)
    if f:                                               # cột phụ thuộc (≤ cột vừa đổi) được chỉnh theo
        ctx.locked -= _lte_smalls(layout, f.name) - set(fixed)
    _repair(layout, ctx)
    return [row[f.name] for f in layout.fields]


def generate(layout: Layout, header_layout: Layout, trailer_layout: Layout,
             header_values: dict, n_rows: int = 10, fixed: dict | None = None,
             errors: list[ErrorSpec] | None = None, file_errors=None,
             csv_rows: list[dict] | None = None, seed: int | None = None,
             file_suffix: str | None = None, filename: str | None = None,
             customers: int = 1, lifecycle: bool = True) -> GenResult:
    """
    customers: số khách hàng trong file (custCd dùng chung); 0 = mỗi hợp đồng 1 khách hàng.
    lifecycle: mỗi hợp đồng là chuỗi sự kiện 1 (mới) -> 3 (thay đổi) ... -> 2 (tất toán), ngày tăng dần.
    """
    rnd = random.Random(seed)
    fixed = fixed or {}
    errors = errors or []
    file_errors = set(file_errors or [])

    header = dict(header_values)
    header["recordType"] = header.get("recordType") or "H"
    header["informationType"] = "XXXX" if "BAD_INFO_TYPE" in file_errors else layout.info_cd
    mst = header.get("registrationType", "").upper() == "MST"

    rows, ctxs = [], []
    lc = lifecycle_info(layout)
    if csv_rows is not None:
        for i, src in enumerate(csv_rows, 1):
            ctx = _Ctx(rnd, i, header, _make_persona(rnd), cust=i, row_no=i)
            rows.append(_build_row(layout, ctx, src, fixed, mst))
            ctxs.append(ctx)
    else:
        personas, prev, dates, story = {}, {}, [], []
        plan = _plan_rows(n_rows, lc, customers, lifecycle, mst, rnd)
        for i, (g, cust, j, k, evt) in enumerate(plan, 1):
            persona = personas.setdefault(cust, _make_persona(rnd))
            ctx = _Ctx(rnd, g, header, persona, cust=cust, row_no=i)
            if j > 0 and not lc.history:
                vals = _change_event(layout, lc, ctx, prev, evt, dates[j - 1] if j - 1 < len(dates) else None,
                                     story[j - 1], fixed)
            else:
                src = {}
                if lc.evt and (lifecycle or i == 1) and lc.evt not in fixed:
                    src[lc.evt] = evt                       # dòng đầu tiên / đầu mỗi hợp đồng luôn là 1
                if lc.history and "baseDt" not in fixed:     # LRPY/CRPY: mỗi dòng 1 tháng, tăng dần
                    src["baseDt"] = _month_end(-(k - j)).strftime("%Y%m%d")
                if j > 0 and lc.history:                    # tháng tiếp theo của cùng hợp đồng
                    src.update(_next_month(layout, lc, prev, fixed))
                vals = _build_row(layout, ctx, src or None, fixed, mst)
                if j == 0 and lc.history and lifecycle:
                    _fit_history(layout, ctx, k)
                    vals = [ctx.row[f.name] for f in layout.fields]
                if j == 0 and not lc.history:
                    evts = [p[4] for p in plan[i - 1:i - 1 + k]]
                    if lifecycle:
                        _fit_contract(layout, lc, ctx, evts)
                        vals = [ctx.row[f.name] for f in layout.fields]
                    story = _plan_story(layout, lc, ctx.row, evts, rnd)
                    dates = _event_dates(layout, lc, ctx.row, rnd, evts, story)
            prev = dict(ctx.row)
            rows.append(vals)
            ctxs.append(ctx)

    # ---- chèn lỗi: dòng chỉ định giữ chỗ trước; lỗi theo cột/quy tắc trước, lỗi cấu trúc dòng sau
    report, used, n = [], set(), len(rows)
    for spec in errors:
        if spec.mode == "rows" and n:
            used.update(parse_rows(spec.rows, n))
    for spec in sorted(errors, key=lambda s: s.field == ROW_LEVEL):
        if n == 0:
            break
        for r in _pick_rows(spec, n, rnd, used):
            vals, ctx, note, code = rows[r - 1], ctxs[r - 1], "", ""
            if spec.field == ROW_LEVEL:
                fname, label, code = "(cả dòng)", ROW_ERROR_LABELS[spec.etype], ROW_ERROR_CODES[spec.etype]
                if spec.etype == "MISSING_FIELD":
                    old = f"{len(vals)} cột"
                    vals.pop()
                    new = f"{len(vals)} cột"
                elif spec.etype == "EXTRA_FIELD":
                    old = f"{len(vals)} cột"
                    vals.append("EXTRA")
                    new = f"{len(vals)} cột"
                elif spec.etype == "DUP_NEW":
                    if r == 1 or not lc.evt or not lc.key:
                        continue
                    first = rows[0]                         # dòng 1 luôn là đăng ký mới của hợp đồng đầu tiên
                    keys = [lc.key, lc.cust] + (["baseDt"] if lc.history else [])
                    old = f"{lc.key}={vals[layout.get(lc.key).index]}, {lc.evt}={vals[layout.get(lc.evt).index]}"
                    for kname in filter(None, dict.fromkeys(keys)):
                        vals[layout.get(kname).index] = first[layout.get(kname).index]
                    vals[layout.get(lc.evt).index] = "1"
                    new = f"{lc.key}={vals[layout.get(lc.key).index]}, {lc.evt}=1"
                else:
                    old, new = vals[0], "X"
                    vals[0] = new
            elif spec.field.startswith(RULE_PREFIX):
                rule = layout.rules[int(spec.field[len(RULE_PREFIX):])]
                ctx.row = dict(zip(layout.names, vals))
                res = violate_rule(rule, ctx, layout)
                if res is None:
                    continue
                fname, old, new = res
                vals[:] = [ctx.row[f.name] for f in layout.fields]
                label, code = f"Vi phạm: {rule.label()}", rule.err_code
                if rule.level == "warn":
                    note = "Quy tắc nghiệp vụ (cảnh báo), không có mã lỗi NICE riêng"
            else:
                f = layout.get(spec.field)
                if f is None:
                    raise ValueError(f"Không có cột '{spec.field}' trong layout {layout.info_cd}")
                fname, label = f.name, ERROR_LABELS[spec.etype]
                old = vals[f.index]
                new = make_error_value(f, spec.etype, old, rnd, spec.custom)
                vals[f.index] = new
                code = field_error_code(f, spec.etype)
                if spec.etype == "EMPTY" and not f.is_required:
                    note = "Cột không bắt buộc: để trống có thể KHÔNG bị coi là lỗi"
            used.add(r)
            report.append({"row": r, "field": fname, "error": spec.etype, "code": code, "label": label,
                           "old": old, "new": new, "note": note})

    # ---- ghép file
    lines = []
    has_header = "NO_HEADER" not in file_errors
    if has_header:
        lines.append(header_layout.delimiter.join(header.get(f.name, "") for f in header_layout.fields))
    offset = 1 if has_header else 0
    lines.extend(layout.delimiter.join(v) for v in rows)
    if "NO_TRAILER" not in file_errors:
        count = n + (1 if "TRAILER_COUNT" in file_errors else 0)
        tvals = ["T" if f.name == "recordType" else str(count) if (f.is_numeric or "count" in f.name.lower())
                 else "" for f in trailer_layout.fields]
        lines.append(trailer_layout.delimiter.join(tvals))

    for item in report:
        item["line"] = item["row"] + offset
    out_name = filename or default_filename(header, layout.info_cd, file_suffix)
    if "FILENAME_INST" in file_errors:
        inst = header.get("institutionCode") or "N000000"
        out_name = out_name.replace(inst, inst[:-1] + ("9" if inst[-1] != "9" else "8"), 1)
    file_line = {"BAD_INFO_TYPE": 1, "TRAILER_COUNT": len(lines)}
    notes = {"TRAILER_COUNT": f"Trailer ghi {n + 1}, thực tế {n}", "FILENAME_INST": f"Tên file: {out_name}"}
    for code in sorted(file_errors):
        report.append({"line": file_line.get(code, ""), "row": "", "field": "(file)", "error": code,
                       "code": FILE_ERROR_CODES[code], "label": FILE_ERROR_LABELS[code], "old": "", "new": "",
                       "note": notes.get(code, "")})
    return GenResult(lines, report, {it["line"] for it in report if it["line"] != ""}, out_name)


def write_lines(path, lines: list[str], crlf: bool = False) -> None:
    nl = "\r\n" if crlf else "\n"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(nl.join(lines) + nl)


REPORT_COLUMNS = ["line", "row", "field", "error", "code", "label", "old", "new", "note"]


def write_report(path, report: list[dict]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=REPORT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(report)


# =========================================================================== kiểm tra file

def read_lines(path) -> list[str]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return fh.read().splitlines()


def check_value(f: Field, v: str) -> tuple[str, str] | None:
    """Trả về (thông báo lỗi, mã lỗi NICE) hoặc None."""
    if v == "":
        return ("Thiếu giá trị bắt buộc", f.err_code_missing or f.err_code) if f.is_required else None
    if f.allowed and v not in f.allowed:
        src = f"mã {f.code_ref}" if f.code_ref else ",".join(f.allowed[:10])
        return f"Giá trị không thuộc {src}", f.err_code
    if f.is_date:
        d = _parse_date(v, f.fmt)
        if d is None or len(v) != len(f.fmt):
            return f"Ngày không hợp lệ ({f.fmt})", f.err_code
        if f.not_future and d > date.today():
            return "Ngày trong tương lai", f.err_code
    elif f.fixed_len and len(v) != f.fixed_len:
        return f"Độ dài {len(v)}, yêu cầu đúng {f.fixed_len}", f.err_code
    if f.is_numeric:
        if f.type in INTEGER_TYPES and not re.fullmatch(r"[+-]?\d+", v):
            return "Không phải số nguyên", f.err_code
        n = _num(v)
        if n is None or not n.is_finite():
            return "Không phải số", f.err_code
        digits = len(re.sub(r"\D", "", v))
        if f.max_len and digits > f.max_len:
            return f"{digits} chữ số > tối đa {f.max_len}", f.err_code
    elif f.max_len and len(v) > f.max_len:
        return f"Độ dài {len(v)} > tối đa {f.max_len}", f.err_code
    if f.pattern and not re.fullmatch(f.pattern.strip("^$"), v):
        return "Sai định dạng", f.err_code
    return None


def validate_lines(lines: list[str], repo: dict[str, Layout], info_cd: str | None = None,
                   filename: str | None = None) -> list[dict]:
    """Kiểm tra file. Mỗi lỗi: line, field, code (mã NICE), level (Lỗi/Cảnh báo), issue, value."""
    issues = []

    def add(line, fld, msg, value="", code="", level="Lỗi"):
        issues.append({"line": line, "field": fld, "code": code, "level": level, "issue": msg, "value": value})

    H, T = repo.get(HEADER_CD), repo.get(TRAILER_CD)
    lines = list(lines)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        add("", "(file)", "File rỗng", code="F006")
        return issues

    delim = H.delimiter if H else "|:"
    start, end = 0, len(lines)
    hd: dict = {}
    if lines[0].split(delim)[0] == "H":
        vals = lines[0].split(delim)
        start = 1
        if H:
            if len(vals) != len(H.fields):
                add(1, "(header)", f"Header có {len(vals)} cột, yêu cầu {len(H.fields)}", code="F023")
            hd = dict(zip(H.names, vals))
            for f, v in zip(H.fields, vals):
                res = check_value(f, v)
                if res:
                    add(1, f.name, res[0], v, res[1])
            for rule in H.rules:
                msg = check_rule(rule, hd, H)
                if msg:
                    add(1, "(header)", msg, code=rule.err_code, level="Cảnh báo" if rule.level == "warn" else "Lỗi")
            hinfo = hd.get("informationType", "")
            if hinfo in repo and hinfo not in (HEADER_CD, TRAILER_CD):
                info_cd = hinfo
            else:
                add(1, "informationType", f"Loại dữ liệu '{hinfo}' không có layout", hinfo, "F021")
    else:
        add(1, "(file)", "Thiếu dòng Header", code="F006")

    if filename and hd:
        base = Path(filename).name
        inst, info = hd.get("institutionCode", ""), hd.get("informationType", "")
        if inst and not base.startswith(inst + "_"):
            add("", "(tên file)", f"Tên file '{base}' không khớp mã tổ chức {inst} ở Header", base, "F022")
        if info and f"_{info}" not in base:
            add("", "(tên file)", f"Tên file '{base}' không khớp loại dữ liệu {info} ở Header", base, "F021")

    layout = repo.get(info_cd) if info_cd else None
    if layout is None:
        add("", "(file)", "Không xác định được layout để kiểm tra detail")
        return issues

    trailer_count = None
    if len(lines) > start and lines[-1].split(delim)[0] == "T":
        tv = lines[-1].split(delim)
        end = len(lines) - 1
        if T and len(tv) != len(T.fields):
            add(len(lines), "(trailer)", f"Trailer có {len(tv)} cột, yêu cầu {len(T.fields)}", code="F023")
        try:
            trailer_count = int(tv[1])
        except (IndexError, ValueError):
            add(len(lines), "(trailer)", "Số dòng trong Trailer không phải số", tv[1] if len(tv) > 1 else "", "F004")
    else:
        add(len(lines), "(file)", "Thiếu dòng Trailer", code="F006")

    mst = hd.get("registrationType", "").upper() == "MST"
    lc = lifecycle_info(layout)
    seen: dict = {}       # khóa -> (trạng thái, dòng đăng ký mới)
    d, n_fields = layout.delimiter, len(layout.fields)
    for ln in range(start, end):
        no, vals = ln + 1, lines[ln].split(d)
        if vals[0] != "D":
            add(no, "recordType", "recordType phải là D", vals[0], "F002")
        if len(vals) != n_fields:
            add(no, "(cả dòng)", f"Có {len(vals)} cột, yêu cầu {n_fields}", code="F023")
            continue
        row = dict(zip(layout.names, vals))
        for f, v in zip(layout.fields, vals):
            if f.name == "recordType":
                continue
            res = check_value(f, v)
            if res:
                add(no, f.name, res[0], v, res[1])
        for rule in layout.rules:
            msg = check_rule(rule, row, layout)
            if msg:
                add(no, rule.label(), msg, code=rule.err_code, level="Cảnh báo" if rule.level == "warn" else "Lỗi")
        if lc.evt and lc.key:
            evt, kv = row.get(lc.evt, ""), row.get(lc.key, "")
            key = (kv, row.get("baseDt", "")) if lc.history else kv
            st, at = seen.get(key, (None, None))
            label = f"{lc.key}={kv}" + (f", baseDt={row.get('baseDt')}" if lc.history else "")
            if evt == "1":
                if st:
                    add(no, lc.evt, f"Đăng ký mới trùng: {label} đã có evtCd=1 ở dòng {at}", evt, "E142")
                seen[key] = ("open", at or no)
            elif evt in ("2", "3"):
                if st == "closed":
                    add(no, lc.evt, f"{label} đã tất toán/xóa trước đó trong file", evt, "E124")
                elif st is None:
                    add(no, lc.evt, f"Chưa có đăng ký mới (evtCd=1) cho {label} trong file "
                                    f"(chỉ hợp lệ nếu đã đăng ký từ trước)", evt, "E123", "Cảnh báo")
                seen[key] = ("closed" if evt == "2" else "open", at)
            elif evt == "7":
                seen[key] = ("closed", at)
        if mst:
            evt = next((row[n] for n in EVT_FIELDS if n in row), "1")
            if evt != "1":
                add(no, "evtCd", "File MST chỉ được có sự kiện 1 (đăng ký mới)", evt, "F009")
            if layout.info_cd == "DELQ" and row.get("eventReasonCode", "11") != "11":
                add(no, "eventReasonCode", "File DELQ MST: eventReasonCode phải là 11", row["eventReasonCode"], "F010")

    n_detail = end - start
    if trailer_count is not None and trailer_count != n_detail:
        add(len(lines), "dataCount", f"Trailer ghi {trailer_count} dòng, thực tế {n_detail}", str(trailer_count), "F004")
    return issues
