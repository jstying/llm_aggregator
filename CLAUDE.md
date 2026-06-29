# claude.md

## 1. 🧠 SYSTEM OVERVIEW (Cognitive Summary)

这是一个基于 Flask 框架开发的大语言模型（LLM）聚合与性能对比 Web 应用程序。该系统允许用户输入提示词，同时或单独调用不同的 g4f Provider，并实时对比响应内容和响应时间。系统现已集成基于 Firebase 的完整用户认证模块，支持三种访问身份：匿名访客、游客用户和已登录用户。系统采用后端路由结合前端单页异步交互（AJAX/Fetch）的架构，认证子系统以 Flask Blueprint 形式解耦挂载。

## 2. 🧬 ARCHITECTURE MAP (MOST IMPORTANT SECTION)

系统由三个核心子系统构成：Flask 后端服务、Firebase 认证模块、以及 HTML5/JavaScript 前端交互界面。

### 后端服务（Flask，main.py）

- **路由层**：提供页面渲染路由（`/`、`/home`）以及 LLM API 接口（`/api/providers`、`/api/compare`、`/api/test-single`）和认证 API（`/api/auth/guest`）。
- **多线程并发调度器**：利用 `ThreadPoolExecutor` 并发调用多个 Provider 的请求，防止单点阻塞。
- **g4f 适配层**：封装对 `g4f.ChatCompletion` 的底层调用，处理模型匹配逻辑和异常捕获。

### 认证子系统（Flask Blueprint，auth/）

- **蓝图注册**：`auth_bp` 以无前缀方式挂载，路由直接暴露为 `/login`、`/register`、`/logout`、`/profile`。
- **Firebase 适配层**：`auth/db.py` 在模块初始化时尝试连接 Firebase Firestore，并设置 `FIREBASE_AVAILABLE` 布尔标志。若标志为 `False`，所有认证路由返回 503 响应，不崩溃。
- **Session 管理**：用户身份通过 Flask `session` 在请求间传递，密钥从 `SECRET_KEY` 环境变量加载。

### 前端界面（Jinja2 + JS）

- **三态导航栏**：`auth/base.html` 和 `index.html` 均根据 `session.user_id` 与 `session.is_guest` 状态联动切换导航栏展示内容。
- **异步交互控制器**：通过 Fetch API 与后端进行非阻塞通信，动态更新 DOM。

```
[浏览器]
   |
   |-- GET /         --> [index() 身份检查] --> home.html / index.html
   |-- GET /home     --> [home() 清除 is_guest] --> 重定向 /
   |-- POST /login   --> [auth Blueprint] --> Firebase 验证 --> session 写入
   |-- POST /api/auth/guest --> session['is_guest']=True
   |-- POST /api/compare    --> ThreadPoolExecutor --> g4f Provider 适配层
                                                            |
                                                     (外部 LLM APIs)
```

### 耦合风险与设计注意事项

- **硬编码映射**：`PROVIDER_MODELS_MAP` 属于硬编码。g4f 库升级后必须手动同步修改。
- **全局状态依赖**：系统依赖 `G4F_AVAILABLE` 和 `FIREBASE_AVAILABLE` 两个全局布尔标志。任一初始化失败都会导致对应功能降级。
- **Flash 消息消费规则**：`home.html` 和 `index.html` 均已加入 Flash 消息显示区。如果某个重定向目标页面不显示 Flash 消息，消息将在 session 中堆积，并在下一个 auth 页面集中出现，产生状态矛盾的假象。新增页面必须同步加入 Flash 消息显示区。

## 3. 🧰 TECHNICAL STACK (EVIDENCE-BASED ONLY)

- **编程语言**：Python, JavaScript
- **后端框架**：Flask
- **并发库**：`concurrent.futures.ThreadPoolExecutor`
- **核心依赖库**：g4f (GPT4Free)、firebase-admin (Firebase Admin SDK)、python-dotenv
- **认证与安全**：Werkzeug (`generate_password_hash` / `check_password_hash`)、Flask `session`
- **数据库**：Google Cloud Firestore（通过 Firebase Admin SDK 访问）
- **前端技术**：HTML5, CSS3 (Linear Gradients, Grid 布局, Flex 布局), Vanilla JavaScript
- **模板引擎**：Jinja2
- **运行环境配置**：通过 `os.environ.get('PORT')` 和 `os.environ.get('SECRET_KEY')` 读取环境变量；本地开发通过 `.env` 文件加载（python-dotenv）
- **部署平台**：Google App Engine (GAE Standard / Flexible compatible)

## 4. 📁 CODEBASE STRUCTURE (WITH INTENT)

```
llm_aggregator/
├── main.py                          # Flask 后端入口（含 auth 蓝图注册、session/secret_key 配置）[MODIFIED]
├── auth/                            # 认证模块 Blueprint 目录 [NEW]
│   ├── __init__.py                  # 定义 auth_bp 蓝图，导入 routes 模块
│   ├── db.py                        # Firebase 初始化、FIREBASE_AVAILABLE 标志、4 个用户 CRUD 函数
│   └── routes.py                    # /login, /register, /logout, /profile 路由实现
├── templates/
│   ├── home.html                    # 欢迎页：未认证且非游客的唯一纯净入口 [NEW]
│   ├── index.html                   # LLM 聚合功能主页（含三态导航栏与 Flash 消息区）[MODIFIED]
│   └── auth/                        # 认证模块前端模板目录 [NEW]
│       ├── base.html                # 认证页通用布局（三态导航栏：已登录 / 游客 / 未认证）
│       ├── login.html               # 登录表单（含返回欢迎页、游客体验快捷链接）
│       ├── register.html            # 注册表单（含返回欢迎页、游客体验快捷链接）
│       └── profile.html             # 个人资料页（仅已登录用户可访问，游客被拦截）
├── tests/                           # 自动化测试目录（基于 unittest，不部署）
│   ├── test_main_whitebox.py        # main.py 白盒单元测试（内部函数、模型降级规则）
│   ├── test_main_blackbox.py        # main.py 黑盒集成测试（HTTP 接口驱动）
│   ├── test_main_graybox.py         # main.py 灰盒测试（全局状态、线程池行为）
│   ├── test_auth_whitebox.py        # auth 模块白盒测试（密码哈希、Firestore Mock）
│   └── test_auth_blackbox.py        # auth 模块黑盒测试（路由、Session 读写、访问控制）
├── availability_g4f/                # Provider 可用性探测工具（开发辅助，不部署）
│   ├── find_providers_models.py     # 扫描 g4f 所有 working Provider 及其模型列表
│   └── test_providers.py            # 手动逐一测试候选 Provider 是否真正可用
├── available_providers_models.txt   # find_providers_models.py 的输出结果
├── provider_test_results.txt        # test_providers.py 第一轮测试结果
├── provider_test_results_v2.txt     # test_providers.py 第三轮测试结果（最新，含 PollinationsAI 验证）
├── firebase-key.json                # Firebase 服务账号密钥（本地开发用，严禁提交 git）[NEW]
├── .env                             # 本地环境变量文件（SECRET_KEY 等，严禁提交 git）[NEW]
├── requirements.txt                 # 依赖锁定版本（新增 firebase-admin、python-dotenv）[MODIFIED]
├── app.yaml                         # GAE 部署配置（新增 SECRET_KEY env_variable）[MODIFIED]
└── env/                             # 本地 Python 虚拟环境（不提交 git）
```

