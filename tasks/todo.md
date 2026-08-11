# TODO

## GitHub Push Without Secrets
- [x] Lọc file local và config chứa token/cookie trước khi push public repo
- [x] Thêm ignore + file mẫu sạch cho config local
- [x] Tạo branch, commit toàn bộ code an toàn, rồi push sang repo `YouTube-OS`

## Audio Visualizer Preview Parity Fix
- [x] Audit root cause vì sao render video lệch so với preview
- [x] Vá spectrum render để dùng cùng layout hiển thị như preview
- [x] Thêm regression test chặn chuyện render bị cap layout nội bộ
- [x] Verify bằng pytest đúng module audio visualizer

## Automate Sidebar Dropdown
- [x] Audit vì sao `Short/Long` đang nằm trong page thay vì sidebar trái
- [x] Thêm automate subview vào panel state để sidebar control trực tiếp
- [x] Đổi item `Automate` ở sidebar thành dropdown `Short Video / Long Video`
- [x] Bỏ mode switcher thừa trong page `Automate`
- [x] Verify bằng eslint đúng các file vừa sửa

## Automate Sidebar UI Refresh
- [x] Thiết kế lại submenu `Automate` ở sidebar theo kiểu compact hơn
- [x] Gỡ block `Automation mode` thừa trong page `Automate`
- [x] Verify bằng eslint đúng file vừa sửa

## Automate Header Summary Refresh
- [x] Thiết kế lại cụm `Parent projects / Sessions` đầu trang theo kiểu compact hơn
- [x] Giữ nhịp UI đúng design system hiện có của màn Automate
- [x] Verify bằng eslint đúng file vừa sửa

## Short Automate Roxy Upload Fix
- [x] Audit root cause vì sao `Short Automate` không mở Roxy browser
- [x] Thêm route scan folder video để page `Automate` có dữ liệu batch thật
- [x] Nối `Short Automate` vào flow Roxy upload hiện có
- [x] Verify bằng lint, py_compile, route test

## Short Automate Schedule Step
- [x] Audit chỗ log schedule đang nói quá so với hành vi thật
- [x] Thêm `schedule_at` vào flow Roxy upload
- [x] Nối modal progress để chỉ báo `Scheduled` khi route trả về thật
- [x] Verify bằng lint, py_compile, route test

## Short Automate Schedule Timezone Fix
- [x] Audit root cause vì sao ngày giờ schedule bị lệch so với session UI
- [x] Vá chỗ serialize `schedule_at` để giữ nguyên giờ local user đã chọn
- [x] Normalize backend cho payload có timezone để không lệch sang UTC
- [x] Verify bằng lint + py_compile + route/service tests

## Short Automate Two-Tab Flow
- [x] Audit flow upload hiện tại để chốt cách mở tab riêng và đóng tab sau mỗi video
- [x] Thêm `2 tab max`, `30s checks`, và cooldown giữa các lượt cho short batch
- [x] Đổi queue UI để thấy lane/checks/schedule rõ hơn
- [x] Verify bằng eslint + py_compile + route/service tests

## Reply Center Replied Thread Popup
- [x] Audit luồng comment replied hiện tại và chốt dữ liệu thread đang thiếu
- [x] Thêm `replies[]` vào payload inbox + optimistic thread sau khi gửi reply
- [x] Bật popup conversation khi user bấm comment `replied`, bám design system
- [x] Verify bằng eslint + unit test

## Sora 1080p Denoise Timeline
- [x] Audit state machine hiện tại của Sora worker + backend postprocess
- [x] Thêm post-process thật: `denoise -> upscale 1080p -> save`
- [x] Đổi timeline UI sang `Generate -> Publish -> Remove Watermark -> Denoise -> Upscale 1080p -> Save to Folder`
- [x] Verify bằng lint + route test

## Sora Timeline + Error Copy Sync
- [x] Audit vì sao user vẫn thấy timeline label cũ ở màn job log
- [x] Thêm error copy màu đỏ rõ ràng cho case `unable_to_generate`
- [x] Verify bằng eslint + node check

## Parent Project Child Count Fix
- [x] Repro và chốt root cause count child bị lệch ở list parent
- [x] Vá load parent để tính count từ child data thật
- [x] Đồng bộ store để count tự update sau mỗi lần refresh children
- [x] Verify bằng eslint đúng file vừa sửa

## Reply Center Layout Polish
- [x] Review current Reply Center hierarchy and find the main visual imbalance
- [x] Re-layout the setup rail so sync/actions sit above prompt editing
- [x] Make inbox queue the dominant reading column and tighten workspace copy
- [x] Verify with frontend lint and typecheck

