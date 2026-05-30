"""
红薯采采 Demo 录制脚本
使用 Playwright 录制浏览器操作视频
"""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path(__file__).parent / "demo_output"
OUTPUT_DIR.mkdir(exist_ok=True)

BASE_URL = "http://127.0.0.1:18080"


def run_demo():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            record_video_dir=str(OUTPUT_DIR),
            record_video_size={"width": 1440, "height": 900},
        )
        page = context.new_page()

        # ── Step 1: Login page ──
        print("1. 登录页面...")
        page.goto(f"{BASE_URL}/login")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)
        page.screenshot(path=str(OUTPUT_DIR / "01-login.png"), full_page=True)

        # Switch to admin login tab
        admin_tab = page.locator(".tab", has_text="密码登录")
        if admin_tab.count() > 0:
            admin_tab.first.click()
            page.wait_for_timeout(300)

        page.fill("#username", "admin")
        page.fill("#password", "admin")
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUTPUT_DIR / "02-login-filled.png"), full_page=True)

        page.click("button[type=submit]")
        page.wait_for_url(f"{BASE_URL}/")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)
        print("   ✅ 登录成功")

        # ── Step 2: 采集工作台 ──
        print("2. 采集工作台...")
        page.screenshot(path=str(OUTPUT_DIR / "03-collect-tab.png"), full_page=True)

        # Show mode switching
        url_mode = page.locator(".mode-tab", has_text="URL")
        if url_mode.count() > 0:
            url_mode.first.click()
            page.wait_for_timeout(400)
        page.screenshot(path=str(OUTPUT_DIR / "04-mode-url.png"), full_page=True)

        user_mode = page.locator(".mode-tab", has_text="用户主页")
        if user_mode.count() > 0:
            user_mode.first.click()
            page.wait_for_timeout(400)
        page.screenshot(path=str(OUTPUT_DIR / "05-mode-user.png"), full_page=True)

        # Back to keyword mode
        kw_mode = page.locator(".mode-tab", has_text="关键词")
        if kw_mode.count() > 0:
            kw_mode.first.click()
            page.wait_for_timeout(400)

        # Scroll to watermark section
        page.evaluate("document.getElementById('wmNoteUrl')?.scrollIntoView({behavior:'instant'})")
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUTPUT_DIR / "06-watermark.png"), full_page=True)

        # ── Step 3: Cookie 管理 ──
        print("3. Cookie 管理...")
        cookie_tab = page.locator(".nav-tab", has_text="Cookie")
        if cookie_tab.count() > 0:
            cookie_tab.first.click()
            page.wait_for_timeout(600)
        page.screenshot(path=str(OUTPUT_DIR / "07-cookie-tab.png"), full_page=True)

        # ── Step 4: AI 分析 ──
        print("4. AI 分析...")
        ai_tab = page.locator(".nav-tab", has_text="AI")
        if ai_tab.count() > 0:
            ai_tab.first.click()
            page.wait_for_timeout(600)
        page.screenshot(path=str(OUTPUT_DIR / "08-ai-tab.png"), full_page=True)

        # Fill AI config
        page.select_option("#aiProvider", "deepseek")
        page.fill("#aiApiKey", "sk-your-api-key-here")
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUTPUT_DIR / "09-ai-config.png"), full_page=True)

        # ── Step 5: Back to collect, show full flow ──
        print("5. 采集流程演示...")
        collect_tab = page.locator(".nav-tab", has_text="采集")
        if collect_tab.count() > 0:
            collect_tab.first.click()
            page.wait_for_timeout(400)

        page.fill("#keyword", "苏州探店")
        page.fill("#count", "5")
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUTPUT_DIR / "10-collect-ready.png"), full_page=True)

        # ── Done ──
        print("\n✅ Demo 录制完成！")
        page.close()
        context.close()
        browser.close()

    # Find the video
    videos = list(OUTPUT_DIR.glob("*.webm"))
    if videos:
        video_path = videos[0]
        # Rename
        final_path = OUTPUT_DIR / "hongshu-caicai-demo.webm"
        video_path.rename(final_path)
        print(f"📹 视频: {final_path}")
        print(f"   大小: {final_path.stat().st_size / 1024 / 1024:.1f} MB")

    # List screenshots
    pngs = sorted(OUTPUT_DIR.glob("*.png"))
    print(f"\n📸 截图 ({len(pngs)} 张):")
    for p in pngs:
        print(f"   {p.name}")


if __name__ == "__main__":
    run_demo()