### `main.py` [MODIFIED]

- **role**: 系统的核心后端驱动程序，负责初始化配置、注册认证蓝图、定义 LLM 路由、管理并发请求。
- **key logic**:
  - 启动时调用 `load_dotenv()` 加载 `.env` 文件。
  - `app.secret_key` 从 `SECRET_KEY` 环境变量加载，缺失时 fallback 为 `secrets.token_hex(32)`（每次重启会变，session 将失效）。
  - `from auth import auth_bp` + `app.register_blueprint(auth_bp)` 将认证蓝图以无前缀方式挂载。
  - `index()` 路由：检查 `session.user_id` 或 `session.is_guest`，两者皆无时渲染 `home.html`，否则渲染 `index.html`。
  - `home()` 路由（`GET /home`）：清除 `session['is_guest']`，重定向到 `/`，用于"返回欢迎页"操作。
  - `guest_login()` 路由（`POST /api/auth/guest`）：设置 `session['is_guest'] = True`，返回 JSON。
  - `determine_actual_model(provider_name, requested_model)`: 纯函数，封装规则 A/B/C 的模型决策逻辑。
  - `init_result_object(provider_name, model)`: 纯函数，统一初始化标准 Result 字典（7 个 key）。
  - `detect_and_truncate(text)`: 纯函数，句级 + 滑动窗口重复检测，触发时截断并追加提示语。
  - `parse_peer_review_json(text)`: 纯函数，从互评响应文本中提取 JSON，返回 `(score: int, comment: str)`；任何解析失败均容错返回 `(80, raw_text)`；score 被夹入 [1, 100]。
  - `test_g4f_provider()`: 核心 LLM 测试函数，调用上述辅助函数、应用隐形 Prompt 路由（`ROUTE_PROMPTS_MAP`）、将响应经 `detect_and_truncate` 处理后写入 result、统计响应耗时。
  - `test_single_provider()`: `/api/test-single` 路由处理函数，直接调用 `test_g4f_provider`，因此隐形 Prompt 路由和重复截断与 `/api/compare` 完全同路径生效。外层 `except Exception` 返回中文友好 500 消息，不暴露原始异常。
  - `run_peer_review(reviewer_provider, reviewer_model, review_prompt)`: 单次互评请求，内置 429/queue-full 错误单次重试（检测到 `'429'` 或 `'queue'` 关键词时等待 2+random(0,1)s 后重试一次）。`network_keywords` 列表现包含 `'429'` 和 `'queue'`，任何匹配项最终触发"系统正忙"友好消息而非原始异常文本。成功路径内部调用 `parse_peer_review_json` 解析响应，返回 `{reviewer_provider, reviewer_model, score, comment}`，不含 `response_time`。
  - `compare_providers()`: 两阶段并发执行。第一阶段 `ThreadPoolExecutor` 并发测试各 Provider，except 块区分 `TimeoutError`（`logger.warning` 无 traceback）和其他异常（`logger.error` 带 `exc_info`）。第二阶段整体包裹在独立 `try-except` 中：任何崩溃（任务构建失败、Executor 异常等）均被捕获并记 `logger.error`，第一轮结果（含 `peer_reviews: []`）仍可安全返回，接口不会因互评失败而报 500。第二阶段内部每个 `future.result(timeout=25)` 也有独立 except，单条互评的 `TimeoutError` 单独 `logger.warning`，其他异常 `logger.error`。互评任务开始、每条完成（含裁判名、被评 Provider 名、score）、阶段整体完成均有 `logger.info`。最终按成功状态和耗时排序。外层 `except Exception` 的 500 响应返回中文友好消息，不暴露原始异常信息给用户。
  - `PEER_REVIEW_PROMPTS_MAP`: 互评裁判提示词表，key 为模型名称，value 为要求模型输出 `{"score": int, "comment": str}` JSON 格式的提示词前缀。
  - `ROUTE_PROMPTS_MAP`: 隐形 Prompt 路由表，key 为 `(provider_name, model)` 元组，value 为追加到用户 prompt 尾部的风格提示词。**设计约束**：首句必须含"立刻"urgency 指令（防超时）；其次凸显各模型真实角色个性：gpt-4 扮演"严谨分析师"（结论→依据→反思三层结构，300 字），gpt-3.5-turbo 扮演"高效助手"（TLDR 一句话结论优先，口语化中文，150 字），aria 扮演"实战顾问"（跳过铺垫直接给 1-2 个可操作动作，200 字），openai-fast 扮演"极速速答者"（一句结论+一句理由，英文输出，100 字内）。新增条目须同时满足速度指令和角色鲜明两项要求，字数上限不得超过 300 字。
  - `SENSITIVE_KEYWORDS`: 模块级敏感词列表，当前为空列表占位，可直接填充关键词生效，`detect_and_truncate` 读取此变量。