## Brand Channel + Global AI Settings
- [x] Tách `Brand Channel` thành config riêng theo từng parent project
- [x] Chuyển `OpenRouter key + fallback models` sang `Settings` dạng global
- [x] Cho `Reply Center` dùng global AI settings + brand config riêng từng project
- [x] Dọn UI `Reply Center` để bỏ panel config thừa
- [x] Verify bằng lint, typecheck, unit test

## Settings Layout Density Pass
- [x] Audit root cause layout `Settings` bị rỗng và thiếu hierarchy
- [x] Thiết kế lại `Settings` theo hướng control tower + dense app UI
- [x] Nén lại spacing, thêm summary band, và làm AI routing dễ scan hơn
- [x] Verify bằng eslint + typecheck

## OpenRouter Auto Verify Status
- [x] Đổi box `Last verify` sang status sống cho OpenRouter
- [x] Tự verify sau khi user nhập API key, rồi cập nhật sang `Connected`
- [x] Verify bằng eslint + typecheck

## Settings Panel Alignment Pass
- [x] Chuẩn hóa layout chung cho `OAuth / API Access` và `Extension Bridge`
- [x] Cân lại summary rows, input rhythm, và action alignment theo system UI
- [x] Verify bằng eslint + typecheck

## AI Routing Compact Pass
- [x] Thu nhỏ action verify ở `AI Reply Engine`, bỏ cảm giác button quá to
- [x] Đổi fallback models sang kiểu `+ Add fallback`, không render sẵn nhiều ô trống
- [x] Cho phép xoá từng fallback row thay vì dùng `No fallback`
- [x] Verify bằng eslint + typecheck

## OAuth Inline Status Pass
- [x] Đổi `OAuth / API Access` sang layout stacked theo mockup tham chiếu
- [x] Bỏ nút `Verify access`, thay bằng text status auto verify
- [x] Verify bằng eslint + typecheck

## Bridge Inline Layout Pass
- [x] Đổi `Extension Bridge` sang cùng layout stacked như `OAuth`
- [x] Giữ `Regenerate key`, nhưng bỏ summary rail cũ
- [x] Verify bằng eslint + typecheck

## Bridge Status Badge Pass
- [x] Thêm badge trạng thái extension connected/offline dựa trên state thật
- [x] Đổi màu nút `Regenerate key` để tách khỏi button mặc định
- [x] Verify bằng frontend checks và backend account-connect test

## Reply Center Console Removal
- [x] Gỡ block `Channel Console` khỏi rail trái trong `Reply Center`
- [x] Giữ các panel còn lại ổn định sau khi bỏ block
- [x] Verify bằng eslint + typecheck

## Reply Center Two Column Layout
- [x] Gỡ hẳn block `Brand Channel` còn sót trong `Reply Center`
- [x] Thu layout từ 3 cột về đúng 2 cột chính
- [x] Verify bằng eslint + typecheck

## Reply Center Header + Card Density
- [x] Bỏ header `Reply Center` ở top màn hình
- [x] Nén card comment trong `Inbox Queue` để danh sách gọn hơn
- [x] Verify bằng eslint + typecheck

## Reply Center Auto Reply UX Reset
- [x] Chốt lại UX `Manual` vs `AI Auto` ngay trong `Reply Center`
- [x] Bỏ cảm giác editor luôn bật, chỉ hiện textarea khi đang ở `Manual`
- [x] Cho `AI Auto` hiển thị trạng thái auto-reply rõ ràng khi app mở và khi có comment mới
- [x] Persist mode về config project hiện có, không tạo thêm setting rời
- [x] Verify bằng eslint + typecheck

## Reply Center Auto Reply Layout Rebuild
- [x] Dùng rulebook `ui-ux-pro-max` để chốt lại hierarchy của toàn màn auto reply
- [x] Bỏ cụm stat card đầu trang, thay bằng control rail + mode rail rõ hơn
- [x] Thiết kế lại queue card comment gọn nhưng vẫn đọc được trạng thái auto/review
- [x] Thiết kế lại workspace phải thành automation console + manual workspace đúng mode
- [x] Verify bằng eslint + typecheck

## Audio Visualizer Spectrum Smoothness
- [x] Audit lõi spectrum hiện tại ở preview và backend render
- [x] Chốt tuning chỉ tăng độ mượt + độ nhạy, không thêm style hay gradient
- [x] Tăng độ phân giải audio analysis và giảm smoothing quá lì
- [x] Đồng bộ lại motion/peak giữa preview và render
- [x] Verify bằng lint + unit test + smoke render

