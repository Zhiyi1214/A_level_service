# AI Assistant - Powered by Dify

一个产品级的AI助手应用，集成Dify API，提供现代化的对话界面。

## 功能特性

✨ **智能对话** - 与AI进行自然流畅的多轮对话
📸 **图片支持** - 上传并分析图片内容
💬 **对话管理** - 保存和管理多个对话历史
🧠 **多知识库切换** - 新会话前可选知识库，会话开始后自动锁定
🎨 **现代UI** - 响应式设计，美观易用的界面
🔐 **安全可靠** - 完整的身份验证和数据处理

## 系统架构

```
┌─────────────────────────────────────────┐
│         前端 (HTML/CSS/JavaScript)       │
│    - 现代化的聊天界面                    │
│    - 图片上传和预览                      │
│    - 实时消息展示                        │
└────────────┬────────────────────────────┘
             │ HTTP/WebSocket
┌────────────▼────────────────────────────┐
│      后端网关 (Flask Python)            │
│    - 请求转发和聚合                      │
│    - 文件处理和验证                      │
│    - 对话状态管理                        │
│    - CORS和认证                          │
└────────────┬────────────────────────────┘
             │ HTTP + Bearer Token
┌────────────▼────────────────────────────┐
│        Dify API (http://localhost/v1)   │
│    - AI模型处理                          │
│    - 复杂推理能力                        │
│    - 多模态支持                          │
└─────────────────────────────────────────┘
```

## 快速开始

### 前置要求