- **depends_on**: `flask`, `g4f`, `concurrent.futures`, `time`, `json`, `logging`, `re`, `os`, `secrets`, `random`, `dotenv`, `auth.auth_bp`
- **affects**: `home.html`、`index.html`（通过 Jinja2 注入变量），所有前端 API 请求。

### `auth/__init__.py` [NEW]

- **role**: 定义 `auth_bp = Blueprint('auth', __name__)`，并通过 `from . import routes` 触发路由注册。

### `auth/db.py` [NEW]

- **role**: Firebase 适配层，负责初始化 Firebase Admin SDK 并暴露用户 CRUD 函数。
- **key logic**:
  - 初始化策略：若项目根目录存在 `firebase-key.json`，优先使用它（本地开发）；否则使用 `ApplicationDefault()`（GAE 环境）。`ApplicationDefault()` 的凭据解析是惰性的，它在 `firestore.client()` 时才真正触发，因此必须优先检查 key 文件，不能依赖其构造函数的异常来做 fallback。
  - `FIREBASE_AVAILABLE`：模块级布尔标志。初始化成功为 `True`，任何异常（包括 `ImportError`）均设为 `False`。
  - 4 个 CRUD 函数：`get_user_by_username`、`get_user_by_email`、`create_user`、`get_user_by_id`。这 4 个函数仅在 `FIREBASE_AVAILABLE` 为 `True` 时被调用，调用方（`auth/routes.py`）负责守卫。
- **depends_on**: `firebase_admin`、`werkzeug.security`

### `auth/routes.py` [NEW]

- **role**: 实现 `/login`、`/register`、`/logout`、`/profile` 四条路由，管理 Session 状态转换。
- **key logic**:
  - 每条路由顶层都有 `try/except Exception` 兜底，错误通过 Flash 消息反馈，不返回裸异常。
  - 登录成功后写入 `session['user_id']`、`session['username']`，并清除 `session['is_guest']`。
  - 注册成功后写入 `session['user_id']`、`session['username']`，并清除 `session['is_guest']`。
  - 退出登录后清除 `session['user_id']`、`session['username']`、`session['is_guest']`，重定向到 `url_for('index')`（即根路由 `/`，根路由检测到无身份状态后渲染 `home.html`）。
  - `/profile` 路由先检查 `session['user_id']`，不存在时立即重定向到 `/login`，游客无法绕过访问。
- **depends_on**: `auth.db`、`werkzeug.security`、`flask.session`

### `templates/home.html` [NEW]

- **role**: 系统的第一入口，仅对"未认证且非游客"的用户展示。
- **key logic**:
  - 提供三个操作入口：Login（跳转 `/login`）、Sign Up（跳转 `/register`）、Continue as Guest（Fetch POST `/api/auth/guest`，成功后重定向 `/`）。
  - 页面内含 Flash 消息显示区，确保退出登录等操作的提示消息能在此页面被立即消费，不向下一个页面泄漏。Flash 消息在渲染后 3 秒自动淡出消失（opacity 渐变 0.4s，淡出后从 DOM 移除并清理父容器）。

### `templates/index.html` [MODIFIED]

- **role**: LLM 聚合功能主页，已登录用户和游客均可访问。
- **key logic**:
  - 顶部新增三态导航栏：已登录时显示用户名、Profile、Logout；游客时显示 Guest Mode 徽章、Login、Register。
  - **移动端响应式导航栏**：`@media (max-width: 520px)` 将 Logo 从"LLM Aggregator"切换为"G4F"（通过 `.logo-full` / `.logo-short` 双 span 实现），隐藏 `.nav-welcome`，压缩 nav 间距与字号，确保导航栏在手机竖屏单行显示。`.guest-badge` 始终设置 `white-space: nowrap` 防止"Guest Mode"被截断为两行。
  - 游客状态下在导航栏下方显示黄色提示条，引导注册或登录。
  - 已登录用户的 header 区域展示个性化欢迎语（`Welcome back, {{ session.username }}`）。
  - 新增 Flash 消息显示区（位于 `.container` 内、`.header` 之上），确保注册成功等提示在此处被立即消费。Flash 消息在渲染后 3 秒自动淡出消失（opacity 渐变 0.4s，淡出后从 DOM 移除，若 `.flash-messages` 容器变空则一并移除）。
  - `escapeHtml(str)`: 所有动态内容（provider 名、model 名、response、error）均通过此函数转义后注入 DOM，防止 XSS。
  - `renderPeerReviews(reviews, uid)`: 将 `peer_reviews` 数组渲染为可折叠面板，展示"来自 [Provider] 的盲评 [N分]：[comment]"。面板默认折叠，通过 `togglePeerReview(uid)` 切换显示状态。
  - `displayResults(data)` 在每个成功结果的 `.provider-response` 下方附加互评面板；失败结果不展示互评。
- **depends_on**: 后端路由 `/` 传来的 `providers`、`provider_models_json` 以及 `session` 全局对象；`/api/compare` 返回的 `peer_reviews` 字段。

### `templates/auth/base.html` [NEW]

- **role**: 认证模块所有页面的通用布局基础模板。
- **key logic**: 导航栏根据 `session.user_id` 和 `session.is_guest` 进行三态切换：已登录显示 Profile + Logout；游客显示 Guest Mode 徽章 + Login + Register；未认证显示 Login + Register。Flash 消息（`.alert` 类）统一在 `.card` 容器顶部渲染，3 秒后自动淡出消失（opacity 渐变 0.4s，淡出后从 DOM 移除）。

### `templates/auth/login.html` 和 `templates/auth/register.html` [NEW]

- **role**: 登录和注册表单页。
- **key logic**: 表单下方提供两个快捷链接。"Back to welcome page" 链接指向 `url_for('home')`（即 `GET /home` 路由），该路由会清除游客状态后重定向到根路由，确保游客点击后能看到 `home.html` 而非 `index.html`。"Continue as guest" 链接通过 Fetch POST `/api/auth/guest` 切换为游客身份。

