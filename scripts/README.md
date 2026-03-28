# Shell 脚本

均在**项目根目录**（`A_level/`，与 `docker-compose.yml` 同级）下执行。脚本开头会自动 `cd` 到该根目录。

项目说明文档在 [`docs/`](../docs/README.md)。

| 脚本 | 说明 |
|------|------|
| `setup.sh` | 本机：创建 venv、安装依赖、复制 `.env` 模板 |
| `start.sh` | 本机：激活 venv 并运行 `python app.py` |
| `docker-start.sh` | Docker：`start` / `stop` / `logs` 等（见 `./scripts/docker-start.sh help`） |
| `test-api.sh` | 用 curl 测本地 `8000` 端口 API（需服务已启动） |

首次使用可赋予执行权限：

```bash
chmod +x scripts/*.sh
```
