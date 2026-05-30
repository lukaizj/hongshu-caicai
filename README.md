# 红薯采采

红薯采采是本地部署的小红书采集台，提供浏览器 UI、Cookie 管理、多模式采集、评论同步、AI 智能分析和 Excel 下载。项目定位为个人娱乐、学习交流和技术验证，不面向生产或商业场景。

> **免责声明：本项目仅供个人娱乐和学习交流使用。禁止用于商业化运营、批量采集、绕过平台风控、侵犯用户隐私、抓取敏感信息或任何违反目标平台规则与法律法规的行为。使用者需自行承担由使用本项目产生的全部责任。**

## 功能演示

🎬 **介绍视频（带旁白+字幕）**

[![红薯采采介绍视频](demo_output/hongshu-caicai-intro.mp4)](demo_output/hongshu-caicai-intro.mp4)

> 点击上图下载或播放完整介绍视频（约 80 秒，带中文旁白和字幕）

## 核心功能

| 模块 | 功能 |
|------|------|
| 🔐 登录保护 | admin/admin 登录采集台，可选手机验证码直登 |
| 🍪 Cookie 管理 | 手动粘贴 / 手机验证码 / 二维码扫码 三种方式 |
| 🔍 三种采集模式 | 关键词搜索 / URL 采集 / 用户主页采集 |
| 💬 评论采集 | 可选同步采集一级评论和二级评论 |
| 📊 实时进度 | 任务状态、阶段进度、时间线事件和采集指标 |
| 📥 Excel 导出 | 笔记与评论分别生成 `.xlsx`，页面内直接下载 |
| 🤖 AI 分析 | Claude / OpenAI / DeepSeek 三家大模型，5 种分析技能 |
| 🖼️ 去水印 | 粘贴笔记链接 → 无水印视频/图片直链 |
| 🔎 用户搜索 | 搜索小红书用户，查看头像/昵称/主页链接 |
| 🎨 灵动 UI | 菜单栏切换、浮动粒子、卡片倾斜、五彩纸屑等视觉效果 |

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 20+

### 安装依赖

```bash
pip install -r requirements.txt
npm install crypto-js
```

### 配置 Cookie

在项目根目录 `.env` 中配置登录后的小红书 Web Cookie：

```env
COOKIES='a1=xxx; web_session=xxx'
```

也可以通过浏览器打开采集台后使用以下方式获取：
- **手机验证码**：Cookie 管理 Tab → 一键获取 Cookie → 发送验证码 → 登录
- **二维码扫码**：Cookie 管理 Tab → 扫码登录 → 生成二维码 → 小红书 App 扫码
- **手动粘贴**：Cookie 管理 Tab → 手动替换 Cookie → 粘贴浏览器复制的 Cookie

### 启动 Web 采集台

```bash
cd /opt/hongshu-caicai
HOST=0.0.0.0 PYTHONPATH=/opt/hongshu-caicai .venv/bin/python web_app.py
```

浏览器打开 `http://<server-ip>:18080`，默认账号 `admin / admin`。

### 环境变量

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `HOST` | `127.0.0.1` | 监听地址 |
| `PORT` | `18080` | Web 采集台端口 |
| `APP_USERNAME` | `admin` | 登录用户名 |
| `APP_PASSWORD` | `admin` | 登录密码 |
| `COOKIES` | `.env` 读取 | 小红书登录 Cookie |
| `AI_PROVIDER` | — | AI 提供商 (claude/openai/deepseek) |
| `AI_API_KEY` | — | AI API Key |
| `AI_MODEL` | — | 模型名称 |
| `AI_ENABLED` | — | 是否启用 AI 分析 |

## 使用流程

1. 打开 `http://<server-ip>:18080`，登录采集台
2. 在 **Cookie 管理** Tab 确认 Cookie 可用
3. 在 **采集工作台** Tab 选择采集模式：
   - **关键词搜索**：输入关键词 + 数量
   - **URL 采集**：粘贴单篇笔记链接
   - **用户主页**：粘贴用户主页链接