### `availability_g4f/` 和 `tests/`

两个目录的功能与原有描述保持不变，不部署到 GAE。

## 5. 🔄 EXECUTION & DATA FLOW (CRITICAL)

### 1. 初始化阶段

- Flask 应用启动，`load_dotenv()` 加载 `.env` 文件。
- `app.secret_key` 从环境变量读取，Session 加密功能激活。
- `auth_bp` 蓝图注册，`/login`、`/register`、`/logout`、`/profile` 路由挂载。
- `auth/db.py` 初始化：检测 `firebase-key.json`，优先使用它连接 Firebase；否则使用 ADC。
- Flask 应用尝试导入 `g4f`，设置 `G4F_AVAILABLE` 标志。

### 2. 首次访问与身份路由

- 用户访问根路由 `/`。
- `index()` 路由检查 `session.user_id`（已登录）和 `session.is_guest`（游客）。
- 两者皆无时渲染 `home.html`（欢迎页）；任一存在时渲染 `index.html`（功能页）。

### 3. 身份建立

- **游客路径**：用户在 `home.html` 点击"Continue as Guest"，前端 Fetch POST `/api/auth/guest`，后端设置 `session['is_guest'] = True` 并返回 JSON，前端重定向到 `/`，此时 `index.html` 被渲染。
- **登录路径**：用户填写 `/login` 表单，后端验证密码哈希，成功后写入 `session['user_id']` 和 `session['username']`，清除 `is_guest`，重定向到 `/`。
- **注册路径**：与登录路径类似，写入相同的 session 键后重定向到 `/`。

### 4. LLM 聚合请求流程

- 用户在 `index.html` 输入 Prompt，勾选 Provider，点击对比按钮。
- 前端 Fetch POST `/api/compare`，后端启动第一个 `ThreadPoolExecutor` 线程池。
- 各子线程并发执行 `test_g4f_provider`，模型降级规则 A/B/C 在此应用，隐形 Prompt 路由（`ROUTE_PROMPTS_MAP`）追加风格提示词。
- 主线程收集第一阶段结果，为每条 result 初始化 `peer_reviews: []`。
- 互评触发条件：`tested_providers >= 2` 且 `successful_results >= 2`。满足时启动第二个 `ThreadPoolExecutor`，每个成功者 B 对成功者 A 执行 `run_peer_review`（B 不对自身评）；互评响应经 `parse_peer_review_json` 解析为 `{score, comment}`，挂载到 A 的 `peer_reviews` 列表。
- 两阶段完成后按 `(失败状态, 耗时升序)` 排序，返回 JSON。
- 前端 `displayResults()` 渲染统计卡片、响应内容，并在每个成功结果下方附加可折叠互评面板。

### 5. 退出登录流程

- 用户点击导航栏 Logout，GET `/logout`。
- 后端清除 `session['user_id']`、`session['username']`、`session['is_guest']`，Flash "You have been logged out"，重定向到 `/`。
- `index()` 检测到无身份状态，渲染 `home.html`。
- `home.html` 的 Flash 消息显示区渲染并消费"You have been logged out"提示。

## 6. 🧠 CORE LOGIC / DOMAIN RULES

### 用户身份三层不变量（Identity State Contract）

系统在任意时刻有且仅有以下三种互斥的身份状态：

**状态 1：匿名/未认证（Anonymous）**

- `session` 中不含 `user_id`，也不含 `is_guest`。
- 访问根路径 `/` 时，`index()` 渲染 `home.html`。
- 系统不为此状态存储任何用户数据。

**状态 2：游客（Guest）**

- `session['is_guest'] == True`，且 `session` 中不含 `user_id`。
- 访问根路径 `/` 时，`index()` 渲染 `index.html`，展示 Guest Mode 徽章和黄色提示条。
- 游客数据不持久化，仅限单次会话内使用 LLM 聚合功能。
- 游客可访问 `/login` 和 `/register`，但无法访问 `/profile`（后端拦截，重定向到 `/login`）。

**状态 3：已登录用户（Authenticated）**

- `session['user_id']` 存在（Firestore 文档 ID 字符串）。
- `session['username']` 同步存储，用于前端展示，无需额外数据库查询。
- 身份跨请求持久化，数据与 Firebase Firestore `users` 集合同步。
- 已登录用户可访问 `/profile`，导航栏展示 Profile 和 Logout 链接。

### Session 状态转换规范（Flash 冲突预防）

以下规则防止 Flash 消息在 session 中堆积：

- **任何完成身份切换的重定向目标页面，必须包含 Flash 消息显示区。** `home.html` 和 `index.html` 均已加入显示区。新增页面如需作为重定向目标，必须同步加入。
- **游客切换为已登录状态时**，必须在同一个请求内执行：`session['user_id'] = ...`、`session['username'] = ...`、`session.pop('is_guest', None)`，三步必须原子执行，不能遗漏任何一步。
- **已登录状态退出时**，必须清除全部三个键：`session.pop('user_id', None)`、`session.pop('username', None)`、`session.pop('is_guest', None)`。
- **`GET /home` 路由**的唯一职责是清除 `is_guest` 并重定向到 `/`，不操作 `user_id`。已登录用户访问此路由不会被退出登录。

### 模型自适应降级规则

当用户发起 LLM 请求时，系统遵循以下决策树决定最终传递给 g4f 的模型名称。该逻辑已提取为纯函数 `determine_actual_model(provider_name, requested_model)`：

- **规则 A**：若用户指定的模型包含在当前 Provider 的支持映射表内，则使用该指定模型。
- **规则 B**：若用户指定的模型不被支持或未指定，则自动选取映射表中第一个作为默认模型。
- **规则 C**：若该 Provider 没有任何模型配置，则兜底降级为 `"gpt-3.5-turbo"`。

### AI 盲评触发与评分规则

互评阶段在 `compare_providers()` 第一阶段完成后执行，遵循以下不变量：

