# A-Level Chemistry AI Assistant

基于 Dify 的 A-Level 化学 AI 助手，支持 AQA / CIE / Edexcel 多考试局知识库切换、图片上传与多轮对话。

## 功能

- **多知识库** — 新对话前选择考试局，会话开始后自动锁定
- **图片理解** — 上传化学结构、题目截图等，AI 同步解读
- **对话持久化** — SQLite 存储，重启不丢失
- **Markdown + LaTeX** — 助手回复支持公式渲染（KaTeX）
- **深色 / 浅色主题** — 一键切换
- **响应式布局** — 桌面 / 平板 / 手机自适应

## 项目结构

```
A_level/
├── app.py                  # Flask 入口：初始化、蓝图注册、错误处理
├── extensions.py           # Limiter / CORS / ProxyFix
├── config/
│   ├── settings.py         # 集中管理所有环境变量
│   └── sources.json        # 知识库定义（id / api_url / auth_ref）
├── routes/
│   ├── chat.py             # POST /api/sessions, /api/chat
│   ├── conversations.py    # GET/DELETE /api/conversations
│   └── sources.py          # GET /api/sources
├── services/
│   ├── chat_service.py     # Dify API 调用 + 响应解析
│   ├── image_service.py    # 图片压缩、去重、上传
│   └── source_service.py   # 知识库注册表 + 热重载
├── storage/
│   ├── base.py             # ConversationStore Protocol
│   └── sqlite.py           # SQLite 实现（WAL 模式、线程安全）
├── data/                   # SQLite 数据库（自动创建，已 gitignore）
├── static/
│   ├── script.js
│   ├── style.css
│   └── vendor/             # KaTeX / marked / DOMPurify
├── templates/
│   └── index.html
├── scripts/                # Shell 工具脚本
├── Dockerfile
├── docker-compose.yml
├── nginx.conf
└── requirements.txt
```

## 快速开始

### 1. 安装依赖

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境

```bash
cp .env.example .env
```

编辑 `.env`，填入你的 Dify API Key：

```bash
DIFY_API_URL=http://localhost/v1

# 每个知识库对应一个 key，变量名须与 sources.json 中 auth_ref 一致
DIFY_API_KEY_AQA=app-xxxxxxxxxxxx
DIFY_API_KEY_CIE=app-xxxxxxxxxxxx
DIFY_API_KEY_EDX=app-xxxxxxxxxxxx
```

知识库列表由 `config/sources.json` 控制。增删 source 后在 `.env` 中添加对应的 API key 即可，前端自动展示。

### 3. 启动

```bash
python3 app.py
```

打开 http://localhost:5000

## Docker 部署

```bash
cp .env.example .env
# 编辑 .env，将 DIFY_API_URL 改为 http://host.docker.internal/v1

docker compose up -d
```

| 入口 | 地址 |
|------|------|
| Nginx 代理 | http://localhost:8080 |
| 直连后端 | http://localhost:8000 |
| 健康检查 | http://localhost:8000/api/health |

`data/` 目录通过 volume 挂载，数据库文件在容器重建后保留。

## API

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/sources` | 获取可用知识库列表 |
| POST | `/api/sessions` | 创建会话（锁定知识库） |
| POST | `/api/chat` | 发送消息（支持 multipart 图片上传） |
| GET | `/api/conversations` | 获取对话列表 |
| GET | `/api/conversations/<id>` | 获取对话详情 |
| DELETE | `/api/conversations/<id>` | 删除对话 |
| GET | `/api/health` | 健康检查 |

### 示例

```bash
# 创建会话
curl -X POST http://localhost:5000/api/sessions \
  -H "Content-Type: application/json" \
  -d '{"source_id": "AQA", "user_id": "user_1"}'

# 发送消息
curl -X POST http://localhost:5000/api/chat \
  -F "message=What is electronegativity?" \
  -F "conversation_id=<session_id>" \
  -F "user_id=user_1"

# 带图片
curl -X POST http://localhost:5000/api/chat \
  -F "message=Explain this reaction mechanism" \
  -F "files=@mechanism.jpg" \
  -F "conversation_id=<session_id>"
```

## 配置参考

所有配置通过环境变量管理，集中定义在 `config/settings.py`：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DIFY_API_URL` | `http://localhost/v1` | Dify API 地址 |
| `SOURCES_CONFIG_PATH` | `./config/sources.json` | 知识库配置路径 |
| `FLASK_ENV` | `production` | `development` 开启调试 |
| `PORT` | `5000` | 监听端口 |
| `MAX_CONTENT_LENGTH` | `52428800` | 上传大小限制（50 MB） |
| `MAX_MESSAGE_LENGTH` | `10000` | 单条消息字符上限 |
| `MAX_CONVERSATIONS_PER_USER` | `50` | 每用户最大会话数（超出自动淘汰最早的） |
| `LOG_LEVEL` | `INFO` | 日志级别 |

## 架构说明

```
Browser ──► Flask (routes/) ──► services/ ──► Dify API
                                   │
                                   ▼
                              storage/sqlite
                                   │
                                   ▼
                              data/*.db
```

- **routes/** 只做 HTTP 协议转换（参数校验、状态码）
- **services/** 包含全部业务逻辑（Dify 调用、图片压缩、知识库管理）
- **storage/** 数据持久化，通过 Protocol 定义接口，当前为 SQLite 实现，可替换为 PostgreSQL / Redis

`sources.json` 支持运行时热重载：宿主机修改文件后，下一个请求自动生效，无需重启。

## 许可证

MIT

---

**维护者**: Zhiyi Zhang