## Rust Spectrum Renderer Foundation
- [x] Audit lại kiến trúc render hiện tại và chốt hướng native path
- [x] Scaffold crate `rust/audio_spectrum_renderer`
- [x] Thêm bridge Python + env flag cho native renderer
- [x] Nối native renderer vào flow `spectrum_bars` với fallback sạch
- [x] Build thật + smoke render thật + unit test
- [x] Thử `Metal` backend có giữ state để tránh compile/init mỗi frame
- [x] Benchmark lại `python vs rust-cpu vs rust-metal`

## Audio Visualizer Render Speed + Preview Parity
- [x] Repro render chậm/fail với file audio thật
- [x] Đo bottleneck giữa audio analysis, frame rasterize, encode, composite
- [x] Chốt root cause timeout/tự fail khi render audio dài
- [x] Tối ưu pipeline render để nhanh hơn nhưng vẫn giữ đúng form preview
- [x] Verify lại bằng benchmark + render thật

## Audio Visualizer Render Stuck
- [x] Repro render job thật qua API với file audio dài
- [x] Chốt root cause progress đứng 0% ở nhánh `spectrum_bars`
- [x] Stream spectrum frame theo thời gian thực thay vì precompute cả job
- [x] Vá case chunk rỗng không crash renderer
- [x] Verify lại bằng test + poll job render thật

## MiniMax UI Text Fix
- [x] Audit root cause text model bị hiển thị sai ở màn MiniMax
- [x] Humanize label model để text đọc đúng, dễ nhìn
- [x] Vá select UI để không còn chồng chữ native/custom
- [x] Verify lại bằng lint frontend

## Reply Center Session Inbox
- [x] Audit flow `Load Inbox` session-mode và repro bằng API thật
- [x] Vá helper session để không nuốt lỗi comment
- [x] Mở rộng nguồn quét video/comment cho channel mới
- [x] Verify lại inbox sau patch
- [x] Thêm nền sync từ extension vào app cache
- [x] Chặn pair sai channel kiểu `YouTube`
- [x] Ưu tiên báo lỗi sync Studio thật thay vì inbox rỗng giả
- [x] Vá false positive `chưa đăng nhập` khi tab Studio thật vẫn đang mở
- [x] Bỏ yêu cầu user tự mở `Studio comments`, extension tự mở tab ẩn để sync nền

## Reply Center OAuth Bridge
- [x] Thêm màn `Settings` ở sidebar để nhập OAuth/API config và verify
- [x] Chuyển `YTB Connect` từ session-scrape sang OAuth bridge cho đúng profile anti-detect
- [x] Cho extension login Google/YouTube, chọn kênh, map `channel -> project`
- [x] Lưu `refresh token + channel mapping` ở desktop app, bỏ phụ thuộc tab Studio comment
- [x] Thêm `disconnect / remap` cho từng project đã pair
- [x] Chuyển `Reply Center` sang đọc/reply comment hoàn toàn bằng YouTube Data API
- [x] Thêm quota tracker nhẹ trong app để ước tính usage theo ngày

## Reply Center Auto Reply DNA
- [x] Thêm config `auto reply mode` cho từng project
- [x] Thêm `Channel DNA` và `Audience profile` để điều khiển tone reply theo từng kênh
- [x] Gộp `system prompt + DNA + audience` thành prompt thực tế gửi model
- [x] Tự draft comment mới khi bật `safe-only`
- [x] Tự reply comment an toàn, đẩy câu hỏi/negative/sensitive sang review
- [x] Vá merge polling để không auto-reply lặp lại comment cũ
- [x] Verify lại bằng lint, typecheck, unit test

### Scope Locked
- `Settings` là nơi duy nhất lưu `oauth client id`, `client secret`, `bridge key`, trạng thái verify.
- `YTB Connect` chỉ còn làm 3 việc: login đúng profile anti-detect, chọn kênh YouTube, map kênh vào project.
- `Reply Center` chỉ dùng YouTube Data API cho inbox + reply, không còn dựa vào `session_cookie`, hidden tab, hay scrape Studio.
- Mapping chuẩn: `1 project -> 1 connected YouTube channel`.
- Có `Disconnect` và `Remap` rõ ràng, không giữ mapping mập mờ.

### Existing Pieces To Reuse
- Reuse bridge hiện có ở `account_connect.py` và popup `YTB Connect`, nhưng đổi payload từ `session` sang `oauth token bundle`.
- Reuse route `youtube_reply.py` và UI `ReplyCenter/index.tsx`, nhưng cắt nhánh `provider_mode=session`.
- Reuse vùng edit parent/project hiện có để link sang Settings và hiển thị trạng thái connected.