- **触发条件**：`len(providers_to_test) >= 2` 且 `len(successful_results) >= 2`。任一条件不满足，所有 `peer_reviews` 保持空列表。
- **互评配对**：对每个成功者 A，由其余所有成功者 B 扮演裁判发起点评（B 不对自身评）。2 个成功者时各有 1 条互评；N 个成功者时各有 N-1 条。
- **提示词格式**：`PEER_REVIEW_PROMPTS_MAP[reviewer_model]` 提供裁判人设前缀，要求模型严格输出 `{"score": int, "comment": str}` JSON，不含其他文字。
- **JSON 容错解析**：`parse_peer_review_json` 从响应中扫描首个 `{...}` 块尝试解析。score 被夹入 [1, 100] 并强转为 int。任何解析失败（格式错误、缺失 score 字段、异常）均 fallback 为 `(80, raw_text)`，接口不崩溃。
- **失败者不参与互评**：第一阶段失败的 Provider 既不作为被评对象，也不作为裁判，其 `peer_reviews` 永远为空列表。

### 结果排序权重规则

- **第一优先级**：`success` 状态。成功的请求必须排在失败的请求前面。
- **第二优先级**：`response_time`。相同成功状态下，耗时越短的 Provider 排在越前面。

## 7. 🧾 DATA MODELS / STATE DESIGN

### 核心数据传输对象 (DTO)：LLM Result

`test_g4f_provider` 返回的字典结构如下，此 7-key 契约严禁增删（`peer_reviews` 字段由 `compare_providers` 在外层追加，不属于此契约）：

```python
{
    'provider': str,        # Provider 类的名称
    'success': bool,        # 是否请求成功
    'response': str,        # 模型返回的文本内容（成功时）
    'error': str,           # 异常信息简述（失败时）
    'response_time': float, # 响应耗时，单位秒，保留两位小数
    'model': str,           # 实际使用的模型名称
    'type': 'g4f'           # 固定类型标识
}
```

`compare_providers` 在第一阶段结束后为每条 result 追加 `peer_reviews` 字段，使最终 `/api/compare` 响应中每条 result 共有 8 个 key：

```python
result['peer_reviews'] = [
    {
        'reviewer_provider': str,  # 裁判 Provider 名称
        'reviewer_model': str,     # 裁判使用的模型名称
        'score': int,              # 评分（1-100，由 parse_peer_review_json 夹入范围）
        'comment': str,            # 一句话点评（JSON 解析失败时为原始响应文本）
    },
    ...
]
```

互评 DTO key 集合（`reviewer_provider`、`reviewer_model`、`score`、`comment`）为前后端契约，严禁增删。

### Firestore 用户文档结构（`users` 集合）

```python
{
    'username': str,          # 用户名，唯一索引字段
    'email': str,             # 邮箱，唯一索引字段
    'password_hash': str,     # Werkzeug generate_password_hash 生成的哈希值
    'created_at': Timestamp   # Firestore SERVER_TIMESTAMP
}
```

文档 ID 由 Firestore 自动生成，存储为 `session['user_id']`。

### Flask Session 键规范

```python
session['user_id']   # str：Firestore 文档 ID，已登录用户独有
session['username']  # str：用于前端展示，已登录用户独有
session['is_guest']  # bool：固定为 True，游客状态独有
```

三个键互斥：`user_id` 存在时 `is_guest` 必须已被清除；`is_guest` 存在时 `user_id` 必须不存在。

### 全局布尔标志

- `G4F_AVAILABLE`：g4f 库是否可用，决定 LLM 功能是否降级。
- `FIREBASE_AVAILABLE`：Firebase 是否初始化成功，决定认证功能是否可用。

## 8. 🔌 EXTERNAL INTERFACES

### 后端 API 接口规范（完整列表）

**LLM 聚合接口：**

- `GET /api/providers`：返回所有可用 Provider 的元数据列表。
- `POST /api/compare`：接收 `prompt`、`providers`、`model`、`max_workers`，返回并发测试后的聚合排序结果。
- `POST /api/test-single`：接收 `prompt`、`provider`、`model`，单独测试某一通道并返回标准 Result 对象。
- `GET /health`：健康检查，返回系统状态、`g4f_available`、Provider 列表、`routing_rules_loaded`（`ROUTE_PROMPTS_MAP` 是否非空）、`peer_review_rules_loaded`（`PEER_REVIEW_PROMPTS_MAP` 是否非空）及时间戳。

**页面路由：**

- `GET /`：身份检查入口，渲染 `home.html` 或 `index.html`。
- `GET /home`：清除游客状态，重定向到 `/`（"返回欢迎页"专用路由）。

**认证接口（auth Blueprint）：**

- `GET /login`：渲染登录页。
- `POST /login`：处理登录表单，验证密码哈希，建立 session。
- `GET /register`：渲染注册页。
- `POST /register`：处理注册表单，创建 Firestore 用户文档，建立 session。
- `GET /logout`：清除 session，重定向到 `/`。
- `GET /profile`：渲染个人资料页，要求 `session['user_id']` 存在。

**游客接口：**

- `POST /api/auth/guest`：设置 `session['is_guest'] = True`，返回 `{"status": "ok"}`。

### 第三方集成

- **g4f 库**：通过模拟浏览器或逆向接口，无凭证调用各大免费 AI 渠道（如 `Yqcloud`、`OperaAria`、`PollinationsAI`）。
- **Firebase Admin SDK**：连接 Google Cloud Firestore，管理 `users` 集合的读写。本地开发使用 `firebase-key.json`，GAE 生产环境使用 Application Default Credentials（ADC）。

## 9. ⚠️ SYSTEM RISKS / CODE QUALITY AUDIT

- ~~**超时机制不一致**~~（**已修复**）：`future.result()` 的外层等待超时已从 `25` 秒调整为 `21` 秒，与内部 `g4f.ChatCompletion.create(timeout=20)` 保持一致，仅预留 1 秒线程调度缓冲。

- ~~**前端异常捕获漏洞**~~（**已修复**）：`index.html` 的 Fetch 处理逻辑已在调用 `response.json()` 前检查 `response.ok`。非 2xx 响应时先尝试解析 JSON `error` 字段，若 body 非 JSON 则回退到 `Server error: 状态码`，不再导致前端崩溃。

