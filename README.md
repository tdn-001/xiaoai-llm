# xiaoai-llm · 小爱音箱大模型网关

把小米 AI 音箱从「小爱原生回答」无缝切换到任意 **OpenAI 兼容大模型**（GPT-4o、DeepSeek、豆包、通义千问、Kimi、智谱、Ollama、自部署模型等），保留米家账号、设备控制与原有 TTS 链路。无需刷机、不修改音箱固件，依托小米云端既有接口实现。

> 仓库：https://github.com/tdn-001/xiaoai-llm
>
> 项目自部署门槛低，单台主机（X86 / ARM / macOS）跑一条 FastAPI 进程即可让家里所有小爱音箱接入。

---

## 项目简介

`xiaoai-llm` 是一个**非刷机云端方案**，针对小米 AI 音箱（包括小爱音箱 Pro、Play、Art、HD、S12A 等型号）做 LLM 替换。它通过监听米家云端对话记录接口，识别「AI助手 / 问AI / 你好小爱」等唤醒词开头的问题，把问句转发给大模型回答，再用米家 TTS / EdgeTTS 播报。

整套系统由 FastAPI + Pydantic + httpx 实现，没有数据库、没有重量级依赖。Web 管理台浅橙色主题，所有参数都能在浏览器里改、点保存即热加载。

主要解决三个问题：

1. **小爱原生回答太弱、不可定制**：用任何 LLM 替换，并能按设备配置不同人设、模型、记忆策略；
2. **没法在音箱上接入实时信息**：内置网络搜索（搜狗 / Bing），先把搜索资料交给大模型，再由大模型整理回答；
3. **没有针对音箱的运维面板**：内置 Web 管理台，配置修改、热加载、日志查看、设备测试都能在一个页面完成。

### 数据流

```
用户说话 → 小爱识别 → 米家云端对话记录接口出现新 ASR 问句
        → 命中唤醒词 → UBus mute 拦截原生回答
        → (可选 web_search + web_fetch) → LLM → TTS 播报
        → 未命中 → 不产生任何 UBus 调用，交给小爱原生处理
```

### 特点

- **轮询而非推送**：MiNA 云端不向第三方推送事件（WebSocket 仅音箱固件内部使用），本服务以轻量 HTTPS 对话接口轮询（默认 10s 空闲 / 2s 活跃，UBus 仅命中后调用）。
- **UBus 最小化**：空闲轮询走 HTTPS、不下发任何 UBus 指令；命中唤醒词后才 UBus mute / TTS。
- **型号兼容表**：内置 20 项小米音箱型号兼容表（自动识别 + 手动覆盖），含 S12A 等。
- **会话记忆**：按设备隔离、估算 Token 裁剪、会话超时自动清空。
- **网络搜索**：搜狗 / Bing 通用搜索 + 网页正文抓取（白名单公网 URL，禁用内网/本机），搜索结果作为不可信资料注入 Prompt。
- **多账号 / 多设备**：支持多个米家账号，每台音箱独立人设、模型、TTS、唤醒词。
- **配置零迁移**：所有配置集中在 `config.json`（自动生成、权限 0600），含完整示例 `config.example.json`。

---

## 功能

- **唤醒词过滤**：全局多唤醒词 + 每设备覆盖；开头匹配（默认）或任意位置匹配；未命中绝不触发 mute/LLM/TTS；ASR 空格 / 大小写自动归一。
- **多设备独立监听**：每台音箱（DID）独立轮询任务、独立会话记忆、独立人设 / 模型 / TTS 配置；单设备故障不影响其他设备。
- **多套 LLM 预设**：OpenAI / DeepSeek / 通义 / 智谱 / Kimi / Ollama 等任意兼容接口，每设备可选不同预设。
- **上下文记忆**：按 DID 隔离，会话超时自动清空，按估算 Token 裁剪历史，LLM 失败不写入历史。
- **最佳努力屏蔽原生回答**：命中后立即暂停音箱播放；受云端延迟影响可能短暂双音（详见"已知限制"）。
- **TTS**：米家原生 TTS（所有型号）或 EdgeTTS（局域网音频 URL，需音箱能访问服务地址）；失败可降级；支持思考语（"让我想想"）。
- **网络搜索**：搜狗 / Bing / DuckDuckGo 通用搜索 + 前 N 条网页正文抓取；结果按不可信资料注入 Prompt；失败自动降级普通回答。
- **Web 管理台**：访问密码保护；含 总览 / 米家账号 / 设备管理 / LLM 预设 / 人设 / TTS / 网络搜索 / 日志 / 应用配置 共 9 个页面。
- **日志**：内存环形 1000 条，敏感信息自动脱敏，支持级别过滤与增量拉取。
- **思考过程剥离**：内置 `<think>` / 元叙述剥离逻辑，避免豆包、DeepSeek 等 reasoning 模型把思考过程播报出来。
- **热加载**：Web 改完点"应用配置"立即生效（重建监听任务）；仅 Web 端口变更需重启容器。

---

## 快速开始

### 方式一：Python（本地或 venv）

