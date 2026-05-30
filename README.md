# 红薯采采

红薯采采是本地部署的小红书采集台，提供浏览器 UI、Cookie 管理、关键词笔记采集、评论同步采集、实时进度跟踪和 Excel 下载。项目定位为个人娱乐、学习交流和技术验证，不面向生产或商业场景。

> **免责声明：本项目仅供个人娱乐和学习交流使用。禁止用于商业化运营、批量采集、绕过平台风控、侵犯用户隐私、抓取敏感信息或任何违反目标平台规则与法律法规的行为。使用者需自行承担由使用本项目产生的全部责任。**

## 核心功能

| 模块 | 功能 |
|---|---|
| 登录保护 | 默认 `admin/admin` 登录采集台，可通过环境变量修改账号密码 |
| Cookie 管理 | 页面内查看 Cookie 状态、手动粘贴更新、清除当前 Cookie |
| 关键词采集 | 输入关键词和目标篇数，采集小红书搜索结果笔记 |
| 评论采集 | 可选同步采集一级评论和二级评论 |
| 实时进度 | 展示任务状态、阶段进度、时间线事件和采集指标 |
| Excel 导出 | 笔记与评论分别生成 `.xlsx` 文件，页面内直接下载 |
| 安全下载 | 下载接口只允许读取 `datas/excel_datas/` 下的 Excel 文件，阻止路径穿越 |

## 采集台界面

本地 Web 采集台已经做成小红书风格的单页任务台，主要区域如下：

- **账号区**：显示当前 Cookie 是否可用、关键字段脱敏摘要，支持手动保存或清除 Cookie。
- **采集指令区**：填写关键词、设置采集篇数，勾选是否同步采集评论。
- **实时档案区**：显示排队、采集中、完成或异常状态，展示进度条、笔记数、评论数和下载按钮。
- **阶段流程区**：按准备任务、搜索笔记、解析详情、采集评论、生成文件展示任务进度。
- **事件时间线**：记录每个任务阶段的关键事件、异常提示和完成归档信息。

访问地址：

```text
http://<server-ip>:18080
```

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 20+

### 安装依赖

```bash
pip install -r requirements.txt
npm install
```

### 配置 Cookie

在项目根目录 `.env` 中配置登录后的小红书 Web Cookie：

```env
COOKIES='your_cookie_here'
```

也可以启动采集台后，在页面的「手动替换 Cookie」输入框中粘贴并保存。

Cookie 获取方式：浏览器登录小红书网页版后，打开开发者工具，进入 Network / Fetch/XHR，选择任意接口请求，复制请求头里的 `cookie` 字段。

### 启动 Web 采集台

```bash
cd /opt/hongshu-caicai
HOST=0.0.0.0 PYTHONPATH=/opt/hongshu-caicai .venv/bin/python web_app.py
```

默认配置：

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `HOST` | `127.0.0.1` | 监听地址；外部访问用 `0.0.0.0` |
| `PORT` | `18080` | Web 采集台端口 |
| `APP_USERNAME` | `admin` | 登录用户名 |
| `APP_PASSWORD` | `admin` | 登录密码 |
| `APP_SESSION_SECRET` | 随机生成 | 会话签名密钥，生产环境建议固定配置 |
| `COOKIES` | `.env` 读取 | 小红书登录 Cookie |

## 使用流程

1. 打开 `http://<server-ip>:18080`。
2. 使用默认账号登录，或使用自定义 `APP_USERNAME` / `APP_PASSWORD`。
3. 在 Cookie 状态区确认 Cookie 可用；失效时粘贴新 Cookie 并保存。
4. 输入关键词，例如 `苏州探店`。
5. 设置采集篇数，当前单次限制 1-50 篇。
6. 如需评论，勾选「同步采集评论」。首次建议 1-10 篇小批量测试。
7. 点击「开始采集」，等待实时进度完成。
8. 下载生成的笔记 Excel 和评论 Excel。

## 输出文件

| 数据 | 文件路径 |
|---|---|
| 笔记结果 | `datas/excel_datas/<关键词>.xlsx` |
| 评论结果 | `datas/excel_datas/<关键词>_comments.xlsx` |

评论采集依赖笔记 URL 中的 `xsec_token` 和 `xsec_source`。如果评论结果为 0，可能原因包括：笔记本身无可见评论、Cookie 过期、接口风控、上下文 token 失效。

## API 接口

### 启动任务

```bash
curl -s -X POST http://127.0.0.1:18080/api/start \
  -d 'keyword=榴莲&count=1&with_comments=on'
```

返回：

```json
{"id":"<job_id>"}
```

### 查询状态

```bash
curl -s 'http://127.0.0.1:18080/api/status?id=<job_id>'
```

常用字段：

| 字段 | 说明 |
|---|---|
| `state` | `queued`、`running`、`success`、`failed` |
| `stage` | `init`、`search`、`notes`、`comments`、`complete`、`failed` |
| `percent` | 页面进度百分比 |
| `message` | 当前状态文案 |
| `events` | 任务事件时间线 |
| `metrics.notes` | 已导出笔记数 |
| `metrics.comments` | 已导出评论数 |
| `downloads` | 可下载 Excel 文件列表 |

### 下载文件

```bash
curl -OJ 'http://127.0.0.1:18080/download?file=榴莲_comments.xlsx'
```

## 项目结构

```text
/opt/hongshu-caicai/
├── web_app.py                 # 本地 Web 采集台入口
├── spider/
│   └── spider.py              # 采集任务封装
├── apis/
│   ├── xhs_pc_apis.py         # 小红书 PC 端采集接口
│   ├── xhs_creator_apis.py    # 创作者平台接口
│   ├── xhs_pugongying_apis.py # 蒲公英接口
│   └── xhs_qianfan_apis.py    # 千帆接口
├── xhs_utils/
│   ├── common_util.py         # 配置读取
│   ├── cookie_util.py         # Cookie 解析
│   ├── data_util.py           # Excel 保存、媒体处理、评论导出
│   └── xhs_util.py            # PC 端签名封装
├── static/                    # 本地签名脚本
├── datas/excel_datas/         # Excel 输出目录
├── docs/local-web-console.md  # 本地采集台补充说明
├── requirements.txt
├── package.json
└── Dockerfile
```

## 验证命令

```bash
cd /opt/hongshu-caicai
PYTHONPATH=/opt/hongshu-caicai .venv/bin/python -m py_compile web_app.py spider/spider.py xhs_utils/data_util.py apis/xhs_pc_apis.py
```

## 安全注意

- 采集台持有登录 Cookie，建议只开放给可信网络访问。
- 默认登录账号密码请在长期部署时通过环境变量修改。
- `.env` 不要提交到代码仓库。
- 评论采集请求量更大，建议先小批量验证。
- Cookie 失效后需要重新从浏览器复制或在页面更新。
