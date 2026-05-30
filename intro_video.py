"""
红薯采采 介绍视频制作
- 录制演示视频
- 生成自然旁白 (逐句录制 + 真实停顿)
- 烧录 SRT 字幕
"""
import subprocess
import os
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:18080"
OUTPUT = Path(__file__).parent / "demo_output"

# ============================================================
# 旁白脚本 (开始秒, 结束秒, 文本)
# ============================================================
LINES = [
    (0, 5,   "大家好，今天介绍一款某书数据采集分析工具——红薯采采"),
    (5, 11,  "打开浏览器登录后，进入采集工作台，这是核心操作页面"),
    (11, 18, "支持三种采集模式：关键词搜索，输入关键词和数量即可"),
    (18, 23, "URL采集，粘贴单篇笔记链接直接抓取"),
    (23, 28, "用户主页采集，粘贴主页链接抓取全部笔记"),
    (28, 33, "页面底部内置去水印工具，提取无水印视频图片直链"),
    (33, 40, "Cookie管理页提供三种登录：手动粘贴、手机验证码、二维码扫码"),
    (40, 45, "还支持用户搜索，输入关键词搜索平台用户"),
    (45, 54, "AI分析页支持Claude、OpenAI、DeepSeek，配置后可测试连接"),
    (54, 60, "内置五种分析技能：趋势、情感、策略、画像、爆款特征，也支持自定义"),
    (60, 65, "回到采集台，输入关键词，设置数量，点击开始采集"),
    (65, 71, "系统自动搜索笔记、解析详情、采集评论，实时显示进度"),
    (71, 77, "完成后下载Excel，配置AI还可对结果进行智能分析"),
    (77, 83, "红薯采采，让某书数据采集变得简单高效，感谢观看"),
]

TOTAL_DURATION = 85  # seconds


def record_video():
    """录制 ~85s 演示视频"""
    print("🎬 录制演示视频...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            record_video_dir=str(OUTPUT),
            record_video_size={"width": 1440, "height": 900},
        )
        page = context.new_page()
        w = page.wait_for_timeout

        # 0-5s: Login page
        page.goto(f"{BASE_URL}/login")
        page.wait_for_load_state("networkidle")
        w(2000)
        page.locator(".tab").nth(1).click()
        w(800)
        page.fill("#username", "admin")
        page.fill("#password", "admin")
        w(1000)
        page.locator("button[type=submit]").click()
        page.wait_for_url(f"{BASE_URL}/")
        page.wait_for_load_state("networkidle")
        w(2000)

        # 5-11s: Collect tab overview
        w(3000)
        page.fill("#keyword", "苏州探店")
        w(800)
        page.fill("#count", "10")
        w(2500)

        # 11-18s: Keyword mode
        page.locator(".mode-tab").nth(1).click()  # URL mode
        w(4000)
        page.locator(".mode-tab").nth(2).click()  # User mode
        w(4000)

        # 23-28s: User mode continued, back to keyword
        page.locator(".mode-tab").nth(0).click()
        w(2000)

        # 28-33s: Watermark
        page.evaluate("document.getElementById('wmNoteUrl')?.scrollIntoView({behavior:'instant'})")
        w(4000)
        page.evaluate("window.scrollTo({top:0, behavior:'instant'})")
        w(1500)

        # 33-40s: Cookie tab
        page.locator(".nav-tab").nth(1).click()
        w(2000)
        page.evaluate("window.scrollTo({top:document.body.scrollHeight/2, behavior:'instant'})")
        w(2000)
        page.evaluate("window.scrollTo({top:document.body.scrollHeight, behavior:'instant'})")
        w(2000)
        page.evaluate("window.scrollTo({top:0, behavior:'instant'})")
        w(1500)

        # 40-45s: User search area
        page.locator("#searchUserQuery").scroll_into_view_if_needed()
        w(3000)
        page.evaluate("window.scrollTo({top:0, behavior:'instant'})")
        w(1000)

        # 45-54s: AI tab
        page.locator(".nav-tab").nth(2).click()
        w(2000)
        page.select_option("#aiProvider", "deepseek")
        w(800)
        page.fill("#aiApiKey", "sk-your-key-here")
        w(2000)
        page.evaluate("window.scrollTo({top:document.body.scrollHeight, behavior:'instant'})")
        w(3000)
        page.evaluate("window.scrollTo({top:0, behavior:'instant'})")
        w(1500)

        # 54-60s: AI skills
        page.evaluate("window.scrollTo({top:document.body.scrollHeight/2, behavior:'instant'})")
        w(4000)
        page.evaluate("window.scrollTo({top:0, behavior:'instant'})")
        w(1000)

        # 60-65s: Back to collect, start job
        page.locator(".nav-tab").nth(0).click()
        w(1500)
        page.fill("#keyword", "某书热门探店")
        w(600)
        page.fill("#count", "3")
        w(2000)
        page.locator("#startBtn").click()
        w(3000)

        # 65-71s: Job running
        w(5000)

        # 71-77s: Job result (simulated)
        w(6000)

        # 77-83s: Ending
        w(5000)

        page.close()
        context.close()
        browser.close()

    videos = sorted(OUTPUT.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
    if videos:
        dest = OUTPUT / "raw_demo.webm"
        if dest.exists():
            dest.unlink()
        videos[0].rename(dest)
        dur = float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
            "-of","default=noprint_wrappers=1:nokey=1",str(dest)], capture_output=True, text=True).stdout.strip())
        print(f"✅ 视频: {dest} ({dest.stat().st_size/1024/1024:.1f} MB, {dur:.1f}s)")
        return dest
    return None


