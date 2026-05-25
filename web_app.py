import html
import json
import os
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from spider.spider import Data_Spider
from xhs_utils.common_util import init

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "18080"))
MAX_COUNT = 50
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
EXCEL_DIR = os.path.join(PROJECT_DIR, "datas", "excel_datas")
JOBS = {}
JOBS_LOCK = threading.Lock()
MAX_EVENTS = 80


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
  <title>小红书采集台 · Spider_XHS</title>
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
    .masthead { display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; margin-bottom: 30px; animation: rise .52s cubic-bezier(.16,1,.3,1); }
    .overline { font-family: var(--font-mono); letter-spacing: .18em; font-size: 12px; color: var(--seal-red); text-transform: uppercase; }
    h1 { margin: 8px 0 8px; font-family: var(--font-display); font-size: clamp(42px, 7vw, 86px); line-height: .92; letter-spacing: -.04em; }
    .subtitle { margin: 0; color: var(--ink-soft); font-size: 17px; }
    .status-pill { min-width: 118px; padding: 10px 14px; border: 1px solid var(--line); background: rgba(255,250,241,.64); font-family: var(--font-mono); text-align: center; color: var(--ink-soft); box-shadow: 0 10px 28px var(--shadow); }
    .grid { display: grid; grid-template-columns: minmax(320px, .82fr) minmax(420px, 1.18fr); gap: 26px; align-items: start; }
    .card { position: relative; background: var(--card); border: 1px solid var(--line); box-shadow: 0 22px 70px var(--shadow); backdrop-filter: blur(10px); }
    .command { padding: 28px; animation: slide-left .56s cubic-bezier(.16,1,.3,1); }
    .command:before { content: ""; position: absolute; inset: 0 auto 0 0; width: 8px; background: linear-gradient(180deg, var(--xhs-red), var(--seal-red)); }
    .section-title { margin: 0 0 22px; font-family: var(--font-display); font-size: 30px; }
    label { display: block; margin: 18px 0 8px; color: var(--ink-soft); font-size: 13px; letter-spacing: .08em; text-transform: uppercase; }
    input[type="text"], input[type="number"] {
      width: 100%; border: 0; border-bottom: 2px solid var(--line); padding: 13px 2px 12px; background: transparent; color: var(--ink); font-size: 22px; font-family: var(--font-display); outline: none; transition: border-color .22s cubic-bezier(.2,.8,.2,1), box-shadow .22s;
    }
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
    .hint { margin: 16px 0 0; color: var(--muted); font-size: 13px; line-height: 1.65; }
    .dossier { padding: 28px; min-height: 560px; animation: slide-right .56s cubic-bezier(.16,1,.3,1); }
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
    .fill { height: 100%; width: 0%; background: linear-gradient(90deg, var(--seal-red), var(--xhs-red)); transition: width .35s cubic-bezier(.2,.8,.2,1); }
    .fill.running:after { content: ""; display: block; height: 100%; background: repeating-linear-gradient(45deg, rgba(255,255,255,.18) 0 8px, transparent 8px 16px); animation: hatch 1.2s linear infinite; }
    .timeline { position: relative; margin: 0 0 22px; padding-left: 26px; }
    .timeline:before { content: ""; position: absolute; left: 8px; top: 8px; bottom: 8px; width: 2px; background: linear-gradient(var(--xhs-red), rgba(185,22,45,.16)); }
    .step { position: relative; padding: 0 0 18px; color: var(--muted); }
    .step:before { content: ""; position: absolute; left: -24px; top: 5px; width: 12px; height: 12px; border: 1px solid currentColor; background: var(--paper); }
    .step.active { color: var(--seal-red); }
    .step.active:before { background: var(--xhs-red); border-color: var(--xhs-red); animation: pulse 1.8s infinite; }
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
    @keyframes rise { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: none; } }
    @keyframes slide-left { from { opacity: 0; transform: translateX(-16px); } to { opacity: 1; transform: none; } }
    @keyframes slide-right { from { opacity: 0; transform: translateX(16px); } to { opacity: 1; transform: none; } }
    @keyframes hatch { to { transform: translateX(18px); } }
    @keyframes pulse { 0%,100% { box-shadow: 0 0 0 0 rgba(255,36,66,.35); } 50% { box-shadow: 0 0 0 9px rgba(255,36,66,0); } }
    @media (max-width: 860px) { .grid { grid-template-columns: 1fr; } .metrics { grid-template-columns: repeat(2, 1fr); } .masthead { flex-direction: column; } }
    @media (prefers-reduced-motion: reduce) { * { animation: none !important; transition-duration: .01ms !important; } }
  </style>