```bash
git clone https://github.com/tdn-001/xiaoai-llm.git
cd xiaoai-llm
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py            # 配置文件默认 ./config.json
```

### 方式二：Docker Compose（推荐局域网部署）

```bash
docker compose up -d --build
```

打开 `http://<主机IP>:33003`：

1. 首次访问设置管理密码；同时输入启动日志中的 `bootstrap_token`（也可在首次生成的 `config.json` 中查看）。初始化成功后该令牌自动清除。
2. **米家账号** 选择登录方式：
   - 账号密码：填入小米账号（手机号 / 邮箱 / 小米 ID）和密码。
   - passToken：填入 userId（小米 ID，纯数字）和 passToken；令牌可从本地 `.mi.token` 文件（xiaogpt / MiService 等工具生成）或抓包中获取，适合服务器异地登录触发风控时使用。
3. **设备管理** 勾选音箱保存绑定（自动识别型号），按需覆盖人设 / 唤醒词 / TTS 等。
4. **LLM 设置** 填入 API Base URL / Key / 模型 ID 并测试连接。
5. 右上角 **应用配置** 热加载生效。

### 方式三：Makefile（国内 / 境外构建一体化）

仓库提供 `Makefile`，统一构建入口：

```bash
make build          # 构建 xiaoai-llm:latest（境外默认）
make build-cn       # 构建 xiaoai-llm:cn（阿里云 apt + pip 镜像）
make run            # docker compose up -d
make stop           # docker compose down
make logs           # 跟踪日志
make rebuild        # clean + build（从零开始）
make clean          # 停止并删除本项目所有镜像 + 清理 dangling
make prune          # 仅清理 dangling 镜像
```

国内构建会注入 `CN_MIRROR=1` 与 `PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/`，最终 tag 为 `xiaoai-llm:cn`，与境外 `xiaoai-llm:latest` 共存。

### EdgeTTS 注意事项

音箱必须能直接访问本服务拉取音频，请在 TTS 设置里把"音箱可访问的本服务地址"配置为 `http://<局域网IP>:33003`（**不要写 localhost**）。容器部署使用端口映射即可；如拉取失败可改用 `network_mode: host`。

---

## 镜像管理

构建/清理时常见的 `docker images` 输出：

| 镜像 | 含义 | 是否应保留 |
| --- | --- | --- |
| `xiaoai-llm:latest` / `xiaoai-llm:cn` | 本项目最终镜像 | 应保留 |
| `python:3.12-slim` | Dockerfile 多阶段构建的 base 镜像 | **正常保留**（Docker 缓存层） |
| `<none>:<none>` | dangling 镜像（被覆盖的旧 tag、中断的构建、buildkit 残留等） | 可清理 |

清理 dangling 与缓存：

```bash
make prune         # 仅清理 dangling
docker builder prune -af    # 清理 buildkit 缓存（释放更多空间）
```

完整清理（包括本项目所有镜像）：

```bash
make clean
```

> **为什么 `<none>:<none>` 会出现？**
>
> - 同一 tag 多次构建时，旧镜像会被覆盖成 `<none>`（Docker 默认行为）；
> - 用 `docker buildx build`（含 `--check`）或指定多平台 `--platform` 时，buildkit 会在内部创建临时镜像，构建完成后部分缓存层可能以 dangling 形式保留；
> - 构建被 Ctrl+C 中断时，部分中间层也会变 dangling。
>
> 这是 Docker 缓存机制，不是 bug；只要定期 `prune` 即可。

---

## 配置文件

所有配置集中在 `config.json`（首次启动自动生成，权限 `0600`），完整示例见 [`config.example.json`](config.example.json)。要点：

- 敏感字段（小米密码、API Key、管理密码哈希）**仅存本地**；Web API 与日志永不回传明文；前端留空表示保持原值。
- 全局默认（`defaults` / `tts`）+ 单设备覆盖（`devices.<did>`，`null` 表示继承）。
- 修改后点「应用配置」热加载（重建监听任务）；仅 Web 端口变更需重启容器。

关键参数一览：

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `mijia.login_type` | `password` | `password`（账号密码）或 `pass_token`（userId + passToken） |
| `mijia.poll_source` | `userprofile` | 空闲轮询数据源：`userprofile`（HTTPS 对话接口，不用 UBus）或 `ubus`（设备级 nlp_result_get） |
| `mijia.poll_fallback_to_ubus` | `true` | userprofile 失败时是否回退 UBus |
| `mijia.fetch_native_answer` | `false` | 命中唤醒词后是否读取小爱原生回答（日志对比用） |
| `mijia.poll_interval_seconds` | `2` | 发现新语音后的活跃轮询间隔（秒） |
| `mijia.idle_poll_interval_seconds` | `10` | 空闲状态轮询间隔（秒） |
| `mijia.active_window_seconds` | `60` | 发现新问句后维持快速轮询的时间 |
| `mijia.max_backoff_seconds` | `60` | 出错退避上限 |
| `defaults.session_timeout_minutes` | `30` | 会话超时清空 |
| `defaults.max_context_tokens` | `4096` | 上下文 Token 预算（估算裁剪） |
| `llm_profiles[].timeout_seconds` | `200` | LLM 超时（秒） |
| `tts.timeout_seconds` | `30` | TTS 生成超时（秒） |
| `web_search.search_url` | `https://www.sogou.com/web` | 搜索端点（搜狗 / Bing / DuckDuckGo） |
| `web_search.max_results` | `5` | 抓取的搜索结果条数（1-10） |
| `web_search.fetch_top_results` | `2` | 抓取正文的数量（0-5） |

