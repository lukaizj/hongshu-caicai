# 红薯采采 (Hongshu Caicai)

小红书数据采集与智能分析控制台。基于 Python HTTP Server 的单文件 Web 应用，支持关键词搜索、URL 采集、用户主页采集、评论抓取、AI 分析等功能。

## 快速启动

```bash
cd /opt/hongshu-caicai
pip install -r requirements.txt
npm install crypto-js           # 小红书签名 JS 依赖
HOST=0.0.0.0 PYTHONPATH=/opt/hongshu-caicai .venv/bin/python web_app.py
```

浏览器打开 `http://<server-ip>:18080`，默认账号 `admin / admin`。

## Web 控制台

### 三个功能 Tab

| Tab | 功能 |
|-----|------|
| **采集工作台** | 三种采集模式 + 进度面板 + AI 分析入口 + 去水印工具 |
| **Cookie 管理** | Cookie 状态 / 手动替换 / 手机验证码登录 / 二维码扫码登录 / 用户搜索 |
| **AI 分析** | AI 配置（Claude / OpenAI / DeepSeek）+ 分析技能说明 |

### 采集工作台 — 三种模式

| 模式 | 输入 | 说明 |
|------|------|------|
| 关键词搜索 | 关键词 + 数量 | 搜索小红书关键词，支持评论同步采集 |
| URL 采集 | 笔记链接 | 粘贴单篇笔记 URL 直接抓取 |
| 用户主页 | 用户主页链接 | 抓取该用户全部笔记 |

去水印工具在采集表单底部：粘贴笔记链接 → 提取无水印视频/图片直链。

### Cookie 管理 — 三种登录方式

| 方式 | 说明 |
|------|------|
| 手动替换 | 浏览器 F12 复制 Cookie 粘贴保存 |
| 手机验证码 | 输入绑定小红书的手机号，接收短信验证码登录 |
| 二维码扫码 | 生成二维码，小红书 App 扫码登录 |

### AI 分析

支持三家大模型：**Claude (Anthropic)** / **OpenAI** / **DeepSeek**。API Key 在 AI 分析 Tab 配置，支持测试连接。

五种预设分析技能 + 自定义分析：

| 技能 | 分析内容 |
|------|---------|
| 内容趋势分析 | 热门主题、内容形式偏好、互动规律、时间趋势 |
| 评论情感分析 | 情感分布、高频关键词、改进建议、口碑亮点 |
| 选题策略建议 | 爆款基因、受众洞察、内容缺口、具体选题方向 |
| 用户画像分析 | 用户身份、消费行为、内容偏好、决策旅程 |
| 爆款特征提取 | 标题规律、互动比率、形式因素、时间因素 |

## API 端点

### 认证
- `GET /login` — 登录页面（支持密码 + 手机验证码双模式）
- `POST /api/login` — 用户名密码登录
- `POST /api/logout` — 退出登录

### 采集任务
- `POST /api/start` — 启动采集 (`mode=keyword|url|user`)
- `GET /api/status?id=<job_id>` — 查询任务进度
- `GET /download?file=<xlsx>` — 下载 Excel（路径遍历保护）

### Cookie 管理
- `GET /api/cookie/status` — 查看 Cookie 状态（含 masked summary）
- `POST /api/cookie/save` — 手动保存 Cookie（验证 a1 和 web_session）
- `POST /api/cookie/clear` — 清除 Cookie
- `POST /api/cookie/phone/send` — 发送手机验证码（无需登录）
- `POST /api/cookie/phone/login` — 验证码登录（成功后自动写入 COOKIES）

### 二维码登录
- `POST /api/qrcode/start` — 生成二维码（返回 base64 PNG）
- `POST /api/qrcode/status` — 查询扫码状态

### AI 分析
- `GET /api/ai/config` — 查看 AI 配置（含 masked key）
- `POST /api/ai/config` — 保存 AI 配置
- `POST /api/ai/analyze` — 启动 AI 分析
- `GET /api/ai/status?id=<analysis_id>` — 查询分析结果
- `GET /api/ai/test` — 测试 LLM 连接

### 工具
- `POST /api/watermark` — 去水印（笔记链接 → 无水印视频/图片 URL）
- `POST /api/search/user` — 搜索用户

## 数据流

1. Cookie 可通过手动粘贴、手机验证码或二维码扫码三种方式获取，成功登录后写入 `.env` 的 `COOKIES=`。
2. Web 表单提交采集参数（模式、关键词/URL、数量、是否采集评论）
3. `run_job()` / `run_job_url()` / `run_job_user()` 加载 `.env` 中的 `COOKIES`
4. `Data_Spider` 抓取笔记/评论，导出 `{name}.xlsx`
5. 任务完成后可选择 AI 分析 Excel 数据

## 本地修复与改进

- 搜索生成的笔记 URL 包含 `xsec_source=pc_search` 使评论 API 获得有效上下文
- 评论 API 调用在主评论和子评论端点均传递 `xsec_source`
- `handle_comment_info()` 容错处理可选/缺失的评论字段，单条畸形评论不影响同行数据
- 评论导出在零评论情况下通过进度事件报告，而非静默成功
- 电话端点已公开（无需预先认证），手机验证码登录成功后自动设置 admin session
- 二维码登录保留 URL 中的 xsec_token/xsec_source 参数

## 环境变量 (.env)

```
COOKIES='a1=xxx; web_session=xxx'     # 小红书 Cookie（必需）
AI_PROVIDER='claude'                  # AI 提供商
AI_API_KEY='sk-xxx'                   # API Key
AI_MODEL='claude-sonnet-4-6'          # 模型名称
AI_ENABLED='1'                        # 是否启用 AI
```

## 验证命令

```bash
cd /opt/hongshu-caicai
PYTHONPATH=/opt/hongshu-caicai .venv/bin/python -m py_compile web_app.py xhs_utils/ai_utils.py

# API 冒烟测试
curl -s -X POST http://127.0.0.1:18080/api/login -d 'username=admin&password=admin'
curl -s -X POST http://127.0.0.1:18080/api/start \
  -d 'mode=keyword&keyword=榴莲&count=1'
curl -s 'http://127.0.0.1:18080/api/status?id=<job_id>'
```

## 安全注意事项

- `.env` 包含小红书 Cookie 和 AI API Key，**不要提交到 Git**
- 对外监听时 app 有登录保护，但仍需注意 Cookie 泄露风险
- 不要记录完整的 Cookie 值或 SMS 登录会话 cookie；仅记录 masked summary 或 cookie 键名
- 评论采集请求量大于笔记搜索，建议先用 1-10 篇测试
- AI API Key 可被已登录用户查看/修改