</head>
<body>
  <main class="shell">
    <header class="masthead">
      <div>
        <div class="overline">SPIDER_XHS / DATA CONSOLE</div>
        <h1>小红书采集台</h1>
        <p class="subtitle">关键词驱动的笔记与评论采集任务台</p>
      </div>
      <div id="mastState" class="status-pill">IDLE</div>
    </header>

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
        <p class="hint">外部访问未加密码。采集期间请勿高频刷新；任务进度会自动更新。</p>
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
    let pollTimer = null;

    function stageIndex(stage) {
      return ['init', 'search', 'notes', 'comments', 'complete'].indexOf(stage);
    }

    function setState(job) {
      const labels = { queued: '排队中', running: '采集中', success: '已完成', failed: '异常' };
      stateEl.textContent = labels[job.state] || '待命';
      mastState.textContent = (job.state || 'idle').toUpperCase();
      stateEl.className = 'state ' + (job.state || '');
      fillEl.className = 'fill ' + (job.state === 'running' ? 'running' : '');
      messageEl.textContent = job.message || '等待任务';
      percentEl.textContent = `${job.percent || 0}%`;
      fillEl.style.width = `${job.percent || 0}%`;
      mTarget.textContent = job.count || 0;
      mNotes.textContent = job.metrics?.notes || 0;
      mComments.textContent = job.metrics?.comments || 0;
      mFailed.textContent = job.metrics?.failed_comments || 0;

      const active = stageIndex(job.stage);
      steps.forEach((step, index) => {
        step.classList.toggle('done', active > index || job.state === 'success');
        step.classList.toggle('active', active === index && job.state === 'running');
      });

      eventsEl.innerHTML = (job.events || []).slice().reverse().map(event => `
        <div class="event ${event.level || 'info'}"><time>${event.time}</time><div><b>${escapeHtml(event.title)}</b><br>${escapeHtml(event.detail || '')}</div></div>
      `).join('');

      downloadsEl.innerHTML = (job.downloads || []).map(item => {
        const href = '/download?file=' + encodeURIComponent(item.file);
        return `<a class="download" href="${href}">↓ ${escapeHtml(item.label)}</a>`;
      }).join('');
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
    }

    async function poll(jobId) {
      const res = await fetch('/api/status?id=' + encodeURIComponent(jobId));
      const job = await res.json();
      setState(job);
      if (job.state === 'success' || job.state === 'failed') {
        clearInterval(pollTimer);
        pollTimer = null;
        startBtn.disabled = false;
        startBtn.textContent = '开始采集';
      }
    }

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


class SpiderHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.send_html(render_page())
            return
        if parsed.path == "/api/status":
            self.handle_status(parsed.query)
            return
        if parsed.path == "/download":
            self.handle_download(parsed.query)
            return
        self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/start":
            self.handle_start()
            return
        self.send_error(404)

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

    def send_html(self, content):
        data = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        print(f"{self.address_string()} - {format % args}")


if __name__ == "__main__":
    os.chdir(PROJECT_DIR)
    server = ThreadingHTTPServer((HOST, PORT), SpiderHandler)
    print(f"Spider_XHS web app running at http://{HOST}:{PORT}")
    server.serve_forever()
