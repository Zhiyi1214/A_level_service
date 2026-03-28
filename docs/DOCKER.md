# Docker 部署说明

## 前置条件

- 已安装 [Docker](https://www.docker.com/products/docker-desktop) 与 Docker Compose
- 已准备 Dify 的 API Base URL 与 API Key（勿将真实 Key 写入仓库文档，仅放在本地 `.env`）

## 配置 `.env`

```bash
cp .env.example .env
```

在 `.env` 中设置（示例值请按你的环境修改）：

```bash
DIFY_API_URL=http://host.docker.internal/v1
DIFY_API_KEY=your_dify_api_key_here
FLASK_ENV=production
```

**说明**：在 Docker Desktop（macOS/Windows）上，本机宿主机上的 Dify 通常用 `host.docker.internal` 访问；Linux 上可能需改为宿主机 IP 或把 Dify 与助手加入同一 Docker 网络。

## 启动

```bash
docker compose up -d
# 或: docker-compose up -d
```

常用入口：

| 说明 | 地址 |
|------|------|
| 经 Nginx | http://localhost:8080 |
| 直连后端 | http://localhost:8000 |
| 健康检查 | http://localhost:8000/api/health |

## 常用命令

```bash
docker compose logs -f ai-assistant
docker compose ps
docker compose down
```

也可使用 `scripts/docker-start.sh`（在项目根目录执行 `./scripts/docker-start.sh start` 等）。

## 与 Dify 的网络

- **Dify 在宿主机、助手在容器**：`DIFY_API_URL` 常设为 `http://host.docker.internal:<端口>/v1`（端口与 Dify 对外暴露一致）。
- **同一 Compose 网络**：可将 Dify 与 `ai-assistant` 置于同一 `networks`，并用 Dify 服务名作为主机名。

## 相关文档

- [文档索引](README.md)
- [快速入门](QUICKSTART.md)
- [API 说明](API.md)
