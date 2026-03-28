# 快速入门指南

## ⚡ 5分钟快速开始

### 第一步：准备工作

1. **克隆/进入项目**
   ```bash
   cd /path/to/A_level
   ```

2. **有Dify API Key吗？**
   - 如果有：继续下一步
   - 如果没有：[获取Dify API Key](#获取dify-api-key)

### 第二步：配置环境

1. **创建.env文件**
   ```bash
   cp .env.example .env
   ```

2. **编辑.env文件**
   ```bash
   # 用你的编辑器打开 .env
   # 将以下内容改为你的实际值：
   
   DIFY_API_URL=http://localhost/v1
   DIFY_API_KEY=sk_live_xxxxxxxxxxxxxx  # 替换为你的API Key
   ```

### 第三步：启动应用

**macOS/Linux:**
```bash
chmod +x scripts/*.sh
./scripts/start.sh
```

**Windows:**
```bash
start.bat
```

**或者直接用Python:**
```bash
pip install -r requirements.txt
python app.py
```

### 第四步：访问应用

打开浏览器访问：
```
http://localhost:5000
```

🎉 完成！现在你可以开始聊天了！

---

## 获取Dify API Key

### 步骤1：登录Dify

访问你的Dify部署（例如: http://localhost:8000）

### 步骤2：进入应用设置

1. 选择一个已发布的应用
2. 进入 "设置" → "API"

### 步骤3：获取API Key

1. 找到 "API Key" 部分
2. 点击 "复制" 或 "新建API Key"
3. 将其粘贴到 `.env` 文件中

### 步骤4：获取API Endpoint

API Endpoint 通常是：
```
http://localhost/v1
```

如果你的Dify在不同的地址，需要调整 `DIFY_API_URL`

---

## 功能速览

### 💬 基础聊天
```
1. 点击 "New Chat" 开始新对话
2. 在输入框输入消息
3. 连续按两次 Enter 发送（Shift+Enter 换行）；也可点击发送按钮
```

### 📸 上传图片
```
1. 点击左下角的 "📎" 按钮
2. 选择图片文件
3. 输入描述问题
4. 发送（同上）
```

### 💾 管理对话
```
- 所有对话自动保存在左侧
- 点击对话名称切换
- 鼠标悬停显示删除按钮
```

---

## 常见问题

### ❌ 无法连接到Dify API
```
解决方案：
1. 检查 .env 中的 DIFY_API_URL 是否正确
2. 确保 Dify 服务正在运行
3. 尝试用浏览器访问 API URL，看是否可达
4. 检查防火墙设置
```

### ❌ API Key无效
```
解决方案：
1. 重新从Dify后台复制API Key
2. 确保没有多余的空格
3. 确保是正确的应用的API Key
4. 尝试重新生成API Key
```

### ❌ 图片上传失败
```
解决方案：
1. 检查图片大小（最大50MB）
2. 确保是支持的格式（jpg, png, gif, webp）
3. 检查磁盘空间
4. 查看浏览器控制台的错误信息
```

### ❌ 页面加载很慢
```
解决方案：
1. 清除浏览器缓存
2. 检查网络连接
3. 尝试用无痕窗口打开
4. 查看服务器日志是否有错误
```

---

## 快速测试

### 测试1：验证后端正常

```bash
curl http://localhost:5000/api/health
```

预期响应：
```json
{
  "status": "healthy",
  "dify_api_configured": true
}
```

### 测试2：发送简单消息

```bash
curl -X POST http://localhost:5000/api/chat \
  -F "message=Hello"
```

### 测试3：查看前端

打开浏览器：
```
http://localhost:5000
```

应该看到欢迎屏幕

---

## 生产环境部署

### 使用Docker

```bash
# 构建镜像
docker build -t ai-assistant .

# 运行容器
docker run -p 5000:5000 \
  -e DIFY_API_URL=http://dify:8000/v1 \
  -e DIFY_API_KEY=your_key \
  ai-assistant
```

### 使用Docker Compose

```bash
# 编辑 .env 文件填入API Key

# 启动
docker compose up -d

# 查看日志
docker compose logs -f ai-assistant

# 停止
docker compose down
```

### 使用Gunicorn + Nginx

```bash
# 安装Gunicorn
pip install gunicorn

# 启动应用
gunicorn -w 4 -b 0.0.0.0:5000 app:app

# Nginx配置见 nginx.conf
```

---

## 项目结构说明

```
A_level/
├── app.py                    # 后端主程序
├── requirements.txt          # 依赖列表
├── .env                      # 环境配置（需要自己创建）
├── .env.example              # 配置模板
│
├── templates/
│   └── index.html            # 前端HTML
│
├── static/
│   ├── style.css             # 前端样式
│   └── script.js             # 前端脚本
│
├── uploads/                  # 上传文件目录（自动创建）
│
├── scripts/                  # Shell 工具脚本（Docker / 本机启动等）
│   ├── README.md
│   ├── start.sh
│   ├── setup.sh
│   ├── docker-start.sh
│   └── test-api.sh
├── start.bat                 # Windows启动脚本
│
├── Dockerfile
├── docker-compose.yml
├── nginx.conf
│
├── README.md                 # 项目主说明（仓库根目录）
└── docs/                     # 文档（本目录）
    ├── README.md             # 文档索引
    ├── QUICKSTART.md         # 本文件
    ├── API.md
    └── DOCKER.md
```

---

## 下一步

- 📖 阅读 [README.md](../README.md) 了解更多功能
- 🔌 查看 [API.md](API.md) 学习 API 集成
- 🐳 查看 [DOCKER.md](DOCKER.md) 了解 Docker 部署
- ⚙️ 根据需求自定义样式和功能

---

## 获取帮助

### 查看日志

**后端日志：**
```bash
# 在终端中查看实时日志
python app.py
```

**浏览器控制台：**
```bash
# 按 F12 打开开发者工具
# 查看 Console 标签页的错误信息
```

### 常用命令

```bash
# 检查Python版本
python --version

# 检查虚拟环境
source venv/bin/activate  # 激活
deactivate                # 停用

# 检查依赖
pip list
pip show Flask

# 重新安装依赖
pip install --upgrade -r requirements.txt

# 清理缓存
rm -rf __pycache__
rm -rf .pytest_cache
```

---

## 反馈和改进

有任何问题或建议？

1. 📝 检查本文档是否有帮助
2. 🔍 查看 [API.md](API.md) 或 [README.md](../README.md)
3. 💬 查看浏览器控制台或终端日志

---

**祝你使用愉快！🚀**