- ~~**异常回滚伪造**~~（**已修复**）：`compare_providers` 的 `except` 块已改为复用 `determine_actual_model()` 和 `init_result_object()` 两个辅助函数，模型决策规则与正常流程完全一致，key 集合严格统一。

- ~~**Flash 消息堆积 Bug**~~（**已修复**）：`home.html` 和 `index.html` 原本没有 Flash 消息显示区，导致退出登录等操作产生的 Flash 消息滞留 session，在下一个 auth 页面（使用 `auth/base.html`）上集中出现，引发"注册成功 + 已退出登录"同时显示的假象。两个文件均已加入 Flash 消息显示区。

- **互评阶段双层保护**：互评阶段采用两层独立 try-except。外层兜住整个阶段（任务构建崩溃、Executor 初始化失败等），内层兜住单条 `future.result(timeout=25)` 的超时或执行异常。任何一层失败均不影响第一轮 LLM 结果返回，`peer_reviews` 字段在互评 try 块之前已初始化为 `[]`。`run_peer_review` 内部 `g4f.ChatCompletion.create(timeout=15)` 为 advisory 超时（非硬截断）；外层硬截断留有约 10 秒缓冲。互评 prompt 远长于普通请求（含完整回答文本），若缩短任一超时值须同步评估另一侧的缓冲余量。**429 重试时序**：429 通常在 0.1s 内即返回，重试等待 2-3s，第一个并发请求通常已完成，因此第二次尝试几乎不会再遇到队列满。两次尝试总耗时上限约 2-3s + 15s = 18s，安全在 25s outer timeout 以内。

- **线程池潜在安全隐患**：`max_workers` 的计算逻辑为 `min(data.get('max_workers', 3), 5)`。`G4F_PROVIDERS` 目前有 3 个（`Yqcloud`、`OperaAria`、`PollinationsAI`），实际最大线程数不超过 3。

- **SECRET_KEY 持久化风险**：若 `SECRET_KEY` 环境变量未设置，每次服务重启都会生成新的随机密钥，导致所有已登录用户的 session 失效。生产环境必须在 `app.yaml` 的 `env_variables` 中固定设置 `SECRET_KEY`，本地开发必须在 `.env` 文件中设置。

- **Firebase 凭据惰性解析**：`credentials.ApplicationDefault()` 的构造函数不立即验证凭据，凭据解析在 `firestore.client()` 时才真正触发。因此 `auth/db.py` 必须优先检测 `firebase-key.json` 文件，而不能依赖 `ApplicationDefault()` 构造函数的异常作为本地开发的 fallback 信号。

## 10. 🧭 EXTENSION & MODIFICATION GUIDE (VERY IMPORTANT)

### 🟢 安全区（Safe Zones）：如何安全地添加新 LLM 通道

若需要引入新的 g4f 支持的 Provider，只需修改 `main.py` 的初始化部分：

1. 先运行 `availability_g4f/find_providers_models.py` 扫描当前 g4f 库中 `working=True` 且 `needs_auth=False` 的 Provider，再更新 `availability_g4f/test_providers.py` 加入候选条目，运行后取成功的 Provider。（参考：PollinationsAI `openai-fast` 于 2026-06-28 经第三轮测试验证通过）
2. 将验证通过的 Provider 追加进 `G4F_PROVIDERS` 列表中。
3. 在 `PROVIDER_MODELS_MAP` 中添加对应的模型数组（第一个为默认模型）。
4. 在 `ROUTE_PROMPTS_MAP` 中为 `(provider_name, model)` 元组添加隐形风格提示词（可选）。
5. 在 `PEER_REVIEW_PROMPTS_MAP` 中为该 Provider 使用的模型名称添加互评裁判提示词（可选；缺失时使用默认值 `'请评估以下回答的质量，指出优点与不足。'`）。新增条目必须要求模型输出 `{"score": int, "comment": str}` JSON 格式，否则 `parse_peer_review_json` 将 fallback 为 80 分。
6. 前端具备完全动态的联动机制，无需修改任何 HTML/JS 代码。

### 🟢 安全区：如何安全地添加新页面

添加新页面时，若该页面可能作为重定向的目标（如 `flash()` + `redirect()` 之后），必须在该页面加入以下 Flash 消息显示代码块，防止消息堆积：

```jinja2
{% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
        {% for category, message in messages %}
            <div class="flash flash-{{ category }}">{{ message }}</div>
        {% endfor %}
    {% endif %}
{% endwith %}
```

### 🔴 危险区（Danger Zones）：严禁触碰的逻辑

- 不要修改 `test_g4f_provider` 的返回值字典结构（7-key 契约）。`compare_providers` 在其外层追加 `peer_reviews`，不属于此契约范围，但同样不可删除。
- 不要修改互评 DTO 的 key 集合（`reviewer_provider`、`reviewer_model`、`score`、`comment`）。前端 `renderPeerReviews` 直接读取 `r.score` 和 `r.comment`，任何 key 变更将导致互评面板渲染空白。
- 不要移除根路由 `/` 中的 `provider_models_json` 注入。它是前端模型联动过滤机制的唯一数据源。
- 不要在 session 中同时设置 `user_id` 和 `is_guest`。两个键必须互斥。任何改变身份状态的路由都必须在写入新键的同时清除旧键。
- 不要在 auth 路由中直接调用 CRUD 函数而不先检查 `FIREBASE_AVAILABLE`。若 Firebase 未初始化，`db` 对象为 `None`，直接调用会触发 `AttributeError`。
- 不要修改 `GET /home` 路由的行为（即不要让它清除 `user_id`）。该路由专为"返回欢迎页"设计，已登录用户误触不应导致退出登录。
- 不要将 `run_peer_review` 的返回结构改回含 `response_time` 字段。互评阶段不计入前端展示的耗时统计，两者混用会使前端数据语义混乱。

## 11. 🛠️ BUILD, RUN & TEST COMMANDS

