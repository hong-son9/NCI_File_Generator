# NICE Test Data Generator

Tạo file dữ liệu chuẩn **Header + Detail + Trailer** cho 10 loại thông tin NICE (INID, COID, LOAN, LRPY, CARD, CRPY, DELQ, GUAR, COLL, EWST). Dữ liệu sinh ra đúng theo NCI Code Master và các quy tắc nghiệp vụ. App cũng cho phép chèn lỗi có chủ đích, mỗi lỗi kèm **mã lỗi NICE mong đợi** (C34/C35).

## Chạy

```
python datagen_ui.py
```

Hoặc đổi tên thành `datagen_ui.pyw` rồi double-click. Cần Python 3.9 trở lên, không phải cài thêm thư viện nào.

## Thư mục

```
layouts/   HEADER.xml, TRAILER.xml + 1 file cho mỗi loại thông tin
codes/     nci_codes.csv: NCI Code Master (bấm "Nạp Code Master (.xlsx)..." để cập nhật khi NICE ra bản mới)
samples/   CSV mẫu (dòng đầu = tên cột)
configs/   tự tạo: giá trị cột và danh sách lỗi đã chọn cho từng loại
output/    file sinh ra + <tên>.errors.csv (danh sách lỗi đã chèn, có mã NICE)
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

## Số khách hàng & vòng đời hợp đồng (hàng thứ 3 trên cùng)

- **Số khách hàng = 1** (mặc định): cả file dùng chung một `custCd`. Với INID/COID thì mọi dòng là cùng một người hoặc một doanh nghiệp. Nếu nhập N thì các hợp đồng chia đều cho N khách hàng; nhập 0 thì mỗi hợp đồng là một khách hàng riêng.
- **Vòng đời** (mặc định bật):
  - Mỗi hợp đồng là một chuỗi sự kiện: `1` (đăng ký mới) → `3` (thay đổi) … → `2` (tất toán). **Dòng đầu tiên luôn là evtCd = 1.**
  - Ngày thay đổi (`chgClsDt`, hoặc `custLstUpdtDt` với INID/COID) tăng dần theo từng sự kiện. Thông tin hợp đồng giữ nguyên; số tiền thay đổi; lý do sự kiện khớp với evtCd.
  - LRPY/CRPY là dữ liệu lịch sử, nên mỗi dòng là một tháng (`baseDt` = cuối tháng, tăng dần) và đều có evtCd = 1.
- **Kiểm tra trình tự sự kiện:**
  - E142: đăng ký mới trùng cho cùng một hợp đồng.
  - E124: thay đổi hoặc tất toán sau khi đã tất toán/xóa.
  - E123 (cảnh báo): thay đổi mà trong file chưa có đăng ký mới; trường hợp này vẫn hợp lệ nếu hợp đồng đã đăng ký từ trước.
- Trong mẫu giá trị cột: `{seq}` là số thứ tự hợp đồng, `{cust}` là số thứ tự khách hàng, `{row}` là số thứ tự dòng.

## Khai báo trong layout XML

Thuộc tính của `<field>` (ưu tiên hơn nội dung `desc`):

| Thuộc tính | Ý nghĩa |
|---|---|
| `required="Y\|N\|M"` | bắt buộc / không / có điều kiện |
| `maxLen`, `length`, `format="yyyymmdd\|yyyymm"`, `pattern="^\d{12}$"` | định dạng |
| `codeRef="C17"`, `values="110,120,410-418"` | danh sách mã (`values` để giới hạn tập con) |
| `notFuture="Y"` | ngày không được sau hôm nay |
| `errCode="E132"`, `errCodeMissing="E131"` | mã lỗi NICE khi sai / khi thiếu |
| `default="LN{seq:08d}"`, `gen`, `genRange`, `weights="1:80,3:15"`, `fillRate="0.3"` | chỉ dùng khi sinh dữ liệu |

Quy tắc liên cột, đặt bằng thẻ `<rule>`. `level="warn"` nghĩa là quy tắc nghiệp vụ không có mã lỗi NICE riêng, nên chỉ báo cảnh báo:

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