### Explicitly Remove
- `session_cookie`
- `provider_mode=session`
- hidden tab sync Studio comments
- `youtube_session_bridge.mjs` cho Reply Center
- mọi copy/UI đang bảo user mở tab Studio comments

### New Data Model Needed
- App-level OAuth settings: `client_id`, `client_secret`, `verified_at`
- Channel connection record: `parent_project_id`, `channel_id`, `channel_name`, `google_account_email`, `access_token`, `refresh_token`, `token_expiry`, `connected_at`
- Optional quota snapshot theo ngày: `date_pt`, `estimated_units_used`

### UX Flow
- Sidebar thêm `Settings` icon bánh răng
- `Settings`: nhập OAuth/API config -> `Verify` -> `Connected/Failed`
- Extension: `Login Google` -> chọn kênh -> chọn project -> `Connect`
- App: lưu mapping -> `Reply Center` tự load bằng API
- User có thể `Disconnect` hoặc `Remap` bất cứ lúc nào

### Kill Criteria
- Sau khi chuyển OAuth xong, nếu flow nào còn bắt user mở `studio.youtube.com/.../comments` thì coi như chưa done

## Audio Visualizer Render Speed
- [x] Audit flow render hiện tại ở route + service
- [x] Xác định cổ chai chính trong pipeline FFmpeg
- [x] Kiểm tra phần cứng / encoder khả dụng trên máy
- [x] Chạy benchmark mini để lấy số liệu thật
- [x] Chốt plan tăng tốc theo 3 nấc: quick win, strong path, 10x path

## AI Image Output Naming + Folder Conflict Flow
- [x] Audit full save flow của `AIGen` từ UI tới backend route `ai_gen`
- [x] Đổi quy ước lưu ảnh generated/upscaled sang `image_001`, `image_002`... thống nhất theo folder
- [x] Thêm check folder đã có ảnh khi user chọn output folder
- [x] Hiện popup chọn cách xử lý: ghi đè, giữ cả 2, hoặc xoá ảnh cũ rồi thay ảnh mới
- [x] Verify đủ 4 case: folder rỗng, overwrite, keep both, replace all

## AI Gen Stat Tooltip
- [x] Thêm icon dấu hỏi cạnh `Completed`, `Failed`, `Pending`
- [x] Hover vào icon sẽ hiện tooltip giải thích ý nghĩa từng trạng thái
- [x] Verify lại UI `AIGen` bằng eslint đúng file

## AI Gen Override Modal Always Show
- [x] Repro và chốt root cause vì sao popup override không hiện mỗi lần Generate
- [x] Bỏ cơ chế nhớ lựa chọn cũ theo folder trong `AIGen`
- [x] Ép popup luôn mở mỗi lần Generate khi có output folder
- [x] Rút UI popup về đúng 3 button: `Override`, `Keep both`, `Close`
- [x] Verify lại `AIGen` bằng eslint đúng file

## Sora Publish + No Watermark Flow Restore
- [x] Repro và chốt root cause flow publish/remove watermark bị mất ở extension
- [x] Khôi phục bridge network observer + publish flow theo bản tham chiếu
- [x] Cho extension ưu tiên `no_watermark_url`, fallback `downloadable_url`, cuối cùng mới `job-upload`
- [x] Verify bằng syntax check JS + unit test route Sora

## Sora Extension Publish Flow Restore
- [x] Repro và chốt root cause flow Sora không tự publish/no-watermark nữa

## Sora Session Reset + Unable Generate Retry
- [x] Phát tín hiệu batch mới từ backend để extension tự clear live session
- [x] Bổ sung detect `unable_to_generate` theo network/API, không parse UI text
- [x] Áp policy retry 3 lần nhanh + cooldown 1-3 phút lặp lại tới khi xong hoặc user stop
- [x] Verify bằng JS checks + Python tests liên quan

## Sora Output Folder Quick Open
- [x] Thêm button `Open Folder` cho SoraGen, bám đúng design system hiện tại
- [x] Nối button với route backend mở folder output trên macOS
- [x] Verify bằng eslint + py_compile + unittest route

## Reply Center Quota Tracker
- [x] Audit chỗ call YouTube API thật để biết nơi cộng quota estimate
- [x] Lưu snapshot quota theo ngày PT trong state account connect
- [x] Expose quota API cho frontend và hiện usage ở Settings + Reply Center
- [x] Verify bằng unit test backend + eslint frontend
- [x] Khôi phục bridge network observer trong extension theo code tham chiếu
- [x] Nối lại flow publish -> remove watermark -> báo `job-done` về app
- [x] Giữ fallback `job-upload` nếu không lấy được URL ổn định
- [x] Verify bằng route test + syntax check JS extension