### 本地开发环境搭建

```bash
# 1. 创建并激活虚拟环境（首次）
python3 -m venv env
source env/bin/activate          # Linux/macOS
# env\Scripts\activate           # Windows

# 2. 安装依赖（严格锁版本）
pip install -r requirements.txt
```

### 本地运行前置条件

启动前必须确认以下三项：

1. `firebase-key.json` 存在于项目根目录（从 Firebase Console 下载服务账号密钥）。
2. `.env` 文件存在于项目根目录，包含持久化的 `SECRET_KEY`（格式：`SECRET_KEY="your-fixed-key"`）。
3. `firebase-admin==7.4.0` 和 `python-dotenv==1.2.2` 已安装（包含在 `requirements.txt` 中）。

### 本地运行

```bash
# 直接用 Flask 开发服务器启动（默认端口 8080）
python main.py

# 指定端口
PORT=5000 python main.py

# 用 gunicorn 模拟 GAE 运行环境（需额外安装 gunicorn）
pip install gunicorn
gunicorn -b :8080 main:app
```

启动后访问 `http://localhost:8080`（未认证时显示欢迎页），`http://localhost:8080/health` 验证服务状态。

### 自动化测试框架

项目使用 Python 内置的 `unittest` 框架，测试文件存放于 `tests/` 目录。

- **test_main_whitebox.py**：直接测试 `main.py` 的核心内部函数。测试内容包括模型降级规则 A/B/C 的全部边界条件、结果字典的 key 完整性、`test_g4f_provider` 的成功路径与异常路径、`detect_and_truncate` 的句级与滑动窗口重复检测、`ROUTE_PROMPTS_MAP` 路由注入行为、`parse_peer_review_json` 的 JSON 解析/夹值/容错 fallback 全部边界条件，以及 `run_peer_review` 的 429 重试逻辑（`TestRunPeerReview`，5 个用例：429 触发重试且成功、queue-full 同样触发重试、非 429 不重试、重试后仍失败返回友好消息、结果 4-key 契约始终完整）。
- **test_main_blackbox.py**：通过 Flask `test_client()` 以 HTTP 协议测试 `main.py` 暴露的全部对外 API 端点，包含互评触发条件、`peer_reviews` 字段存在性、互评 DTO key 集合（`{reviewer_provider, reviewer_model, score, comment}`）以及 `score` 类型为 `int` 的断言。新增对 `/health` 接口的 `routing_rules_loaded` 和 `peer_review_rules_loaded` 两个字段的存在性与类型断言；新增对 `/api/test-single` 的 `ROUTE_PROMPTS_MAP` 隐形路由生效验证（检查 g4f 实际收到的 prompt 含 style suffix）以及 `detect_and_truncate` 被调用验证。
- **test_main_graybox.py**：感知后端全局状态与线程池行为。测试内容包括排序契约、fallback 路径、`max_workers` 双重上限、`G4F_AVAILABLE` 降级响应，以及三 Provider 场景下 1 个失败时互评仅在 2 个成功者之间双向触发的行为（`TestPeerReviewPartialFailure`）。排序测试（`TestSortOrderInvariant`）在 setUp 中 mock `run_peer_review`，避免 2 个成功者触发真实网络调用。新增 `TestPeerReviewPhaseRobustness`（3 个用例）：通过令 `PEER_REVIEW_PROMPTS_MAP.get` 在任务构建阶段抛出 `RuntimeError`，模拟互评阶段整体崩溃，断言接口仍返回 200、第一轮结果完整、`peer_reviews` 字段为空列表。新增 `TestTestSingleRobustness`（1 个用例）：令 `test_g4f_provider` 直接抛出异常以触发 `test_single_provider` 外层 except，断言返回 500 中文友好消息。新增 `TestHealthConfigFlags`（2 个用例）：分别将 `ROUTE_PROMPTS_MAP` 和 `PEER_REVIEW_PROMPTS_MAP` patch 为空字典，断言 `/health` 对应标志字段为 `False`。
- **test_auth_whitebox.py**：使用 `unittest.mock.patch` 绕过真实 Firebase 网络连接。测试内容包括 Werkzeug 密码哈希生成与校验，以及 `get_user_by_username` 和 `get_user_by_email` 两个函数在"用户存在"和"用户不存在"两个分支下的行为。
- **test_auth_blackbox.py**：通过 Flask `test_client()` 测试 auth 蓝图的全部路由。测试内容包括登录成功后 `session['user_id']` 的写入与重定向、注册成功后 `session['is_guest']` 的清除、`/profile` 路由的未登录重定向以及已登录用户的正常访问。

```bash
# 发现并运行 tests/ 目录下的全部测试（共 189 个用例，5 个测试文件）
python -m unittest discover -s tests

# 带详细输出运行
python -m unittest discover -s tests -v

# 单独运行 main.py 相关测试
python -m unittest tests.test_main_whitebox
python -m unittest tests.test_main_blackbox
python -m unittest tests.test_main_graybox

# 单独运行 auth 模块相关测试
python -m unittest tests.test_auth_whitebox
python -m unittest tests.test_auth_blackbox
```

### 快速冒烟测试（手动 curl）

```bash
# 健康检查
curl http://localhost:8080/health

# 获取 Provider 列表
curl http://localhost:8080/api/providers

# 单 Provider 测试
curl -X POST http://localhost:8080/api/test-single \
  -H "Content-Type: application/json" \
  -d '{"prompt": "What is 2+2?", "provider": "Yqcloud"}'

# 多 Provider 对比
curl -X POST http://localhost:8080/api/compare \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Hello", "providers": ["Yqcloud", "OperaAria"]}'

# 建立游客 session（需 Cookie 支持）
curl -X POST http://localhost:8080/api/auth/guest
```

### Provider 可用性探测脚本（g4f 库升级后使用）

```bash
cd availability_g4f

# 第一步：扫描 g4f 所有 working Provider
python find_providers_models.py
# 结果写入 ../available_providers_models.txt

# 第二步：对候选 Provider 发真实请求验证可用性
python test_providers.py
# 结果写入 ../provider_test_results_v2.txt
```

