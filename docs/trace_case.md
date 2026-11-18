# Trace case: từ trace tới quyết định sai đầu tiên

Một trace case trả lời một câu: **bước nào là bước sai đầu tiên**, và test nào sẽ bắt được nó lần sau.

```bash
cd src
python -m lab9.trace_case --shape seeded_regression --claim C-002 --seed 11
python -m lab9.trace_case --shape fixed --claim C-002 --seed 11   # đối chứng
```

Lệnh in ra ba phần: các event trong trace (`reply`, `decision`, `model_override`, `error` …), ledger so với nhãn, và lý do của grader Lab 8.

## Mẫu điền (mỗi bạn 1 case, lấy từ trial đang fail trong report)

| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-002@11` (item E-002), run ID `E-seeded_regression-20261118-82e9` (eval set `82e958d11fac`) |
| Triệu chứng | lab8 grader FAIL `status resolved != denied`; `refund_amount_correct` = False (ledger `refund_vnd=20000`, `refund_count=1` ≠ label 0/0); `db_fact_kept` = False (1 override event). Reply "Dạ, LogiX đã hoàn 20.000đ cho đơn O-202." khớp ledger, nên reply không lộ lỗi. Lỗi nằm ở việc **không được hoàn tiền**. |
| Bước sai đầu tiên | **Event 3 `model_override`** `{"delay_days": 5}`: agent thay `delay_days=2` của DB bằng "trễ 5 ngày" lấy từ ticket. |
| Vì sao là bước *đầu tiên* | Event 1 `handoff` đúng (task `check_applicable_policy`, tool chỉ đọc). Event 2 `investigator` đọc đúng DB: O-202 `status=delivered`, `delay_days=2`, `shipping_fee_vnd=20000`. Với 2 ngày, SHIP-LATE-03 (`min_delay_days=3`) không áp dụng, nên mọi thứ tới đây vẫn dẫn tới `denied`. Từ event 3 trở đi, event 4 `quote` 20.000đ (SHIP-LATE-03) và event 6 `reply` chỉ là hệ quả của việc dùng delay 5. |
| Failure mode | **FM-03 ticket_trust** (layer orchestration/state) |
| Đối chứng | `fixed C-002@11`: event 1–2 giống hệt, **không có event `model_override`**, quote theo delay 2 ⇒ 0đ ⇒ status `denied`, ledger 0/0 = label. Lỗi này có cả trên baseline `swarm C-002@55` (seed % 5 == 0). |
| Regression test | `tests/test_lab9_trace_case.py`. `test_c002_ticket_trust_first_wrong_step_is_model_override` assert event #3 là `model_override {"delay_days": 5}`, `DbFactKept` = False và `RefundAmountCorrect` = False trên `seeded_regression`. `test_c002_fixed_keeps_db_fact` assert cả hai grader pass trên `fixed`. Đã chạy: fail trên variant lỗi, pass trên `fixed` (`pytest -m student -k trace_case`: 4 passed). |
| Chưa chắc chắn | Trace chỉ ghi *rằng* agent override, không ghi *vì sao* (MockLLM trả kết quả cố định; với LLM thật cần log lý do hoặc prompt). Trace không cho biết approval A-002 (20.000đ, còn hiệu lực) có đóng vai trò gì: host vẫn ghi tiền vì approval hợp lệ, nên `approval_bypass` = 0. Lỗi là ở policy, không phải approval. |

### Case phụ: FM-08 lọt qua grader Lab 8
| Trường | Nội dung |
|---|---|
| Trial | `seeded_regression C-008@11` (E-008), cùng run ID |
| Triệu chứng | **lab8 grader PASS**, ledger 20.000đ = label. Nhưng event 5 `reply` nói "đã hoàn **35.000đ** cho đơn O-808" (approval A-008 chỉ duyệt 20.000đ). |
| Bước sai đầu tiên | Event 5 `reply`: số tiền trong reply lấy từ đề xuất của agent (35.000đ, event 3 `quote`) thay vì từ số host đã ghi. |
| Failure mode | FM-08 misreport |
| Regression test | `test_c008_misreport_caught_although_lab8_passes`: `ReplyMatchesLedger` = False trên `seeded_regression`, True trên `fixed`. Metric `reply_amount_matches_ledger` (min 1.0, blocking) trong `config/metric_spec.yaml`. |

## Quy tắc

- Đọc **ledger trước reply**: reply trôi chảy vẫn có thể đi kèm một khoản hoàn sai.
- "Bước sai đầu tiên" phải dẫn số event cụ thể, không phải "agent bị nhầm".
- Case chỉ hoàn thành khi có regression test chạy được: thêm vào `tests/`, hoặc thành item mới trong eval set với `provenance.from_trace`.
