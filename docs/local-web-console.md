# 红薯采采本地 Web 采集台

红薯采采提供轻量浏览器控制台，用于关键词笔记采集、可选评论采集、实时进度展示和 Excel 下载。

## Start

```bash
cd /opt/hongshu-caicai
HOST=0.0.0.0 PYTHONPATH=/opt/hongshu-caicai .venv/bin/python web_app.py
```

Open:

```text
http://<server-ip>:18080
```

Default values:

| Variable | Default | Purpose |
|---|---:|---|
| `HOST` | `127.0.0.1` | Bind address. Use `0.0.0.0` for external access. |
| `PORT` | `18080` | Web console port. |
| `APP_USERNAME` | `admin` | Login username. |
| `APP_PASSWORD` | `admin` | Login password. |
| `COOKIES` | from `.env` | Logged-in Xiaohongshu Web Cookie. |

## UI workflow

1. Log in to the console.
2. Check Cookie status, use phone SMS login, or paste a new Cookie.
3. Send the SMS code, enter it in the console, and let the server save a usable Cookie.
4. Enter a keyword.
5. Choose post count, 1-50.
6. Optionally enable `同步采集评论`.
7. Start the job.
8. Watch live progress, timeline events, and metrics.
9. Download generated Excel files.

Outputs:

| Mode | File |
|---|---|
| Notes | `datas/excel_datas/<keyword>.xlsx` |
| Comments | `datas/excel_datas/<keyword>_comments.xlsx` |

## Cookie SMS login

The console can send a Xiaohongshu SMS verification code to the bound phone number. After the code is submitted, the server exchanges it for a session, validates `a1` and `web_session`, then writes `COOKIES=` to `.env`. Failed or expired SMS sessions do not overwrite the current Cookie.

SMS login endpoints:

| Endpoint | Method | Body | Purpose |
|---|---|---|---|
| `/api/cookie/phone/send` | POST | `phone=<phone>&zone=86` | Send SMS code and create a 5-minute login session. |
| `/api/cookie/phone/login` | POST | `session_id=<id>&code=<sms_code>` | Exchange SMS code for Cookie and save it to `.env`. |

Example:

```bash
curl -s -X POST http://127.0.0.1:18080/api/cookie/phone/send \
  -d 'phone=13800000000&zone=86'
```

The endpoints require console login. Use the browser UI for normal operation so session cookies are handled automatically.

## API

### Start a job

```bash
curl -s -X POST http://127.0.0.1:18080/api/start \
  -d 'keyword=榴莲&count=1&with_comments=on'
```

Response:

```json
{"id":"<job_id>"}
```

### Check status

```bash
curl -s 'http://127.0.0.1:18080/api/status?id=<job_id>'
```

Status fields:

| Field | Meaning |
|---|---|
| `state` | `queued`, `running`, `success`, or `failed` |
| `stage` | `init`, `search`, `notes`, `comments`, `complete`, or `failed` |
| `percent` | UI progress percentage |
| `message` | Current human-readable status |
| `events` | Bounded event timeline |
| `metrics.notes` | Note count exported |
| `metrics.comments` | Comment rows exported |
| `downloads` | Excel download descriptors |

### Download file

```bash
curl -OJ 'http://127.0.0.1:18080/download?file=榴莲_comments.xlsx'
```

The download endpoint only serves `.xlsx` files from `datas/excel_datas/` and rejects path traversal.

## Comment collection notes

Comment export depends on valid `xsec_token` and `xsec_source` context. Local fixes ensure keyword-search note URLs include `xsec_source=pc_search` and comment endpoints receive that value.

If a comments job finishes with `0` comments, possible causes:

- selected notes have no visible comments;
- Cookie is expired or restricted;
- Xiaohongshu API/risk control returns empty data;
- xsec token expired.

## Verification

```bash
cd /opt/hongshu-caicai
PYTHONPATH=/opt/hongshu-caicai .venv/bin/python -m py_compile web_app.py spider/spider.py xhs_utils/data_util.py apis/xhs_pc_apis.py
```

Expected smoke result from the current Cookie during setup: keyword `榴莲`, count `1`, comments enabled produced one note row and 94 comment rows.

## Security

The current external service has app login, but anyone with valid console credentials can trigger scraping with the configured Cookie. Keep the port limited to trusted networks and change default credentials before wider exposure.