4. 可选勾选「同步采集评论」
5. 点击「开始采集」，等待进度完成
6. 下载 Excel 文件
7. 可选：在 **AI 分析** Tab 配置大模型 API Key，对采集结果进行智能分析
8. 可选：使用去水印工具提取无水印媒体链接

## API 接口

### 采集任务
```bash
# 启动关键词采集
curl -s -X POST http://127.0.0.1:18080/api/start \
  -d 'mode=keyword&keyword=榴莲&count=5'

# 启动 URL 采集  
curl -s -X POST http://127.0.0.1:18080/api/start \
  -d 'mode=url&note_url=https://www.xiaohongshu.com/explore/xxx'

# 启动用户主页采集
curl -s -X POST http://127.0.0.1:18080/api/start \
  -d 'mode=user&user_url=https://www.xiaohongshu.com/user/profile/xxx'

# 查询状态
curl -s 'http://127.0.0.1:18080/api/status?id=<job_id>'

# 下载文件
curl -OJ 'http://127.0.0.1:18080/download?file=榴莲.xlsx'
```

### Cookie 管理
```bash
curl -s -X POST http://127.0.0.1:18080/api/cookie/phone/send \
  -d 'phone=13800138000&zone=86'

curl -s -X POST http://127.0.0.1:18080/api/cookie/phone/login \
  -d 'session_id=<id>&code=123456'
```

### AI 分析
```bash
curl -s http://127.0.0.1:18080/api/ai/config
curl -s -X POST http://127.0.0.1:18080/api/ai/analyze \
  -d 'job_id=<job_id>&skill_type=trend'
curl -s http://127.0.0.1:18080/api/ai/test
```

### 工具
```bash
# 去水印
curl -s -X POST http://127.0.0.1:18080/api/watermark \
  -d 'note_url=https://www.xiaohongshu.com/explore/xxx'

# 搜索用户
curl -s -X POST http://127.0.0.1:18080/api/search/user \
  -d 'query=美妆博主&count=10'
```

### 状态字段

| 字段 | 说明 |
|------|------|
| `state` | `queued` / `running` / `success` / `failed` |
| `stage` | `init` / `search` / `notes` / `comments` / `complete` |
| `percent` | 进度百分比 |
| `message` | 当前状态文案 |
| `events` | 任务事件时间线 |
| `downloads` | 可下载文件列表 |

## 项目结构

```text
hongshu-caicai/
├── web_app.py                 # Web 采集台入口（单文件应用）
├── spider/spider.py           # 采集任务封装
├── apis/
│   ├── xhs_pc_apis.py         # PC 端采集接口
│   ├── xhs_pc_login_apis.py   # PC 端登录（手机/二维码）
│   ├── xhs_creator_apis.py    # 创作者平台接口
│   └── xhs_pugongying_apis.py # 蒲公英 KOL 接口
├── xhs_utils/
│   ├── ai_utils.py            # AI 分析引擎（新增）
│   ├── common_util.py         # 配置读取
│   ├── cookie_util.py         # Cookie 解析
│   ├── data_util.py           # Excel 保存、数据处理
│   └── xhs_util.py            # 签名封装
├── static/                    # 本地签名 JS 脚本
├── datas/excel_datas/         # Excel 输出目录
├── demo_output/               # 演示截图与视频
├── requirements.txt
├── package.json
├── Dockerfile
└── README.md
```

## 安全注意

- 采集台持有登录 Cookie，建议只开放给可信网络访问
- `.env` 包含小红书 Cookie 和 AI API Key，**不要提交到 Git**
- 默认登录密码请在长期部署时通过环境变量修改
- 评论采集请求量更大，建议先 1-10 篇小批量验证
- AI API Key 可被已登录用户查看/修改，注意访问控制