### 依赖管理

- `requirements.txt` 采用完全锁版本策略（`pip freeze` 输出格式），所有间接依赖均固定。
- 更新依赖时：在虚拟环境中 `pip install <package>`，然后 `pip freeze > requirements.txt`。
- 不要手动编辑 `requirements.txt` 中的版本号，避免依赖冲突。
- 新增依赖后，必须同步检查 `app.yaml` 的 runtime 是否支持该依赖。

### 部署到 Google App Engine

```bash
# 需已安装并配置 Google Cloud SDK (gcloud)
gcloud app deploy app.yaml

# 查看实时日志
gcloud app logs tail -s default
```

- GAE 使用 `app.yaml` 中的 `entrypoint: gunicorn -b :$PORT main:app` 启动服务。
- Runtime 为 `python312`，自动缩放 1 到 10 个实例，CPU 目标利用率 60%。
- `SECRET_KEY` 必须在 `app.yaml` 的 `env_variables` 块中设置，GAE 环境无 `.env` 文件。
- `firebase-key.json` 不部署到 GAE，GAE 使用 Application Default Credentials 自动获取凭据。

---

## 12. ✏️ CODE STYLE & CONVENTIONS

### Python 代码规范

- **命名约定**：
  - 全局常量：`UPPER_SNAKE_CASE`（如 `G4F_AVAILABLE`、`FIREBASE_AVAILABLE`、`PROVIDER_MODELS_MAP`）
  - 函数/变量：`lower_snake_case`（如 `test_g4f_provider`、`provider_name`）
  - Flask 路由函数名与路径语义对齐（如 `compare_providers` 对应 `/api/compare`，`guest_login` 对应 `/api/auth/guest`）

- **日志规范**：
  - 使用模块级 `logger = logging.getLogger(__name__)`，不使用 `print`。
  - `INFO` 记录正常流程节点（如请求开始、Provider 完成、Firebase 初始化成功）。
  - `ERROR` 用于异常，必须带 `exc_info=True` 以打印完整堆栈。
  - 日志消息中截断长字符串：`prompt[:50]`，避免日志膨胀。

- **错误处理模式**：
  - LLM 路由函数顶层用 `try/except Exception` 兜底，返回标准 JSON 错误体 `{'error': '...'}` 和对应 HTTP 状态码。
  - auth 路由函数顶层用 `try/except Exception` 兜底，通过 `flash()` 反馈错误，`render_template` 返回页面。
  - `test_g4f_provider` 内部用 `try/except/finally`，在 `finally` 中计算 `response_time`，确保耗时字段始终有值。
  - Firebase 初始化用两层 except（`ImportError` 和 `Exception`）分别处理库缺失和初始化异常。

- **结果字典结构不变原则**：`test_g4f_provider` 返回的字典 key 集合（`provider`、`success`、`response`、`error`、`response_time`、`model`、`type`）为前后端契约，严禁增删 key。

### JavaScript / 前端规范

- 使用 Vanilla JS，不引入任何前端框架或构建工具。
- 前端通过 Jinja2 变量 `{{ provider_models_json | tojson }}` 接收后端数据，在页面初始化时解析为 JS 对象。
- Fetch 请求必须先检查 `response.ok` 再调用 `response.json()`。非 2xx 响应时先尝试解析 JSON `error` 字段，解析失败则回退到 `Server error: 状态码`。
- 游客切换相关的 Fetch（`/api/auth/guest`）必须处理网络错误，在按钮上显示 Loading 状态并在失败时恢复原始文案。

### 提交规范

- 提交信息用中文或英文均可，项目历史两者混用。
- 保持原子提交：一次提交只做一件事。

## 13. 🧠 MEMORY ANCHORS (FOR CLAUDE CODE)

- **LLM 核心调用链路（两阶段）**：`index.html (Form Submit)` + `POST /api/compare` + `ThreadPoolExecutor[1]` + `test_g4f_provider()` + `g4f.ChatCompletion.create()` → 收集结果 → `ThreadPoolExecutor[2]` + `run_peer_review()` + `parse_peer_review_json()` → 挂载 `peer_reviews`。
- **认证核心调用链路**：`home.html (Login/Register/Guest)` + `auth Blueprint` + `Firebase Firestore` + `session 写入` + `redirect url_for('index')`。
- **身份状态入口**：根路由 `/` 的 `index()` 函数是唯一的身份状态路由器，所有重定向最终都回到这里。
- **关键不变量 1**：LLM 对比结果的排序始终是"成功在前，耗时短在前"。
- **关键不变量 2**：`session['user_id']` 和 `session['is_guest']` 永远不同时存在。
- **关键不变量 3**：所有作为 Flash + redirect 目标的页面，必须包含 Flash 消息显示区，否则会产生消息堆积 Bug。
- **关键不变量 4**：`test_g4f_provider` 的返回值严格为 7-key 契约；`peer_reviews` 字段由 `compare_providers` 在外层追加，使最终 result 共有 8 个 key；`run_peer_review` 的返回值严格为 `{reviewer_provider, reviewer_model, score, comment}` 4-key 契约。
- **互评触发条件**：`tested >= 2` 且 `success >= 2`，缺一不触发。只有成功者相互点评，失败者不参与任何方向。
- **`parse_peer_review_json` 容错行为**：任何解析失败（格式错误、缺失 score、异常）均返回 `(80, raw_text)`，不抛出异常，接口永不因互评解析失败而崩溃。
- **核心文件**：LLM 后端入口为 `main.py`，认证后端为 `auth/routes.py` 和 `auth/db.py`，三个前端入口分别为 `templates/home.html`（未认证）、`templates/index.html`（已认证或游客）、`templates/auth/` 目录（认证流程页）。
- **自适应策略**：指定模型不匹配时，自动降级为映射表中的第一个模型，最终兜底为 `"gpt-3.5-turbo"`。
- **Firebase 初始化顺序**：key 文件优先于 ADC，不能反转，因为 ADC 的构造函数不立即抛出异常。
