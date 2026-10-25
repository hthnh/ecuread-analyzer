# Bộ dữ liệu RAW đã lọc — 18 tệp (kiểm kê ban đầu)

**Mục đích:** làm nguồn CTX2 và kiểm thử Model Harness với session dài. **Chưa** thay thế chính thức manifest 57 entries cũ.

- 18 file JSONL, 100.688 frame Honda Table 17 hợp lệ (24 byte/frame), checksum `sum(bytes) % 256 == 0`.
- Tổng thời lượng cộng theo tệp: 25.189,348 giây (~7 giờ); không coi tổng này là giờ thu thập độc lập đã xác minh.
- 7 file dài hơn 5.000 frame; 3 file dài hơn 10.000 frame.
- Không có file trùng SHA; không phát hiện chồng lặp RAW dài ≥16 frame liên tiếp giữa hai tệp. Đây **không** phải bằng chứng chắc chắn mỗi tệp là một capture vật lý khác nhau.
- Mọi tệp có `elapsed_ms` tăng đơn điệu, khoảng cách mẫu thường ~250 ms, không có gap >1 giây.
- Tín hiệu tốc độ ứng viên từ native RAW byte[19] tồn tại trên cả 18 file (theo xác nhận người lái; chưa hiệu chuẩn GPS độc lập).

## Một vài nhận xét

- `session_000016`, `000018`, `000023` chứa lần lượt 17.965 / 17.976 / 17.539 frame; thích hợp để kiểm thử end-to-end pipeline và hiệu năng, **không được cắt ở mốc 5.000**.
- `session_000001` và `000020` chỉ dài ~29s / 21,5s: vẫn là dữ liệu nguồn, không tự xem như reference nominal.
- IDs `000008`–`000013` không có trong bộ 18 file đã gửi. Không nên tự tạo hoặc suy diễn rằng 6 tệp đó thất lạc.
- Đầu vào hiện chỉ có `elapsed_ms` và `raw_hex`, chưa có metadata capture ngày/giờ, thiết bị và xác nhận lịch sử. Phải đối chiếu với kho 57 entries cũ khi xây corpus v2.

## Nguyên tắc tích hợp

1. Copy `raw/*.jsonl` vào thư mục nhập RAW nghiên cứu mới, read-only và ghi nhận SHA manifest.
2. Truy vết file cha của alias/split, so khớp frame và thời gian; bảo tồn corpus/analysis cũ nguyên trạng.
3. Chạy long-session ingestion→canonical→IF→shadow contextual→evidence/aggregation→RCA/history và CTX2 trên `000016/000018/000023`. Không nhận PASS nếu bất kỳ bước nào cắt im lặng.
4. Không auto-accept annotation, không tạo synthetic augmentation, không khẳng định số capture độc lập khi chưa xác minh provenance.

## Chi tiết từng tệp

| File | Frame | Duration (s) | Speed candidate max | ECT đầu→cuối (°C) | Checksum errors |
|---|---:|---:|---:|---|---:|
| session_000001.jsonl | 117 | 28.999 | 6 | 45→48 | 0 |
| session_000002.jsonl | 5,781 | 1446.503 | 79 | 48→90 | 0 |
| session_000003.jsonl | 1,249 | 312.499 | 57 | 89→89 | 0 |
| session_000004.jsonl | 3,017 | 755.001 | 64 | 89→90 | 0 |
| session_000005.jsonl | 7,851 | 1963.997 | 62 | 90→92 | 0 |
| session_000006.jsonl | 923 | 230.750 | 29 | 70→89 | 0 |
| session_000007.jsonl | 6,389 | 1598.002 | 66 | 89→90 | 0 |
| session_000014.jsonl | 1,202 | 300.500 | 18 | 32→65 | 0 |
| session_000015.jsonl | 531 | 132.501 | 43 | 61→84 | 0 |
| session_000016.jsonl | 17,965 | 4494.502 | 87 | 80→89 | 0 |
| session_000017.jsonl | 3,492 | 873.500 | 65 | 78→89 | 0 |
| session_000018.jsonl | 17,976 | 4496.001 | 82 | 92→94 | 0 |
| session_000019.jsonl | 2,194 | 548.670 | 68 | 95→93 | 0 |
| session_000020.jsonl | 87 | 21.501 | 10 | 95→93 | 0 |
| session_000021.jsonl | 3,564 | 891.250 | 67 | 90→92 | 0 |
| session_000022.jsonl | 3,021 | 755.001 | 72 | 92→92 | 0 |
| session_000023.jsonl | 17,539 | 4391.172 | 89 | 93→94 | 0 |
| session_000024.jsonl | 7,790 | 1948.999 | 78 | 79→90 | 0 |

Manifest và kiểm kê do phân tích file đầu vào tạo ra. Các thống kê mô tả không thay thế kiểm chứng thực địa hay kiểm thử Model Harness trong repository.
