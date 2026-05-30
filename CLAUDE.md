# 红薯采采 Local Notes

## Current local additions

This checkout runs 红薯采采, a lightweight local browser console for keyword note collection and optional comment export.

- Web entry: `web_app.py`
- Bind defaults: `HOST=127.0.0.1`, `PORT=18080`
- Current external launch pattern:
  ```bash
  cd /opt/hongshu-caicai
  HOST=0.0.0.0 PYTHONPATH=/opt/hongshu-caicai .venv/bin/python web_app.py
  ```
- Browser URL: `http://<server-ip>:18080`

## Web console behavior

- `GET /` serves the Xiaohongshu-style data console.
- `POST /api/start` starts a background job and returns `{id}`.
- `GET /api/status?id=<job_id>` returns progress JSON.
- `GET /download?file=<xlsx>` downloads generated Excel from `datas/excel_datas/` with path traversal checks.

## Data flow

1. Web form submits keyword, count, and optional comments flag.
2. `run_job()` loads `.env` `COOKIES` via `xhs_utils.common_util.init()`.
3. `Data_Spider.spider_some_search_note()` searches notes and exports `{keyword}.xlsx`.
4. If comments enabled, `Data_Spider.spider_some_note_comments()` fetches comments for returned note URLs and exports `{keyword}_comments.xlsx`.

## Local fixes applied

- Search-generated note URLs include `xsec_source=pc_search` so comment APIs receive valid context.
- Comment API calls pass `xsec_source` through top-level and sub-comment endpoints.
- `handle_comment_info()` tolerates optional/missing comment fields so one malformed comment does not drop sibling rows.
- Comment export reports zero-comment cases through progress events instead of silently looking successful.

## Verification commands

```bash
cd /opt/hongshu-caicai
PYTHONPATH=/opt/hongshu-caicai .venv/bin/python -m py_compile web_app.py spider/spider.py xhs_utils/data_util.py apis/xhs_pc_apis.py
```

Quick API smoke test:

```bash
curl -s -X POST http://127.0.0.1:18080/api/start \
  -d 'keyword=榴莲&count=1&with_comments=on'
```

Then poll:

```bash
curl -s 'http://127.0.0.1:18080/api/status?id=<job_id>'
```

## Safety notes

- Current external listener has app login but still holds a live Xiaohongshu Cookie after login.
- Keep `.env` private. It contains logged-in Xiaohongshu Cookie.
- Comments collection creates more requests than note search; test with 1-10 posts first.