- Python 3.8+
- Pip 或 Poetry
- 运行中的Dify服务 (http://localhost/v1)
- Dify API Key

### 安装步骤

1. **克隆或进入项目目录**
```bash
cd /Users/ray_zhang/Developer/A_level
```

2. **创建虚拟环境（推荐）**
```bash
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
# 或
venv\Scripts\activate  # Windows
```

3. **安装依赖**
```bash
pip install -r requirements.txt
```

4. **配置环境变量**
```bash
cp .env.example .env
```

编辑 `.env` 文件，填入你的配置：
```bash
DIFY_API_URL=http://localhost/v1
DIFY_API_KEY=your_actual_api_key_here
SOURCES_CONFIG_PATH=./config/sources.json
SRC_KB_A_API_KEY=your_kb_a_api_key_here
SRC_KB_B_API_KEY=your_kb_b_api_key_here
FLASK_ENV=development
HOST=0.0.0.0
PORT=5000
```

`config/sources.json` 控制前端可选知识库数量和内容。新增/删除 source 后，前端会自动展示对应选项。

5. **启动应用**
```bash
python app.py
```

应用将在 `http://localhost:5000` 运行

## API 端点

### 主要端点

| 方法 | 端点 | 描述 |
|------|------|------|
| POST | `/api/chat` | 发送消息，支持文件上传 |
| GET | `/api/conversations` | 获取对话列表 |
| GET | `/api/conversations/<id>` | 获取单个对话详情 |
| DELETE | `/api/conversations/<id>` | 删除对话 |
| GET | `/api/health` | 健康检查 |

### 发送消息

**请求示例：**
```bash
curl -X POST http://localhost:5000/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "你好，请告诉我今天的天气",
    "conversation_id": "conv_123",
    "user_id": "user_123"
  }'
```

**带图片的请求：**
```bash
curl -X POST http://localhost:5000/api/chat \
  -F "message=这个图片里有什么?" \
  -F "files=@/path/to/image.jpg" \
  -F "conversation_id=conv_123" \
  -F "user_id=user_123"
```

**响应示例：**
```json
{
  "success": true,
  "conversation_id": "conv_123",
  "response": "你好！我是一个AI助手...",
  "message_id": "msg_456",
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 20
  }
}
```

## 项目结构

```
A_level/
├── app.py                 # 主应用文件
├── requirements.txt       # Python 依赖
├── .env.example           # 环境变量示例
├── README.md              # 本文件（项目主说明）
├── docs/                  # 文档目录
│   ├── README.md          # 文档索引
│   ├── QUICKSTART.md      # 快速入门
│   ├── API.md             # REST API 说明
│   └── DOCKER.md          # Docker 部署说明
├── scripts/               # Shell 脚本（见 scripts/README.md）
├── templates/
│   └── index.html         # 前端 HTML
├── static/
│   ├── style.css          # 前端样式
│   └── script.js          # 前端脚本
├── docker-compose.yml     # 容器编排（可选）
├── Dockerfile
├── nginx.conf             # 可选 Nginx 配置
└── uploads/               # 上传目录（自动创建，已 gitignore）
```

更多说明见 [docs/QUICKSTART.md](docs/QUICKSTART.md)、[docs/API.md](docs/API.md)、[docs/DOCKER.md](docs/DOCKER.md)（[文档索引](docs/README.md)）。

## 功能详解

### 1. 消息处理
- 支持纯文本消息
- 支持带有多张图片的消息
- 自动处理文件上传和存储
- 消息格式化和展示

### 2. 对话管理
- 自动保存对话历史
- 支持多个并发对话
- 对话搜索和查看
- 对话删除和清理

### 3. 安全性
- Bearer Token认证
- CORS跨域支持
- 文件类型和大小限制
- HTML转义防止XSS

### 4. 用户体验
- 响应式设计（桌面/平板/手机）
- 实时消息更新
- 加载状态指示
- 错误提示和处理

## 配置说明

### Dify API 配置

**获取API Key：**
1. 登录你的Dify后台
2. 进入应用设置
3. 找到API Keys部分
4. 复制你的API Key

**API端点配置：**
- 默认：`http://localhost/v1`
- 可根据实际Dify服务地址修改

### 文件上传配置

```python
MAX_CONTENT_LENGTH = 52428800  # 50MB
ALLOWED_EXTENSIONS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'pdf', 'txt', 'doc', 'docx']
```

## 前端功能说明

### 界面组成

1. **左侧边栏**
   - 新建聊天按钮
   - 最近对话列表
   - 对话删除选项

2. **顶部标题栏**
   - 对话标题
   - 对话信息
   - 菜单按钮

3. **消息区域**
   - 欢迎屏幕（新对话）
   - 消息展示
   - 自动滚动到最新消息

4. **输入区域**
   - 文本输入框
   - 文件上传按钮
   - 已上传文件列表
   - 发送按钮

### 快捷键

- **Enter** - 发送消息
- **Shift + Enter** - 换行
- 点击快速开始按钮 - 快速提问

## 部署指南

### Docker 部署

项目已包含 `Dockerfile` 与 `docker-compose.yml`。详见 [docs/DOCKER.md](docs/DOCKER.md)。

```bash
docker compose up -d
```

默认后端端口为 **8000**（容器内 `PORT=8000`）；经 Compose 中的 Nginx 访问一般为 **8080**。

### 生产环境配置

使用 Gunicorn：
```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

使用 Nginx 反向代理：
```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

## 故障排除

### 问题1：无法连接到Dify API
```
解决方案：
1. 检查DIFY_API_URL配置
2. 确保Dify服务正在运行
3. 验证API Key是否正确
4. 检查网络连接
```

### 问题2：文件上传失败
```
解决方案：
1. 检查文件大小（最大50MB）
2. 验证文件格式是否支持
3. 确保uploads目录存在且可写
4. 检查磁盘空间
```

### 问题3：消息发送慢
```
解决方案：
1. 检查网络连接
2. 增加API超时时间
3. 检查Dify API性能
4. 考虑使用流式响应
```

## 高级配置

### 自定义样式

编辑 `static/style.css` 中的CSS变量：
```css
:root {
    --primary-color: #10a37f;
    --secondary-color: #0d47a1;
    --accent-color: #ff6b6b;
    /* ... 更多配置 */
}
```

### 添加新功能

1. **后端**：在 `app.py` 中添加新的路由和逻辑
2. **前端**：在 `static/script.js` 中添加新的JavaScript函数
3. **样式**：在 `static/style.css` 中添加新的样式

## 常见问题

**Q: 如何更改默认端口？**
A: 在 `.env` 文件中修改 `PORT` 值

**Q: 如何支持更多文件类型？**
A: 修改 `.env` 中的 `ALLOWED_EXTENSIONS`

**Q: 如何实现用户认证？**
A: 在 `app.py` 中添加认证中间件

**Q: 对话历史是否持久化？**
A: 当前使用内存存储，可集成数据库（如SQLite、PostgreSQL）

## 安全建议

1. ✅ 定期更新依赖包
2. ✅ 不要在代码中硬编码API Key
3. ✅ 使用HTTPS进行生产环境部署
4. ✅ 实现速率限制防止滥用
5. ✅ 定期备份对话数据
6. ✅ 监控API使用情况

## 许可证

本项目采用 MIT 许可证

## 支持和联系

如有问题或建议，请提交Issue或联系开发者。

---

**创建时间**: 2026年3月20日
**版本**: 1.0.0
**维护者**: Zhiyi Zhang