def generate_srt():
    """生成 SRT 字幕文件"""
    srt_path = OUTPUT / "subtitles.srt"
    lines = []
    for i, (start, end, text) in enumerate(LINES, 1):
        lines.append(str(i))
        lines.append(f"{sec_to_srt(start)} --> {sec_to_srt(end)}")
        lines.append(text)
        lines.append("")
    srt_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ 字幕: {srt_path}")
    return srt_path


def sec_to_srt(seconds):
    """Convert seconds to SRT timestamp HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_audio():
    """生成自然旁白 — 逐句录制 + 真实停顿"""
    print("\n🎤 生成旁白...")
    tmp = Path(tempfile.mkdtemp())
    segments = []

    for i, (start, end, text) in enumerate(LINES):
        seg = tmp / f"seg_{i:02d}.aiff"
        # Add slight pause at beginning and end of each segment
        padded = f"[[slnc 200]]{text}[[slnc 200]]"
        subprocess.run([
            "say", "-v", "Tingting",
            "-r", "195",  # slightly slower for natural feel
            "-o", str(seg),
            padded
        ], check=True)
        dur = float(subprocess.run([
            "ffprobe","-v","error","-show_entries","format=duration",
            "-of","default=noprint_wrappers=1:nokey=1",str(seg)
        ], capture_output=True, text=True).stdout.strip())
        segments.append((start, seg, dur))
        print(f"  {i+1}/{len(LINES)}: {start}s-{end}s ({dur:.1f}s) '{text[:25]}...'")

    # Create silent base
    base = tmp / "silence.aiff"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"anullsrc=r=44100:cl=mono:d={TOTAL_DURATION}",
        "-acodec", "pcm_s16le", str(base)
    ], check=True, capture_output=True)

    # Mix all segments at their start times
    inputs = ["-i", str(base)]
    filters = []
    labels = []
    for i, (start, seg, dur) in enumerate(segments):
        inputs.extend(["-i", str(seg)])
        delay_ms = int(start * 1000)
        filters.append(f"[{i+1}:a]adelay={delay_ms}|{delay_ms}[a{i}]")
        labels.append(f"[a{i}]")

    mix = "".join(labels) + f"[0:a]amix=inputs={len(segments)+1}:normalize=0[out]"
    filter_str = ";".join(filters) + ";" + mix

    mixed = OUTPUT / "narration.aiff"
    subprocess.run([
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_str,
        "-map", "[out]", "-ac", "1", "-ar", "44100",
        str(mixed)
    ], check=True, capture_output=True)

    # Convert to MP3
    mp3 = OUTPUT / "narration.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(mixed),
        "-acodec", "libmp3lame", "-ab", "128k",
        str(mp3)
    ], check=True, capture_output=True)

    print(f"✅ 旁白: {mp3} ({mp3.stat().st_size/1024/1024:.1f} MB)")
    return mp3


def burn_subtitles_and_combine(video_path, audio_path, srt_path):
    """烧录字幕 + 合成音视频"""
    print("\n🎬 合成最终视频（含字幕）...")
    final = OUTPUT / "hongshu-caicai-intro.mp4"

    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-vf", f"subtitles={srt_path}:force_style='FontName=PingFang SC,FontSize=22,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,Shadow=1,Alignment=2,MarginV=40'",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "128k",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest",
        str(final)
    ], capture_output=True, text=True)

    if result.returncode == 0:
        print(f"✅ 最终视频: {final} ({final.stat().st_size/1024/1024:.1f} MB)")
        return final
    else:
        print(f"❌ 错误: {result.stderr[-300:]}")
        return None


if __name__ == "__main__":
    # Step 1: Record video
    video = record_video()
    if not video:
        print("❌ 录制失败")
        exit(1)

    # Step 2: Generate SRT subtitles
    srt = generate_srt()

    # Step 3: Generate narration audio
    audio = generate_audio()

    # Step 4: Burn subtitles + combine
    final = burn_subtitles_and_combine(video, audio, srt)

    if final:
        print(f"\n🎉 完成！介绍视频: {final}")