## Sora Server 500 Fallback
- [x] Repro và chốt root cause job fail dù worker gen xong
- [x] Ép nhánh `job-done` check lỗi thật từ backend
- [x] Fallback sang `job-upload` nếu backend không kéo được URL
- [ ] Verify lại bằng syntax check + route test + DB scenario

## Sora Multi-worker No Watermark Investigation
- [x] Kiểm tra DB thật của 3 job gần nhất
- [x] Chốt symptom: 3 job đều `done` nhưng `permalink` rỗng, tức nhánh publish/no-watermark bị skip
- [x] Chốt root cause hypothesis: publish/draft refresh đang phụ thuộc quá cứng vào captured auth headers từ page, dễ hụt khi nhiều worker/tab chạy song song
- [x] Vá worker để publish + draft refresh fallback sang cookie-only same-origin request
- [x] Tăng thời gian chờ no-watermark draft cho case multi-worker
- [x] Mở rộng parser response của proxy Snapzora
- [x] Verify syntax `sora-content.js` + `background.js`

## Review
- Root cause bug count child: `ParentProjects` chỉ fetch parent list rồi gọi `toProjectState(..., 0)`, nên parent như `SeniorForge 365` dễ hiện `0 children` dù DB có child thật. Count đó còn lệch tiếp sau mỗi lần `setChildren()` vì store không recompute lại `parentProjects`.
- Đã vá `ParentProjects` để load song song `/api/parent-projects/` và `/api/child-projects/`, rồi đếm `parent_project_id` từ dữ liệu child thật trước khi set UI.
- Đã vá store ở `setParentProjects` và `setChildren` để `childCount` tự sync theo `childProjects`, nên sau này create/delete/fetch child xong badge ở list cha và sidebar sẽ update đúng hơn.
- Verify: `./node_modules/.bin/eslint src/pages/Projects/ParentProjects.tsx src/store/app.store.ts src/pages/Projects/ChildProjects.tsx` pass.
- Root cause MiniMax UI: label model đang lấy raw machine id như `speech-2.5-hd-preview`, nên text nhìn sai ngữ nghĩa; riêng select Minimax cũng chưa set lớp ẩn native chặt như các chỗ khác nên dễ bị chồng chữ trên macOS/Tauri.
- Đã humanize model label từ API/proxy sang dạng đọc được như `Speech 2.5 HD Preview`, và fallback default cũng hiển thị theo format này.
- Đã thêm `appearance-none`, nền trong suốt, text fill trong suốt và `min-w-0 flex-1` cho select display của MiniMax để chặn native text leak + giữ truncate ổn định.
- Verify: `./node_modules/.bin/eslint src/pages/Projects/tools/AIAudio.tsx src/lib/audioService.ts` pass.
- Lưu ý: `npm run lint` toàn frontend vẫn fail sẵn vì lỗi cũ ở các file khác ngoài scope patch này.
- `Load Inbox` session-mode hiện có thể trả `Loaded 0 comment(s)` giả vì helper đang `catch` lỗi `yt.getComments()` rồi `continue`.
- Helper cũng đang phụ thuộc vào vài video gần nhất của feed kênh, nên rất dễ miss comment test.
- Đã vá helper để fallback `TOP_COMMENTS`, quét rộng hơn, có RSS fallback, và trả lỗi thật khi không thấy video public.
- Với channel `James Anh`, RSS upload feed hiện đang rỗng, nên case test hiện không có video public để session helper quét.
- Extension giờ đã lấy cookie browser bằng `chrome.cookies`, chuẩn bị cho nhánh creator-side fetch mạnh hơn.
- Đã thêm `background.js` cho `YTB Connect`, tự sync snapshot inbox từ Studio về app cache theo chu kỳ.
- `youtube_reply/inbox` giờ ưu tiên đọc cache do extension sync trước khi fallback sang helper cũ.
- Popup detect giờ chặn hẳn label generic kiểu `YouTube`, không cho pair sai thành channel rác nữa.
- Nếu sync Studio thất bại mà fallback session helper vẫn không ra comment, `Reply Center` sẽ hiện lỗi thật từ cache thay vì view rỗng im lặng.
- Root cause mới: heuristic `logged out` ở extension cũ quét `innerHTML` quá rộng, nên Studio page thật có thể bị nhận nhầm là trang login Google.
- Hidden sync giờ không còn phụ thuộc việc user tự mở `studio.youtube.com/.../comments`; extension sẽ thử route theo channel trước, rồi fallback sang inbox generic trong tab ẩn.
- Hướng product mới đã chốt: dùng `OAuth/API` cho read/reply comment, extension chỉ còn là cầu nối login đúng profile anti-detect và map kênh vào project.
- Reply Center giờ có `Channel DNA`, `Audience profile`, `system prompt`, và `auto reply mode` theo từng project; khi bật `safe-only`, comment praise/neutral đơn giản sẽ auto draft + auto reply, còn câu hỏi/negative/sensitive sẽ chỉ được xếp vào review.
- `audio_visualizer.py` hiện render bằng `showfreqs + overlay + libx264`, tức filter CPU + encode CPU.
- Máy hiện có `h264_videotoolbox`, `hevc_videotoolbox`, `prores_videotoolbox`.
- Benchmark 20s audio @1080p: `libx264` ~18.38s, `h264_videotoolbox` ~6.92s, nhanh hơn ~2.66x.
- Sau tối ưu filter graph, benchmark 20s audio @1080p + `h264_videotoolbox`: ~3.21s.
- Visual layer giờ render nội bộ `960x240`, rồi upscale lên `1920x360`, nhưng file xuất vẫn `1920x1080`.
- Root cause bug lần này: preview đang là canvas custom mirrored bars, còn render backend lại là `showfreqs` band đơn của FFmpeg nên lệch form; patch mới chuyển render `spectrum_bars` sang mirrored bar layout + glow gần preview hơn.
- Render `spectrum_bars` giờ đi qua renderer backend riêng, port theo pipeline preview rồi mới ghép/encode bằng FFmpeg + `h264_videotoolbox`.
- Verify mới nhất: render mẫu 8s qua đúng flow `render_visualizer()` pass, extract frame ra đã bám gần preview rõ rệt.
- Root cause bug render mới nhất: `spectrum_bars` backend precompute toàn bộ state của audio dài trước khi báo progress, nên job đứng `0%` rất lâu và nhìn như chết; thêm nữa case chunk rỗng có thể làm renderer nổ ở cuối buffer.
- Đã đổi renderer sang stream từng frame vào FFmpeg, progress cập nhật ngay trong pha overlay, và composite progress không còn reset ngược về `0%`.
- Verify mới nhất: `py_compile` pass, `python -m unittest tests.test_audio_visualizer -v` pass, poll job render thật với file `A_ [upbeat] Welcome .mp3` đã thấy progress nhích `1% -> 4%` thay vì đứng im.
- Root cause chậm/fail đợt này là tổ hợp 3 lớp:
  1. frontend tự `fail` sau đúng 10 phút dù backend vẫn còn chạy,
  2. backend render `spectrum_bars` đi qua Python raster + overlay encode + final composite encode, tức double-pass rất nặng,
  3. bước overlay từng dùng `stderr=PIPE` mà không hạ loglevel, dễ nghẹn khi job dài.
