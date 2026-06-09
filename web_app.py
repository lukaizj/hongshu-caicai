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

from apis.xhs_pc_login_apis import XHSLoginApi
from spider.spider import Data_Spider
from xhs_utils.common_util import init
from xhs_utils.cookie_util import trans_cookies
from xhs_utils import ai_utils
import io
from apis.xhs_pc_apis import XHS_Apis

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
PHONE_SESSIONS = {}
PHONE_LOCK = threading.Lock()
PHONE_SESSION_TTL = 300
QR_SESSIONS = {}
QR_LOCK = threading.Lock()
QR_SESSION_TTL = 300
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


def validate_choice(raw_value, allowed, default=0):
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = default
    return value if value in allowed else default


def validate_text_field(value, field_name, max_length):
    value = (value or "").strip()
    if len(value) > max_length:
        raise ValueError(f"{field_name}不能超过 {max_length} 个字符")
    return value


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
    return " / ".join(f"{key}=***{str(cookies[key])[-4:]}" for key in keys)


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


def create_phone_session(phone, zone):
    phone = (phone or "").strip()
    zone = (zone or "86").strip() or "86"
    if not phone:
        raise ValueError("手机号不能为空")

    login_api = XHSLoginApi()
    cookies = login_api.generate_init_cookies()
    success, msg, res = login_api.send_phone_code(phone, cookies, zone)
    if not success:
        raise ValueError(msg or "验证码发送失败")

    session_id = uuid.uuid4().hex
    with PHONE_LOCK:
        PHONE_SESSIONS[session_id] = {
            "phone": phone,
            "zone": zone,
            "cookies": cookies,
            "created_at": time.time(),
        }
    return {"session_id": session_id, "message": "验证码已发送，请查看手机短信", "expires_in": PHONE_SESSION_TTL}


def login_phone_session(session_id, code):
    code = (code or "").strip()
    if not session_id:
        raise ValueError("缺少手机登录会话")
    if not code:
        raise ValueError("验证码不能为空")

    with PHONE_LOCK:
        session = PHONE_SESSIONS.get(session_id)
        if not session:
            raise ValueError("验证码会话已失效，请重新发送")
        if time.time() - session["created_at"] > PHONE_SESSION_TTL:
            PHONE_SESSIONS.pop(session_id, None)
            raise ValueError("验证码已过期，请重新发送")
        phone = session["phone"]
        zone = session["zone"]
        cookies = dict(session["cookies"])

    login_api = XHSLoginApi()
    success, msg, data = login_api.login_by_phone(phone, code, cookies, zone)
    if not success:
        raise ValueError(msg or "验证码登录失败")

    cookies = data["cookies"]
    cookie_str = login_api.cookies_to_str(cookies)
    save_cookie_string(cookie_str)
    with PHONE_LOCK:
        PHONE_SESSIONS.pop(session_id, None)
    return {"message": "Cookie 获取成功，已自动保存", "cookie_status": get_cookie_status()}


def create_qrcode_session():
    login_api = XHSLoginApi()
    cookies = login_api.generate_init_cookies()
    success, msg, res = login_api.generate_qrcode(cookies)
    if not success:
        raise ValueError(msg or "二维码生成失败")
    qr_id = res.get("qr_id", "")
    qr_url = res.get("qr_url", "")
    code = res.get("code", "")
    import qrcode as qr_module
    qr_img = qr_module.make(qr_url)
    buf = io.BytesIO()
    qr_img.save(buf, format="PNG")
    img_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    session_id = uuid.uuid4().hex
    with QR_LOCK:
        QR_SESSIONS[session_id] = {
            "qr_id": qr_id,
            "code": code,
            "cookies": cookies,
            "created_at": time.time(),
        }
    return {"session_id": session_id, "qr_image": f"data:image/png;base64,{img_b64}", "qr_url": qr_url, "expires_in": QR_SESSION_TTL}


def check_qrcode_session(session_id):
    if not session_id:
        raise ValueError("缺少二维码会话")
    with QR_LOCK:
        session = QR_SESSIONS.get(session_id)
        if not session:
            raise ValueError("二维码会话已失效，请重新生成")
        if time.time() - session["created_at"] > QR_SESSION_TTL:
            QR_SESSIONS.pop(session_id, None)
            raise ValueError("二维码已过期，请重新生成")
        qr_id = session["qr_id"]
        code = session["code"]
        cookies = dict(session["cookies"])

    login_api = XHSLoginApi()
    success, msg, new_cookies = login_api.check_qrcode_status(qr_id, code, cookies)
    if not success:
        if "过期" in (msg or ""):
            QR_SESSIONS.pop(session_id, None)
            raise ValueError(msg or "二维码已过期")
        return {"status": "waiting", "message": msg or "请使用小红书 App 扫描二维码"}

    cookie_str = login_api.cookies_to_str(new_cookies)
    save_cookie_string(cookie_str)
    with QR_LOCK:
        QR_SESSIONS.pop(session_id, None)
    return {"status": "done", "message": "扫码登录成功，Cookie 已自动保存", "cookie_status": get_cookie_status()}


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


def create_job(keyword, count, with_comments, job_options=None):
    options = job_options or {}
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "state": "queued",
        "workflow": options.get("workflow", "collect"),
        "keyword": keyword,
        "count": count,
        "with_comments": with_comments,
        "sort_type_choice": options.get("sort_type_choice", 0),
        "note_type": options.get("note_type", 0),
        "note_time": options.get("note_time", 0),
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


def run_job(job_id, keyword, count, with_comments, search_options=None):
    search_options = search_options or {}
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
            sort_type_choice=search_options.get("sort_type_choice", 0),
            note_type=search_options.get("note_type", 0),
            note_time=search_options.get("note_time", 0),
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


def run_job_url(job_id, note_url, with_comments):
    try:
        update_job(job_id, state="running", stage="init", percent=5, message="读取 Cookie 与输出目录")
        add_event(job_id, "读取配置", "从 .env 加载 COOKIES")
        cookies_str, base_path = init()
        if not cookies_str:
            raise ValueError(".env 缺少 COOKIES")

        spider = Data_Spider()
        update_job(job_id, stage="search", percent=20, message="抓取单篇笔记详情")
        add_event(job_id, "抓取笔记", note_url)
        success, msg, note_info = spider.spider_note(note_url, cookies_str)
        if not success:
            raise RuntimeError(str(msg))

        note_list = [note_info] if note_info else []
        note_filename = f"{note_info.get('title','note')[:20]}.xlsx"
        note_path = os.path.join(base_path["excel"], note_filename)
        # spider_note already calls handle_note_info internally, use result directly
        from xhs_utils.data_util import save_to_xlsx
        save_to_xlsx([note_info], note_path, "note")

        downloads = [{"label": "下载笔记 Excel", "file": note_filename}]
        update_job(job_id, stage="notes", percent=60 if with_comments else 95,
                   message=f"笔记抓取完成", downloads=downloads,
                   metrics={"notes": 1, "comments": 0, "failed_comments": 0, "skipped_comments": 0})
        add_event(job_id, "生成笔记文件", note_filename)

        comment_count = 0
        if with_comments:
            def comment_progress(stage, current, total, info):
                total = total or 1
                update_job(job_id, stage="comments", percent=62 + int((current / total) * 30),
                           message=f"评论采集中 {current}/{total}")
                add_event(job_id, "采集评论", f"{current}/{total}，写入 {info.get('added', 0)} 条")
            update_job(job_id, stage="comments", percent=62, message="开始采集评论")
            add_event(job_id, "采集评论", "拉取笔记评论")
            comment_filename = f"{note_info.get('title','note')[:20]}_comments.xlsx"
            comment_count, failed_urls, skipped = spider.spider_some_note_comments(
                [note_url], cookies_str, base_path, comment_filename.replace(".xlsx", ""),
                progress_callback=comment_progress,
            )
            if os.path.exists(os.path.join(base_path["excel"], comment_filename)):
                downloads.append({"label": "下载评论 Excel", "file": comment_filename})

        update_job(job_id, state="success", stage="complete", percent=100, message="采集完成",
                   downloads=downloads,
                   metrics={"notes": 1, "comments": comment_count, "failed_comments": 0, "skipped_comments": 0})
        add_event(job_id, "完成归档", "Excel 文件已准备下载")
    except Exception as exc:
        update_job(job_id, state="failed", stage="failed", percent=100, message="采集失败", error=str(exc))
        add_event(job_id, "任务失败", str(exc), "error")


