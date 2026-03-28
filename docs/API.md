# API 文档

## 基础信息

- **基础 URL**：本地默认 `http://localhost:5000`；Docker Compose 直连后端为 `http://localhost:8000`
- **认证**：调用 Dify 时使用 Bearer Token（由服务端读取 `.env` 中的 `DIFY_API_KEY`）
- **响应格式**：JSON

## 端点列表

### 1. 发送消息

**请求**

```http
POST /api/chat
Content-Type: multipart/form-data
```

**参数**

- `message` (string, 必需): 用户消息内容
- `conversation_id` (string, 可选): 对话 ID；为空则新建；建议沿用上次响应中的 `conversation_id`（与 Dify 侧会话一致）
- `user_id` (string, 可选): 用户 ID，默认为 `default_user`
- `files` (file[], 可选): 上传的文件/图片

也支持 `Content-Type: application/json`，字段名相同（不含文件时使用）。

**cURL 示例**

```bash
curl -X POST http://localhost:5000/api/chat \
  -F "message=你好" \
  -F "conversation_id=" \
  -F "user_id=user_123"

curl -X POST http://localhost:5000/api/chat \
  -F "message=这个图片里有什么?" \
  -F "files=@/path/to/image.jpg"
```

**成功响应 (200)**

```json
{
  "success": true,
  "conversation_id": "<由 Dify 返回或本地生成的会话 ID>",
  "response": "……",
  "message_id": "msg_xxx",
  "usage": {}
}
```

**错误响应 (400)**

```json
{
  "error": "Message cannot be empty"
}
```

---

### 2. 获取对话列表

`GET /api/conversations?user_id=user_123`

### 3. 获取单个对话详情

`GET /api/conversations/{conversation_id}`

### 4. 删除对话

`DELETE /api/conversations/{conversation_id}`

### 5. 健康检查

`GET /api/health`

---

## 错误处理

| 状态码 | 描述           |
|--------|----------------|
| 200    | 成功           |
| 400    | 参数错误       |
| 404    | 资源不存在     |
| 413    | 文件过大       |
| 500    | 服务器内部错误 |

---

## 限制

| 项目       | 限制                                                |
|------------|-----------------------------------------------------|
| 单文件大小 | 默认 50MB（可由环境变量 `MAX_CONTENT_LENGTH` 调整） |
| 请求超时   | 调用 Dify 默认约 60 秒（见 `app.py` 中 `requests` 超时） |
| 文件类型   | 由 `ALLOWED_EXTENSIONS` 配置，默认含常见图片与文档  |

---

## 相关文档

- [文档索引](README.md)
- [快速入门](QUICKSTART.md)
- [Docker 部署](DOCKER.md)
