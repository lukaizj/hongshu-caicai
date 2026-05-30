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
2. Check Cookie status or paste a new Cookie.
3. Enter a keyword.
4. Choose post count, 1-50.
5. Optionally enable `同步采集评论`.
6. Start the job.
7. Watch live progress, timeline events, and metrics.
8. Download generated Excel files.

Outputs:

| Mode | File |
|---|---|
| Notes | `datas/excel_datas/<keyword>.xlsx` |
| Comments | `datas/excel_datas/<keyword>_comments.xlsx` |

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