- Đã vá:
  - poll frontend chuyển từ `absolute 10m timeout` sang `stall 10m + hard cap 60m`,
  - renderer spectrum batch hóa state processing, bỏ `PIL` khỏi hot path chính,
  - overlay internal render hạ còn `960x540` rồi upscale về layout preview khi ghép final,
  - overlay ffmpeg thêm `-v error -nostats`.
- Verify mới nhất:
  - `cd frontend && ./node_modules/.bin/eslint src/pages/Projects/tools/AudioVisualizer.tsx` pass
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_audio_visualizer -v` pass
  - benchmark `render_spectrum_overlay_video()` với clip 20s @ `960x540` nội bộ: `~28.9s`
  - API render thật với clip 20s không còn fail oan, progress đi từ `0 -> 90%` thay vì chết ở timeout 10 phút cũ
- Tối ưu tiếp theo đã làm:
  - bỏ hẳn nhánh `overlay mp4 -> composite final`, chuyển `spectrum_bars` sang one-pass final encode,
  - ffmpeg final nhận rawvideo trực tiếp từ renderer preview-parity rồi encode ra file cuối luôn.
- Verify mới nhất sau one-pass:
  - benchmark one-pass clip 20s: `~13.65s total`
  - API render thật clip 20s: `done` ở khoảng `14s`, progress nhảy `16 -> 35 -> 54 -> 72 -> 91 -> 100`
- Benchmark 20s audio @720p + `h264_videotoolbox`: ~2.8s.
- Kết luận: đổi encoder + thêm profile render nhanh sẽ ăn ngay; muốn 10x nữa thì phải tách khỏi FFmpeg visual filter CPU hiện tại.
- Root cause phần spectrum còn hơi “cứng”: FFT đang thấp (`1024`), browser/backend smoothing đầu vào còn nặng, `monstercat + blur` smear hơi quá tay nên cột ăn nhịp chưa đủ nhạy.
- Đã tune lại theo hướng audioMotion nhưng vẫn giữ nguyên đúng 1 style `spectrum_bars`:
  - FFT tăng lên `2048`
  - set `min/max decibels` nhạy hơn
  - smoothing đầu vào đổi sang `attack/release` thay vì blend cố định
  - giảm smear ở `monstercat` và `blur`
  - thêm một lớp `shape blend` rất nhẹ giữa các cột để chuyển động mềm hơn
  - peak rise/decay cũng được tune lại cho tự nhiên hơn
- Verify mới nhất:
  - `cd frontend && ./node_modules/.bin/eslint src/pages/Projects/tools/AudioVisualizer.tsx` pass
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_audio_visualizer -v` pass
  - smoke render 30 frame đầu từ clip 20s pass, `72` bins / `72` peaks, không lỗi runtime
