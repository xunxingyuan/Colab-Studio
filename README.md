# Colab Studio — Google Colab GPU 视频生成工作台控制台

> **本地轻量控制面 + 云端 Colab GPU 算力**：专为 AI 视频生成（ComfyUI / Wan2.1 / MiniMax-H3）打造的跨平台自研控制台，内置防烧钱看门狗、网页实例一键接管、SSH 断线自动重连、批量工作流队列与电影级中文字幕硬烧录。

---

## 核心特性

- **一等公民「网页实例接管 + 补挂看门狗」**
  - 当 Colab `/tun/m/assign` 出现临时 503 或排队拥堵时，可在 Colab 网页端开卡后在控制台一键接管，自动注入云端防忘关机看门狗并直接部署 ComfyUI，彻底避免孤儿实例空转扣费。
- **双层防烧钱护栏与零残留回执**
  - **云端独立看门狗（`backend/vm_assets/watchdog.py`）**：零第三方依赖，运行在 Colab VM 独立进程组，同时监测任务心跳与 `nvidia-smi` GPU 利用率；即使本机断网或合盖，超时/闲置仍自动调用 `$TBE_RUNTIME_ADDR/unassign` 停止计费。
  - **一键全关审计回执**：释放实例后轮询核验 `assignments` 清空并持久化至 SQLite。
- **原生 OpenSSH over WebSocket 端口映射**
  - 自动生成隔离 Ed25519 密钥，将云端 `8188` 等服务映射回 `127.0.0.1`。
  - 内置 **每 5 秒断线自动重连** 与 **`HTTP 429 already-active-session` 自动清理残留 `sshd` 重试**。
- **批量 ComfyUI 视频工作流队列**
  - 支持内置 **Wan2.1-T2V-1.3B** 模板（按行输入 `画面提示词 | 中文字幕台词`）及 **自定义 ComfyUI API JSON**（自动替换 `{{PROMPT}}`、`{{SEED}}`、`{{PREFIX}}` 占位符）。
  - 支持 **自动拼接完整成片**、**自动压制中文字幕 + `.srt`** 以及 **全部出片后自动关机释放 GPU**。
- **通用中文字幕硬烧录引擎（Pillow + FFmpeg `overlay`）**
  - 自动探测系统 CJK 字体（macOS `PingFang SC` / `STHeiti` / Linux `Noto Sans CJK` / Windows `Microsoft YaHei`），渲染圆角半透明黑底白字透明 PNG 并通过 FFmpeg `overlay` 合成，不依赖 `libass` / `libfreetype`。
- **多账号 OS Keyring 凭据仓 & 零计费沙盒模式**
  - OAuth 令牌与 Hugging Face Token 全部加密存入系统钥匙串（macOS Keychain / Windows 凭据管理器），绝不落明文文件；沙盒模式（`--mock`）使用纯内存虚拟账号，绝不污染真实账号列表。

---

## 技术栈与项目结构

- **后端**：Python `>= 3.12` · `uv` · FastAPI · Uvicorn · Pydantic v2 · SQLite (WAL) · `keyring` · `httpx[socks]` · `websocket-client` · Pillow · FFmpeg
- **前端**：React 19 · TypeScript · Tailwind CSS v4 · Vite · Lucide Icons

```text
colab-studio/
├── backend/
│   ├── main.py                  # 服务启动入口与 CLI 子命令
│   ├── config.py                # 路径隔离与 HTTP/SOCKS5 代理穿透配置
│   ├── db.py                    # SQLite (WAL) 状态、任务、成品与回执仓储
│   ├── models.py                # Pydantic 领域模型
│   ├── vault.py                 # OS Keyring 多账号凭据仓与日志脱敏器
│   ├── backends/
│   │   ├── base.py              # ColabBackend 统一抽象协议
│   │   ├── direct.py            # Google Colab HTTPS + Jupyter Kernel WS v5.3 驱动
│   │   └── mock.py              # 0 计费沙盒引擎
│   ├── ssh_bridge.py            # 隔离密钥 + OpenSSH -L + 断线重连 + 429 自愈
│   ├── recipe_loader.py         # TOML 配方严格校验器（禁 8080、防路径穿越）
│   ├── orchestrator.py          # 核心编排：开卡重抽、孤儿接管、部署、批量队列、收割
│   ├── video_composer.py        # FFmpeg 多镜拼接 + Pillow 中文字幕硬烧录 + SRT
│   ├── api/routes.py            # FastAPI REST + WebSocket (/ws/events) + SPA 托管
│   └── vm_assets/watchdog.py    # 运行在 Colab VM 内的无依赖自毁看门狗
├── recipes/
│   ├── comfyui-video/           # ComfyUI + Manager + Wan2.1-T2V-1.3B 预装配方
│   └── minimax-h3/              # G4 / A100 高显存视频人物替换配方
├── workflows/
│   └── wan21_livestream_9x16.json  # 示例：9:16 竖屏 Wan2.1 ComfyUI API 工作流
├── frontend/                    # React + TypeScript + Tailwind 5 页 Web 控制台
└── tests/
    └── test_studio.py           # 端到端自动化测试套件（0 计费）
```

---

## 快速开始

### 环境要求
- Python `>= 3.12` 与 [`uv`](https://docs.astral.sh/uv/)
- Node.js `>= 20`（用于构建前端）
- 系统自带 OpenSSH（`ssh` / `ssh-keygen`）与 `ffmpeg` / `ffprobe`

### 1. 安装依赖并构建前端
```bash
# 安装后端 Python 依赖
uv sync

# 安装并构建 React 前端（产物位于 frontend/dist，由 FastAPI 自动托管）
cd frontend && npm install && npm run build && cd ..
```

### 2. 启动控制台
```bash
# 方式 A：零计费沙盒模式（体验全流程不消耗 CCU）
uv run colab-studio --mock --port 8770

# 方式 B：真实 Google Colab 生产模式
uv run colab-studio --real --port 8770
```
启动后在浏览器访问：**`http://127.0.0.1:8770`**

1. 进入 **「账号与护栏」** 绑定你的 Google 账号（可同时录入 Hugging Face Token）。
2. 进入 **「极速部署」** 选择 `ComfyUI 视频生成工作台 (Wan2.1 / LTX)` 开卡，或在 **「算力总览」** 一键接管网页端已开的 GPU。
3. 进入 **「批量队列与字幕」** 输入多镜分镜脚本或粘贴自定义 ComfyUI API JSON，即可全自动出片、拼接、压制中文字幕并落盘至 `outputs/`。

---

## CLI 快捷命令

控制台运行后，也支持直接通过命令行调度（内置 `trust_env=False` 绕过本地代理冲突）：

```bash
uv run colab-studio status
uv run colab-studio adopt <orphan-endpoint>
uv run colab-studio deploy comfyui-video --accel L4
uv run colab-studio harvest <session-name> --auto-stop
uv run colab-studio stop <session-name>
uv run colab-studio stop-all
```

---

## 自动化测试

所有测试均在本地临时目录与 Mock 引擎下运行，不触碰真实 Google 账号且 0 计费：

```bash
uv run pytest -v
```
