# NICE Test Data Generator

Tạo file dữ liệu chuẩn **Header + Detail + Trailer** cho 10 loại thông tin NICE (INID, COID, LOAN, LRPY, CARD, CRPY, DELQ, GUAR, COLL, EWST). Dữ liệu sinh ra đúng theo NCI Code Master và các quy tắc nghiệp vụ. App cũng cho phép chèn lỗi có chủ đích, mỗi lỗi kèm **mã lỗi NICE mong đợi** (C34/C35).

## Chạy trên máy

Double-click `run.bat` (hoặc `python datagen_ui.py`). Cần Python 3.9 trở lên, không phải cài thêm thư viện nào.

## Bản web (Cloudflare Pages)

Thư mục `web/` là bản web cùng logic: trình duyệt chạy Python bằng [Pyodide](https://pyodide.org) và dùng lại nguyên `datagen_core.py`, `layouts/`, `rules/`, `codes/`. Không cần server; dữ liệu không rời máy người dùng. Cấu hình (giá trị cột, lỗi đã chọn, header) lưu trong trình duyệt.

- **Chạy thử trên máy:** double-click `serve.bat` → mở http://localhost:8080
- **Deploy bằng lệnh:** double-click `deploy.bat` (cần Node.js; lần đầu sẽ mở trình duyệt để đăng nhập Cloudflare). Project tên `nci-file-generator`.
- **Hoặc deploy tự động từ GitHub:** Cloudflare Dashboard → Workers & Pages → Create → Pages → Connect to Git → chọn repo này, rồi đặt:
  - Build command: `python web/build.py`
  - Build output directory: `web/dist`

  Sau đó mỗi lần push lên `main`, Cloudflare tự build và deploy lại.

`web/build.py` gom trang web, `datagen_core.py` và nội dung `layouts/`, `rules/`, `codes/` vào `web/dist/` (không commit thư mục này).

## Thư mục

```
layouts/   HEADER.xml, TRAILER.xml + 1 file cho mỗi loại thông tin (chỉ <field name type index desc>)
rules/     quy tắc cho từng layout, cùng tên file (LOAN.xml ↔ rules/LOAN.xml)
codes/     nci_codes.csv: NCI Code Master (sửa trực tiếp file CSV khi NICE ra bản mới)
samples/   CSV mẫu (dòng đầu = tên cột)
configs/   tự tạo: giá trị cột và danh sách lỗi đã chọn cho từng loại
output/    file sinh ra + <tên>.errors.csv (danh sách lỗi đã chèn, có mã NICE)
web/       bản web cho Cloudflare Pages (index.html, app.js, web_api.py, build.py)
```

## Dữ liệu "đúng thực tế" gồm những gì

- **Mã lấy từ Code Master.** Ví dụ loanTypeCd theo C17, eventReasonCode theo C37/C38, provincialCd theo C32. Tần suất xuất hiện gần với thực tế: khoảng 75% là đăng ký mới, khoảng 86% nợ nhóm 1.
- **Một "người" thống nhất trong mỗi dòng.** Tên, giới tính, ngày sinh, CCCD (mã tỉnh, thế kỷ, giới tính, năm sinh), địa chỉ, mã bưu chính, đầu số điện thoại và biển số xe đều cùng một tỉnh.
- **Các quy tắc liên cột** (xem tab 2), ví dụ:
  - ngày mở < ngày đáo hạn;
  - evtCd 2/3 thì phải có ngày thay đổi/tất toán;
  - số kỳ trả = thời hạn ÷ chu kỳ;
  - dư nợ ≤ số tiền vay;
  - thẻ revolving thì có tỷ lệ thanh toán tối thiểu;
  - thuê tài chính (410–417) thì có thông tin tài sản thuê.
- **Liên kết giữa các file.** Dùng cùng seed và cùng số dòng thì `custCd` / `ctrtNo` khớp nhau: LOAN `LN00000001` ↔ LRPY, DELQ, GUAR.debtorCtrtNo, COLL.debtorCtrtNo; CARD `CD00000001` ↔ CRPY.
- **Header là MST** thì app tự sinh evtCd = 1 cho mọi dòng (F009).

## Số khách hàng & vòng đời hợp đồng (hàng trên cùng)

- **Số khách hàng = 1** (mặc định): cả file dùng chung một `custCd`. Với INID/COID thì mọi dòng là cùng một người hoặc một doanh nghiệp. Nếu nhập N thì các hợp đồng chia đều cho N khách hàng; nhập 0 thì mỗi hợp đồng là một khách hàng riêng.
- **Vòng đời** (mặc định bật):
  - Mỗi hợp đồng là một chuỗi sự kiện: `1` (đăng ký mới) → `3` (thay đổi) … → `2` (tất toán). **Dòng đầu tiên luôn là evtCd = 1.**
  - Ngày thay đổi (`chgClsDt`, hoặc `custLstUpdtDt` với INID/COID) tăng dần, cách ngày mở ít nhất `gap` của quy tắc, không quá hôm nay.
  - Mỗi lần thay đổi chỉ đổi đúng 1 thứ, khớp lý do: `12` đổi số tiền vay/hạn mức (DELQ/GUAR: dư nợ, số quá hạn); `13` đổi lãi suất, nhóm nợ hoặc thông tin khác. Tất toán không đổi giá trị nào.
  - Đăng ký mới có nhóm nợ ban đầu (LOAN/CARD nhóm 1, DELQ nhóm 2). Hợp đồng chưa tất toán thì chưa đến hạn; hợp đồng đã đến hạn thì tất toán (lý do 21) vào khoảng ngày đáo hạn.
  - Khoảng 1/4 hợp đồng tất toán là nợ xấu: nhóm nợ tăng dần lên 4–5 rồi xử lý bằng xóa nợ/bán nợ/ghi nhận tổn thất/phá sản (23, 24, 26, 32); `25`/`28` chỉ khi có tài sản bảo đảm, `27` chỉ khi có bảo lãnh.
  - Ân hạn không quá 1/3 thời hạn vay; chu kỳ trả không đều (`22`/`99`) thì để trống số kỳ trả.
  - LRPY/CRPY là dữ liệu lịch sử, nên mỗi dòng là một tháng (`baseDt` = cuối tháng, tăng dần) và đều có evtCd = 1. Các tháng của cùng hợp đồng giữ nhóm nợ, hạn mức, số trả mỗi kỳ; dư nợ giảm đúng số gốc đã trả, số kỳ còn lại giảm 1.
- **Kiểm tra trình tự sự kiện:**
  - E142: đăng ký mới trùng cho cùng một hợp đồng.
  - E124: thay đổi hoặc tất toán sau khi đã tất toán/xóa.
  - E123 (cảnh báo): thay đổi mà trong file chưa có đăng ký mới; trường hợp này vẫn hợp lệ nếu hợp đồng đã đăng ký từ trước.
- Trong mẫu giá trị cột: `{seq}` là số thứ tự hợp đồng, `{cust}` là số thứ tự khách hàng, `{row}` là số thứ tự dòng.

## Layout và quy tắc

`layouts/LOAN.xml` giữ đúng chuẩn layout: mỗi `<field>` chỉ có `name`, `type`, `index`, `desc`.
Quy tắc sinh/kiểm tra nằm ở `rules/LOAN.xml`, ghép với layout theo `name`:

```xml
<rules infoCd="LOAN">
    <field name="consentFg" required="Y" maxLen="1" values="1,0" errCode="E191" weights="1:97,0:3"/>
    <rule type="dateOrder" a="occrDt" b="matuDt" strict="Y" gap="180-3650" errCode="E306"/>
</rules>
```

Cột không có trong file quy tắc thì app tự suy quy tắc từ `desc` (ví dụ `(Y, String, 1)`, `1:..., 0:...`, `Code Master C17`).

Thuộc tính của `<field>` trong `rules/`:

| Thuộc tính | Ý nghĩa |
|---|---|
| `required="Y\|N\|M"` | bắt buộc / không / có điều kiện |
| `maxLen`, `length`, `format="yyyymmdd\|yyyymm"`, `pattern="^\d{12}$"` | định dạng |
| `codeRef="C17"`, `values="110,120,410-418"` | danh sách mã (`values` để giới hạn tập con) |
| `notFuture="Y"` | ngày không được sau hôm nay |
| `errCode="E132"`, `errCodeMissing="E131"` | mã lỗi NICE khi sai / khi thiếu |
| `default="LN{seq:08d}"`, `gen`, `genRange`, `weights="1:80,3:15"`, `fillRate="0.3"` | chỉ dùng khi sinh dữ liệu |

Quy tắc liên cột, đặt bằng thẻ `<rule>` trong `rules/`. `level="warn"` nghĩa là quy tắc nghiệp vụ không có mã lỗi NICE riêng, nên chỉ báo cảnh báo:

```xml
<rule type="dateOrder"    a="occrDt" b="matuDt" strict="Y" gap="180-3650" errCode="E306"/>
<rule type="requiredWhen" field="chgClsDt" when="evtCd" in="2,3" errCode="E146"/>
<rule type="atLeastOne"   fields="homeAdr,officeAdr" errCode="E138"/>
<rule type="lte"          small="dlqAmt" big="bal" ratio="0.02-0.3" level="warn"/>
<rule type="valueMap"     field="eventReasonCode" when="evtCd" map="1:11;3:12-14;2:21-33,99" level="warn"/>
<rule type="repayCount"   field="principalRepayCnt" cycle="principalRepayCycle" a="occrDt" b="matuDt" level="warn"/>
```

## Chèn lỗi (tab 3)

- **Theo cột:** để trống, mã ngoài danh sách, sai độ dài, ngày sai, ngày tương lai, sai định dạng, không phải số, giá trị tự nhập.
- **Theo quy tắc:** chọn "(Quy tắc n)" trong danh sách cột.
- **Cả dòng:** thiếu cột, thừa cột (F023); sai recordType (F002).
- **Cấp file:** thiếu Header/Trailer (F006), Trailer đếm sai (F004), Header sai loại (F021), tên file sai mã tổ chức (F022).

**Kiểm tra file...** đối chiếu một file bất kỳ với layout và Code Master, rồi liệt kê lỗi kèm mã NICE và mức độ (Lỗi / Cảnh báo).