- Root cause flicker khi audio đang nói rồi ngắt nhịp ngắn: preview `spectrum_bars` đang có ngưỡng `maxVal < 2 -> return`, nên visual bị tắt hẳn thay vì decay mềm trong khoảng nghỉ ngắn.
- Đã bỏ early-return đó để cột tiếp tục giảm mềm rồi bật lại tự nhiên khi audio quay lại.
- Đã scaffold `rust/audio_spectrum_renderer` và bridge `rust_audio_renderer.py` để mở đường sang native render path cho `spectrum_bars`.
- `audio_visualizer.py` giờ hỗ trợ native renderer qua `AUTOCAPCUT_AUDIO_VIS_ENGINE=rust`; mặc định `auto` vẫn giữ Python path ổn định để tránh regression hiệu năng.
- Verify mới nhất cho native path:
  - `cargo build --release` pass tại `rust/audio_spectrum_renderer`
  - `PYTHONPATH=. .venv/bin/python -m unittest tests.test_audio_visualizer -v` pass `8/8`
  - smoke render native path pass, tạo `/tmp/rust_spectrum_smoke.mp4`
- Benchmark thật với clip 20s:
  - Rust native scaffold đầu tiên: `~18.46s`
  - Python one-pass tối ưu trước đó: `~13.65s`
- Kết luận: native bridge đã cắm xong và chạy được thật, nhưng chưa phải fast path cuối cùng; bước tăng tốc lớn tiếp theo phải là `Metal` backend cho phần render frame, không chỉ mới đổi Python sang Rust CPU.
- Đã thêm `Metal` raster path theo kiểu giữ `device/pipeline/buffer` 1 lần, không còn compile shader mỗi frame.
- Benchmark renderer trực tiếp với payload nhỏ hiện tại:
  - `rust-cpu`: `~0.62s`
  - `rust-metal`: `~1.61s`
- Benchmark full render 20s @ `1080p` hiện tại:
  - `python`: `~41.36s`
  - `rust-cpu`: `~76.02s`
  - `rust-metal`: `~76.20s`
- Kết luận mới: bottleneck thật hiện không nằm ở chỗ khởi tạo renderer nữa; native path vẫn thua Python one-pass hiện tại ở full pipeline, nên chưa được bật mặc định.
- Root cause `AIGen` save tên file chưa ổn: route `ai_gen` đang auto-save theo `prompt + seed + timestamp`, nên không ra dãy `image_001`, `image_002` và còn dễ loạn khi batch chạy song song.
- Đã chuyển save generated/upscaled sang index cố định `image_001`, `image_002`... ở backend và thêm reserve range để batch song song không đụng số.
- `AIGen` giờ inspect output folder trước khi chạy; nếu folder đã có ảnh thì bật popup cho 3 mode: `Overwrite`, `Keep both`, `Replace all`.
- `Replace all` sẽ xoá toàn bộ ảnh cũ trong folder rồi lưu lại từ `image_001`; `Overwrite` giữ ảnh dư cũ; `Keep both` nhảy sang số tiếp theo.
- Verify mới:
  - `cd frontend && ./node_modules/.bin/eslint src/pages/Projects/tools/AIGen.tsx` pass
  - `.venv/bin/python -m unittest tests.test_ai_gen_routes` pass `5/5`
  - `cd frontend && ./node_modules/.bin/tsc -b --pretty false` vẫn fail vì lỗi cũ ngoài scope ở `audioService.ts`, `AIAudio.tsx`, `SoraGen.tsx`
