import base64
import hashlib
import hmac
import html
import json
import os
import threading
import time
import urllib.parse
import uuid
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from spider.spider import Data_Spider
from xhs_utils.common_util import init
from xhs_utils.cookie_util import trans_cookies

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "18080"))
APP_USERNAME = os.environ.get("APP_USERNAME", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "admin")
APP_SESSION_SECRET = os.environ.get("APP_SESSION_SECRET") or base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
APP_SESSION_COOKIE = "hongshu_caicai_session"
APP_SESSION_MAX_AGE = 12 * 60 * 60
MAX_COUNT = 50
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
EXCEL_DIR = os.path.join(PROJECT_DIR, "datas", "excel_datas")
ENV_PATH = os.path.join(PROJECT_DIR, ".env")
JOBS = {}
JOBS_LOCK = threading.Lock()
MAX_EVENTS = 80


def encode_session(username):
    expires = int(time.time()) + APP_SESSION_MAX_AGE
    payload = f"{username}:{expires}"
    signature = hmac.new(APP_SESSION_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    token = f"{payload}:{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(token).decode("ascii")


def decode_session(token):
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        username, expires, signature = decoded.rsplit(":", 2)
        payload = f"{username}:{expires}"
        expected = hmac.new(APP_SESSION_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return ""
        if int(expires) < int(time.time()):
            return ""
        return username
    except Exception:
        return ""


def parse_cookie_header(header):
    cookie = SimpleCookie()
    try:
        cookie.load(header or "")
    except Exception:
        return {}
    return {key: morsel.value for key, morsel in cookie.items()}


def session_cookie_header(username):
    token = encode_session(username)
    return f"{APP_SESSION_COOKIE}={token}; Max-Age={APP_SESSION_MAX_AGE}; Path=/; HttpOnly; SameSite=Lax"


def clear_session_cookie_header():
    return f"{APP_SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax"


def validate_keyword(keyword):
    keyword = keyword.strip()
    if not keyword:
        raise ValueError("关键词不能为空")
    if len(keyword) > 80:
        raise ValueError("关键词不能超过 80 个字符")
    if "/" in keyword or "\\" in keyword or ".." in keyword:
        raise ValueError("关键词不能包含路径字符")
    return keyword


def validate_count(raw_count):
    try:
        count = int(raw_count)
    except (TypeError, ValueError):
        raise ValueError("数量必须是整数")
    if count < 1 or count > MAX_COUNT:
        raise ValueError(f"数量必须在 1 到 {MAX_COUNT} 之间")
    return count


def validate_cookie_string(cookies_str):
    cookies_str = (cookies_str or "").strip()
    if not cookies_str:
        raise ValueError("Cookie 不能为空")
    cookies = trans_cookies(cookies_str)
    missing = [key for key in ("a1", "web_session") if not cookies.get(key)]
    if missing:
        raise ValueError("Cookie 缺少必要字段：" + ", ".join(missing))
    return cookies_str


def mask_cookie(cookies_str):
    if not cookies_str:
        return ""
    cookies = trans_cookies(cookies_str)
    keys = [key for key in ("a1", "web_session", "webId", "gid", "websectiga") if cookies.get(key)]
    return " / ".join(f"{key}=***{cookies[key][-6:]}" for key in keys)


def load_cookie_string():
    cookies_str, _ = init()
    return (cookies_str or "").strip()


def get_cookie_status():
    cookies_str = load_cookie_string()
    if not cookies_str:
        return {"configured": False, "message": "未配置 Cookie", "summary": ""}
    try:
        cookies = trans_cookies(cookies_str)
        missing = [key for key in ("a1", "web_session") if not cookies.get(key)]
        if missing:
            return {"configured": False, "message": "Cookie 缺少字段：" + ", ".join(missing), "summary": mask_cookie(cookies_str)}
        return {"configured": True, "message": "Cookie 已配置", "summary": mask_cookie(cookies_str)}
    except Exception as exc:
        return {"configured": False, "message": f"Cookie 解析失败：{exc}", "summary": ""}


def quote_env_value(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def save_cookie_string(cookies_str):
    cookies_str = validate_cookie_string(cookies_str)
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as file:
            lines = file.read().splitlines()
    written = False
    new_lines = []
    for line in lines:
        if line.startswith("COOKIES="):
            new_lines.append("COOKIES=" + quote_env_value(cookies_str))
            written = True
        else:
            new_lines.append(line)
    if not written:
        new_lines.append("COOKIES=" + quote_env_value(cookies_str))
    with open(ENV_PATH, "w", encoding="utf-8") as file:
        file.write("\n".join(new_lines).rstrip() + "\n")
    os.environ["COOKIES"] = cookies_str


def clear_cookie_string():
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as file:
            lines = file.read().splitlines()
    new_lines = [line for line in lines if not line.startswith("COOKIES=")]
    with open(ENV_PATH, "w", encoding="utf-8") as file:
        file.write("\n".join(new_lines).rstrip() + ("\n" if new_lines else ""))
    os.environ.pop("COOKIES", None)


def now_ts():
    return time.strftime("%H:%M:%S")


def update_job(job_id, **fields):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_at"] = time.time()


def add_event(job_id, title, detail="", level="info"):
    event = {"time": now_ts(), "title": title, "detail": detail, "level": level}
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.setdefault("events", []).append(event)
        job["events"] = job["events"][-MAX_EVENTS:]
        job["updated_at"] = time.time()


def get_job(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        return json.loads(json.dumps(job, ensure_ascii=False))


def create_job(keyword, count, with_comments):
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "state": "queued",
        "keyword": keyword,
        "count": count,
        "with_comments": with_comments,
        "stage": "queued",
        "percent": 0,
        "message": "任务已排队",
        "events": [],
        "downloads": [],
        "error": "",
        "metrics": {"notes": 0, "comments": 0, "failed_comments": 0, "skipped_comments": 0},
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    add_event(job_id, "准备任务", f"关键词：{keyword}，目标帖子：{count}")
    return job_id


def run_job(job_id, keyword, count, with_comments):
    try:
        update_job(job_id, state="running", stage="init", percent=5, message="读取 Cookie 与输出目录")
        add_event(job_id, "读取配置", "从 .env 加载 COOKIES")
        cookies_str, base_path = init()
        if not cookies_str:
            raise ValueError(".env 缺少 COOKIES")

        spider = Data_Spider()

        def progress_callback(stage, current, total, info):
            total = total or 1
            if stage == "search":
                percent = 25
                message = f"搜索完成，返回 {current} 条帖子"
                update_job(job_id, stage="search", percent=percent, message=message)
                add_event(job_id, "搜索笔记", message)
            elif stage == "note_detail":
                percent = 35 + int((current / total) * 25)
                ok = "成功" if info.get("success") else "失败"
                message = f"解析笔记 {current}/{total}"
                update_job(job_id, stage="notes", percent=min(percent, 60), message=message)
                add_event(job_id, "解析详情", f"{current}/{total} {ok}", "info" if info.get("success") else "warn")
            elif stage == "comments":
                percent = 60 + int((current / total) * 30)
                added = info.get("added", 0)
                raw = info.get("raw", 0)
                ok = info.get("success")
                message = f"评论采集中 {current}/{total}"
                update_job(job_id, stage="comments", percent=min(percent, 90), message=message)
                level = "info" if ok else "warn"
                add_event(job_id, "采集评论", f"{current}/{total}，接口返回 {raw} 条，写入 {added} 条", level)

        update_job(job_id, stage="search", percent=12, message="搜索关键词帖子")
        add_event(job_id, "搜索笔记", "请求小红书搜索结果")
        note_list, success, msg = spider.spider_some_search_note(
            keyword,
            count,
            cookies_str,
            base_path,
            "excel",
            progress_callback=progress_callback,
        )
        if not success:
            raise RuntimeError(str(msg))

        note_filename = f"{keyword}.xlsx"
        note_path = os.path.join(base_path["excel"], note_filename)
        if not os.path.exists(note_path):
            raise RuntimeError("笔记 Excel 文件未生成")

        downloads = [{"label": "下载笔记 Excel", "file": note_filename}]
        update_job(
            job_id,
            stage="notes",
            percent=60 if with_comments else 95,
            message=f"笔记导出完成：{len(note_list)} 条",
            downloads=downloads,
            metrics={"notes": len(note_list), "comments": 0, "failed_comments": 0, "skipped_comments": 0},
        )
        add_event(job_id, "生成笔记文件", note_filename)

        comment_count = 0
        failed_urls = []
        skipped_comments = 0
        if with_comments:
            update_job(job_id, stage="comments", percent=62, message="开始采集评论")
            add_event(job_id, "采集评论", "逐帖拉取一级评论与二级评论")
            comment_filename = f"{keyword}_comments.xlsx"
            comment_count, failed_urls, skipped_comments = spider.spider_some_note_comments(
                note_list,
                cookies_str,
                base_path,
                f"{keyword}_comments",
                progress_callback=progress_callback,
            )
            comment_path = os.path.join(base_path["excel"], comment_filename)
            if os.path.exists(comment_path):
                downloads.append({"label": "下载评论 Excel", "file": comment_filename})
            if comment_count == 0:
                add_event(job_id, "评论为空", "接口返回 0 条可写入评论，可能是无评论、Cookie/参数限制或接口风控", "warn")
            if failed_urls:
                add_event(job_id, "部分评论失败", f"失败帖子数：{len(failed_urls)}", "warn")

        update_job(
            job_id,
            state="success",
            stage="complete",
            percent=100,
            message="采集完成，文件已生成",
            downloads=downloads,
            metrics={
                "notes": len(note_list),
                "comments": comment_count,
                "failed_comments": len(failed_urls),
                "skipped_comments": skipped_comments,
            },
        )
        add_event(job_id, "完成归档", "Excel 文件已准备下载")
    except Exception as exc:
        update_job(job_id, state="failed", stage="failed", percent=100, message="采集失败", error=str(exc))
        add_event(job_id, "任务失败", str(exc), "error")


def render_page():
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>红薯采采 · 小红书采集台</title>
  <style>
    :root {
      --paper: #f8f1e8;
      --paper-deep: #eadfce;
      --ink: #201816;
      --ink-soft: #786a62;
      --muted: #a08e80;
      --xhs-red: #ff2442;
      --seal-red: #b9162d;
      --gold: #b88746;
      --card: rgba(255, 250, 241, .82);
      --line: rgba(32, 24, 22, .14);
      --shadow: rgba(80, 34, 24, .16);
      --font-display: "Iowan Old Style", "Songti SC", "Noto Serif CJK SC", "Source Han Serif SC", "STSong", serif;
      --font-body: "Avenir Next", "Gill Sans", "PingFang SC", "Microsoft YaHei", sans-serif;
      --font-mono: "Cascadia Mono", "SFMono-Regular", "Consolas", monospace;
    }
    * { box-sizing: border-box; }
    body {
      min-height: 100vh;
      margin: 0;
      color: var(--ink);
      font-family: var(--font-body);
      background:
        radial-gradient(circle at 10% 10%, rgba(255, 36, 66, .12), transparent 28rem),
        radial-gradient(circle at 88% 80%, rgba(32, 24, 22, .08), transparent 26rem),
        repeating-linear-gradient(0deg, rgba(32, 24, 22, .035) 0, rgba(32, 24, 22, .035) 1px, transparent 1px, transparent 32px),
        var(--paper);
    }
    .shell { max-width: 1180px; margin: 0 auto; padding: 38px 24px 52px; }
    .masthead { display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; margin-bottom: 30px; }
    .overline { font-family: var(--font-mono); letter-spacing: .18em; font-size: 12px; color: var(--seal-red); text-transform: uppercase; }
    h1 { margin: 8px 0 8px; font-family: var(--font-display); font-size: clamp(42px, 7vw, 86px); line-height: .92; letter-spacing: -.04em; }
    .subtitle { margin: 0; color: var(--ink-soft); font-size: 17px; }
    .mast-actions { display: flex; gap: 10px; align-items: center; }
    .status-pill { min-width: 118px; padding: 10px 14px; border: 1px solid var(--line); background: rgba(255,250,241,.64); font-family: var(--font-mono); text-align: center; color: var(--ink-soft); box-shadow: 0 10px 28px var(--shadow); }
    .grid { display: grid; grid-template-columns: minmax(320px, .82fr) minmax(420px, 1.18fr); gap: 26px; align-items: start; }
    .auth-grid { display: grid; grid-template-columns: minmax(320px, .72fr) minmax(420px, 1.28fr); gap: 18px; margin-bottom: 24px; }
    .card { position: relative; background: var(--card); border: 1px solid var(--line); box-shadow: 0 22px 70px var(--shadow); backdrop-filter: blur(10px); }
    .auth-card { padding: 22px; }
    .command { padding: 28px; }
    .command:before { content: ""; position: absolute; inset: 0 auto 0 0; width: 8px; background: linear-gradient(180deg, var(--xhs-red), var(--seal-red)); }
    .section-title { margin: 0 0 22px; font-family: var(--font-display); font-size: 30px; }
    label { display: block; margin: 18px 0 8px; color: var(--ink-soft); font-size: 13px; letter-spacing: .08em; text-transform: uppercase; }
    input[type="text"], input[type="number"], textarea {
      width: 100%; border: 0; border-bottom: 2px solid var(--line); padding: 13px 2px 12px; background: transparent; color: var(--ink); font-size: 22px; font-family: var(--font-display); outline: none; transition: border-color .22s cubic-bezier(.2,.8,.2,1), box-shadow .22s;
    }
    textarea { min-height: 86px; resize: vertical; font-size: 13px; font-family: var(--font-mono); line-height: 1.55; }
    input:focus { border-color: var(--xhs-red); box-shadow: 0 10px 22px rgba(255,36,66,.08); }
    .row { display: grid; grid-template-columns: 140px 1fr; gap: 20px; align-items: end; }
    .switch-line { display: flex; justify-content: space-between; gap: 18px; align-items: center; margin-top: 24px; padding: 15px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }
    .switch-copy strong { display: block; font-size: 16px; }
    .switch-copy span { display: block; margin-top: 4px; color: var(--muted); font-size: 13px; }
    .switch { position: relative; width: 68px; height: 34px; flex: 0 0 68px; }
    .switch input { opacity: 0; width: 0; height: 0; }
    .slider { position: absolute; inset: 0; border: 1px solid var(--ink); background: rgba(255,250,241,.74); cursor: pointer; transition: .24s cubic-bezier(.2,.8,.2,1); }
    .slider:before { content: ""; position: absolute; width: 24px; height: 24px; left: 4px; top: 4px; background: var(--ink); transition: .24s cubic-bezier(.2,.8,.2,1); }
    .switch input:checked + .slider { background: var(--xhs-red); border-color: var(--xhs-red); box-shadow: 0 0 0 6px rgba(255,36,66,.10); }
    .switch input:checked + .slider:before { transform: translateX(34px); background: var(--paper); }
    .slider:after { content: "评"; position: absolute; right: 10px; top: 7px; color: var(--paper); font-weight: 700; opacity: 0; }
    .switch input:checked + .slider:after { opacity: 1; }
    .primary { width: 100%; margin-top: 26px; border: 0; padding: 16px 18px; background: var(--seal-red); color: #fff8ef; font-size: 17px; letter-spacing: .08em; cursor: pointer; box-shadow: 0 18px 34px rgba(185,22,45,.25); transition: transform .22s, background .22s, box-shadow .22s; }
    .primary:hover { transform: translateY(-2px); background: #8f1021; box-shadow: 0 22px 44px rgba(185,22,45,.34); }
    .primary:disabled { opacity: .58; cursor: not-allowed; transform: none; }
    .secondary { border: 1px solid var(--line); padding: 10px 12px; background: rgba(255,250,241,.62); color: var(--ink); cursor: pointer; font-family: var(--font-body); }
    .secondary.danger { color: var(--seal-red); border-color: rgba(185,22,45,.28); }
    .auth-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 16px; }
    .cookie-status { padding: 12px 14px; border: 1px solid var(--line); background: rgba(255,250,241,.56); font-family: var(--font-mono); font-size: 13px; color: var(--ink-soft); }
    .cookie-status.ok { color: var(--ink); border-color: rgba(184,135,70,.65); }
    .hint { margin: 16px 0 0; color: var(--muted); font-size: 13px; line-height: 1.65; }
    .steps { margin: 14px 0 0; padding: 12px 14px 12px 30px; border: 1px dashed rgba(32,24,22,.18); color: var(--ink-soft); font-size: 13px; line-height: 1.7; background: rgba(255,250,241,.42); }
    .steps code { font-family: var(--font-mono); color: var(--seal-red); }
    .dossier { padding: 28px; min-height: 560px; }
    .dossier-head { display: flex; justify-content: space-between; gap: 18px; align-items: start; margin-bottom: 22px; }
    .state { padding: 8px 12px; border: 1px solid var(--line); font-family: var(--font-mono); color: var(--ink-soft); background: rgba(255,250,241,.7); }
    .state.running { color: var(--seal-red); border-color: rgba(255,36,66,.28); box-shadow: 0 0 0 5px rgba(255,36,66,.08); }
    .state.success { color: var(--ink); border-color: rgba(184,135,70,.65); }
    .state.failed { color: #fff8ef; background: var(--seal-red); }
    .metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 18px 0 24px; }
    .metric { padding: 13px 12px; border: 1px solid var(--line); background: rgba(255,250,241,.48); }
    .metric span { display: block; color: var(--muted); font-size: 12px; }
    .metric strong { display: block; margin-top: 6px; font-family: var(--font-mono); font-size: 22px; }
    .progress-top { display: flex; justify-content: space-between; color: var(--ink-soft); font-family: var(--font-mono); font-size: 13px; }
    .track { position: relative; height: 14px; margin: 10px 0 26px; border: 1px solid var(--line); background: rgba(32,24,22,.05); overflow: hidden; }
    .fill { height: 100%; width: 0%; background: linear-gradient(90deg, var(--seal-red), var(--xhs-red)); }
    .fill.running { background-image: repeating-linear-gradient(45deg, rgba(255,255,255,.18) 0 8px, transparent 8px 16px), linear-gradient(90deg, var(--seal-red), var(--xhs-red)); background-size: 28px 28px, 100% 100%; }
    .timeline { position: relative; margin: 0 0 22px; padding-left: 26px; }
    .timeline:before { content: ""; position: absolute; left: 8px; top: 8px; bottom: 8px; width: 2px; background: linear-gradient(var(--xhs-red), rgba(185,22,45,.16)); }
    .step { position: relative; padding: 0 0 18px; color: var(--muted); }
    .step:before { content: ""; position: absolute; left: -24px; top: 5px; width: 12px; height: 12px; border: 1px solid currentColor; background: var(--paper); }
    .step.active { color: var(--seal-red); }
    .step.active:before { background: var(--xhs-red); border-color: var(--xhs-red); }
    .step.done { color: var(--ink); }
    .step.done:before { border-color: var(--seal-red); box-shadow: inset 0 0 0 3px var(--paper); background: var(--seal-red); }
    .step strong { display: block; font-size: 15px; }
    .step span { font-size: 12px; }
    .events { max-height: 180px; overflow: auto; border-top: 1px solid var(--line); padding-top: 14px; }
    .event { display: grid; grid-template-columns: 72px 1fr; gap: 10px; padding: 8px 0; border-bottom: 1px dashed rgba(32,24,22,.10); font-size: 13px; }
    .event time { color: var(--muted); font-family: var(--font-mono); }
    .event.warn b { color: var(--gold); }
    .event.error b { color: var(--seal-red); }
    .downloads { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }
    .download { display: inline-block; padding: 12px 14px; border-left: 5px solid var(--seal-red); border-top: 1px solid var(--line); border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); color: var(--ink); background: rgba(255,250,241,.74); text-decoration: none; transition: transform .2s, background .2s; }
    .download:hover { transform: translateY(-2px); background: #fffaf1; }
    @media (max-width: 860px) { .grid, .auth-grid { grid-template-columns: 1fr; } .metrics { grid-template-columns: repeat(2, 1fr); } .masthead { flex-direction: column; } }
    @media (prefers-reduced-motion: reduce) { * { animation: none !important; transition-duration: .01ms !important; } }
  </style>
</head>
<body>
  <main class="shell">
    <header class="masthead">
      <div>
        <div class="overline">HONGSHU CAICAI / DATA CONSOLE</div>
        <h1>红薯采采</h1>
        <p class="subtitle">关键词驱动的笔记与评论采集任务台</p>
      </div>
      <div class="mast-actions">
        <button id="logoutBtn" class="secondary" type="button">退出登录</button>
        <div id="mastState" class="status-pill">IDLE</div>
      </div>
    </header>

    <section class="auth-grid">
      <section class="card auth-card">
        <div class="overline">ACCOUNT</div>
        <h2 class="section-title">Cookie 状态</h2>
        <div id="cookieStatus" class="cookie-status">读取中</div>
        <div class="auth-actions">
          <button id="refreshCookieBtn" class="secondary" type="button">刷新状态</button>
          <button id="clearCookieBtn" class="secondary danger" type="button">清除 Cookie</button>
        </div>
        <label for="cookieInput">手动替换 Cookie</label>
        <textarea id="cookieInput" placeholder="粘贴 Request Headers 里的 cookie 全量内容"></textarea>
        <ol class="steps">
          <li>电脑浏览器打开网页版小红书并登录。</li>
          <li>按 <code>F12</code> 打开开发者工具，进入 <code>Network</code>。</li>
          <li>刷新页面或点任意小红书请求，找到 <code>scripting</code> 请求。</li>
          <li>在 <code>Request Headers</code> 中复制 <code>cookie</code> 的完整内容。</li>
          <li>粘贴到上方输入框，点击保存 Cookie。</li>
        </ol>
        <button id="saveCookieBtn" class="primary" type="button">保存 Cookie</button>
      </section>

      <section class="card auth-card">
        <div class="overline">NEXT</div>
        <h2 class="section-title">一键获取 Cookie</h2>
        <p class="hint">API 扫码已移除：它确认登录但拿不到采集必需的 <code>web_session</code>。下一步可改成真实浏览器自动登录：页面显示网页版二维码，扫码后自动读取浏览器 Cookie。</p>
        <p class="hint">当前推荐方式仍是左侧手动粘贴浏览器 Cookie，成功率最高。</p>
      </section>
    </section>

    <section class="grid">
      <form id="collectForm" class="card command">
        <h2 class="section-title">采集指令</h2>
        <label for="keyword">Keyword</label>
        <input id="keyword" name="keyword" type="text" maxlength="80" placeholder="输入关键词，如：苏州探店" required>
        <div class="row">
          <div>
            <label for="count">Count</label>
            <input id="count" name="count" type="number" min="1" max="{{MAX_COUNT}}" value="10" required>
          </div>
          <div class="switch-line">
            <div class="switch-copy"><strong>同步采集评论</strong><span>请求更多，建议 1-10 篇测试</span></div>
            <label class="switch"><input id="withComments" name="with_comments" type="checkbox"><span class="slider"></span></label>
          </div>
        </div>
        <button id="startBtn" class="primary" type="submit">开始采集</button>
        <p class="hint">当前已登录 admin。采集期间请勿高频刷新；任务进度会自动更新。</p>
      </form>

      <section class="card dossier">
        <div class="dossier-head">
          <div>
            <div class="overline">LIVE DOSSIER</div>
            <h2 class="section-title">采集进度</h2>
          </div>
          <div id="state" class="state">待命</div>
        </div>
        <div class="metrics">
          <div class="metric"><span>目标</span><strong id="mTarget">0</strong></div>
          <div class="metric"><span>笔记</span><strong id="mNotes">0</strong></div>
          <div class="metric"><span>评论</span><strong id="mComments">0</strong></div>
          <div class="metric"><span>失败</span><strong id="mFailed">0</strong></div>
        </div>
        <div class="progress-top"><span id="message">等待任务</span><span id="percent">0%</span></div>
        <div class="track"><div id="fill" class="fill"></div></div>
        <div id="timeline" class="timeline">
          <div class="step" data-stage="init"><strong>准备任务</strong><span>校验关键词与 Cookie</span></div>
          <div class="step" data-stage="search"><strong>搜索笔记</strong><span>请求关键词搜索结果</span></div>
          <div class="step" data-stage="notes"><strong>解析详情</strong><span>提取标题、作者、互动数据</span></div>
          <div class="step" data-stage="comments"><strong>采集评论</strong><span>拉取一级评论与二级评论</span></div>
          <div class="step" data-stage="complete"><strong>完成归档</strong><span>文件准备下载</span></div>
        </div>
        <div id="downloads" class="downloads"></div>
        <div id="events" class="events"></div>
      </section>
    </section>
  </main>

  <script src="https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js"></script>
  <script>
    const form = document.getElementById('collectForm');
    const startBtn = document.getElementById('startBtn');
    const stateEl = document.getElementById('state');
    const mastState = document.getElementById('mastState');
    const messageEl = document.getElementById('message');
    const percentEl = document.getElementById('percent');
    const fillEl = document.getElementById('fill');
    const eventsEl = document.getElementById('events');
    const downloadsEl = document.getElementById('downloads');
    const mTarget = document.getElementById('mTarget');
    const mNotes = document.getElementById('mNotes');
    const mComments = document.getElementById('mComments');
    const mFailed = document.getElementById('mFailed');
    const steps = [...document.querySelectorAll('.step')];
    const cookieStatus = document.getElementById('cookieStatus');
    const refreshCookieBtn = document.getElementById('refreshCookieBtn');
    const clearCookieBtn = document.getElementById('clearCookieBtn');
    const saveCookieBtn = document.getElementById('saveCookieBtn');
    const cookieInput = document.getElementById('cookieInput');
    const logoutBtn = document.getElementById('logoutBtn');
    const reduceMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    let pollTimer = null;
    let fillLoopTween = null;
    let activeStepTween = null;
    let lastStage = '';
    let lastState = '';
    let lastEventsKey = '';
    let lastDownloadsKey = '';

    function hasGsap() {
      return Boolean(window.gsap);
    }

    function prefersReducedMotion() {
      return reduceMotionQuery.matches;
    }

    function motionDuration(seconds) {
      return prefersReducedMotion() ? 0 : seconds;
    }

    function stopLoopingMotion() {
      if (fillLoopTween) {
        fillLoopTween.kill();
        fillLoopTween = null;
      }
      if (activeStepTween) {
        activeStepTween.kill();
        activeStepTween = null;
      }
      if (hasGsap()) gsap.set(steps, { clearProps: 'boxShadow' });
    }

    function runIntroMotion() {
      if (!hasGsap()) return;
      gsap.set(['.masthead', '.auth-card', '.command', '.dossier'], { autoAlpha: 1, clearProps: 'visibility' });
      if (prefersReducedMotion()) return;

      gsap.timeline({ defaults: { duration: 0.56, ease: 'power3.out' } })
        .from('.masthead', { y: 16, autoAlpha: 0 })
        .from('.auth-card', { y: 14, autoAlpha: 0, stagger: 0.08 }, '-=0.30')
        .from('.command', { x: -16, autoAlpha: 0 }, '-=0.22')
        .from('.dossier', { x: 16, autoAlpha: 0 }, '-=0.46');
    }

    function animateProgress(percent, isRunning) {
      const safePercent = Math.max(0, Math.min(100, Number(percent || 0)));
      fillEl.className = 'fill ' + (isRunning ? 'running' : '');

      if (!hasGsap()) {
        fillEl.style.width = `${safePercent}%`;
        return;
      }

      gsap.to(fillEl, { width: `${safePercent}%`, duration: motionDuration(0.42), ease: 'power2.out', overwrite: 'auto' });

      if (!isRunning || prefersReducedMotion()) {
        if (fillLoopTween) {
          fillLoopTween.kill();
          fillLoopTween = null;
        }
        gsap.set(fillEl, { backgroundPosition: '0px 0px, 0px 0px' });
        return;
      }

      if (!fillLoopTween) {
        fillLoopTween = gsap.to(fillEl, { backgroundPosition: '28px 0px, 0px 0px', duration: 1.2, ease: 'none', repeat: -1 });
      }
    }

    function animateMetric(el, nextValue) {
      const next = Number(nextValue || 0);
      const current = Number(el.textContent || 0);
      if (!hasGsap() || prefersReducedMotion() || current === next) {
        el.textContent = next;
        return;
      }

      const counter = { value: current };
      gsap.to(counter, {
        value: next,
        duration: 0.38,
        ease: 'power2.out',
        overwrite: 'auto',
        onUpdate: () => { el.textContent = Math.round(counter.value); }
      });
      gsap.fromTo(el, { y: -2 }, { y: 0, duration: 0.22, ease: 'power2.out', overwrite: 'auto', clearProps: 'transform' });
    }

    function animateStatusChange(el) {
      if (!hasGsap() || prefersReducedMotion()) return;
      gsap.fromTo(el, { y: -4, autoAlpha: 0.72 }, { y: 0, autoAlpha: 1, duration: 0.24, ease: 'power2.out', overwrite: 'auto', clearProps: 'transform,opacity,visibility' });
    }

    function animateTimeline(stage, state) {
      const active = stageIndex(stage);
      steps.forEach((step, index) => {
        step.classList.toggle('done', active > index || state === 'success');
        step.classList.toggle('active', active === index && state === 'running');
      });

      if (!hasGsap()) return;
      const changed = stage !== lastStage || state !== lastState;
      if (!changed) return;

      if (activeStepTween) {
        activeStepTween.kill();
        activeStepTween = null;
      }

      const activeStep = steps[active];
      if (activeStep && state === 'running') {
        gsap.fromTo(activeStep, { x: -4 }, { x: 0, duration: motionDuration(0.28), ease: 'power2.out', overwrite: 'auto', clearProps: 'transform' });
        if (!prefersReducedMotion()) {
          activeStepTween = gsap.to(activeStep, { boxShadow: '0 0 0 6px rgba(255,36,66,0.08)', duration: 0.9, ease: 'sine.inOut', repeat: -1, yoyo: true, overwrite: 'auto' });
        }
      }

      if (state === 'success' || state === 'failed') stopLoopingMotion();
      lastStage = stage || '';
      lastState = state || '';
    }

    function eventsKey(events) {
      return (events || []).map(event => [event.time, event.title, event.detail, event.level].join('|')).join('::');
    }

    function downloadsKey(downloads) {
      return (downloads || []).map(item => [item.label, item.file].join('|')).join('::');
    }

    function renderEvents(events) {
      const key = eventsKey(events);
      eventsEl.innerHTML = (events || []).slice().reverse().map(event => `
        <div class="event ${event.level || 'info'}"><time>${event.time}</time><div><b>${escapeHtml(event.title)}</b><br>${escapeHtml(event.detail || '')}</div></div>
      `).join('');

      if (hasGsap() && !prefersReducedMotion() && key !== lastEventsKey) {
        const firstEvent = eventsEl.querySelector('.event');
        if (firstEvent) {
          gsap.fromTo(firstEvent, { y: -8, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.26, ease: 'power2.out', overwrite: 'auto', clearProps: 'transform,opacity,visibility' });
        }
      }
      lastEventsKey = key;
    }

    function renderDownloads(downloads) {
      const key = downloadsKey(downloads);
      downloadsEl.innerHTML = (downloads || []).map(item => {
        const href = '/download?file=' + encodeURIComponent(item.file);
        return `<a class="download" href="${href}">↓ ${escapeHtml(item.label)}</a>`;
      }).join('');

      if (hasGsap() && !prefersReducedMotion() && key !== lastDownloadsKey) {
        gsap.fromTo(downloadsEl.querySelectorAll('.download'), { y: 10, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.34, ease: 'back.out(1.4)', stagger: 0.06, overwrite: 'auto', clearProps: 'transform,opacity,visibility' });
      }
      lastDownloadsKey = key;
    }

    function bindPressMotion(selector) {
      document.querySelectorAll(selector).forEach(el => {
        el.addEventListener('pointerdown', () => {
          if (!hasGsap() || prefersReducedMotion() || el.disabled) return;
          gsap.to(el, { scale: 0.985, duration: 0.08, ease: 'power1.out', overwrite: 'auto' });
        });
        const release = () => {
          if (!hasGsap() || prefersReducedMotion()) return;
          gsap.to(el, { scale: 1, duration: 0.16, ease: 'power2.out', overwrite: 'auto', clearProps: 'transform' });
        };
        el.addEventListener('pointerup', release);
        el.addEventListener('pointerleave', release);
      });
    }

    reduceMotionQuery.addEventListener('change', () => {
      if (!prefersReducedMotion()) return;
      stopLoopingMotion();
      if (hasGsap()) {
        gsap.set(['.masthead', '.auth-card', '.command', '.dossier'], { autoAlpha: 1, x: 0, y: 0, clearProps: 'transform,opacity,visibility' });
      }
    });

    function stageIndex(stage) {
      return ['init', 'search', 'notes', 'comments', 'complete'].indexOf(stage);
    }

    function setState(job) {
      const labels = { queued: '排队中', running: '采集中', success: '已完成', failed: '异常' };
      const stateChanged = (job.state || '') !== lastState;
      stateEl.textContent = labels[job.state] || '待命';
      mastState.textContent = (job.state || 'idle').toUpperCase();
      stateEl.className = 'state ' + (job.state || '');
      messageEl.textContent = job.message || '等待任务';
      percentEl.textContent = `${job.percent || 0}%`;
      animateProgress(job.percent || 0, job.state === 'running');
      animateMetric(mTarget, job.count || 0);
      animateMetric(mNotes, job.metrics?.notes || 0);
      animateMetric(mComments, job.metrics?.comments || 0);
      animateMetric(mFailed, job.metrics?.failed_comments || 0);
      if (stateChanged) {
        animateStatusChange(stateEl);
        animateStatusChange(mastState);
      }
      animateTimeline(job.stage, job.state);
      renderEvents(job.events || []);
      renderDownloads(job.downloads || []);
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
    }

    function redirectIfUnauthorized(res) {
      if (res.status === 401) {
        window.location.href = '/login';
        return true;
      }
      return false;
    }

    async function refreshCookieStatus() {
      const res = await fetch('/api/cookie/status');
      if (redirectIfUnauthorized(res)) return;
      const payload = await res.json();
      cookieStatus.textContent = payload.message + (payload.summary ? ' · ' + payload.summary : '');
      cookieStatus.className = 'cookie-status ' + (payload.configured ? 'ok' : '');
      animateStatusChange(cookieStatus);
      return payload;
    }

    async function poll(jobId) {
      const res = await fetch('/api/status?id=' + encodeURIComponent(jobId));
      if (redirectIfUnauthorized(res)) return;
      const job = await res.json();
      setState(job);
      if (job.state === 'success' || job.state === 'failed') {
        clearInterval(pollTimer);
        pollTimer = null;
        startBtn.disabled = false;
        startBtn.textContent = '开始采集';
      }
    }

    runIntroMotion();
    bindPressMotion('.primary, .secondary');

    refreshCookieBtn.addEventListener('click', refreshCookieStatus);

    logoutBtn.addEventListener('click', async () => {
      await fetch('/api/logout', { method: 'POST' });
      window.location.href = '/login';
    });

    clearCookieBtn.addEventListener('click', async () => {
      if (!confirm('确认清除当前 Cookie？清除后采集会失败，直到重新登录或手动保存。')) return;
      const res = await fetch('/api/cookie/clear', { method: 'POST' });
      if (redirectIfUnauthorized(res)) return;
      const payload = await res.json();
      if (!res.ok) throw new Error(payload.error || '清除失败');
      cookieInput.value = '';
      await refreshCookieStatus();
    });

    saveCookieBtn.addEventListener('click', async () => {
      saveCookieBtn.disabled = true;
      try {
        const data = new URLSearchParams();
        data.set('cookies', cookieInput.value);
        const res = await fetch('/api/cookie/save', { method: 'POST', body: data });
        if (redirectIfUnauthorized(res)) return;
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || '保存失败');
        cookieInput.value = '';
        await refreshCookieStatus();
      } catch (error) {
        cookieStatus.textContent = error.message;
        cookieStatus.className = 'cookie-status';
      } finally {
        saveCookieBtn.disabled = false;
      }
    });

    refreshCookieStatus();

    form.addEventListener('submit', async event => {
      event.preventDefault();
      clearInterval(pollTimer);
      downloadsEl.innerHTML = '';
      eventsEl.innerHTML = '';
      startBtn.disabled = true;
      startBtn.textContent = '任务启动中';
      const data = new URLSearchParams(new FormData(form));
      try {
        const res = await fetch('/api/start', { method: 'POST', body: data });
        if (redirectIfUnauthorized(res)) return;
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || '任务启动失败');
        await poll(payload.id);
        pollTimer = setInterval(() => poll(payload.id), 1200);
      } catch (error) {
        setState({ state: 'failed', stage: 'failed', percent: 100, message: error.message, events: [{ time: new Date().toLocaleTimeString(), title: '启动失败', detail: error.message, level: 'error' }], metrics: {} });
        startBtn.disabled = false;
        startBtn.textContent = '开始采集';
      }
    });
  </script>
</body>
</html>""".replace("{{MAX_COUNT}}", str(MAX_COUNT))


def render_login_page(error=""):
    safe_error = html.escape(error)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>登录 · 红薯采采</title>
  <style>
    :root {{ --paper: #f8f1e8; --ink: #201816; --ink-soft: #786a62; --seal-red: #b9162d; --line: rgba(32,24,22,.14); --shadow: rgba(80,34,24,.16); --font-display: "Iowan Old Style", "Songti SC", serif; --font-body: "Avenir Next", "PingFang SC", "Microsoft YaHei", sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ min-height: 100vh; margin: 0; display: grid; place-items: center; color: var(--ink); font-family: var(--font-body); background: radial-gradient(circle at 20% 20%, rgba(255,36,66,.12), transparent 28rem), var(--paper); }}
    .card {{ width: min(420px, calc(100vw - 32px)); padding: 34px; border: 1px solid var(--line); background: rgba(255,250,241,.86); box-shadow: 0 22px 70px var(--shadow); }}
    .overline {{ letter-spacing: .18em; font-size: 12px; color: var(--seal-red); }}
    h1 {{ margin: 8px 0 22px; font-family: var(--font-display); font-size: 48px; line-height: .95; }}
    label {{ display: block; margin: 18px 0 8px; color: var(--ink-soft); font-size: 13px; letter-spacing: .08em; text-transform: uppercase; }}
    input {{ width: 100%; border: 0; border-bottom: 2px solid var(--line); padding: 13px 2px 12px; background: transparent; color: var(--ink); font-size: 22px; outline: none; }}
    input:focus {{ border-color: var(--seal-red); }}
    button {{ width: 100%; margin-top: 28px; border: 0; padding: 16px 18px; background: var(--seal-red); color: #fff8ef; font-size: 17px; letter-spacing: .08em; cursor: pointer; }}
    .error {{ margin: 16px 0 0; color: var(--seal-red); min-height: 22px; }}
    .hint {{ margin: 18px 0 0; color: var(--ink-soft); font-size: 13px; }}
  </style>
</head>
<body>
  <form class="card" method="post" action="/api/login">
    <div class="overline">HONGSHU CAICAI</div>
    <h1>登录</h1>
    <label for="username">Username</label>
    <input id="username" name="username" type="text" autocomplete="username" value="admin" required autofocus>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required>
    <button type="submit">进入采集台</button>
    <p class="error">{safe_error}</p>
    <p class="hint">默认账号：admin / admin</p>
  </form>
  <script>
    document.querySelector('form').addEventListener('submit', async event => {{
      event.preventDefault();
      const form = event.currentTarget;
      const res = await fetch('/api/login', {{ method: 'POST', body: new URLSearchParams(new FormData(form)) }});
      const payload = await res.json();
      if (res.ok) {{ window.location.href = '/'; return; }}
      document.querySelector('.error').textContent = payload.error || '登录失败';
    }});
  </script>
</body>
</html>"""


class SpiderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/login":
            if self.is_authenticated():
                self.redirect("/")
                return
            self.send_html(render_login_page())
            return
        if parsed.path == "/":
            if not self.require_auth(parsed.path):
                return
            self.send_html(render_page())
            return
        if parsed.path == "/api/status":
            if not self.require_auth(parsed.path):
                return
            self.handle_status(parsed.query)
            return
        if parsed.path == "/api/cookie/status":
            if not self.require_auth(parsed.path):
                return
            self.send_json(get_cookie_status())
            return
        if parsed.path == "/download":
            if not self.require_auth(parsed.path):
                return
            self.handle_download(parsed.query)
            return
        self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/login":
            self.handle_login()
            return
        if parsed.path == "/api/logout":
            self.send_json({"ok": True}, headers=[("Set-Cookie", clear_session_cookie_header())])
            return
        if parsed.path == "/api/start":
            if not self.require_auth(parsed.path):
                return
            self.handle_start()
            return
        if parsed.path == "/api/cookie/save":
            if not self.require_auth(parsed.path):
                return
            self.handle_cookie_save()
            return
        if parsed.path == "/api/cookie/clear":
            if not self.require_auth(parsed.path):
                return
            self.handle_cookie_clear()
            return
        self.send_error(404)

    def is_authenticated(self):
        cookies = parse_cookie_header(self.headers.get("Cookie", ""))
        return decode_session(cookies.get(APP_SESSION_COOKIE, "")) == APP_USERNAME

    def require_auth(self, path):
        if self.is_authenticated():
            return True
        if path.startswith("/api/"):
            self.send_json({"error": "未登录"}, 401)
        else:
            self.redirect("/login")
        return False

    def handle_login(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        form = urllib.parse.parse_qs(body)
        username = form.get("username", [""])[0]
        password = form.get("password", [""])[0]
        if hmac.compare_digest(username, APP_USERNAME) and hmac.compare_digest(password, APP_PASSWORD):
            self.send_json({"ok": True}, headers=[("Set-Cookie", session_cookie_header(username))])
            return
        self.send_json({"error": "用户名或密码错误"}, 401)

    def handle_start(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        form = urllib.parse.parse_qs(body)
        try:
            keyword = validate_keyword(form.get("keyword", [""])[0])
            count = validate_count(form.get("count", ["10"])[0])
            with_comments = form.get("with_comments", [""])[0] == "on"
            job_id = create_job(keyword, count, with_comments)
            thread = threading.Thread(target=run_job, args=(job_id, keyword, count, with_comments), daemon=True)
            thread.start()
            self.send_json({"id": job_id}, 202)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_status(self, query):
        params = urllib.parse.parse_qs(query)
        job_id = params.get("id", [""])[0]
        job = get_job(job_id)
        if not job:
            self.send_json({"error": "任务不存在"}, 404)
            return
        self.send_json(job)

    def handle_cookie_save(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        form = urllib.parse.parse_qs(body)
        try:
            save_cookie_string(form.get("cookies", [""])[0])
            self.send_json(get_cookie_status())
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_cookie_clear(self):
        clear_cookie_string()
        self.send_json(get_cookie_status())

    def handle_download(self, query):
        params = urllib.parse.parse_qs(query)
        filename = params.get("file", [""])[0]
        if not filename.endswith(".xlsx") or "/" in filename or "\\" in filename or ".." in filename:
            self.send_error(400, "Invalid file")
            return

        excel_dir = os.path.abspath(EXCEL_DIR)
        file_path = os.path.abspath(os.path.join(excel_dir, filename))
        if not file_path.startswith(excel_dir + os.sep) or not os.path.exists(file_path):
            self.send_error(404)
            return

        with open(file_path, "rb") as file:
            content = file.read()
        encoded_name = urllib.parse.quote(filename)
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{encoded_name}")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def send_html(self, content, status=200, headers=None):
        data = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in headers or []:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload, status=200, headers=None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in headers or []:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}")


if __name__ == "__main__":
    os.chdir(PROJECT_DIR)
    server = ThreadingHTTPServer((HOST, PORT), SpiderHandler)
    print(f"红薯采采 web app running at http://{HOST}:{PORT}")
    server.serve_forever()