def run_job_user(job_id, user_url, with_comments):
    try:
        update_job(job_id, state="running", stage="init", percent=5, message="读取 Cookie 与输出目录")
        add_event(job_id, "读取配置", "从 .env 加载 COOKIES")
        cookies_str, base_path = init()
        if not cookies_str:
            raise ValueError(".env 缺少 COOKIES")

        spider = Data_Spider()
        user_id = user_url.rstrip("/").split("/")[-1].split("?")[0]

        update_job(job_id, stage="search", percent=15, message="获取用户所有笔记")
        add_event(job_id, "用户主页", user_url)
        # spider_user_all_note internally calls spider_some_note which handles progress
        note_list, success, msg = spider.spider_user_all_note(
            user_url, cookies_str, base_path, "excel", excel_name=user_id,
        )
        if not success:
            raise RuntimeError(str(msg))

        note_filename = f"{user_id}.xlsx"
        note_path = os.path.join(base_path["excel"], note_filename)
        downloads = [{"label": "下载用户笔记 Excel", "file": note_filename}]
        update_job(job_id, stage="notes", percent=60 if with_comments else 95,
                   message=f"用户笔记导出完成：{len(note_list)} 条", downloads=downloads,
                   metrics={"notes": len(note_list), "comments": 0, "failed_comments": 0, "skipped_comments": 0})
        add_event(job_id, "生成笔记文件", note_filename)

        comment_count = 0
        if with_comments and note_list:
            update_job(job_id, stage="comments", percent=62, message="开始采集评论")
            comment_filename = f"{user_id}_comments.xlsx"
            comment_count, failed_urls, skipped = spider.spider_some_note_comments(
                note_list, cookies_str, base_path, comment_filename.replace(".xlsx", ""),
                progress_callback=progress_callback,
            )
            if os.path.exists(os.path.join(base_path["excel"], comment_filename)):
                downloads.append({"label": "下载评论 Excel", "file": comment_filename})

        update_job(job_id, state="success", stage="complete", percent=100, message="采集完成",
                   downloads=downloads,
                   metrics={"notes": len(note_list), "comments": comment_count, "failed_comments": 0, "skipped_comments": 0})
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
      --paper: #f8f1e8; --paper-deep: #eadfce; --ink: #201816; --ink-soft: #786a62; --muted: #a08e80;
      --xhs-red: #ff2442; --xhs-red-light: #ff6b7f; --seal-red: #b9162d; --gold: #b88746; --gold-light: #d4a65a;
      --card: rgba(255,250,241,.82); --line: rgba(32,24,22,.14); --shadow: rgba(80,34,24,.16); --shadow-deep: rgba(80,34,24,.24);
      --xhs-glow: rgba(255,36,66,.10);
      --font-display: "Iowan Old Style","Songti SC","Noto Serif CJK SC","Source Han Serif SC","STSong",serif;
      --font-body: "Avenir Next","Gill Sans","PingFang SC","Microsoft YaHei",sans-serif;
      --font-mono: "Cascadia Mono","SFMono-Regular","Consolas",monospace;
    }
    * { box-sizing: border-box; }
    body {
      min-height: 100vh; margin: 0; color: var(--ink); font-family: var(--font-body);
      background: radial-gradient(circle at 10% 10%, rgba(255,36,66,.09), transparent 30rem),
                  radial-gradient(circle at 88% 88%, rgba(184,135,70,.06), transparent 26rem),
                  radial-gradient(circle at 50% 50%, rgba(32,24,22,.04), transparent 50rem),
                  repeating-linear-gradient(0deg, rgba(32,24,22,.028) 0, rgba(32,24,22,.028) 1px, transparent 1px, transparent 28px);
      background-color: var(--paper);
    }

    /* ── header ── */
    .app-header {
      display: flex; align-items: center; justify-content: space-between; gap: 16px;
      padding: 14px 24px; border-bottom: 1px solid var(--line);
      background: rgba(255,250,241,.76); backdrop-filter: blur(12px);
      position: sticky; top: 0; z-index: 100;
    }
    .header-brand { display: flex; align-items: center; gap: 12px; }
    .header-brand h1 { margin: 0; font-family: var(--font-display); font-size: 28px; line-height: 1; letter-spacing: -.02em; }
    .header-brand .overline { font-size: 10px; letter-spacing: .15em; color: var(--seal-red); }
    .header-right { display: flex; align-items: center; gap: 14px; }
    .cookie-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--muted); box-shadow: 0 0 6px rgba(160,142,128,.35); flex-shrink: 0; }
    .cookie-dot.ok { background: #2ecc71; box-shadow: 0 0 10px rgba(46,204,113,.40); }
    .cookie-dot-label { font-size: 12px; color: var(--ink-soft); }
    .btn-small { border: 1px solid var(--line); padding: 6px 12px; background: rgba(255,250,241,.56); color: var(--ink-soft); cursor: pointer; font-size: 12px; transition: .2s; }
    .btn-small:hover { border-color: var(--xhs-red); color: var(--seal-red); }

    /* ── nav tabs ── */
    .nav-bar {
      display: flex; gap: 0; border-bottom: 1px solid var(--line); padding: 0 24px;
      background: rgba(255,250,241,.44); backdrop-filter: blur(6px);
      position: sticky; top: 61px; z-index: 99;
    }
    .nav-tab {
      padding: 12px 22px; font-size: 14px; color: var(--muted); cursor: pointer;
      background: none; border: 0; border-bottom: 3px solid transparent; margin-bottom: -1px;
      transition: color .22s, border-color .22s; font-family: var(--font-body); letter-spacing: .03em;
    }
    .nav-tab.active { color: var(--seal-red); border-bottom-color: var(--seal-red); font-weight: 600; }
    .nav-tab:hover:not(.active) { color: var(--ink-soft); }
    .nav-badge { display: inline-block; margin-left: 4px; padding: 1px 6px; border-radius: 9px; background: var(--xhs-red); color: #fff; font-size: 10px; font-weight: 700; vertical-align: middle; }

    /* ── panels ── */
    .tab-panel { display: none; padding: 24px; }
    .tab-panel.active { display: block; }
    .panel-grid { display: grid; grid-template-columns: minmax(320px,.82fr) minmax(420px,1.18fr); gap: 24px; align-items: start; }
    .auth-grid { display: grid; grid-template-columns: minmax(320px,.72fr) minmax(420px,1.28fr); gap: 18px; }

    /* ── cards ── */
    .card { position: relative; background: var(--card); border: 1px solid var(--line); box-shadow: 0 22px 70px var(--shadow); backdrop-filter: blur(10px); transform-style: preserve-3d; perspective: 900px; transition: box-shadow .35s ease; }
    .card:hover { box-shadow: 0 28px 80px var(--shadow-deep); }
    .card::before { content: ""; position: absolute; inset: 0; background: linear-gradient(105deg, transparent 40%, rgba(255,255,255,.08) 50%, transparent 60%); background-size: 200% 100%; opacity: 0; transition: opacity .35s; pointer-events: none; z-index: 1; }
    .card:hover::before { opacity: 1; animation: card-shimmer 1.8s ease-in-out infinite; }
    @keyframes card-shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
    .card-padded { padding: 26px; }

    .section-title { margin: 0 0 18px; font-family: var(--font-display); font-size: 28px; }
    label { display: block; margin: 16px 0 6px; color: var(--ink-soft); font-size: 12px; letter-spacing: .08em; text-transform: uppercase; }
    input, select { width: 100%; border: 0; border-bottom: 2px solid var(--line); padding: 12px 2px 11px; background: transparent; color: var(--ink); font-size: 20px; font-family: var(--font-display); outline: none; transition: border-color .22s cubic-bezier(.2,.8,.2,1), box-shadow .22s; }
    textarea { width: 100%; min-height: 80px; border: 0; border-bottom: 2px solid var(--line); padding: 11px 2px 10px; background: transparent; color: var(--ink); font-size: 13px; font-family: var(--font-mono); line-height: 1.55; resize: vertical; outline: none; }
    input:focus, select:focus, textarea:focus { border-color: var(--xhs-red); box-shadow: 0 10px 22px rgba(255,36,66,.08); }
    select { cursor: pointer; appearance: none; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='7'%3E%3Cpath d='M0 0l6 7 6-7z' fill='%23786a62'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 4px center; padding-right: 24px; }

    .primary { width: 100%; margin-top: 22px; border: 0; padding: 15px 18px; background: var(--seal-red); color: #fff8ef; font-size: 16px; letter-spacing: .06em; cursor: pointer; box-shadow: 0 18px 34px rgba(185,22,45,.25); transition: transform .22s, background .22s, box-shadow .22s; position: relative; overflow: hidden; }
    .primary:hover { transform: translateY(-2px); background: #8f1021; box-shadow: 0 22px 44px rgba(185,22,45,.34), 0 0 28px var(--xhs-glow); }
    .primary:disabled { opacity: .56; cursor: not-allowed; transform: none; }
    .secondary { border: 1px solid var(--line); padding: 9px 12px; background: rgba(255,250,241,.62); color: var(--ink); cursor: pointer; font-family: var(--font-body); font-size: 13px; transition: background .22s, border-color .22s; white-space: nowrap; }
    .secondary:hover { background: #fffaf1; border-color: var(--xhs-red); }
    .secondary:disabled { opacity: .45; cursor: not-allowed; }
    .secondary.danger { color: var(--seal-red); border-color: rgba(185,22,45,.28); }

    .ripple { position: absolute; border-radius: 50%; background: rgba(255,255,255,.32); transform: scale(0); animation: ripple-anim .65s ease-out forwards; pointer-events: none; }
    @keyframes ripple-anim { to { transform: scale(6); opacity: 0; } }

    /* ── collection form ── */
    .command:before { content: ""; position: absolute; inset: 0 auto 0 0; width: 8px; background: linear-gradient(180deg, var(--xhs-red), var(--seal-red)); }
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
    .hint { margin: 16px 0 0; color: var(--muted); font-size: 13px; line-height: 1.6; }

    /* ── dossier ── */
    .dossier-head { display: flex; justify-content: space-between; gap: 18px; align-items: start; margin-bottom: 22px; }
    .state { padding: 8px 12px; border: 1px solid var(--line); font-family: var(--font-mono); color: var(--ink-soft); background: rgba(255,250,241,.7); white-space: nowrap; }
    .state.running { color: var(--seal-red); border-color: rgba(255,36,66,.28); box-shadow: 0 0 0 5px rgba(255,36,66,.08); }
    .state.success { color: var(--ink); border-color: rgba(184,135,70,.65); }
    .state.failed { color: #fff8ef; background: var(--seal-red); }
    .metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 16px 0 22px; }
    .metric { padding: 12px 10px; border: 1px solid var(--line); background: rgba(255,250,241,.48); }
    .metric span { display: block; color: var(--muted); font-size: 11px; }
    .metric strong { display: block; margin-top: 4px; font-family: var(--font-mono); font-size: 20px; }
    .progress-top { display: flex; justify-content: space-between; color: var(--ink-soft); font-family: var(--font-mono); font-size: 13px; }
    .track { position: relative; height: 14px; margin: 8px 0 24px; border: 1px solid var(--line); background: rgba(32,24,22,.05); overflow: hidden; }
    .fill { height: 100%; width: 0%; background: linear-gradient(90deg, var(--seal-red), var(--xhs-red)); position: relative; box-shadow: inset 0 1px 3px rgba(255,255,255,.22); }
    .fill.running { background-image: repeating-linear-gradient(45deg, rgba(255,255,255,.18) 0 8px, transparent 8px 16px), linear-gradient(90deg, var(--seal-red), var(--xhs-red)); background-size: 28px 28px, 100% 100%; }
    .fill.running::after { content: ""; position: absolute; inset: 0; background: linear-gradient(90deg, transparent 0%, rgba(255,255,255,.18) 50%, transparent 100%); background-size: 200% 100%; animation: shimmer-sweep 2.2s ease-in-out infinite; }
    @keyframes shimmer-sweep { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
    .timeline { position: relative; margin: 0 0 20px; padding-left: 26px; }
    .timeline:before { content: ""; position: absolute; left: 8px; top: 8px; bottom: 8px; width: 2px; background: linear-gradient(var(--xhs-red), rgba(185,22,45,.16)); }
    .step { position: relative; padding: 0 0 16px; color: var(--muted); }
    .step:before { content: ""; position: absolute; left: -24px; top: 5px; width: 12px; height: 12px; border: 1px solid currentColor; background: var(--paper); }
    .step.active { color: var(--seal-red); }
    .step.active:before { background: var(--xhs-red); border-color: var(--xhs-red); animation: pulse-step 1.4s ease-in-out infinite; }
    @keyframes pulse-step { 0%,100% { box-shadow: 0 0 0 0 rgba(255,36,66,.25); } 50% { box-shadow: 0 0 0 8px rgba(255,36,66,.06); } }
    .step.done { color: var(--ink); }
    .step.done:before { border-color: var(--seal-red); box-shadow: inset 0 0 0 3px var(--paper); background: var(--seal-red); }
    .step strong { display: block; font-size: 14px; }
    .step span { font-size: 11px; }
    .events { max-height: 160px; overflow: auto; border-top: 1px solid var(--line); padding-top: 12px; }
    .event { display: grid; grid-template-columns: 68px 1fr; gap: 8px; padding: 7px 0; border-bottom: 1px dashed rgba(32,24,22,.10); font-size: 12px; }
    .event time { color: var(--muted); font-family: var(--font-mono); }
    .event.warn b { color: var(--gold); }
    .event.error b { color: var(--seal-red); }
    .downloads { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }
    .download { display: inline-block; padding: 11px 14px; border-left: 5px solid var(--seal-red); border-top: 1px solid var(--line); border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); color: var(--ink); background: rgba(255,250,241,.74); text-decoration: none; transition: transform .2s, background .2s; }
    .download:hover { transform: translateY(-2px); background: #fffaf1; }

    /* ── cookie panel ── */
    .cookie-status { padding: 12px 14px; border: 1px solid var(--line); background: rgba(255,250,241,.56); font-family: var(--font-mono); font-size: 13px; color: var(--ink-soft); }
    .cookie-status.ok { color: var(--ink); border-color: rgba(184,135,70,.65); }
    .auth-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
    .steps { margin: 12px 0 0; padding: 12px 14px 12px 28px; border: 1px dashed rgba(32,24,22,.18); color: var(--ink-soft); font-size: 13px; line-height: 1.7; background: rgba(255,250,241,.42); }
    .steps code { font-family: var(--font-mono); color: var(--seal-red); }
    .phone-grid { display: grid; grid-template-columns: 80px 1fr; gap: 12px; align-items: end; }
    .code-row { display: flex; gap: 10px; align-items: flex-end; }
    .code-row input { flex: 1; }
    .code-row button { flex: 0 0 auto; margin-top: 0; padding: 11px 14px; }
    .login-status { min-height: 20px; margin-top: 10px; color: var(--ink-soft); font-size: 13px; transition: color .22s; }
    .login-status.ok { color: var(--seal-red); font-weight: 600; }
    .login-note { margin: 16px 0 0; padding: 12px 14px; border: 1px dashed rgba(32,24,22,.18); color: var(--ink-soft); font-size: 12px; line-height: 1.7; background: rgba(255,250,241,.42); }

    /* ── ai panel ── */
    .ai-config-row { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }

    @media (max-width: 860px) {
      .panel-grid, .auth-grid, .ai-config-row { grid-template-columns: 1fr; }
      .metrics { grid-template-columns: repeat(2, 1fr); }
      .nav-bar { padding: 0 12px; }
      .nav-tab { padding: 12px 14px; font-size: 13px; }
      .header-brand h1 { font-size: 22px; }
    }
    @media (prefers-reduced-motion: reduce) { * { animation: none !important; transition-duration: .01ms !important; } }
  </style>
</head>
<body>
  <header class="app-header">
    <div class="header-brand">
      <h1>\u7ea2\u85af\u91c7\u91c7</h1>
      <span class="overline">DATA CONSOLE</span>
    </div>
    <div class="header-right">
      <span class="cookie-dot" id="headerCookieDot" title="Cookie \u72b6\u6001"></span>
      <span class="cookie-dot-label" id="headerCookieLabel">Cookie</span>
      <button id="logoutBtn" class="btn-small" type="button">\u9000\u51fa</button>
    </div>
  </header>

  <nav class="nav-bar">
    <button class="nav-tab active" data-panel="collect">\u91c7\u96c6\u5de5\u4f5c\u53f0</button>
    <button class="nav-tab" data-panel="viral">\u7206\u6b3e\u5de5\u4f5c\u53f0</button>
    <button class="nav-tab" data-panel="cookie">Cookie \u7ba1\u7406</button>
    <button class="nav-tab" data-panel="ai">AI \u5206\u6790</button>
  </nav>

  <!-- ── Tab 1: \u91c7\u96c6\u5de5\u4f5c\u53f0 ── -->
  <div class="tab-panel active" id="panelCollect">
    <div class="panel-grid">
      <form id="collectForm" class="card card-padded command">
        <h2 class="section-title">\u91c7\u96c6\u6307\u4ee4</h2>

        <!-- mode tabs -->
        <div style="display:flex;gap:0;border-bottom:2px solid var(--line);margin-bottom:18px">
          <button type="button" class="mode-tab active" data-mode="keyword" style="flex:1;padding:8px 0 10px;font-size:13px;color:var(--seal-red);cursor:pointer;background:none;border:0;border-bottom:3px solid var(--seal-red);margin-bottom:-2px;font-weight:600;transition:.2s">\u5173\u952e\u8bcd\u641c\u7d22</button>
          <button type="button" class="mode-tab" data-mode="url" style="flex:1;padding:8px 0 10px;font-size:13px;color:var(--muted);cursor:pointer;background:none;border:0;border-bottom:3px solid transparent;margin-bottom:-2px;transition:.2s">URL \u91c7\u96c6</button>
          <button type="button" class="mode-tab" data-mode="user" style="flex:1;padding:8px 0 10px;font-size:13px;color:var(--muted);cursor:pointer;background:none;border:0;border-bottom:3px solid transparent;margin-bottom:-2px;transition:.2s">\u7528\u6237\u4e3b\u9875</button>
        </div>

        <input type="hidden" name="mode" id="collectMode" value="keyword">

        <!-- keyword mode -->
        <div class="mode-panel" id="panelModeKeyword">
          <label for="keyword">Keyword</label>
          <input id="keyword" name="keyword" type="text" maxlength="80" placeholder="\u8f93\u5165\u5173\u952e\u8bcd\uff0c\u5982\uff1a\u82cf\u5dde\u63a2\u5e97" required>
          <div class="row">
            <div>
              <label for="count">Count</label>
              <input id="count" name="count" type="number" min="1" max=""" + str(MAX_COUNT) + """" value="10" required>
            </div>
            <div class="switch-line">
              <div class="switch-copy"><strong>\u540c\u6b65\u91c7\u96c6\u8bc4\u8bba</strong><span>\u5efa\u8bae 1-10 \u7bc7\u6d4b\u8bd5</span></div>
              <label class="switch"><input id="withComments" name="with_comments" type="checkbox"><span class="slider"></span></label>
            </div>
          </div>
        </div>

        <!-- url mode -->
        <div class="mode-panel" id="panelModeUrl" style="display:none">
          <label for="noteUrl">\u7b14\u8bb0\u94fe\u63a5</label>
          <input id="noteUrl" name="note_url" type="text" placeholder="\u7c98\u8d34\u5c0f\u7ea2\u4e66\u7b14\u8bb0\u94fe\u63a5\uff0c\u5982 https://www.xiaohongshu.com/explore/xxx">
          <div class="switch-line" style="margin-top:20px">
            <div class="switch-copy"><strong>\u540c\u6b65\u91c7\u96c6\u8bc4\u8bba</strong></div>
            <label class="switch"><input id="withCommentsUrl" name="with_comments" type="checkbox"><span class="slider"></span></label>
          </div>
        </div>

        <!-- user mode -->
        <div class="mode-panel" id="panelModeUser" style="display:none">
          <label for="userUrl">\u7528\u6237\u4e3b\u9875\u94fe\u63a5</label>
          <input id="userUrl" name="user_url" type="text" placeholder="\u7c98\u8d34\u5c0f\u7ea2\u4e66\u7528\u6237\u4e3b\u9875\u94fe\u63a5\uff0c\u5982 https://www.xiaohongshu.com/user/profile/xxx">
          <div class="switch-line" style="margin-top:20px">
            <div class="switch-copy"><strong>\u540c\u6b65\u91c7\u96c6\u8bc4\u8bba</strong><span>\u91c7\u96c6\u8be5\u7528\u6237\u6240\u6709\u7b14\u8bb0\u7684\u8bc4\u8bba</span></div>
            <label class="switch"><input id="withCommentsUser" name="with_comments" type="checkbox"><span class="slider"></span></label>
          </div>
        </div>

        <button id="startBtn" class="primary" type="submit">\u5f00\u59cb\u91c7\u96c6</button>
        <p class="hint">\u91c7\u96c6\u671f\u95f4\u8bf7\u52ff\u9ad8\u9891\u5237\u65b0\uff1b\u4efb\u52a1\u8fdb\u5ea6\u4f1a\u81ea\u52a8\u66f4\u65b0\u3002</p>

        <div style="margin-top:18px;border-top:1px solid var(--line);padding-top:14px">
          <div style="font-size:11px;color:var(--seal-red);letter-spacing:.1em;margin-bottom:6px">WATERMARK REMOVAL</div>
          <div style="display:flex;gap:8px">
            <input id="wmNoteUrl" type="text" placeholder="\u7c98\u8d34\u7b14\u8bb0\u94fe\u63a5\u53bb\u6c34\u5370" style="flex:1;font-size:14px">
            <button id="wmBtn" class="secondary" type="button" style="flex:0 0 auto;margin-top:0;white-space:nowrap;padding:8px 16px">\u63d0\u53d6</button>
          </div>
          <div id="wmResult" style="margin-top:8px;font-size:13px"></div>
        </div>
      </form>

      <section class="card card-padded">
        <div class="dossier-head">
          <div>
            <div class="overline">LIVE DOSSIER</div>
            <h2 class="section-title" style="margin-bottom:0">\u91c7\u96c6\u8fdb\u5ea6</h2>
          </div>
          <div id="state" class="state">\u5f85\u547d</div>
        </div>
        <div class="metrics">
          <div class="metric"><span>\u76ee\u6807</span><strong id="mTarget">0</strong></div>
          <div class="metric"><span>\u7b14\u8bb0</span><strong id="mNotes">0</strong></div>
          <div class="metric"><span>\u8bc4\u8bba</span><strong id="mComments">0</strong></div>
          <div class="metric"><span>\u5931\u8d25</span><strong id="mFailed">0</strong></div>
        </div>
        <div class="progress-top"><span id="message">\u7b49\u5f85\u4efb\u52a1</span><span id="percent">0%</span></div>
        <div class="track"><div id="fill" class="fill"></div></div>
        <div id="timeline" class="timeline">
          <div class="step" data-stage="init"><strong>\u51c6\u5907\u4efb\u52a1</strong><span>\u6821\u9a8c\u5173\u952e\u8bcd\u4e0e Cookie</span></div>
          <div class="step" data-stage="search"><strong>\u641c\u7d22\u7b14\u8bb0</strong><span>\u8bf7\u6c42\u5173\u952e\u8bcd\u641c\u7d22\u7ed3\u679c</span></div>
          <div class="step" data-stage="notes"><strong>\u89e3\u6790\u8be6\u60c5</strong><span>\u63d0\u53d6\u6807\u9898\u3001\u4f5c\u8005\u3001\u4e92\u52a8\u6570\u636e</span></div>
          <div class="step" data-stage="comments"><strong>\u91c7\u96c6\u8bc4\u8bba</strong><span>\u62c9\u53d6\u4e00\u7ea7\u8bc4\u8bba\u4e0e\u4e8c\u7ea7\u8bc4\u8bba</span></div>
          <div class="step" data-stage="complete"><strong>\u5b8c\u6210\u5f52\u6863</strong><span>\u6587\u4ef6\u51c6\u5907\u4e0b\u8f7d</span></div>
        </div>
        <div id="downloads" class="downloads"></div>
        <div id="aiSection" style="display:none;margin-top:18px;border-top:1px solid var(--line);padding-top:16px">
          <div class="overline">AI ANALYSIS</div>
          <h3 style="font-family:var(--font-display);font-size:20px;margin:4px 0 10px">\u667a\u80fd\u5206\u6790</h3>
          <label for="aiSkill">\u5206\u6790\u6a21\u5f0f</label>
          <select id="aiSkill">
            <option value="trend">\u5185\u5bb9\u8d8b\u52bf\u5206\u6790</option>
            <option value="sentiment">\u8bc4\u8bba\u60c5\u611f\u5206\u6790</option>
            <option value="strategy">\u9009\u9898\u7b56\u7565\u5efa\u8bae</option>
            <option value="persona">\u7528\u6237\u753b\u50cf\u5206\u6790</option>
            <option value="viral">\u7206\u6b3e\u7279\u5f81\u63d0\u53d6</option>
            <option value="custom">\u81ea\u5b9a\u4e49\u5206\u6790</option>
          </select>
          <div id="customPromptWrap" style="display:none">
            <label for="aiCustomPrompt">\u81ea\u5b9a\u4e49\u5206\u6790\u9700\u6c42</label>
            <textarea id="aiCustomPrompt" placeholder="\u63cf\u8ff0\u4f60\u60f3\u5206\u6790\u4ec0\u4e48\uff0c\u5982\uff1a\u5206\u6790\u8fd9\u4e9b\u7b14\u8bb0\u7684\u6807\u9898\u98ce\u683c\u548c\u53d7\u4f17\u753b\u50cf\uff0c\u7ed9\u51fa5\u4e2a\u4f18\u5316\u5efa\u8bae"></textarea>
          </div>
          <button id="analyzeBtn" class="primary" type="button" disabled>\u5f00\u59cb AI \u5206\u6790</button>
          <div id="aiNoConfig" style="display:none;padding:12px;border:1px dashed var(--line);background:rgba(255,250,241,.5);margin-top:12px;font-size:13px;color:var(--ink-soft)">
            \u26a0\ufe0f AI \u5c1a\u672a\u914d\u7f6e\uff0c\u8bf7\u5148\u5728 <a href="javascript:void(0)" onclick="document.querySelector('[data-panel=ai]').click()" style="color:var(--seal-red);text-decoration:underline">AI \u5206\u6790</a> \u9875\u9762\u914d\u7f6e API Key\u3002
          </div>
          <div id="aiResult" style="display:none;margin-top:14px">
            <div class="progress-top"><span id="aiStatusText">\u5206\u6790\u4e2d\u2026</span></div>
            <div id="aiResultContent" style="padding:14px;border:1px solid var(--line);background:rgba(255,250,241,.62);font-size:14px;line-height:1.7;max-height:480px;overflow:auto;white-space:pre-wrap;font-family:var(--font-body)"></div>
          </div>
        </div>
        <div id="events" class="events"></div>
      </section>
    </div>
  </div>

  <!-- ── Viral 工作台 ── -->
  <div class="tab-panel" id="panelViral">
    <div class="panel-grid">
      <form id="viralForm" class="card card-padded command">
        <h2 class="section-title">爆款采集</h2>
        <p class="hint">按热度排序采集对标笔记，后续用于爆款拆解和自有风格草稿生成。</p>
        <label for="viralKeyword">关键词</label>
        <input id="viralKeyword" name="keyword" type="text" maxlength="80" placeholder="如：苏州探店、职场穿搭、育儿好物">
        <div class="row">
          <div>
            <label for="viralCount">采集数量</label>
            <input id="viralCount" name="count" type="number" min="1" max=""" + str(MAX_COUNT) + """" value="10">
          </div>
          <div>
            <label for="viralSort">热度排序</label>
            <select id="viralSort" name="sort_type_choice">
              <option value="2">最多点赞</option>
              <option value="3">最多评论</option>
              <option value="4">最多收藏</option>
            </select>
          </div>
        </div>
        <div class="row">
          <div>
            <label for="viralNoteType">笔记类型</label>
            <select id="viralNoteType" name="note_type">
              <option value="0">不限</option>
              <option value="1">视频</option>
              <option value="2">图文</option>
            </select>
          </div>
          <div>
            <label for="viralNoteTime">发布时间</label>
            <select id="viralNoteTime" name="note_time">
              <option value="0">不限</option>
              <option value="1">一天内</option>
              <option value="2">一周内</option>
              <option value="3">半年内</option>
            </select>
          </div>
        </div>
        <div class="switch-line">
          <div class="switch-copy"><strong>同步采集评论</strong><span>评论可辅助提炼痛点，建议小批量开启</span></div>
          <label class="switch"><input id="viralWithComments" name="with_comments" type="checkbox"><span class="slider"></span></label>
        </div>
        <button id="viralStartBtn" class="primary" type="submit">采集爆款样本</button>
      </form>

      <section class="card card-padded">
        <div class="dossier-head">
          <div>
            <div class="overline">VIRAL PIPELINE</div>
            <h2 class="section-title" style="margin-bottom:0">工作台进度</h2>
          </div>
          <div id="viralState" class="state">待命</div>
        </div>
        <div class="metrics">
          <div class="metric"><span>目标</span><strong id="viralMTarget">0</strong></div>
          <div class="metric"><span>笔记</span><strong id="viralMNotes">0</strong></div>
          <div class="metric"><span>评论</span><strong id="viralMComments">0</strong></div>
          <div class="metric"><span>失败</span><strong id="viralMFailed">0</strong></div>
        </div>
        <div class="progress-top"><span id="viralMessage">等待任务</span><span id="viralPercent">0%</span></div>
        <div class="track"><div id="viralFill" class="fill"></div></div>
        <div id="viralDownloads" class="downloads"></div>
        <div id="viralEvents" class="events"></div>
      </section>
    </div>

    <div class="panel-grid" style="margin-top:18px">
      <section class="card card-padded">
        <div class="overline">AGENT ANALYSIS</div>
        <h2 class="section-title">爆款拆解</h2>
        <p class="hint">采集完成后，Agent 会提炼标题公式、开头钩子、正文结构、封面共性、标签策略和风险点。</p>
        <button id="viralAnalyzeBtn" class="primary" type="button" disabled>开始爆款拆解</button>
        <div id="viralAnalyzeNoConfig" style="display:none;padding:12px;border:1px dashed var(--line);background:rgba(255,250,241,.5);margin-top:12px;font-size:13px;color:var(--ink-soft)">AI 未配置，请先去 AI 分析页配置 API Key。</div>
        <div id="viralAnalysisResult" style="display:none;margin-top:14px">
          <div class="progress-top"><span id="viralAnalysisStatus">分析中…</span></div>
          <div id="viralAnalysisContent" style="padding:14px;border:1px solid var(--line);background:rgba(255,250,241,.62);font-size:14px;line-height:1.7;max-height:420px;overflow:auto;white-space:pre-wrap;font-family:var(--font-body)"></div>
        </div>
      </section>

      <section class="card card-padded">
        <div class="overline">OWN STYLE DRAFT</div>
        <h2 class="section-title">自有风格草稿</h2>
        <label for="draftStyleProfile">账号定位 / 风格描述</label>
        <textarea id="draftStyleProfile" placeholder="如：苏州本地生活，语气真实克制，偏实用攻略，不夸张种草"></textarea>
        <label for="draftTopicAngle">切入选题角度</label>
        <input id="draftTopicAngle" type="text" placeholder="如：周末半日游路线、低预算探店、职场新人避坑">
        <label for="draftAudience">目标人群</label>
        <input id="draftAudience" type="text" placeholder="如：苏州 25-35 岁上班族、宝妈、新手运营">
        <div class="row">
          <div>
            <label for="draftTone">语气</label>
            <select id="draftTone">
              <option value="专业干货">专业干货</option>
              <option value="朋友聊天">朋友聊天</option>
              <option value="真实种草">真实种草</option>
              <option value="情绪共鸣">情绪共鸣</option>
            </select>
          </div>
          <div>
            <label for="draftCount">生成篇数</label>
            <select id="draftCount">
              <option value="1">1 篇</option>
              <option value="2">2 篇</option>
              <option value="3">3 篇</option>
            </select>
          </div>
        </div>
        <div class="switch-line">
          <div class="switch-copy"><strong>包含图片提示词</strong><span>先生成提示词，不调用生图模型</span></div>
          <label class="switch"><input id="draftImagePrompts" type="checkbox" checked><span class="slider"></span></label>
        </div>
        <button id="draftGenerateBtn" class="primary" type="button" disabled>生成草稿</button>
        <div style="display:flex;gap:10px;margin-top:10px">
          <button id="imageGenerateBtn" class="secondary" type="button" disabled style="flex:1;margin-top:0">生成图片（待接入）</button>
          <button id="publishBtn" class="secondary" type="button" disabled style="flex:1;margin-top:0">一键发布（暂不开放）</button>
        </div>
      </section>
    </div>

    <section class="card card-padded" style="margin-top:18px">
      <div class="dossier-head">
        <div>
          <div class="overline">PREVIEW</div>
          <h2 class="section-title" style="margin-bottom:0">笔记预览</h2>
        </div>
        <div style="display:flex;gap:8px">
          <button id="draftCopyBtn" class="secondary" type="button" disabled style="margin-top:0">复制全文</button>
          <button id="draftDownloadBtn" class="secondary" type="button" disabled style="margin-top:0">下载 Markdown</button>
        </div>
      </div>
      <div id="draftStatus" class="hint">生成草稿后在这里预览。发布前请人工检查原创度、事实准确性和平台规则。</div>
      <div id="draftPreview" style="margin-top:12px;padding:16px;border:1px solid var(--line);background:rgba(255,250,241,.62);font-size:14px;line-height:1.7;min-height:140px;max-height:620px;overflow:auto;white-space:pre-wrap;font-family:var(--font-body)"></div>
    </section>
  </div>

  <div class="tab-panel" id="panelCookie">
    <div class="auth-grid">
      <section class="card card-padded">
        <div class="overline">COOKIE STATUS</div>
        <h2 class="section-title">Cookie \u72b6\u6001</h2>
        <div id="cookieStatus" class="cookie-status">\u8bfb\u53d6\u4e2d</div>
        <div class="auth-actions">
          <button id="refreshCookieBtn" class="secondary" type="button">\u5237\u65b0\u72b6\u6001</button>
          <button id="clearCookieBtn" class="secondary danger" type="button">\u6e05\u9664 Cookie</button>
        </div>
        <label for="cookieInput">\u624b\u52a8\u66ff\u6362 Cookie</label>
        <textarea id="cookieInput" placeholder="\u7c98\u8d34 Request Headers \u91cc\u7684 cookie \u5168\u91cf\u5185\u5bb9"></textarea>
        <ol class="steps">
          <li>\u7535\u8111\u6d4f\u89c8\u5668\u6253\u5f00\u7f51\u9875\u7248\u5c0f\u7ea2\u4e66\u5e76\u767b\u5f55\u3002</li>
          <li>\u6309 <code>F12</code> \u6253\u5f00\u5f00\u53d1\u8005\u5de5\u5177\uff0c\u8fdb\u5165 <code>Network</code>\u3002</li>
          <li>\u5237\u65b0\u9875\u9762\u6216\u70b9\u4efb\u610f\u5c0f\u7ea2\u4e66\u8bf7\u6c42\uff0c\u627e\u5230 <code>scripting</code> \u8bf7\u6c42\u3002</li>
          <li>\u5728 <code>Request Headers</code> \u4e2d\u590d\u5236 <code>cookie</code> \u7684\u5b8c\u6574\u5185\u5bb9\u3002</li>
          <li>\u7c98\u8d34\u5230\u4e0a\u65b9\u8f93\u5165\u6846\uff0c\u70b9\u51fb\u4fdd\u5b58 Cookie\u3002</li>
        </ol>
        <button id="saveCookieBtn" class="primary" type="button">\u4fdd\u5b58 Cookie</button>
      </section>

      <section class="card card-padded">
        <div class="overline">PHONE LOGIN</div>
        <h2 class="section-title">\u4e00\u952e\u83b7\u53d6 Cookie</h2>
        <p class="hint">\u4f7f\u7528\u5c0f\u7ea2\u4e66\u7ed1\u5b9a\u624b\u673a\u53f7\u63a5\u6536\u9a8c\u8bc1\u7801\uff0c\u9a8c\u8bc1\u6210\u529f\u540e\u81ea\u52a8\u4fdd\u5b58\u91c7\u96c6 Cookie\u3002</p>
        <div class="phone-grid">
          <div>
            <label for="phoneZone">\u533a\u53f7</label>
            <input id="phoneZone" type="text" value="86" inputmode="numeric">
          </div>
          <div>
            <label for="phoneInput">\u624b\u673a\u53f7</label>
            <input id="phoneInput" type="tel" placeholder="\u8f93\u5165\u624b\u673a\u53f7" autocomplete="tel">
          </div>
        </div>
        <label for="phoneCodeInput">\u77ed\u4fe1\u9a8c\u8bc1\u7801</label>
        <div class="code-row">
          <input id="phoneCodeInput" type="text" placeholder="6 \u4f4d\u9a8c\u8bc1\u7801" inputmode="numeric" autocomplete="one-time-code">
          <button id="sendPhoneCodeBtn" class="secondary" type="button">\u53d1\u9001</button>
        </div>
        <div id="phoneLoginStatus" class="login-status">\u5148\u53d1\u9001\u9a8c\u8bc1\u7801</div>
        <div class="auth-actions">
          <button id="phoneLoginBtn" class="primary" type="button" disabled style="flex:1;margin-top:0">\u767b\u5f55\u5e76\u4fdd\u5b58 Cookie</button>
        </div>
                <div class="login-note">验证码会话 5 分钟内有效。成功后自动写入 .env；失败不会覆盖当前 Cookie。</div>
      </section>
    </div>

    <div class="auth-grid" style="margin-top:18px">
      <section class="card card-padded">
        <div class="overline">QR LOGIN</div>
        <h2 class="section-title">扫码登录</h2>
        <p class="hint">使用小红书 App 扫描二维码，自动获取采集 Cookie。</p>
        <div id="qrContainer" style="display:none;text-align:center;margin:14px 0">
          <img id="qrImage" src="" style="max-width:220px;border:1px solid var(--line)" alt="QR Code">
          <p id="qrStatus" style="margin-top:8px;color:var(--ink-soft);font-size:13px">请使用小红书 App 扫描二维码</p>
        </div>
        <button id="genQrBtn" class="primary" type="button">生成二维码</button>
        <button id="cancelQrBtn" class="secondary" type="button" style="display:none;width:100%;margin-top:8px">取消</button>
      </section>

      <section class="card card-padded">
        <div class="overline">USER SEARCH</div>
        <h2 class="section-title">搜索用户</h2>
        <label for="searchUserQuery">关键词</label>
        <input id="searchUserQuery" type="text" placeholder="搜索小红书用户，如：美妆博主">
        <button id="searchUserBtn" class="primary" type="button">搜索</button>
        <div id="searchUserResult" style="margin-top:14px;max-height:320px;overflow:auto"></div>
      </section>
    </div>
  </div>

  <!-- ── AI 分析 ── -->
  <div class="tab-panel" id="panelAi">
    <div class="panel-grid">
      <section class="card card-padded">
        <div class="overline">AI SETTINGS</div>
        <h2 class="section-title">AI \u5206\u6790\u914d\u7f6e</h2>
        <label for="aiProvider">AI \u63d0\u4f9b\u5546</label>
        <select id="aiProvider">
          <option value="">-- \u9009\u62e9\u63d0\u4f9b\u5546 --</option>
          <option value="claude">Claude (Anthropic)</option>
          <option value="openai">OpenAI</option>
          <option value="deepseek">DeepSeek</option>
        </select>
        <label for="aiApiKey">API Key</label>
        <input id="aiApiKey" type="password" placeholder="sk-...">
        <label for="aiModel">\u6a21\u578b\u540d\u79f0</label>
        <input id="aiModel" type="text" placeholder="\u9ed8\u8ba4\u81ea\u52a8\u586b\u5145">
        <div class="switch-line">
          <div class="switch-copy"><strong>\u542f\u7528 AI \u5206\u6790</strong><span>\u91c7\u96c6\u5b8c\u6210\u540e\u53ef\u5bf9 Excel \u6570\u636e\u505a\u667a\u80fd\u5206\u6790</span></div>
          <label class="switch"><input id="aiEnabled" type="checkbox"><span class="slider"></span></label>
        </div>
        <div style="display:flex;gap:10px;margin-top:22px">
          <button id="saveAiConfigBtn" class="primary" type="button" style="flex:1;margin-top:0">\u4fdd\u5b58\u914d\u7f6e</button>
          <button id="testAiBtn" class="secondary" type="button" style="flex:0 0 auto;margin-top:0">\u6d4b\u8bd5\u8fde\u63a5</button>
        </div>
        <div id="aiTestResult" style="display:none;margin-top:10px;padding:10px 12px;font-size:13px;font-family:var(--font-mono)"></div>
        <div id="aiConfigStatus" class="cookie-status" style="margin-top:14px">\u672a\u914d\u7f6e</div>
      </section>

      <section class="card card-padded">
        <div class="overline">ANALYSIS SKILLS</div>
        <h2 class="section-title">\u5206\u6790\u6280\u80fd</h2>
        <p class="hint">\u91c7\u96c6\u4efb\u52a1\u5b8c\u6210\u540e\uff0c\u5728\u201c\u91c7\u96c6\u5de5\u4f5c\u53f0\u201d\u7684\u8fdb\u5ea6\u9762\u677f\u4e2d\u70b9\u51fb\u201c\u5f00\u59cb AI \u5206\u6790\u201d\u3002</p>
        <div style="margin-top:18px">
          <div style="display:flex;align-items:center;gap:8px;padding:10px 0;border-bottom:1px solid var(--line)"><span style="font-size:16px">📊</span><span>\u5185\u5bb9\u8d8b\u52bf\u5206\u6790</span></div>
          <div style="display:flex;align-items:center;gap:8px;padding:10px 0;border-bottom:1px solid var(--line)"><span style="font-size:16px">💬</span><span>\u8bc4\u8bba\u60c5\u611f\u5206\u6790</span></div>
          <div style="display:flex;align-items:center;gap:8px;padding:10px 0;border-bottom:1px solid var(--line)"><span style="font-size:16px">🎯</span><span>\u9009\u9898\u7b56\u7565\u5efa\u8bae</span></div>
          <div style="display:flex;align-items:center;gap:8px;padding:10px 0;border-bottom:1px solid var(--line)"><span style="font-size:16px">👤</span><span>\u7528\u6237\u753b\u50cf\u5206\u6790</span></div>
          <div style="display:flex;align-items:center;gap:8px;padding:10px 0"><span style="font-size:16px">🔥</span><span>\u7206\u6b3e\u7279\u5f81\u63d0\u53d6</span></div>
        </div>
        <p class="hint" style="margin-top:14px">\u652f\u6301 Claude / OpenAI / DeepSeek \u4e09\u5bb6\u5927\u6a21\u578b\uff0c\u8fd8\u53ef\u81ea\u5b9a\u4e49\u5206\u6790\u9700\u6c42\u3002</p>
      </section>
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js"></script>
  <script>
    const form = document.getElementById('collectForm');
    const startBtn = document.getElementById('startBtn');
    const stateEl = document.getElementById('state');
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
    const phoneZone = document.getElementById('phoneZone');
    const phoneInput = document.getElementById('phoneInput');
    const phoneCodeInput = document.getElementById('phoneCodeInput');
    const phoneLoginStatus = document.getElementById('phoneLoginStatus');
    const sendPhoneCodeBtn = document.getElementById('sendPhoneCodeBtn');
    const phoneLoginBtn = document.getElementById('phoneLoginBtn');
    const genQrBtn = document.getElementById('genQrBtn');
    const cancelQrBtn = document.getElementById('cancelQrBtn');
    const qrContainer = document.getElementById('qrContainer');
    const qrImage = document.getElementById('qrImage');
    const qrStatus = document.getElementById('qrStatus');
    const searchUserQuery = document.getElementById('searchUserQuery');
    const searchUserBtn = document.getElementById('searchUserBtn');
    const searchUserResult = document.getElementById('searchUserResult');
    const headerCookieDot = document.getElementById('headerCookieDot');
    const headerCookieLabel = document.getElementById('headerCookieLabel');
    const aiProvider = document.getElementById('aiProvider');
    const aiApiKey = document.getElementById('aiApiKey');
    const aiModel = document.getElementById('aiModel');
    const aiEnabled = document.getElementById('aiEnabled');
    const saveAiConfigBtn = document.getElementById('saveAiConfigBtn');
    const testAiBtn = document.getElementById('testAiBtn');
    const aiTestResult = document.getElementById('aiTestResult');
    const aiConfigStatus = document.getElementById('aiConfigStatus');
    const aiSection = document.getElementById('aiSection');
    const aiSkill = document.getElementById('aiSkill');
    const aiCustomPrompt = document.getElementById('aiCustomPrompt');
    const customPromptWrap = document.getElementById('customPromptWrap');
    const analyzeBtn = document.getElementById('analyzeBtn');
    const aiResult = document.getElementById('aiResult');
    const aiStatusText = document.getElementById('aiStatusText');
    const aiResultContent = document.getElementById('aiResultContent');
    const viralForm = document.getElementById('viralForm');
    const viralStartBtn = document.getElementById('viralStartBtn');
    const viralState = document.getElementById('viralState');
    const viralMessage = document.getElementById('viralMessage');
    const viralPercent = document.getElementById('viralPercent');
    const viralFill = document.getElementById('viralFill');
    const viralMTarget = document.getElementById('viralMTarget');
    const viralMNotes = document.getElementById('viralMNotes');
    const viralMComments = document.getElementById('viralMComments');
    const viralMFailed = document.getElementById('viralMFailed');
    const viralDownloads = document.getElementById('viralDownloads');
    const viralEvents = document.getElementById('viralEvents');
    const viralAnalyzeBtn = document.getElementById('viralAnalyzeBtn');
    const viralAnalyzeNoConfig = document.getElementById('viralAnalyzeNoConfig');
    const viralAnalysisResult = document.getElementById('viralAnalysisResult');
    const viralAnalysisStatus = document.getElementById('viralAnalysisStatus');
    const viralAnalysisContent = document.getElementById('viralAnalysisContent');
    const draftStyleProfile = document.getElementById('draftStyleProfile');
    const draftTopicAngle = document.getElementById('draftTopicAngle');
    const draftAudience = document.getElementById('draftAudience');
    const draftTone = document.getElementById('draftTone');
    const draftCount = document.getElementById('draftCount');
    const draftImagePrompts = document.getElementById('draftImagePrompts');
    const draftGenerateBtn = document.getElementById('draftGenerateBtn');
    const draftStatus = document.getElementById('draftStatus');
    const draftPreview = document.getElementById('draftPreview');
    const draftCopyBtn = document.getElementById('draftCopyBtn');
    const draftDownloadBtn = document.getElementById('draftDownloadBtn');
    const reduceMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    let pollTimer = null;
    let phoneSessionId = '';
    let aiPollTimer = null;
    let viralPollTimer = null;
    let viralAnalysisPollTimer = null;
    let draftPollTimer = null;
    let viralJobPollInFlight = false;
    let viralAnalysisFailures = 0;
    let draftFailures = 0;
    let aiConfigured = false;
    let currentJobId = '';
    let currentViralJobId = '';
    let viralAnalysisId = '';
    let draftRawMarkdown = '';
    let fillLoopTween = null;
    let activeStepTween = null;
    let lastStage = '';
    let lastState = '';
    let lastEventsKey = '';
    let lastDownloadsKey = '';
    let lastViralEventsKey = '';
    let lastViralDownloadsKey = '';

    function hasGsap() { return Boolean(window.gsap); }
    function prefersReducedMotion() { return reduceMotionQuery.matches; }
    function motionDuration(seconds) { return prefersReducedMotion() ? 0 : seconds; }

    // ── tab switching ──
    document.querySelectorAll('.nav-tab').forEach(function(tab) {
      tab.addEventListener('click', function() {
        document.querySelectorAll('.nav-tab').forEach(function(t) { t.classList.remove('active'); });
        tab.classList.add('active');
        document.querySelectorAll('.tab-panel').forEach(function(p) { p.classList.remove('active'); });
        document.getElementById('panel' + tab.dataset.panel.charAt(0).toUpperCase() + tab.dataset.panel.slice(1)).classList.add('active');
      });
    });

    // ── header cookie dot ──
    function updateHeaderCookieDot(ok) {
      headerCookieDot.className = 'cookie-dot' + (ok ? ' ok' : '');
      headerCookieLabel.textContent = ok ? 'Cookie \u5df2\u914d\u7f6e' : 'Cookie \u672a\u914d\u7f6e';
    }

    // ── GSAP ──
    function stopLoopingMotion() {
      if (fillLoopTween) { fillLoopTween.kill(); fillLoopTween = null; }
      if (activeStepTween) { activeStepTween.kill(); activeStepTween = null; }
      if (hasGsap()) gsap.set(steps, { clearProps: 'boxShadow' });
    }
    function runIntroMotion() {
      if (!hasGsap()) return;
      gsap.set(['.card'], { autoAlpha: 1, clearProps: 'visibility' });
      if (prefersReducedMotion()) return;
      gsap.timeline({ defaults: { duration: 0.52, ease: 'power3.out' } })
        .from('.app-header', { y: -12, autoAlpha: 0 })
        .from('.nav-bar', { autoAlpha: 0 }, '-=0.28')
        .from('.panel-grid', { y: 10, autoAlpha: 0 }, '-=0.20');
    }
    function animateProgress(percent, isRunning) {
      const safePercent = Math.max(0, Math.min(100, Number(percent || 0)));
      fillEl.className = 'fill ' + (isRunning ? 'running' : '');
      if (!hasGsap()) { fillEl.style.width = safePercent + '%'; return; }
      gsap.to(fillEl, { width: safePercent + '%', duration: motionDuration(0.42), ease: 'power2.out', overwrite: 'auto' });
      if (!isRunning || prefersReducedMotion()) {
        if (fillLoopTween) { fillLoopTween.kill(); fillLoopTween = null; }
        gsap.set(fillEl, { backgroundPosition: '0px 0px, 0px 0px' });
        return;
      }
      if (!fillLoopTween) fillLoopTween = gsap.to(fillEl, { backgroundPosition: '28px 0px, 0px 0px', duration: 1.2, ease: 'none', repeat: -1 });
    }
    function animateMetric(el, nextValue) {
      const next = Number(nextValue || 0);
      const current = Number(el.textContent || 0);
      if (!hasGsap() || prefersReducedMotion() || current === next) { el.textContent = next; return; }
      if (next > current) gsap.fromTo(el, { scale: 1.06, color: '#b9162d' }, { scale: 1, color: 'var(--ink)', duration: 0.45, ease: 'back.out(1.7)', overwrite: 'auto', clearProps: 'transform' });
      const counter = { value: current };
      gsap.to(counter, { value: next, duration: 0.38, ease: 'power2.out', overwrite: 'auto', onUpdate: function() { el.textContent = Math.round(counter.value); } });
    }
    function animateStatusChange(el) {
      if (!hasGsap() || prefersReducedMotion()) return;
      gsap.fromTo(el, { y: -4, autoAlpha: 0.72 }, { y: 0, autoAlpha: 1, duration: 0.24, ease: 'power2.out', overwrite: 'auto', clearProps: 'transform,opacity,visibility' });
    }
    function animateTimeline(stage, state) {
      const active = ['init','search','notes','comments','complete'].indexOf(stage);
      steps.forEach(function(step, index) {
        step.classList.toggle('done', active > index || state === 'success');
        step.classList.toggle('active', active === index && state === 'running');
      });
      if (!hasGsap()) return;
      if (stage !== lastStage || state !== lastState) {
        if (activeStepTween) { activeStepTween.kill(); activeStepTween = null; }
        const activeStep = steps[active];
        if (activeStep && state === 'running') {
          gsap.fromTo(activeStep, { x: -4 }, { x: 0, duration: motionDuration(0.28), ease: 'power2.out', overwrite: 'auto', clearProps: 'transform' });
          if (!prefersReducedMotion()) activeStepTween = gsap.to(activeStep, { boxShadow: '0 0 0 6px rgba(255,36,66,0.08)', duration: 0.9, ease: 'sine.inOut', repeat: -1, yoyo: true, overwrite: 'auto' });
        }
      }
      if (state === 'success' || state === 'failed') stopLoopingMotion();
      lastStage = stage || ''; lastState = state || '';
    }
    function eventsKey(events) { return (events || []).map(function(e) { return [e.time,e.title,e.detail,e.level].join('|'); }).join('::'); }
    function downloadsKey(downloads) { return (downloads || []).map(function(d) { return [d.label,d.file].join('|'); }).join('::'); }
    function renderEvents(events) {
      const key = eventsKey(events);
      eventsEl.innerHTML = (events || []).slice().reverse().map(function(event) {
        return '<div class="event ' + (event.level||'info') + '"><time>' + event.time + '</time><div><b>' + escapeHtml(event.title) + '</b><br>' + escapeHtml(event.detail||'') + '</div></div>';
      }).join('');
      if (hasGsap() && !prefersReducedMotion() && key !== lastEventsKey) {
        const firstEvent = eventsEl.querySelector('.event');
        if (firstEvent) gsap.fromTo(firstEvent, { x: -10, autoAlpha: 0 }, { x: 0, autoAlpha: 1, duration: 0.30, ease: 'power3.out', overwrite: 'auto', clearProps: 'transform,opacity,visibility' });
      }
      lastEventsKey = key;
    }
    function renderDownloads(downloads) {
      const key = downloadsKey(downloads);
      downloadsEl.innerHTML = (downloads || []).map(function(item) {
        return '<a class="download" href="/download?file=' + encodeURIComponent(item.file) + '">\u2193 ' + escapeHtml(item.label) + '</a>';
      }).join('');
      if (hasGsap() && !prefersReducedMotion() && key !== lastDownloadsKey) {
        gsap.fromTo(downloadsEl.querySelectorAll('.download'), { y: 10, autoAlpha: 0 }, { y: 0, autoAlpha: 1, duration: 0.34, ease: 'back.out(1.4)', stagger: 0.06, overwrite: 'auto', clearProps: 'transform,opacity,visibility' });
      }
      lastDownloadsKey = key;
    }
    function setViralJobState(job) {
      const labels = { queued: '排队中', running: '采集中', success: '已完成', failed: '异常' };
      viralState.textContent = labels[job.state] || '待命';
      viralState.className = 'state ' + (job.state || '');
      viralMessage.textContent = job.message || '等待任务';
      viralPercent.textContent = (job.percent || 0) + '%';
      viralFill.style.width = Math.max(0, Math.min(100, Number(job.percent || 0))) + '%';
      viralFill.className = 'fill ' + (job.state === 'running' ? 'running' : '');
      viralMTarget.textContent = job.count || 0;
      viralMNotes.textContent = job.metrics?.notes || 0;
      viralMComments.textContent = job.metrics?.comments || 0;
      viralMFailed.textContent = job.metrics?.failed_comments || 0;
      viralDownloads.innerHTML = (job.downloads || []).map(function(item) {
        return '<a class="download" href="/download?file=' + encodeURIComponent(item.file) + '">↓ ' + escapeHtml(item.label) + '</a>';
      }).join('');
      viralEvents.innerHTML = (job.events || []).slice().reverse().map(function(event) {
        return '<div class="event ' + (event.level||'info') + '"><time>' + event.time + '</time><div><b>' + escapeHtml(event.title) + '</b><br>' + escapeHtml(event.detail||'') + '</div></div>';
      }).join('');
    }

    function resetDraftPreview(message) {
      draftRawMarkdown = '';
      draftStatus.textContent = message || '生成草稿后在这里预览。发布前请人工检查原创度、事实准确性和平台规则。';
      draftPreview.innerHTML = '';
      draftCopyBtn.disabled = true;
      draftDownloadBtn.disabled = true;
    }

    function bindPressMotion(selector) {
      document.querySelectorAll(selector).forEach(function(el) {
        el.addEventListener('pointerdown', function() {
          if (!hasGsap() || prefersReducedMotion() || el.disabled) return;
          gsap.to(el, { scale: 0.985, duration: 0.08, ease: 'power1.out', overwrite: 'auto' });
        });
        var release = function() {
          if (!hasGsap() || prefersReducedMotion()) return;
          gsap.to(el, { scale: 1, duration: 0.16, ease: 'power2.out', overwrite: 'auto', clearProps: 'transform' });
        };
        el.addEventListener('pointerup', release);
        el.addEventListener('pointerleave', release);
      });
    }

    // ── particles ──
    function initParticles() {
      if (prefersReducedMotion()) return;
      const canvas = document.createElement('canvas');
      canvas.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:0';
      document.body.prepend(canvas);
      const ctx = canvas.getContext('2d');
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      let W, H;
      function resize() { W = canvas.width = window.innerWidth*dpr; H = canvas.height = window.innerHeight*dpr; ctx.setTransform(1,0,0,1,0,0); ctx.scale(dpr,dpr); }
      resize(); window.addEventListener('resize', resize);
      const COUNT = 35;
      const particles = Array.from({length: COUNT}, function() { return {
        x: Math.random()*window.innerWidth, y: Math.random()*window.innerHeight,
        vx: (Math.random()-0.5)*0.3, vy: (Math.random()-0.5)*0.3,
        r: 1.3+Math.random()*2.2, a: 0.022+Math.random()*0.045, hue: Math.random()<0.6 ? 350 : 34
      };});
      let raf;
      function draw() {
        ctx.clearRect(0,0,window.innerWidth,window.innerHeight);
        for (let i=0;i<particles.length;i++) {
          for (let j=i+1;j<particles.length;j++) {
            const dx=particles[i].x-particles[j].x, dy=particles[i].y-particles[j].y;
            const dist=Math.sqrt(dx*dx+dy*dy);
            if (dist<140) { ctx.beginPath(); ctx.moveTo(particles[i].x,particles[i].y); ctx.lineTo(particles[j].x,particles[j].y); ctx.strokeStyle='rgba(184,135,70,'+(0.022*(1-dist/140))+')'; ctx.lineWidth=0.5; ctx.stroke(); }
          }
        }
        particles.forEach(function(p) {
          p.x+=p.vx; p.y+=p.vy;
          if (p.x<-20) p.x=window.innerWidth+20; if (p.x>window.innerWidth+20) p.x=-20;
          if (p.y<-20) p.y=window.innerHeight+20; if (p.y>window.innerHeight+20) p.y=-20;
          ctx.beginPath(); ctx.arc(p.x,p.y,p.r,0,Math.PI*2);
          ctx.fillStyle = p.hue===350 ? 'rgba(255,36,66,'+p.a+')' : 'rgba(184,135,70,'+p.a+')';
          ctx.fill();
        });
        raf = requestAnimationFrame(draw);
      }
      draw();
      reduceMotionQuery.addEventListener('change', function() { if (prefersReducedMotion()&&raf) { cancelAnimationFrame(raf); raf=null; canvas.remove(); } }, {once:true});
    }

    // ── card tilt ──
    function initCardTilt() {
      if (prefersReducedMotion()) return;
      document.querySelectorAll('.card').forEach(function(card) {
        card.addEventListener('mousemove', function(e) {
          const rect=card.getBoundingClientRect();
          const x=(e.clientX-rect.left)/rect.width-0.5, y=(e.clientY-rect.top)/rect.height-0.5;
          if (hasGsap()) gsap.to(card, { rotateY: x*5, rotateX: -y*5, duration: 0.5, ease: 'power2.out', overwrite: 'auto' });
        });
        card.addEventListener('mouseleave', function() {
          if (hasGsap()) gsap.to(card, { rotateY: 0, rotateX: 0, duration: 0.6, ease: 'back.out(1.5)', overwrite: 'auto', clearProps: 'transform' });
        });
      });
    }

    // ── ripple ──
    function initRippleEffect() {
      document.querySelectorAll('.primary, .secondary').forEach(function(btn) {
        btn.addEventListener('click', function(e) {
          if (prefersReducedMotion()) return;
          const ripple=document.createElement('span'); ripple.className='ripple';
          const rect=btn.getBoundingClientRect(), size=Math.max(rect.width,rect.height);
          ripple.style.width=ripple.style.height=size+'px';
          ripple.style.left=(e.clientX-rect.left-size/2)+'px'; ripple.style.top=(e.clientY-rect.top-size/2)+'px';
          btn.appendChild(ripple);
          ripple.addEventListener('animationend', function() { ripple.remove(); });
        });
      });
    }

    // ── magnetic ──
    function initMagneticHover() {
      if (prefersReducedMotion()) return;
      document.querySelectorAll('.primary').forEach(function(btn) {
        btn.addEventListener('mousemove', function(e) {
          const rect=btn.getBoundingClientRect();
          const dx=(e.clientX-rect.left-rect.width/2)*0.18, dy=(e.clientY-rect.top-rect.height/2)*0.18;
          if (hasGsap()) gsap.to(btn, { x: dx, y: dy, duration: 0.35, ease: 'power2.out', overwrite: 'auto' });
        });
        btn.addEventListener('mouseleave', function() {
          if (hasGsap()) gsap.to(btn, { x: 0, y: 0, duration: 0.5, ease: 'back.out(1.6)', overwrite: 'auto', clearProps: 'transform' });
        });
      });
    }

    // ── confetti ──
    function triggerConfetti() {
      if (prefersReducedMotion()) return;
      const c=document.createElement('canvas');
      c.style.cssText='position:fixed;inset:0;pointer-events:none;z-index:9999'; document.body.appendChild(c);
      const ctx=c.getContext('2d'), W=c.width=window.innerWidth, H=c.height=window.innerHeight;
      const COLORS=['#ff2442','#b9162d','#b88746','#d4a65a','#f8f1e8','#fff8ef'];
      const pieces=Array.from({length:80}, function() { return {
        x: W/2+(Math.random()-0.5)*W*0.6, y: H*0.35,
        vx: (Math.random()-0.5)*12, vy: -Math.random()*16-4,
        r: Math.random()*3.5+1.5, color: COLORS[Math.floor(Math.random()*COLORS.length)],
        rot: Math.random()*360, rotV: (Math.random()-0.5)*8, life: 1
      };});
      function draw() {
        ctx.clearRect(0,0,W,H); let alive=false;
        pieces.forEach(function(p) {
          if (p.life<=0) return;
          p.x+=p.vx; p.vy+=0.2; p.y+=p.vy; p.rot+=p.rotV; p.life-=0.008;
          ctx.save(); ctx.translate(p.x,p.y); ctx.rotate(p.rot*Math.PI/180);
          ctx.globalAlpha=Math.max(0,p.life); ctx.fillStyle=p.color;
          ctx.fillRect(-p.r/2,-p.r,p.r,p.r*2); ctx.restore();
          if (p.life>0) alive=true;
        });
        if (alive) requestAnimationFrame(draw); else c.remove();
      }
      draw();
    }

    function stageIndex(stage) { return ['init','search','notes','comments','complete'].indexOf(stage); }
    function escapeHtml(value) { return String(value).replace(/[&<>"']/g, function(c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }

    function setState(job) {
      const labels = { queued: '\u6392\u961f\u4e2d', running: '\u91c7\u96c6\u4e2d', success: '\u5df2\u5b8c\u6210', failed: '\u5f02\u5e38' };
      const stateChanged = (job.state || '') !== lastState;
      stateEl.textContent = labels[job.state] || '\u5f85\u547d';
      stateEl.className = 'state ' + (job.state || '');
      messageEl.textContent = job.message || '\u7b49\u5f85\u4efb\u52a1';
      percentEl.textContent = (job.percent || 0) + '%';
      animateProgress(job.percent || 0, job.state === 'running');
      animateMetric(mTarget, job.count || 0);
      animateMetric(mNotes, job.metrics?.notes || 0);
      animateMetric(mComments, job.metrics?.comments || 0);
      animateMetric(mFailed, job.metrics?.failed_comments || 0);
      if (stateChanged) { animateStatusChange(stateEl); }
      animateTimeline(job.stage, job.state);
      renderEvents(job.events || []);
      renderDownloads(job.downloads || []);
    }

    function redirectIfUnauthorized(res) {
      if (res.status === 401) { window.location.href = '/login'; return true; }
      return false;
    }

    async function refreshCookieStatus() {
      const res = await fetch('/api/cookie/status');
      if (redirectIfUnauthorized(res)) return;
      const payload = await res.json();
      cookieStatus.textContent = payload.message + (payload.summary ? ' \u00b7 ' + payload.summary : '');
      cookieStatus.className = 'cookie-status ' + (payload.configured ? 'ok' : '');
      updateHeaderCookieDot(payload.configured);
      animateStatusChange(cookieStatus);
      return payload;
    }

    async function poll(jobId) {
      const res = await fetch('/api/status?id=' + encodeURIComponent(jobId));
      if (redirectIfUnauthorized(res)) return;
      const job = await res.json();
      setState(job);
      if (job.state === 'success' || job.state === 'failed') {
        clearInterval(pollTimer); pollTimer = null;
        startBtn.disabled = false; startBtn.textContent = '\u5f00\u59cb\u91c7\u96c6';
        if (job.state === 'success') { triggerConfetti(); showAiSection(job); }
      }
    }

    function setPhoneStatus(message, ok) {
      phoneLoginStatus.textContent = message;
      phoneLoginStatus.className = 'login-status ' + (ok ? 'ok' : '');
      animateStatusChange(phoneLoginStatus);
    }

    async function sendPhoneCode() {
      sendPhoneCodeBtn.disabled = true; phoneLoginBtn.disabled = true;
      setPhoneStatus('\u6b63\u5728\u53d1\u9001\u9a8c\u8bc1\u7801');
      try {
        const data = new URLSearchParams(); data.set('phone', phoneInput.value); data.set('zone', phoneZone.value);
        const res = await fetch('/api/cookie/phone/send', { method: 'POST', body: data });
        if (redirectIfUnauthorized(res)) return;
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || '\u9a8c\u8bc1\u7801\u53d1\u9001\u5931\u8d25');
        phoneSessionId = payload.session_id; phoneCodeInput.value = ''; phoneLoginBtn.disabled = false;
        setPhoneStatus(payload.message, true);
      } catch (error) { phoneSessionId = ''; setPhoneStatus(error.message); }
      finally { sendPhoneCodeBtn.disabled = false; }
    }

    async function loginByPhoneCode() {
      if (!phoneSessionId) { setPhoneStatus('\u8bf7\u5148\u53d1\u9001\u9a8c\u8bc1\u7801'); return; }
      phoneLoginBtn.disabled = true; setPhoneStatus('\u6b63\u5728\u767b\u5f55\u5e76\u4fdd\u5b58 Cookie');
      try {
        const data = new URLSearchParams(); data.set('session_id', phoneSessionId); data.set('code', phoneCodeInput.value);
        const res = await fetch('/api/cookie/phone/login', { method: 'POST', body: data });
        if (redirectIfUnauthorized(res)) return;
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || '\u9a8c\u8bc1\u7801\u767b\u5f55\u5931\u8d25');
        phoneSessionId = ''; phoneCodeInput.value = '';
        setPhoneStatus(payload.message, true);
        await refreshCookieStatus();
      } catch (error) { phoneLoginBtn.disabled = false; setPhoneStatus(error.message); }
    }

    // ── AI ──
    async function loadAiConfig() {
      try {
        const res = await fetch('/api/ai/config');
        if (redirectIfUnauthorized(res)) return;
        const cfg = await res.json();
        aiConfigured = cfg.configured && cfg.enabled;
        aiProvider.value = cfg.provider || ''; aiModel.value = cfg.model || ''; aiEnabled.checked = !!cfg.enabled;
        if (currentViralJobId) {
          viralAnalyzeBtn.disabled = !aiConfigured;
          viralAnalyzeNoConfig.style.display = aiConfigured ? 'none' : 'block';
        }
        if (cfg.configured) {
          aiConfigStatus.textContent = cfg.provider_label + ' \u00b7 ' + (cfg.model || '(\u9ed8\u8ba4\u6a21\u578b)') + ' \u00b7 \u5bc6\u94a5 ' + cfg.key_masked;
          aiConfigStatus.className = 'cookie-status ok';
        } else {
          aiConfigStatus.textContent = '\u672a\u914d\u7f6e \u2014 \u8bf7\u9009\u62e9\u63d0\u4f9b\u5546\u5e76\u586b\u5199 API Key';
          aiConfigStatus.className = 'cookie-status';
        }
      } catch (e) { aiConfigured = false; }
    }
    saveAiConfigBtn.addEventListener('click', async function() {
      saveAiConfigBtn.disabled = true;
      try {
        const data = new URLSearchParams(); data.set('provider', aiProvider.value); data.set('api_key', aiApiKey.value); data.set('model', aiModel.value); data.set('enabled', aiEnabled.checked ? '1' : '0');
        const res = await fetch('/api/ai/config', { method: 'POST', body: data });
        if (redirectIfUnauthorized(res)) return;
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || '\u4fdd\u5b58\u5931\u8d25');
        aiConfigured = payload.configured && payload.enabled;
        if (currentViralJobId) {
          viralAnalyzeBtn.disabled = !aiConfigured;
          viralAnalyzeNoConfig.style.display = aiConfigured ? 'none' : 'block';
        }
        aiConfigStatus.textContent = payload.configured ? (payload.provider_label + ' \u00b7 ' + (payload.model || '(\u9ed8\u8ba4\u6a21\u578b)') + ' \u00b7 \u5bc6\u94a5 ' + payload.key_masked) : '\u672a\u914d\u7f6e';
        aiConfigStatus.className = 'cookie-status ' + (payload.configured ? 'ok' : '');
        aiApiKey.value = '';
      } catch (error) { aiConfigStatus.textContent = error.message; aiConfigStatus.className = 'cookie-status'; }
      finally { saveAiConfigBtn.disabled = false; }
    });
    testAiBtn.addEventListener('click', async function() {
      testAiBtn.disabled = true;
      aiTestResult.style.display = 'block';
      aiTestResult.style.color = 'var(--ink-soft)';
      aiTestResult.style.background = 'rgba(255,250,241,.5)';
      aiTestResult.style.border = '1px solid var(--line)';
      aiTestResult.textContent = '正在测试连接…';
      try {
        const res = await fetch('/api/ai/test');
        if (redirectIfUnauthorized(res)) return;
        const r = await res.json();
        if (r.ok) {
          aiTestResult.style.color = '#2e7d32';
          aiTestResult.style.background = 'rgba(46,204,113,.08)';
          aiTestResult.style.border = '1px solid rgba(46,204,113,.25)';
          aiTestResult.textContent = '✅ 连接成功！' + (r.message ? ' 响应: ' + r.message.substring(0, 120) : '');
        } else {
          aiTestResult.style.color = 'var(--seal-red)';
          aiTestResult.style.background = 'rgba(185,22,45,.06)';
          aiTestResult.style.border = '1px solid rgba(185,22,45,.18)';
          aiTestResult.textContent = '❌ 连接失败: ' + (r.error || '未知错误');
        }
      } catch (e) {
        aiTestResult.style.color = 'var(--seal-red)';
        aiTestResult.style.background = 'rgba(185,22,45,.06)';
        aiTestResult.style.border = '1px solid rgba(185,22,45,.18)';
        aiTestResult.textContent = '❌ 请求失败: ' + e.message;
      } finally {
        testAiBtn.disabled = false;
      }
    });

    aiProvider.addEventListener('change', function() {
      const defs = { claude: 'claude-sonnet-4-6', openai: 'gpt-4o', deepseek: 'deepseek-chat' };
      if (!aiModel.value) aiModel.placeholder = '\u5982 ' + (defs[this.value] || '');
    });
    aiSkill.addEventListener('change', function() { customPromptWrap.style.display = this.value === 'custom' ? 'block' : 'none'; });
    function showAiSection(job) {
      const downloads = job.downloads || [];
      if (downloads.length === 0 || job.state !== 'success') return;
      aiSection.style.display = 'block';
      currentJobId = job.id;
      if (aiConfigured) {
        analyzeBtn.style.display = '';
        analyzeBtn.disabled = false;
        aiSkill.parentElement.style.display = '';
        document.getElementById('aiNoConfig').style.display = 'none';
      } else {
        analyzeBtn.style.display = 'none';
        aiSkill.parentElement.style.display = 'none';
        customPromptWrap.style.display = 'none';
        document.getElementById('aiNoConfig').style.display = 'block';
      }
    }
    function renderMarkdown(text) {
      if (!text) return '';
      return text
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/^### (.+)$/gm, '<h4 style="margin:16px 0 8px;font-family:var(--font-display);font-size:16px">$1</h4>')
        .replace(/^## (.+)$/gm, '<h3 style="margin:20px 0 10px;font-family:var(--font-display);font-size:20px;color:var(--seal-red)">$1</h3>')
        .replace(/^# (.+)$/gm, '<h2 style="margin:22px 0 12px;font-family:var(--font-display);font-size:24px;color:var(--seal-red)">$1</h2>')
        .replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>')
        .replace(/^\\- (.+)$/gm, '<li style="margin-left:18px">$1</li>')
        .replace(/^(\\d+)\\. (.+)$/gm, '<li style="margin-left:18px">$2</li>')
        .replace(/\\n\\n/g, '</p><p style="margin:8px 0">')
        .replace(/^(.+)$/gm, function(m) { return m.startsWith('<') ? m : '<p style="margin:8px 0">' + m + '</p>'; })
        .replace(/```([\\s\\S]*?)```/g, '<pre style="background:rgba(32,24,22,.04);padding:12px;overflow:auto;font-family:var(--font-mono);font-size:13px">$1</pre>')
        .replace(/<\\/p><p style="margin:8px 0"><\\/p>/g, '');
    }
    analyzeBtn.addEventListener('click', async function() {
      analyzeBtn.disabled = true; analyzeBtn.textContent = '\u5206\u6790\u4e2d\u2026';
      aiResult.style.display = 'block'; aiResultContent.innerHTML = '<p style="color:var(--muted)">\u6b63\u5728\u8bfb\u53d6 Excel \u6570\u636e\u5e76\u8c03\u7528 AI \u5206\u6790\uff0c\u8bf7\u7a0d\u5019\u2026</p>';
      try {
        const data = new URLSearchParams(); data.set('job_id', currentJobId); data.set('skill_type', aiSkill.value);
        if (aiSkill.value === 'custom') data.set('custom_prompt', aiCustomPrompt.value);
        const res = await fetch('/api/ai/analyze', { method: 'POST', body: data });
        if (redirectIfUnauthorized(res)) return;
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || '\u5206\u6790\u542f\u52a8\u5931\u8d25');
        pollAiResult(payload.id);
      } catch (error) {
        aiResultContent.innerHTML = '<p style="color:var(--seal-red)">' + escapeHtml(error.message) + '</p>';
        analyzeBtn.disabled = false; analyzeBtn.textContent = '\u5f00\u59cb AI \u5206\u6790';
      }
    });
    function pollAiResult(analysisId) {
      clearInterval(aiPollTimer);
      aiPollTimer = setInterval(async function() {
        try {
          const res = await fetch('/api/ai/status?id=' + encodeURIComponent(analysisId));
          if (redirectIfUnauthorized(res)) { clearInterval(aiPollTimer); return; }
          const r = await res.json();
          if (r.status === 'done') {
            clearInterval(aiPollTimer);
            aiStatusText.textContent = '\u5206\u6790\u5b8c\u6210 \u00b7 ' + (r.skill_name || '');
            aiResultContent.innerHTML = renderMarkdown(r.result);
            analyzeBtn.disabled = false; analyzeBtn.textContent = '\u5f00\u59cb AI \u5206\u6790';
          } else if (r.status === 'error') {
            clearInterval(aiPollTimer);
            aiStatusText.textContent = '\u5206\u6790\u5931\u8d25';
            aiResultContent.innerHTML = '<p style="color:var(--seal-red)">' + escapeHtml(r.error) + '</p>';
            analyzeBtn.disabled = false; analyzeBtn.textContent = '\u5f00\u59cb AI \u5206\u6790';
          } else { aiStatusText.textContent = '\u5206\u6790\u4e2d\u2026'; }
        } catch (e) {}
      }, 1500);
    }

    async function pollViralJob(jobId) {
      const res = await fetch('/api/status?id=' + encodeURIComponent(jobId));
      if (redirectIfUnauthorized(res)) return;
      const job = await res.json();
      setViralJobState(job);
      if (job.state === 'success' || job.state === 'failed') {
        clearInterval(viralPollTimer); viralPollTimer = null;
        viralStartBtn.disabled = false; viralStartBtn.textContent = '\u91c7\u96c6\u7206\u6b3e\u6837\u672c';
        if (job.state === 'success') {
          currentViralJobId = job.id;
          viralAnalyzeBtn.disabled = !aiConfigured;
          viralAnalyzeNoConfig.style.display = aiConfigured ? 'none' : 'block';
          draftGenerateBtn.disabled = true;
          triggerConfetti();
        }
      }
    }

    viralForm.addEventListener('submit', async function(event) {
      event.preventDefault();
      clearInterval(viralPollTimer); clearInterval(viralAnalysisPollTimer); clearInterval(draftPollTimer);
      viralAnalysisId = ''; currentViralJobId = '';
      viralAnalysisResult.style.display = 'none'; viralAnalysisContent.innerHTML = '';
      viralAnalyzeBtn.disabled = true; draftGenerateBtn.disabled = true;
      resetDraftPreview();
      viralStartBtn.disabled = true; viralStartBtn.textContent = '\u4efb\u52a1\u542f\u52a8\u4e2d';
      const data = new URLSearchParams(new FormData(viralForm));
      data.set('mode', 'viral');
      try {
        const res = await fetch('/api/start', { method: 'POST', body: data });
        if (redirectIfUnauthorized(res)) return;
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || '\u7206\u6b3e\u91c7\u96c6\u542f\u52a8\u5931\u8d25');
        await pollViralJob(payload.id);
        viralPollTimer = setInterval(function() { pollViralJob(payload.id); }, 1200);
      } catch (error) {
        setViralJobState({ state: 'failed', percent: 100, message: error.message, events: [{ time: new Date().toLocaleTimeString(), title: '\u542f\u52a8\u5931\u8d25', detail: error.message, level: 'error' }], metrics: {} });
        viralStartBtn.disabled = false; viralStartBtn.textContent = '\u91c7\u96c6\u7206\u6b3e\u6837\u672c';
      }
    });

    viralAnalyzeBtn.addEventListener('click', async function() {
      if (!currentViralJobId) return;
      viralAnalyzeBtn.disabled = true; viralAnalyzeBtn.textContent = '\u62c6\u89e3\u4e2d\u2026';
      draftGenerateBtn.disabled = true;
      viralAnalysisResult.style.display = 'block';
      viralAnalysisStatus.textContent = '\u7206\u6b3e\u62c6\u89e3\u4e2d\u2026';
      viralAnalysisContent.innerHTML = '<p style="color:var(--muted)">\u6b63\u5728\u8bfb\u53d6\u6837\u672c\u5e76\u8c03\u7528 AI \u62c6\u89e3\u7206\u6b3e\u7ed3\u6784\u2026</p>';
      const prompt = '\u8bf7\u8f93\u51fa\uff1a1. \u7206\u6b3e\u6807\u9898\u516c\u5f0f\uff1b2. \u5f00\u5934\u94a9\u5b50\u6a21\u5f0f\uff1b3. \u6b63\u6587\u7ed3\u6784\u6a21\u677f\uff1b4. \u56fe\u7247/\u5c01\u9762\u5171\u6027\uff1b5. \u6807\u7b7e\u7b56\u7565\uff1b6. \u53ef\u4ee5\u8fc1\u79fb\u5230\u6211\u8d26\u53f7\u98ce\u683c\u7684\u521b\u4f5c\u5efa\u8bae\uff1b7. \u907f\u514d\u6284\u88ad\u548c\u5e73\u53f0\u98ce\u9669\u7684\u6ce8\u610f\u4e8b\u9879\u3002';
      try {
        const data = new URLSearchParams();
        data.set('job_id', currentViralJobId);
        data.set('skill_type', 'viral');
        data.set('custom_prompt', prompt);
        const res = await fetch('/api/ai/analyze', { method: 'POST', body: data });
        if (redirectIfUnauthorized(res)) return;
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || '\u7206\u6b3e\u62c6\u89e3\u542f\u52a8\u5931\u8d25');
          viralAnalysisId = payload.id;
        clearInterval(viralAnalysisPollTimer);
        viralAnalysisPollTimer = setInterval(async function() {
          try {
            const statusRes = await fetch('/api/ai/status?id=' + encodeURIComponent(payload.id));
            if (redirectIfUnauthorized(statusRes)) { clearInterval(viralAnalysisPollTimer); return; }
            const r = await statusRes.json();
            if (r.status === 'done') {
              clearInterval(viralAnalysisPollTimer);
              viralAnalysisStatus.textContent = '\u7206\u6b3e\u62c6\u89e3\u5b8c\u6210';
              viralAnalysisContent.innerHTML = renderMarkdown(r.result);
              viralAnalyzeBtn.disabled = false; viralAnalyzeBtn.textContent = '\u91cd\u65b0\u62c6\u89e3';
              draftGenerateBtn.disabled = false;
            } else if (r.status === 'error') {
              clearInterval(viralAnalysisPollTimer);
              viralAnalysisStatus.textContent = '\u7206\u6b3e\u62c6\u89e3\u5931\u8d25';
              viralAnalysisContent.innerHTML = '<p style="color:var(--seal-red)">' + escapeHtml(r.error) + '</p>';
              viralAnalyzeBtn.disabled = false; viralAnalyzeBtn.textContent = '\u5f00\u59cb\u7206\u6b3e\u62c6\u89e3';
            } else { viralAnalysisStatus.textContent = '\u7206\u6b3e\u62c6\u89e3\u4e2d\u2026'; }
          } catch (e) {}
        }, 1500);
      } catch (error) {
        viralAnalysisContent.innerHTML = '<p style="color:var(--seal-red)">' + escapeHtml(error.message) + '</p>';
        viralAnalyzeBtn.disabled = false; viralAnalyzeBtn.textContent = '\u5f00\u59cb\u7206\u6b3e\u62c6\u89e3';
      }
    });

    draftGenerateBtn.addEventListener('click', async function() {
      if (!currentViralJobId) return;
      draftGenerateBtn.disabled = true; draftGenerateBtn.textContent = '\u751f\u6210\u4e2d\u2026';
      resetDraftPreview('\u8349\u7a3f\u751f\u6210\u4e2d\u2026');
      try {
        const data = new URLSearchParams();
        data.set('job_id', currentViralJobId);
        data.set('source_analysis_id', viralAnalysisId);
        data.set('style_profile', draftStyleProfile.value);
        data.set('topic_angle', draftTopicAngle.value);
        data.set('target_audience', draftAudience.value);
        data.set('tone', draftTone.value);
        data.set('draft_count', draftCount.value);
        data.set('include_image_prompts', draftImagePrompts.checked ? '1' : '0');
        const res = await fetch('/api/ai/draft', { method: 'POST', body: data });
        if (redirectIfUnauthorized(res)) return;
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || '\u8349\u7a3f\u751f\u6210\u542f\u52a8\u5931\u8d25');
        clearInterval(draftPollTimer);
        draftPollTimer = setInterval(async function() {
          try {
            const statusRes = await fetch('/api/ai/status?id=' + encodeURIComponent(payload.id));
            if (redirectIfUnauthorized(statusRes)) { clearInterval(draftPollTimer); return; }
            const r = await statusRes.json();
            if (r.status === 'done') {
              clearInterval(draftPollTimer);
              draftRawMarkdown = r.result || '';
              draftStatus.textContent = '\u8349\u7a3f\u751f\u6210\u5b8c\u6210\u3002\u751f\u56fe\u548c\u4e00\u952e\u53d1\u5e03\u6682\u4e0d\u5f00\u653e\uff0c\u8bf7\u4eba\u5de5\u68c0\u67e5\u540e\u590d\u5236\u53d1\u5e03\u3002';
              draftPreview.innerHTML = renderMarkdown(draftRawMarkdown);
              draftCopyBtn.disabled = false; draftDownloadBtn.disabled = false;
              draftGenerateBtn.disabled = false; draftGenerateBtn.textContent = '\u91cd\u65b0\u751f\u6210\u8349\u7a3f';
            } else if (r.status === 'error') {
              clearInterval(draftPollTimer);
              draftStatus.textContent = '\u8349\u7a3f\u751f\u6210\u5931\u8d25\uff1a' + (r.error || '\u672a\u77e5\u9519\u8bef');
              draftGenerateBtn.disabled = false; draftGenerateBtn.textContent = '\u751f\u6210\u8349\u7a3f';
            } else { draftStatus.textContent = '\u8349\u7a3f\u751f\u6210\u4e2d\u2026'; }
          } catch (e) {}
        }, 1500);
      } catch (error) {
        draftStatus.textContent = error.message;
        draftGenerateBtn.disabled = false; draftGenerateBtn.textContent = '\u751f\u6210\u8349\u7a3f';
      }
    });

    draftCopyBtn.addEventListener('click', async function() {
      if (!draftRawMarkdown) return;
      await navigator.clipboard.writeText(draftRawMarkdown);
      draftStatus.textContent = '\u5df2\u590d\u5236\u5168\u6587\u3002';
    });

    draftDownloadBtn.addEventListener('click', function() {
      if (!draftRawMarkdown) return;
      const blob = new Blob([draftRawMarkdown], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'hongshu-draft-' + new Date().toISOString().slice(0, 10) + '.md';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    });

    reduceMotionQuery.addEventListener('change', function() {
      if (!prefersReducedMotion()) return;
      stopLoopingMotion();
      if (hasGsap()) gsap.set(['.card','.app-header','.nav-bar','.panel-grid'], { autoAlpha: 1, x: 0, y: 0, clearProps: 'transform,opacity,visibility' });
    });

    // ── init ──
    runIntroMotion();
    bindPressMotion('.primary, .secondary');
    initParticles();
    initCardTilt();
    initRippleEffect();
    initMagneticHover();

    refreshCookieBtn.addEventListener('click', refreshCookieStatus);
    sendPhoneCodeBtn.addEventListener('click', sendPhoneCode);
    phoneLoginBtn.addEventListener('click', loginByPhoneCode);

    // ── QR login ──
    let qrSessionId = '';
    let qrPollTimer = null;

    genQrBtn.addEventListener('click', async function() {
      genQrBtn.disabled = true;
      genQrBtn.textContent = '正在生成二维码…';
      try {
        const res = await fetch('/api/qrcode/start', { method: 'POST' });
        if (redirectIfUnauthorized(res)) return;
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || '生成失败');
        qrSessionId = payload.session_id;
        qrImage.src = payload.qr_image;
        qrContainer.style.display = 'block';
        cancelQrBtn.style.display = 'block';
        genQrBtn.style.display = 'none';
        qrStatus.textContent = '请使用小红书 App 扫描二维码';
        qrStatus.style.color = 'var(--ink-soft)';
        // Start polling
        clearInterval(qrPollTimer);
        qrPollTimer = setInterval(checkQrStatus, 2000);
      } catch (error) {
        qrStatus.textContent = error.message;
        qrStatus.style.color = 'var(--seal-red)';
        genQrBtn.disabled = false;
        genQrBtn.textContent = '生成二维码';
        genQrBtn.style.display = '';
      }
    });

    cancelQrBtn.addEventListener('click', function() {
      clearInterval(qrPollTimer);
      qrSessionId = '';
      qrContainer.style.display = 'none';
      cancelQrBtn.style.display = 'none';
      genQrBtn.style.display = '';
      genQrBtn.disabled = false;
      genQrBtn.textContent = '生成二维码';
    });

    async function checkQrStatus() {
      try {
        const data = new URLSearchParams(); data.set('session_id', qrSessionId);
        const res = await fetch('/api/qrcode/status', { method: 'POST', body: data });
        const payload = await res.json();
        if (payload.status === 'done') {
          clearInterval(qrPollTimer);
          qrStatus.textContent = '✅ ' + payload.message;
          qrStatus.style.color = '#2e7d32';
          cancelQrBtn.style.display = 'none';
          genQrBtn.style.display = '';
          genQrBtn.disabled = false;
          genQrBtn.textContent = '重新生成';
          await refreshCookieStatus();
        } else if (payload.status === 'waiting') {
          qrStatus.textContent = payload.message || '等待扫码…';
        } else {
          clearInterval(qrPollTimer);
          qrStatus.textContent = '❌ ' + (payload.error || '登录失败');
          qrStatus.style.color = 'var(--seal-red)';
          cancelQrBtn.style.display = 'none';
          genQrBtn.style.display = '';
          genQrBtn.disabled = false;
          genQrBtn.textContent = '重新生成';
        }
      } catch (e) {
        clearInterval(qrPollTimer);
        qrStatus.textContent = '❌ 检查状态失败';
        qrStatus.style.color = 'var(--seal-red)';
      }
    }

    // ── user search ──
    searchUserBtn.addEventListener('click', async function() {
      const query = searchUserQuery.value.trim();
      if (!query) { searchUserResult.innerHTML = '<p style="color:var(--muted)">请输入搜索关键词</p>'; return; }
      searchUserBtn.disabled = true; searchUserBtn.textContent = '搜索中…';
      searchUserResult.innerHTML = '<p style="color:var(--muted)">搜索中…</p>';
      try {
        const data = new URLSearchParams(); data.set('query', query); data.set('count', '15');
        const res = await fetch('/api/search/user', { method: 'POST', body: data });
        if (redirectIfUnauthorized(res)) return;
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || '搜索失败');
        const users = payload.users || [];
        if (users.length === 0) {
          searchUserResult.innerHTML = '<p style="color:var(--muted)">未找到相关用户</p>';
        } else {
          searchUserResult.innerHTML = users.map(function(u) {
            const name = escapeHtml(u.nickname || u.user_id || '');
            const uid = escapeHtml(u.user_id || '');
            const avatar = escapeHtml(u.avatar || '');
            const homeUrl = escapeHtml(u.home_url || ('https://www.xiaohongshu.com/user/profile/' + uid));
            return '<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--line)">' +
              (avatar ? '<img src="' + avatar + '" style="width:36px;height:36px;border-radius:50%;object-fit:cover" onerror="this.remove()">' : '<div style="width:36px;height:36px;border-radius:50%;background:var(--line)"></div>') +
              '<div style="flex:1;min-width:0"><div style="font-size:14px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + name + '</div><div style="font-size:11px;color:var(--muted)">ID: ' + uid + '</div></div>' +
              '<a href="' + homeUrl + '" target="_blank" style="font-size:11px;color:var(--seal-red);text-decoration:none;white-space:nowrap">主页 →</a>' +
              '</div>';
          }).join('');
        }
      } catch (error) {
        searchUserResult.innerHTML = '<p style="color:var(--seal-red)">' + escapeHtml(error.message) + '</p>';
      } finally {
        searchUserBtn.disabled = false; searchUserBtn.textContent = '搜索';
      }
    });

    logoutBtn.addEventListener('click', async function() { await fetch('/api/logout', { method: 'POST' }); window.location.href = '/login'; });
    clearCookieBtn.addEventListener('click', async function() {
      if (!confirm('\u786e\u8ba4\u6e05\u9664\u5f53\u524d Cookie\uff1f\u6e05\u9664\u540e\u91c7\u96c6\u4f1a\u5931\u8d25\uff0c\u76f4\u5230\u91cd\u65b0\u767b\u5f55\u6216\u624b\u52a8\u4fdd\u5b58\u3002')) return;
      const res = await fetch('/api/cookie/clear', { method: 'POST' });
      if (redirectIfUnauthorized(res)) return;
      cookieInput.value = ''; await refreshCookieStatus();
    });
    saveCookieBtn.addEventListener('click', async function() {
      saveCookieBtn.disabled = true;
      try {
        const data = new URLSearchParams(); data.set('cookies', cookieInput.value);
        const res = await fetch('/api/cookie/save', { method: 'POST', body: data });
        if (redirectIfUnauthorized(res)) return;
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || '\u4fdd\u5b58\u5931\u8d25');
        cookieInput.value = ''; await refreshCookieStatus();
      } catch (error) { cookieStatus.textContent = error.message; cookieStatus.className = 'cookie-status'; }
      finally { saveCookieBtn.disabled = false; }
    });

    refreshCookieStatus();
    loadAiConfig();

    // ── watermark ──
    const wmBtn = document.getElementById('wmBtn');
    const wmNoteUrl = document.getElementById('wmNoteUrl');
    const wmResult = document.getElementById('wmResult');

    wmBtn.addEventListener('click', async function() {
      const url = wmNoteUrl.value.trim();
      if (!url) { wmResult.innerHTML = '<p style="color:var(--seal-red)">请输入笔记链接</p>'; return; }
      wmBtn.disabled = true; wmBtn.textContent = '提取中…';
      wmResult.innerHTML = '<p style="color:var(--muted)">正在提取…</p>';
      try {
        const data = new URLSearchParams(); data.set('note_url', url);
        const res = await fetch('/api/watermark', { method: 'POST', body: data });
        if (redirectIfUnauthorized(res)) return;
        const r = await res.json();
        if (!res.ok) throw new Error(r.error || '提取失败');
        let html = '';
        if (r.title) html += '<p style="font-weight:600;margin-bottom:6px">' + escapeHtml(r.title) + '</p>';
        if (r.clean_video_url) {
          html += '<p style="margin:4px 0"><strong>无水印视频：</strong><br><a href="' + escapeHtml(r.clean_video_url) + '" target="_blank" style="color:var(--seal-red);word-break:break-all;font-size:12px">' + escapeHtml(r.clean_video_url) + '</a></p>';
        }
        if (r.clean_image_urls && r.clean_image_urls.length > 0) {
          html += '<p style="margin:4px 0"><strong>无水印图片 (' + r.clean_image_urls.length + '张)：</strong></p>';
          r.clean_image_urls.forEach(function(imgUrl, i) {
            var safeUrl = escapeHtml(imgUrl);
            html += '<div style="display:flex;align-items:center;gap:8px;margin:4px 0"><img src="' + safeUrl + '" style="width:60px;height:60px;object-fit:cover;border:1px solid var(--line)" onerror="this.remove()"><a href="' + safeUrl + '" target="_blank" style="color:var(--seal-red);font-size:11px;word-break:break-all">图' + (i+1) + ' →</a></div>';
          });
        }
        wmResult.innerHTML = html || '<p style="color:var(--muted)">未找到媒体资源</p>';
      } catch (error) {
        wmResult.innerHTML = '<p style="color:var(--seal-red)">' + escapeHtml(error.message) + '</p>';
      } finally {
        wmBtn.disabled = false; wmBtn.textContent = '提取';
      }
    });

    // ── mode tabs ──
    const modeTabs = document.querySelectorAll('.mode-tab');
    const modePanels = {
      keyword: document.getElementById('panelModeKeyword'),
      url: document.getElementById('panelModeUrl'),
      user: document.getElementById('panelModeUser'),
    };
    modeTabs.forEach(function(tab) {
      tab.addEventListener('click', function() {
        modeTabs.forEach(function(t) { t.classList.remove('active'); t.style.color = 'var(--muted)'; t.style.borderBottomColor = 'transparent'; t.style.fontWeight = '400'; });
        tab.classList.add('active'); tab.style.color = 'var(--seal-red)'; tab.style.borderBottomColor = 'var(--seal-red)'; tab.style.fontWeight = '600';
        Object.values(modePanels).forEach(function(p) { p.style.display = 'none'; });
        modePanels[tab.dataset.mode].style.display = 'block';
        document.getElementById('collectMode').value = tab.dataset.mode;
        // Clear required attributes
        document.getElementById('keyword').required = (tab.dataset.mode === 'keyword');
        document.getElementById('noteUrl').required = (tab.dataset.mode === 'url');
        document.getElementById('userUrl').required = (tab.dataset.mode === 'user');
      });
    });

    form.addEventListener('submit', async function(event) {
      event.preventDefault();
      clearInterval(pollTimer); clearInterval(aiPollTimer);
      downloadsEl.innerHTML = ''; eventsEl.innerHTML = '';
      aiSection.style.display = 'none'; aiResult.style.display = 'none';
      startBtn.disabled = true; startBtn.textContent = '\u4efb\u52a1\u542f\u52a8\u4e2d';
      const data = new URLSearchParams(new FormData(form));
      try {
        const res = await fetch('/api/start', { method: 'POST', body: data });
        if (redirectIfUnauthorized(res)) return;
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.error || '\u4efb\u52a1\u542f\u52a8\u5931\u8d25');
        await poll(payload.id);
        pollTimer = setInterval(function() { poll(payload.id); }, 1200);
      } catch (error) {
        setState({ state: 'failed', stage: 'failed', percent: 100, message: error.message, events: [{ time: new Date().toLocaleTimeString(), title: '\u542f\u52a8\u5931\u8d25', detail: error.message, level: 'error' }], metrics: {} });
        startBtn.disabled = false; startBtn.textContent = '\u5f00\u59cb\u91c7\u96c6';
      }
    });
  </script>
</body>
</html>"""

# The count is injected directly above, so no .replace needed



def render_login_page(error=""):
    safe_error = html.escape(error)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>登录 · 红薯采采</title>
  <style>
    :root {{ --paper: #f8f1e8; --ink: #201816; --ink-soft: #786a62; --muted: #a08e80; --seal-red: #b9162d; --xhs-red: #ff2442; --gold: #b88746; --line: rgba(32,24,22,.14); --shadow: rgba(80,34,24,.16); --font-display: "Iowan Old Style", "Songti SC", "Noto Serif CJK SC", serif; --font-body: "Avenir Next", "PingFang SC", "Microsoft YaHei", sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ min-height: 100vh; margin: 0; display: flex; align-items: center; justify-content: center; color: var(--ink); font-family: var(--font-body); background: radial-gradient(circle at 18% 14%, rgba(255,36,66,.09), transparent 30rem), radial-gradient(circle at 84% 82%, rgba(184,135,70,.07), transparent 26rem), repeating-linear-gradient(0deg, rgba(32,24,22,.022) 0, rgba(32,24,22,.022) 1px, transparent 1px, transparent 26px), var(--paper); padding: 24px 16px; }}
    .card {{ width: min(420px, 100%); margin: 0 auto; padding: 34px 32px; border: 1px solid var(--line); background: rgba(255,250,241,.88); box-shadow: 0 24px 76px var(--shadow); backdrop-filter: blur(10px); text-align: center; }}
    .overline {{ letter-spacing: .18em; font-size: 11px; color: var(--seal-red); }}
    h1 {{ margin: 4px 0 24px; font-family: var(--font-display); font-size: 46px; line-height: .94; letter-spacing: -.03em; }}
    .tab-bar {{ display: flex; gap: 0; border-bottom: 2px solid var(--line); margin-bottom: 24px; }}
    .tab {{ flex: 1; padding: 10px 0 12px; font-size: 15px; color: var(--muted); cursor: pointer; background: none; border: 0; border-bottom: 3px solid transparent; margin-bottom: -2px; transition: color .22s, border-color .22s; font-family: var(--font-body); letter-spacing: .04em; }}
    .tab.active {{ color: var(--seal-red); border-bottom-color: var(--seal-red); font-weight: 600; }}
    .tab:hover:not(.active) {{ color: var(--ink-soft); }}
    .panel {{ display: none; text-align: left; }}
    .panel.active {{ display: block; }}
    label {{ display: block; margin: 14px 0 5px; color: var(--ink-soft); font-size: 12px; letter-spacing: .08em; text-transform: uppercase; }}
    input {{ width: 100%; border: 0; border-bottom: 2px solid var(--line); padding: 10px 2px 9px; background: transparent; color: var(--ink); font-size: 19px; font-family: var(--font-display); outline: none; transition: border-color .22s ease, box-shadow .22s ease; }}
    input:focus {{ border-color: var(--xhs-red); box-shadow: 0 8px 18px rgba(255,36,66,.05); }}
    input[type="password"] {{ letter-spacing: .10em; }}
    .primary {{ width: 100%; margin-top: 20px; border: 0; padding: 14px 16px; background: var(--seal-red); color: #fff8ef; font-size: 16px; letter-spacing: .06em; cursor: pointer; box-shadow: 0 12px 28px rgba(185,22,45,.20); transition: transform .22s ease, background .22s ease, box-shadow .22s ease; overflow: hidden; position: relative; }}
    .primary:hover {{ transform: translateY(-1px); background: #8f1021; box-shadow: 0 18px 38px rgba(185,22,45,.30), 0 0 24px rgba(255,36,66,.08); }}
    .primary:disabled {{ opacity: .50; cursor: not-allowed; transform: none; }}
    .secondary {{ border: 1px solid var(--line); padding: 10px 12px; background: rgba(255,250,241,.62); color: var(--ink); cursor: pointer; font-family: var(--font-body); font-size: 13px; transition: background .22s, border-color .22s; white-space: nowrap; }}
    .secondary:hover {{ background: #fffaf1; border-color: var(--xhs-red); }}
    .secondary:disabled {{ opacity: .45; cursor: not-allowed; }}
    .error {{ margin: 12px 0 0; color: var(--seal-red); min-height: 18px; font-size: 13px; }}
    .hint {{ margin: 14px 0 0; color: var(--ink-soft); font-size: 12px; line-height: 1.6; }}
    .phone-grid {{ display: grid; grid-template-columns: 68px 1fr; gap: 10px; align-items: end; }}
    .code-row {{ display: flex; gap: 10px; align-items: flex-end; }}
    .code-row input {{ flex: 1; }}
    .code-row button {{ flex: 0 0 auto; margin-top: 0; padding: 11px 14px; }}
    .login-status {{ min-height: 20px; margin-top: 10px; color: var(--ink-soft); font-size: 13px; transition: color .22s; }}
    .login-status.ok {{ color: var(--seal-red); font-weight: 600; }}
    .ripple {{ position: absolute; border-radius: 50%; background: rgba(255,255,255,.35); transform: scale(0); animation: ripple-anim .6s ease-out forwards; pointer-events: none; }}
    @keyframes ripple-anim {{ to {{ transform: scale(6); opacity: 0; }} }}
  </style>
</head>
<body>
  <div class="card">
    <div class="overline">HONGSHU CAICAI · DATA CONSOLE</div>
    <h1>红薯采采</h1>
    <div class="tab-bar">
      <button class="tab active" data-tab="phone">手机验证登录</button>
      <button class="tab" data-tab="admin">密码登录</button>
    </div>

    <!-- phone panel -->
    <div class="panel active" id="panelPhone">
      <p class="hint" style="margin-top:0;text-align:center">使用小红书绑定手机号，一键登录并获取采集 Cookie</p>
      <div class="phone-grid">
        <div>
          <label for="loginPhoneZone">区号</label>
          <input id="loginPhoneZone" type="text" value="86" inputmode="numeric">
        </div>
        <div>
          <label for="loginPhoneInput">手机号</label>
          <input id="loginPhoneInput" type="tel" placeholder="输入手机号" autocomplete="tel">
        </div>
      </div>
      <label for="loginPhoneCode">短信验证码</label>
      <div class="code-row">
        <input id="loginPhoneCode" type="text" placeholder="6 位验证码" inputmode="numeric" autocomplete="one-time-code">
        <button id="loginSendCodeBtn" class="secondary" type="button">发送</button>
      </div>
      <div id="loginPhoneStatus" class="login-status">先发送验证码</div>
      <button id="loginPhoneSubmitBtn" class="primary" type="button" disabled>登录并进入采集台</button>
    </div>

    <!-- admin panel -->
    <div class="panel" id="panelAdmin">
      <form id="adminForm">
        <label for="username">Username</label>
        <input id="username" name="username" type="text" autocomplete="username" value="admin" required>
        <label for="password">Password</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required>
        <button class="primary" type="submit">进入采集台</button>
        <p class="error" id="adminError">{safe_error}</p>
      </form>
      <p class="hint" style="text-align:center">默认账号 admin / admin，用于本地维护</p>
    </div>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js"></script>
  <script>
    var reduceMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    function prefersReducedMotion() {{ return reduceMotionQuery.matches; }}

    // --- tab switching ---
    var tabs = document.querySelectorAll('.tab');
    var panels = {{ phone: document.getElementById('panelPhone'), admin: document.getElementById('panelAdmin') }};
    tabs.forEach(function(tab) {{
      tab.addEventListener('click', function() {{
        tabs.forEach(function(t) {{ t.classList.remove('active'); }});
        tab.classList.add('active');
        Object.values(panels).forEach(function(p) {{ p.classList.remove('active'); }});
        panels[tab.dataset.tab].classList.add('active');
      }});
    }});

    // --- admin login ---
    document.getElementById('adminForm').addEventListener('submit', async function(event) {{
      event.preventDefault();
      var form = event.currentTarget;
      var res = await fetch('/api/login', {{ method: 'POST', body: new URLSearchParams(new FormData(form)) }});
      var payload = await res.json();
      if (res.ok) {{ window.location.href = '/'; return; }}
      document.getElementById('adminError').textContent = payload.error || '登录失败';
    }});

    // --- phone login ---
    var loginPhoneZone = document.getElementById('loginPhoneZone');
    var loginPhoneInput = document.getElementById('loginPhoneInput');
    var loginPhoneCode = document.getElementById('loginPhoneCode');
    var loginSendCodeBtn = document.getElementById('loginSendCodeBtn');
    var loginPhoneSubmitBtn = document.getElementById('loginPhoneSubmitBtn');
    var loginPhoneStatus = document.getElementById('loginPhoneStatus');
    var loginPhoneSessionId = '';

    function setPhoneStatus(msg, ok) {{
      loginPhoneStatus.textContent = msg;
      loginPhoneStatus.className = 'login-status' + (ok ? ' ok' : '');
    }}

    loginSendCodeBtn.addEventListener('click', async function() {{
      loginSendCodeBtn.disabled = true;
      loginPhoneSubmitBtn.disabled = true;
      setPhoneStatus('正在发送验证码');
      try {{
        var data = new URLSearchParams();
        data.set('phone', loginPhoneInput.value);
        data.set('zone', loginPhoneZone.value);
        var res = await fetch('/api/cookie/phone/send', {{ method: 'POST', body: data }});
        var payload = await res.json();
        if (!res.ok) throw new Error(payload.error || '验证码发送失败');
        loginPhoneSessionId = payload.session_id;
        loginPhoneCode.value = '';
        loginPhoneSubmitBtn.disabled = false;
        setPhoneStatus('验证码已发送，请查收手机短信', true);
      }} catch (err) {{
        loginPhoneSessionId = '';
        setPhoneStatus(err.message);
      }} finally {{
        loginSendCodeBtn.disabled = false;
      }}
    }});

    loginPhoneSubmitBtn.addEventListener('click', async function() {{
      if (!loginPhoneSessionId) {{ setPhoneStatus('请先发送验证码'); return; }}
      loginPhoneSubmitBtn.disabled = true;
      loginPhoneSubmitBtn.textContent = '登录中…';
      loginSendCodeBtn.disabled = true;
      try {{
        var data = new URLSearchParams();
        data.set('session_id', loginPhoneSessionId);
        data.set('code', loginPhoneCode.value);
        var res = await fetch('/api/cookie/phone/login', {{ method: 'POST', body: data }});
        var payload = await res.json();
        if (!res.ok) throw new Error(payload.error || '验证码登录失败');
        window.location.href = '/';
      }} catch (err) {{
        setPhoneStatus(err.message);
        loginPhoneSubmitBtn.disabled = false;
        loginPhoneSubmitBtn.textContent = '登录并进入采集台';
        loginSendCodeBtn.disabled = false;
      }}
    }});

    // --- ripple ---
    document.querySelectorAll('.primary').forEach(function(btn) {{
      btn.addEventListener('click', function(e) {{
        var ripple = document.createElement('span');
        ripple.className = 'ripple';
        var rect = btn.getBoundingClientRect();
        var size = Math.max(rect.width, rect.height);
        ripple.style.width = ripple.style.height = size + 'px';
        ripple.style.left = (e.clientX - rect.left - size / 2) + 'px';
        ripple.style.top = (e.clientY - rect.top - size / 2) + 'px';
        btn.appendChild(ripple);
        ripple.addEventListener('animationend', function() {{ ripple.remove(); }});
      }});
    }});

    // --- GSAP intro ---
    if (window.gsap && !prefersReducedMotion()) {{
      gsap.timeline({{ defaults: {{ duration: 0.52, ease: 'power3.out' }} }})
        .from('.card', {{ y: 20, autoAlpha: 0 }})
        .from('.overline', {{ autoAlpha: 0 }}, '-=0.28')
        .from('h1', {{ autoAlpha: 0 }}, '-=0.22')
        .from('.tab-bar', {{ autoAlpha: 0 }}, '-=0.18')
        .from('.panel.active', {{ y: 8, autoAlpha: 0 }}, '-=0.10');
    }}
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
        if parsed.path == "/api/ai/config":
            if not self.require_auth(parsed.path):
                return
            self.send_json(ai_utils.load_ai_config())
            return
        if parsed.path == "/api/ai/status":
            if not self.require_auth(parsed.path):
                return
            self.handle_ai_status(parsed.query)
            return
        if parsed.path == "/api/ai/test":
            if not self.require_auth(parsed.path):
                return
            self.handle_ai_test()
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
        if parsed.path == "/api/cookie/phone/send":
            self.handle_cookie_phone_send()
            return
        if parsed.path == "/api/cookie/phone/login":
            self.handle_cookie_phone_login()
            return
        if parsed.path == "/api/qrcode/start":
            if not self.require_auth(parsed.path):
                return
            self.handle_qrcode_start()
            return
        if parsed.path == "/api/qrcode/status":
            if not self.require_auth(parsed.path):
                return
            self.handle_qrcode_status()
            return
        if parsed.path == "/api/search/user":
            if not self.require_auth(parsed.path):
                return
            self.handle_search_user()
            return
        if parsed.path == "/api/watermark":
            if not self.require_auth(parsed.path):
                return
            self.handle_watermark()
            return
        if parsed.path == "/api/ai/config":
            if not self.require_auth(parsed.path):
                return
            self.handle_ai_config_save()
            return
        if parsed.path == "/api/ai/analyze":
            if not self.require_auth(parsed.path):
                return
            self.handle_ai_analyze()
            return
        if parsed.path == "/api/ai/draft":
            if not self.require_auth(parsed.path):
                return
            self.handle_ai_draft()
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
            mode = form.get("mode", ["keyword"])[0]
            with_comments = form.get("with_comments", [""])[0] in ("on", "1", "true")

            if mode == "url":
                note_url = form.get("note_url", [""])[0].strip()
                if not note_url:
                    raise ValueError("笔记 URL 不能为空")
                if "xiaohongshu.com" not in note_url:
                    raise ValueError("请输入有效的小红书笔记链接")
                keyword = note_url.split("/")[-1].split("?")[0][:30] or "note"
                job_id = create_job(keyword, 1, with_comments)
                thread = threading.Thread(target=run_job_url, args=(job_id, note_url, with_comments), daemon=True)
                thread.start()
                self.send_json({"id": job_id}, 202)

            elif mode == "user":
                user_url = form.get("user_url", [""])[0].strip()
                if not user_url:
                    raise ValueError("用户主页 URL 不能为空")
                if "xiaohongshu.com" not in user_url:
                    raise ValueError("请输入有效的小红书用户主页链接")
                user_id = user_url.rstrip("/").split("/")[-1].split("?")[0][:30] or "user"
                keyword = f"{user_id}"
                job_id = create_job(keyword, 50, with_comments)
                thread = threading.Thread(target=run_job_user, args=(job_id, user_url, with_comments), daemon=True)
                thread.start()
                self.send_json({"id": job_id}, 202)

            elif mode == "viral":
                keyword = validate_keyword(form.get("keyword", [""])[0])
                count = validate_count(form.get("count", ["10"])[0])
                sort_type_choice = validate_choice(form.get("sort_type_choice", ["2"])[0], {2, 3, 4}, 2)
                note_type = validate_choice(form.get("note_type", ["0"])[0], {0, 1, 2}, 0)
                note_time = validate_choice(form.get("note_time", ["0"])[0], {0, 1, 2, 3}, 0)
                search_options = {
                    "workflow": "viral",
                    "sort_type_choice": sort_type_choice,
                    "note_type": note_type,
                    "note_time": note_time,
                }
                job_id = create_job(keyword, count, with_comments, search_options)
                thread = threading.Thread(
                    target=run_job,
                    args=(job_id, keyword, count, with_comments),
                    kwargs={"search_options": search_options},
                    daemon=True,
                )
                thread.start()
                self.send_json({"id": job_id}, 202)

            else:
                keyword = validate_keyword(form.get("keyword", [""])[0])
                count = validate_count(form.get("count", ["10"])[0])
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

    def handle_cookie_phone_send(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        form = urllib.parse.parse_qs(body)
        try:
            self.send_json(create_phone_session(form.get("phone", [""])[0], form.get("zone", ["86"])[0]), 201)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_cookie_phone_login(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        form = urllib.parse.parse_qs(body)
        try:
            result = login_phone_session(form.get("session_id", [""])[0], form.get("code", [""])[0])
            self.send_json(result, headers=[("Set-Cookie", session_cookie_header(APP_USERNAME))])
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_qrcode_start(self):
        try:
            self.send_json(create_qrcode_session(), 201)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_qrcode_status(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        form = urllib.parse.parse_qs(body)
        try:
            result = check_qrcode_session(form.get("session_id", [""])[0])
            status = 200 if result.get("status") == "done" else 202
            self.send_json(result, status)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_watermark(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        form = urllib.parse.parse_qs(body)
        try:
            note_url = form.get("note_url", [""])[0].strip()
            if not note_url:
                raise ValueError("请输入小红书笔记链接")
            
            # Extract note_id
            import re
            m = re.search(r'/explore/([a-f0-9]+)', note_url)
            if not m:
                m = re.search(r'/note/([a-f0-9]+)', note_url)
            if not m:
                # Try the last segment
                parts = note_url.rstrip('/').split('/')
                note_id = parts[-1].split('?')[0]
            else:
                note_id = m.group(1)

            cookies_str, _ = init()
            if not cookies_str:
                raise ValueError(".env 缺少 COOKIES")

            # Get note info for images (preserve xsec_token/xsec_source from original URL)
            api = XHS_Apis()
            parsed_url = urllib.parse.urlparse(note_url)
            qs_params = urllib.parse.parse_qs(parsed_url.query)
            xsec_token = qs_params.get("xsec_token", [""])[0]
            xsec_source = qs_params.get("xsec_source", ["pc_search"])[0]
            explore_url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec_token}&xsec_source={xsec_source}"
            success, msg, note_info = api.get_note_info(explore_url, cookies_str)
            if not success:
                raise ValueError(str(msg or "获取笔记信息失败"))

            # Get note info dict
            info = note_info.get("data", {}).get("items", [{}])[0] if isinstance(note_info, dict) else {}
            note_card = info.get("note_card", {}) or info
            
            result = {"note_id": note_id}

            # Check for video
            video_addr = note_card.get("video", {}).get("media", {}).get("stream", {}).get("h264", [{}])[0].get("master_url", "")
            if not video_addr:
                video_addr = note_card.get("video_addr", "")
            if video_addr:
                # Get watermark-free
                _, _, clean_video = XHS_Apis.get_note_no_water_video(note_id)
                result["video_url"] = video_addr
                result["clean_video_url"] = clean_video or video_addr

            # Get images
            image_list = note_card.get("image_list", [])
            if not image_list:
                image_list = note_card.get("image_list_info", [])
            
            images = []
            clean_images = []
            for img in (image_list or []):
                if isinstance(img, dict):
                    url = img.get("url_default", "") or img.get("url", "") or img.get("info_list", [{}])[0].get("url", "")
                else:
                    url = str(img)
                if url:
                    images.append(url)
                    _, _, clean = XHS_Apis.get_note_no_water_img(url)
                    clean_images.append(clean or url)

            result["image_urls"] = images
            result["clean_image_urls"] = clean_images
            result["title"] = note_card.get("title", "") or note_card.get("display_title", "")

            self.send_json(result)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_search_user(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        form = urllib.parse.parse_qs(body)
        try:
            query = form.get("query", [""])[0].strip()
            if not query:
                raise ValueError("搜索关键词不能为空")
            count = min(int(form.get("count", ["10"])[0]), 30)
            cookies_str, _ = init()
            if not cookies_str:
                raise ValueError(".env 缺少 COOKIES")
            api = XHS_Apis()
            success, msg, user_list = api.search_some_user(query, count, cookies_str)
            if not success:
                raise ValueError(str(msg))
            self.send_json({"users": user_list, "total": len(user_list)})
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_ai_config_save(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        form = urllib.parse.parse_qs(body)
        try:
            config = ai_utils.save_ai_config(
                form.get("provider", [""])[0],
                form.get("api_key", [""])[0],
                form.get("model", [""])[0],
                form.get("enabled", ["0"])[0] == "1",
            )
            self.send_json(config)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_ai_analyze(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        form = urllib.parse.parse_qs(body)
        try:
            job_id = form.get("job_id", [""])[0]
            skill_type = form.get("skill_type", ["trend"])[0]
            custom_prompt = form.get("custom_prompt", [""])[0]

            config = ai_utils.load_ai_config()
            if not config["configured"] or not config["enabled"]:
                raise ValueError("AI 未配置或未启用，请先在 AI 设置中配置 API Key")

            job = get_job(job_id)
            if not job:
                raise ValueError("任务不存在")
            if job.get("state") != "success":
                raise ValueError("采集任务尚未完成，请等待采集结束后再分析")

            analysis_id = uuid.uuid4().hex[:12]
            thread = threading.Thread(
                target=ai_utils.run_analysis,
                args=(analysis_id, job, skill_type, custom_prompt, config["provider"], os.environ.get("AI_API_KEY", ""), config["model"]),
                daemon=True,
            )
            thread.start()
            self.send_json({"id": analysis_id}, 202)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_ai_draft(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        form = urllib.parse.parse_qs(body)
        try:
            job_id = form.get("job_id", [""])[0]
            source_analysis_id = form.get("source_analysis_id", [""])[0]
            style_profile = validate_text_field(form.get("style_profile", [""])[0], "账号定位 / 风格描述", 800)
            topic_angle = validate_text_field(form.get("topic_angle", [""])[0], "切入选题角度", 200)
            target_audience = validate_text_field(form.get("target_audience", [""])[0], "目标人群", 200)
            tone = validate_text_field(form.get("tone", [""])[0], "语气", 50)
            include_image_prompts = form.get("include_image_prompts", ["0"])[0] in ("on", "1", "true")
            draft_count = validate_choice(form.get("draft_count", ["1"])[0], {1, 2, 3}, 1)

            config = ai_utils.load_ai_config()
            if not config["configured"] or not config["enabled"]:
                raise ValueError("AI 未配置或未启用，请先在 AI 设置中配置 API Key")

            job = get_job(job_id)
            if not job:
                raise ValueError("任务不存在")
            if job.get("state") != "success":
                raise ValueError("采集任务尚未完成，请等待采集结束后再生成草稿")

            source_analysis = ""
            if source_analysis_id:
                source = ai_utils.get_ai_result(source_analysis_id)
                if source and source.get("status") == "done":
                    source_analysis = source.get("result", "")

            draft_request = {
                "style_profile": style_profile,
                "topic_angle": topic_angle,
                "target_audience": target_audience,
                "tone": tone,
                "draft_count": draft_count,
                "include_image_prompts": include_image_prompts,
                "source_analysis": source_analysis,
            }
            ai_config = {
                "provider": config["provider"],
                "api_key": os.environ.get("AI_API_KEY", ""),
                "model": config["model"],
            }
            draft_id = uuid.uuid4().hex[:12]
            thread = threading.Thread(
                target=ai_utils.run_draft_generation,
                args=(draft_id, job, draft_request, ai_config),
                daemon=True,
            )
            thread.start()
            self.send_json({"id": draft_id}, 202)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)

    def handle_ai_test(self):
        try:
            config = ai_utils.load_ai_config()
            if not config["configured"]:
                raise ValueError("AI 未配置")
            api_key = os.environ.get("AI_API_KEY", "")
            result = ai_utils.call_llm(
                config["provider"], api_key, config["model"],
                "你是一个助手。请回复：连接测试成功。",
                "请回复：连接测试成功，模型正常工作。",
            )
            self.send_json({"ok": True, "message": result[:200]})
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)})

    def handle_ai_status(self, query):
        params = urllib.parse.parse_qs(query)
        analysis_id = params.get("id", [""])[0]
        result = ai_utils.get_ai_result(analysis_id)
        if not result:
            self.send_json({"error": "分析任务不存在或已过期"}, 404)
            return
        self.send_json(result)

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