---

## 目录结构

```
xiaoai-llm/
├── main.py                       # FastAPI 入口
├── app/
│   ├── config.py                 # 配置存储（原子写、脱敏、密钥合并）
│   ├── models.py                 # Pydantic 配置模型 + 默认合并
│   ├── auth.py                   # 管理端密码 + session
│   ├── logging_store.py          # 内存日志（脱敏、1000 条上限）
│   ├── runtime.py                # 多设备运行时（监听 / 编排 / 热加载）
│   ├── mijia/                    # 米家客户端 + 型号兼容表
│   ├── services/                 # 唤醒过滤 / 上下文 / LLM / 网络搜索 / TTS
│   └── web/                      # FastAPI 路由 + 管理台静态页
├── tests/                        # 79 项自动化测试
├── app/web/static/               # Web 管理台前端
├── config.example.json           # 配置示例
├── Dockerfile / docker-compose.yml
├── pyproject.toml
└── requirements.txt
```

---

## API 摘要

```text
GET    /health                          存活检查
POST   /api/auth/setup | login | logout 管理端认证
GET    /api/config                      读取（脱敏）
PUT    /api/config                      保存配置（密钥保留）
POST   /api/config/validate | apply    校验 / 热加载
POST   /api/mijia/login | logout        米家登录 / 退出
GET    /api/mijia/devices | status      设备发现 / 登录状态
GET    /api/models                      型号兼容表
GET    /api/devices                     设备列表
PUT    /api/devices/{did}               单设备覆盖配置
POST   /api/devices/{did}/test/tts|mute|audio   单设备测试
DELETE /api/devices/{did}/context       清除会话
POST   /api/llm/profiles/{id}/test      LLM 连通性测试
POST   /api/web-search/test             网络搜索与网页抓取测试
GET    /api/logs                        日志（增量）
DELETE /api/logs                        清空
GET    /api/status                      运行状态
```

启动后访问 `http://<主机>:33003/docs` 查看自动生成的 API 文档。

---

## 已知限制

本项目采用**非刷机云端接入**，以下行为受小米云端能力限制：

1. **唤醒为模拟实现**：服务无法获得音箱本地唤醒事件，也不存在可用的云端 WebSocket 推送（MiNA 推送仅限音箱固件自身连接；Open-XiaoAI 等方案需刷机）。系统以增量轮询模拟：空闲时默认 10 秒查询一次轻量 HTTPS 对话接口（不产生 UBus 调用），发现新问句后 60 秒内每 2 秒查询一次；UBus 仅用于命中后的 mute / TTS。间隔越小响应越快，但请求越频繁。
2. **双音只能最佳努力抑制**：命中后立即调用暂停，但存在云端延迟，原生回答可能播出一小段；部分型号不支持播放状态查询。
3. **同型号多台设备**：优先使用设备级对话接口区分；极少数老固件若接口异常，建议每账号同一型号只绑定一台。
4. **账号风控**：异地登录、频繁请求可能触发小米风控验证（验证码 / 短信）。密码登录受阻时可改用 passToken 方式：先在常用环境用密码登录一次（或用 xiaogpt 等工具生成 `.mi.token`），再把其中的 `userId` / `passToken` 填入管理台。
5. **服务重启清空上下文**：会话记忆保存在内存中。
6. **search_url 兼容性**：搜狗 / Bing 对频繁访问会触发反爬。本服务会在调用前先访问首页获取 cookie；若搜索被反爬拦截，错误信息会在日志里显示，请稍候重试或切换搜索引擎。

---

## 安全说明

- 首次初始化必须同时提供随机 `bootstrap_token`，防止局域网内其他人抢先接管管理端；初始化后令牌立即失效。
- 管理台仅提供单密码保护，请只在可信局域网暴露；不要直接映射公网。
- `config.json` 与 `data/mi-token.json` 包含敏感凭据（权限 `0600`），请妥善备份与保护；仓库内**不会**包含任何凭据。
- 网络搜索结果按不可信资料处理（Prompt 层注入防护）；`web_fetch` 只允许公网 HTTP(S) 地址，并阻止本机、内网和链路本地地址。

---

## 测试

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

当前 **79 项测试全部通过**，覆盖：配置解析 / 唤醒词匹配 / LLM 编排 / 网络搜索改写 / TTS 调度 / Web API 鉴权 等核心链路。

---

## 致谢

本项目参考了 [MiGPT](https://github.com/idootop/mi-gpt) / [MiGPT-Next](https://github.com/idootop/mi-gpt-next) / [xiaogpt](https://github.com/yihong0618/xiaogpt) 的非刷机接入思路，针对 **多设备 + 大模型 + 实时搜索** 场景做了完整重构。

## 许可证

MIT License。