- Đã thêm icon `?` cạnh `Completed`, `Failed`, `Pending` trong footer stats của `AIGen`.
- Hover hoặc focus vào icon sẽ hiện tooltip guide ngắn để user hiểu ý nghĩa từng trạng thái.
- Verify mới:
  - `cd frontend && ./node_modules/.bin/eslint src/pages/Projects/tools/AIGen.tsx` pass
- Root cause popup override không hiện mỗi lần: `AIGen` đang nhớ conflict mode theo folder trong local state/localStorage, nên sau lần chọn đầu nó tự bỏ qua popup ở các lần `Generate` tiếp theo.
- Đã bỏ cache lựa chọn cũ theo folder; giờ mỗi lần bấm `Generate` có output folder là popup sẽ luôn bật để user chọn lại.
- Đã rút modal về kiểu card gọn hơn, chỉ còn 3 nút `Override`, `Keep both`, `Close`; `Override` map sang hành vi xoá ảnh cũ rồi thay bằng batch mới.
- Verify mới:
  - `cd frontend && ./node_modules/.bin/eslint src/pages/Projects/tools/AIGen.tsx` pass
- Root cause `Sora Gen` không connect: desktop UI đang poll `/api/sora/connection-status`, nhưng repo chưa có Sora worker thực sự nào trong extension gọi `/api/sora/heartbeat` và `/api/sora/next-job`, nên trạng thái luôn kẹt `Not connected`.
- Đã vá theo hướng giữ extension worker:
  - backend `sora` thêm `verify-key` để extension tự check bridge key với desktop app,
  - backend thêm `job-upload` để extension upload trực tiếp blob MP4 khi không có stable download URL,
  - extension `autocapcut-flow-helper` thêm Sora worker panel với ô nhập bridge key, auto verify, status `Connected`, log worker, và nút mở Sora,
  - extension thêm content script `sora-content.js` để heartbeat, poll job, tự fill prompt, click generate theo heuristic, lấy video blob và upload về app,
  - `SoraGen.tsx` đổi copy/UI sang English, bridge key rõ hơn, tone màu bám design system teal/orange hơn, footer stats có tooltip giống `AIGen`, và `Clear` xoá luôn prompt input.
- Verify mới:
  - `cd frontend && ./node_modules/.bin/eslint src/pages/Projects/tools/SoraGen.tsx` pass
  - `node --check extensions/autocapcut-flow-helper/sora-content.js` pass
  - `node --check extensions/autocapcut-flow-helper/sora-panel.js` pass
  - `.venv/bin/python -m unittest tests.test_sora_routes` pass `3/3`
  - `.venv/bin/python -m pytest ...` chưa chạy được vì `.venv` hiện thiếu `pytest`

## Automate Short Foundation
- [x] Audit existing navigation and view structure
- [x] Add sidebar entry and main view wiring for Automate
- [x] Build Automate page with Short/Long tabs
- [x] Build Short Automate session-first UI scaffold
- [x] Verify lint/build for touched frontend files  (lint pass, global build blocked by existing TS errors in audioService.ts, AIAudio.tsx, SoraGen.tsx)

## Short Automate Roxy session bug
- [x] Reproduce root cause from code/logs
- [x] Fix same-profile concurrent open flow
- [x] Verify with tests/checks
- [x] Update lessons if needed

## Self-hosted Web App — Milestone 1
- [ ] Serve the production React SPA from FastAPI
- [ ] Centralize same-origin API/WebSocket URLs and add Vite proxying
- [ ] Add a safe managed media workspace and media records
- [ ] Add media upload/library/preview/download/delete APIs
- [ ] Build the browser Media Picker
- [ ] Adapt Audio Visualizer backend to media IDs and workspace outputs
- [ ] Replace Audio Visualizer Tauri dialogs with browser media selection
- [ ] Add one-command web startup and self-host documentation
- [ ] Verify upload → FFmpeg render → preview/download end to end

## Self-hosted Web App — Full Migration
- [ ] Migrate every remaining Tauri file dialog to Media Library/browser upload
- [ ] Replace every hard-coded frontend HTTP/WebSocket localhost URL
- [ ] Remove CapCut-only navigation, routers, services, and default dependencies
- [ ] Make extension bridge URLs configurable for the self-hosted origin
- [ ] Verify every retained page and tool in the browser
- [ ] Verify a clean production build and one-command self-host startup
- [ ] Run public-repository secret and generated-media scan

## Self-hosted Web App — Review
- Implementation pending. The task is complete only after both the vertical slice and full migration checklists pass. Design: `docs/superpowers/specs/2026-08-11-self-hosted-web-app-design.md`.
- Milestone plan: `docs/superpowers/plans/2026-08-11-web-foundation-audio-visualizer.md`.
