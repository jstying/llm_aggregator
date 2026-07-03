# claude.md

## 1. 🧠 SYSTEM OVERVIEW (Cognitive Summary)

这是一个基于 Flask 框架开发的大语言模型（LLM）聚合与性能对比 Web 应用程序。该系统允许用户输入提示词，同时或单独调用不同的 g4f Provider，并实时对比响应内容和响应时间。系统现已集成基于 Firebase 的完整用户认证模块，支持三种访问身份：匿名访客、游客用户和已登录用户。系统采用后端路由结合前端单页异步交互（AJAX/Fetch）的架构，认证子系统以 Flask Blueprint 形式解耦挂载。已登录用户的对话历史持久化功能已于 2026-07-02 全栈落地：数据层（`history` 集合 CRUD）位于 `auth/db.py`，HTTP 路由（`/api/history` 系列 + `/api/compare` 自动保存）位于 `main.py`，前端 ChatGPT/Claude 风格左侧边栏（Recents 时间分组列表 + 悬浮操作图标 + 骨架屏 + 移动端抽屉式导航）位于 `templates/index.html`；游客对话历史仅存于前端内存不落库，侧边栏对游客/未登录用户展示"登录以保存历史"提示而非发起请求。侧边栏交互层已于 2026-07-02 同日进一步升级为乐观更新（Optimistic Updates）模型：pin/rename/delete 操作点击后立即更新 DOM，后台异步请求失败时精确回滚并弹出 Toast 提示；重命名由原生 `prompt()` 弹窗改为原地 `<input>` 编辑（编辑图标或双击标题触发，Enter 提交/Escape 取消）；游客的增删改置顶操作全部改为对 `window.guestHistory` 内存数组的真实读写并驱动同一套渲染逻辑，完整模拟已登录体验且零网络请求。

## 2. 🧬 ARCHITECTURE MAP (MOST IMPORTANT SECTION)

系统由三个核心子系统构成：Flask 后端服务、Firebase 认证模块、以及 HTML5/JavaScript 前端交互界面。

### 后端服务（Flask，main.py）

- **路由层**：提供页面渲染路由（`/`、`/home`）、LLM API 接口（`/api/providers`、`/api/compare`、`/api/test-single`）、认证 API（`/api/auth/guest`），以及对话历史 API（`/api/history` 系列，2026-07-02 新增，仅已登录用户可访问）。
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
│   ├── db.py                        # Firebase 初始化、FIREBASE_AVAILABLE 标志、4 个用户 CRUD 函数、5 个对话历史 CRUD 函数 [MODIFIED]
│   └── routes.py                    # /login, /register, /logout, /profile 路由实现
├── templates/
│   ├── home.html                    # 欢迎页：未认证且非游客的唯一纯净入口 [NEW]
│   ├── index.html                   # LLM 聚合功能主页（含三态导航栏、Flash 消息区、左侧 Recents 历史侧边栏）[MODIFIED]
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
  - `_get_authenticated_user_id()`（2026-07-02 新增）：对话历史路由专用守卫辅助函数，返回 `(user_id, None)` 或 `(None, (jsonify错误体, 401))`。未登录（含游客，即只有 `is_guest` 无 `user_id`）一律判定为未认证。4 个对话历史路由（`get_history`、`update_history_title`、`delete_history`、`toggle_history_pin`）均在函数体首行调用它并在拿到非 `None` 的错误响应时立即 `return err_response`。
  - `get_history()`（`GET /api/history`）：`page`/`limit` 查询参数经 `request.args.get(..., type=int)` 解析，非数字值自动回退为默认值（Flask 内置行为）；`page` 夹到 `>= 1`，`limit` 夹到 `[1, 100]`，换算 `offset = (page - 1) * limit` 后调用 `get_chat_history_list`。
  - `update_history_title()`（`PATCH /api/history/<history_id>/title`）：从请求体读取 `new_title` 并 `strip()`，为空返回 400；调用 `update_chat_history_title(session['user_id'], history_id, new_title)`，返回 `False` 时统一按 404 处理（不区分"不属于该用户"和"Firebase 不可用"两种原因，详见 `auth/db.py` 对话历史 CRUD 函数契约的设计说明）。
  - `delete_history()`（`DELETE /api/history/<history_id>`）：调用 `delete_chat_history`，`False` 同样统一按 404 处理。
  - `toggle_history_pin()`（`POST /api/history/<history_id>/toggle-pin`）：调用 `toggle_pin_chat_history`，返回值为 `None` 时视为 404；返回 `True`/`False`（翻转后的新状态）时均视为成功，包装为 `{'is_pinned': <bool>}` 返回 200——**必须用 `is None` 判断失败，不能用 `not new_pinned`，否则翻转到 `False` 的成功结果会被误判为失败**。
  - `compare_providers()` 新增逻辑（2026-07-02）：排序完成后、`jsonify` 之前，若 `session.get('user_id')` 存在，调用 `save_chat_history(session['user_id'], prompt, results)` 并将返回的 `id` 写入响应体的 `history_id` 字段；此调用被独立 `try/except Exception` 包裹（`logger.error` 记录，不重新抛出），持久化失败不影响本次对比结果的正常返回。游客与匿名请求不触发保存，响应体 `history_id` 固定为 `None`。`history_id` 是 `/api/compare` 响应体新增的顶层字段，不属于 8-key result DTO 契约的一部分。
  - `determine_actual_model(provider_name, requested_model)`: 纯函数，封装规则 A/B/C 的模型决策逻辑。
  - `init_result_object(provider_name, model)`: 纯函数，统一初始化标准 Result 字典（7 个 key）。
  - `detect_and_truncate(text)`: 纯函数，句级 + 滑动窗口重复检测，触发时截断并追加提示语（英文：`... (truncated automatically due to repeated content)`；敏感词命中时返回英文拦截提示 `Content contains sensitive information and has been blocked.`，2026-07-02 起两条提示语均已由中文改为美式英文）。
  - `parse_peer_review_json(text)`: 纯函数，从互评响应文本中提取 JSON，返回 `(score: int, comment: str)`；任何解析失败均容错返回 `(80, raw_text)`；score 被夹入 [1, 100]。
  - `test_g4f_provider()`: 核心 LLM 测试函数，调用上述辅助函数、应用隐形 Prompt 路由（`ROUTE_PROMPTS_MAP`）、将响应经 `detect_and_truncate` 处理后写入 result、统计响应耗时。异常分支（2026-07-03 新增内容策略判定）：优先检查 `CONTENT_POLICY_ERROR_KEYWORDS`（命中时返回 `"This provider's content filter blocked the response. Try rephrasing your prompt."`），未命中再检查 `NETWORK_ERROR_KEYWORDS`（命中时返回原有"系统正忙"英文提示），两者均未命中则原样返回 `str(e)`。**判定顺序不可颠倒**：内容策略类错误文本（如 Azure OpenAI 的 `content management policy`/`content_filter`）有时会与网络类关键词同时出现在同一条错误信息里，必须优先判定内容策略——重试对内容策略拦截无意义，若被误判成"系统正忙"会诱导用户重试一个注定再次被拦截的相同请求。
  - `test_single_provider()`: `/api/test-single` 路由处理函数，直接调用 `test_g4f_provider`，因此隐形 Prompt 路由、重复截断、内容策略友好提示与 `/api/compare` 完全同路径生效。外层 `except Exception` 返回英文友好 500 消息（`Service temporarily unavailable. Please try again later.`，2026-07-02 起由中文改为英文），不暴露原始异常。
  - `run_peer_review(reviewer_provider, reviewer_model, review_prompt)`: 单次互评请求，内置 429/queue-full 错误单次重试（检测到 `'429'` 或 `'queue'` 关键词时等待 2+random(0,1)s 后重试一次）。内部 `g4f.ChatCompletion.create` 的 advisory `timeout` 已于 2026-07-03 从 `15` 提升到 `25`（原因见下方"互评超时调优"条目）。重试耗尽或非 429/queue 异常直接跳出重试循环后，异常文案判定顺序与 `test_g4f_provider` 一致：先查 `CONTENT_POLICY_ERROR_KEYWORDS`（命中返回 `"This provider's content filter blocked the review. Try rephrasing your prompt."`），未命中再查 `PEER_REVIEW_NETWORK_ERROR_KEYWORDS`（即 `NETWORK_ERROR_KEYWORDS` 追加 `'429'`、`'queue'` 两项，命中触发英文友好消息 `The system is busy and trying to reconnect. Please try again shortly.`，2026-07-02 起由中文"系统正忙"改为英文），两者均未命中则返回 `f'Review failed: {str(last_exc)}'`（原中文"点评失败："前缀同步改为英文）。成功路径内部调用 `parse_peer_review_json` 解析响应，返回 `{reviewer_provider, reviewer_model, score, comment}`，不含 `response_time`。
  - `compare_providers()`: 两阶段并发执行。第一阶段 `ThreadPoolExecutor` 并发测试各 Provider，except 块区分 `TimeoutError`（`logger.warning` 无 traceback）和其他异常（`logger.error` 带 `exc_info`）；`TimeoutError` 分支的 fallback 友好消息同样是上述英文版"系统正忙"文案。第二阶段整体包裹在独立 `try-except` 中：任何崩溃（任务构建失败、Executor 异常等）均被捕获并记 `logger.error`，第一轮结果（含 `peer_reviews: []`）仍可安全返回，接口不会因互评失败而报 500。第二阶段内部每个 `future.result(timeout=32)`（2026-07-03 由 `25` 提升，见下方"互评超时调优"条目）也有独立 except，单条互评的 `TimeoutError` 单独 `logger.warning`，其他异常 `logger.error`。互评任务开始、每条完成（含裁判名、被评 Provider 名、score）、阶段整体完成均有 `logger.info`。最终按成功状态和耗时排序。外层 `except Exception` 的 500 响应返回英文友好消息（同 `test_single_provider`），不暴露原始异常信息给用户。
  - `NETWORK_ERROR_KEYWORDS` / `PEER_REVIEW_NETWORK_ERROR_KEYWORDS` / `CONTENT_POLICY_ERROR_KEYWORDS`（2026-07-03 新增，模块级常量，均在 g4f import try/except 块之外定义，与 `G4F_AVAILABLE` 无关）：前两者驱动"系统正忙"友好文案的关键词匹配（`PEER_REVIEW_NETWORK_ERROR_KEYWORDS = NETWORK_ERROR_KEYWORDS + ['429', 'queue']`，专供 `run_peer_review` 重试耗尽后的兜底判定使用，`test_g4f_provider` 只用不含 429/queue 的基础版本，因为它没有重试逻辑、无需与"是否值得重试"的语义挂钩）；后者驱动内容策略友好文案的关键词匹配（`'content management policy'`、`'content_filter'`、`'content filtering polic'`、`'response was filtered'`、`'responsible ai'`），命中时说明是 Provider 底层供应商（如 Azure OpenAI）自身的内容审查拦截了响应，与网络问题无关、重试无意义，因此 `test_g4f_provider` 和 `run_peer_review` 的异常分支都必须先判定内容策略关键词，再判定网络关键词。
  - `PEER_REVIEW_PROMPTS_MAP`: 互评裁判提示词表，key 为模型名称，value 为要求模型输出 `{"score": int, "comment": str}` JSON 格式的提示词前缀。2026-07-02 起表内 `gpt-4`/`gpt-3.5-turbo`/`aria` 三条提示词已由中文改写为英文（`openai-fast` 原本即为英文，未变动），语义与角色设定不变，仅语言变更。
  - `ROUTE_PROMPTS_MAP`: 隐形 Prompt 路由表，key 为 `(provider_name, model)` 元组，value 为追加到用户 prompt 尾部的风格提示词。**设计约束**：首句必须含"立刻/immediately"urgency 指令（防超时）；其次凸显各模型真实角色个性：gpt-4 扮演"严谨分析师"（结论→依据→反思三层结构，300 字），gpt-3.5-turbo 扮演"高效助手"（TLDR 一句话结论优先，口语化语气，150 字），aria 扮演"实战顾问"（跳过铺垫直接给 1-2 个可操作动作，200 字），openai-fast 扮演"极速速答者"（一句结论+一句理由，英文输出，100 字内）。新增条目须同时满足速度指令和角色鲜明两项要求，字数上限不得超过 300 字。2026-07-02 起 `gpt-4`/`gpt-3.5-turbo`/`aria` 三条提示词已由中文改写为英文（`openai-fast` 原本即为英文，未变动），角色设定、字数上限、urgency 指令均保持不变，仅语言变更；今后新增条目也应统一使用英文撰写。
  - `SENSITIVE_KEYWORDS`: 模块级敏感词列表，当前为空列表占位，可直接填充关键词生效，`detect_and_truncate` 读取此变量。命中后返回的拦截提示语已改为英文，见上方 `detect_and_truncate` 条目。
- **depends_on**: `flask`, `g4f`, `concurrent.futures`, `time`, `json`, `logging`, `re`, `os`, `secrets`, `random`, `dotenv`, `auth.auth_bp`, `auth.db`（`save_chat_history`、`get_chat_history_list`、`delete_chat_history`、`update_chat_history_title`、`toggle_pin_chat_history`，2026-07-02 新增）
- **affects**: `home.html`、`index.html`（通过 Jinja2 注入变量），所有前端 API 请求。

### `auth/__init__.py` [NEW]

- **role**: 定义 `auth_bp = Blueprint('auth', __name__)`，并通过 `from . import routes` 触发路由注册。

### `auth/db.py` [MODIFIED]

- **role**: Firebase 适配层，负责初始化 Firebase Admin SDK 并暴露用户 CRUD 函数和对话历史 CRUD 函数。
- **key logic**:
  - 初始化策略：若项目根目录存在 `firebase-key.json`，优先使用它（本地开发）；否则使用 `ApplicationDefault()`（GAE 环境）。`ApplicationDefault()` 的凭据解析是惰性的，它在 `firestore.client()` 时才真正触发，因此必须优先检查 key 文件，不能依赖其构造函数的异常来做 fallback。
  - `FIREBASE_AVAILABLE`：模块级布尔标志。初始化成功为 `True`，任何异常（包括 `ImportError`）均设为 `False`。
  - 4 个用户 CRUD 函数：`get_user_by_username`、`get_user_by_email`、`create_user`、`get_user_by_id`。这 4 个函数内部不检查 `FIREBASE_AVAILABLE`（无守卫），仅在 `FIREBASE_AVAILABLE` 为 `True` 时被调用，调用方（`auth/routes.py`）负责守卫。
  - 5 个对话历史 CRUD 函数（`history` 集合，2026-07-02 新增）：`save_chat_history`、`get_chat_history_list`、`delete_chat_history`、`update_chat_history_title`、`toggle_pin_chat_history`。与用户 CRUD 函数不同，这 5 个函数**各自在函数体首行内部检查 `if not FIREBASE_AVAILABLE`**，不可用时直接返回安全的 fallback 值（`None`/`False`/`[]`，具体见下方"对话历史 CRUD 函数契约"）并 `logger.warning`，不依赖调用方守卫。这是有意的双重防护设计，因为这批函数未来可能被 `main.py`（LLM 路由）而非仅 `auth/routes.py` 调用，不能假设所有调用方都会先检查标志位。
  - `save_chat_history`、`delete_chat_history`、`update_chat_history_title`、`toggle_pin_chat_history` 四者均以 `user_id` 为参数，且后三者在执行写操作前先 `.get()` 读取文档并校验 `doc.to_dict().get('user_id') == user_id`，不匹配或文档不存在时拒绝操作并返回 fallback 值，防止用户 A 通过猜测/伪造 `history_id` 删除或修改用户 B 的历史记录。**路由层（`main.py`，2026-07-02 落地）未重复做归属校验（数据层已保证），但每个路由都先校验 `session['user_id']` 存在（即用户已登录，非游客）——两层校验合起来才是完整防线：路由层挡住未登录/游客，数据层挡住越权操作他人记录。**
  - **`pinned_at` 字段与 Pin 排序修复（2026-07-02 用户报告，仿照 Gemini 网页版逻辑）**：`toggle_pin_chat_history` 翻转 `is_pinned` 时，同步写入/清除 `pinned_at` —— 置顶（`new_pinned=True`）时设为 `firestore.SERVER_TIMESTAMP`，取消置顶（`new_pinned=False`）时用 `firestore.DELETE_FIELD` 整体删除该字段（不是设为 `None`，删除是为了让下次重新置顶时拿到全新时间戳，而不是残留一个陈旧值）。`pinned_at` 记录的是"这次置顶操作发生的时刻"，与 `created_at`（对话创建时刻）语义完全不同：`get_chat_history_list` 的 `sort_key` 已改为在置顶分组内按 `pinned_at` **升序**（最早被置顶的排最前）排序，而非像之前那样仍沿用 `created_at` 降序——旧逻辑会导致"先置顶的对话如果创建时间较早，会被后置顶但创建时间较新的对话顶到上面"，即用户报告的"第二个 pin 的顶替了第一个 pin 的位置"这一 Bug；修复后第一个被置顶的对话会稳定停留在置顶区最上方，后续每次新置顶的对话依次排在它下面。未置顶分组的排序（`created_at` 降序）不受影响。`sort_key` 返回 `(0, pinned_at_timestamp)` 给置顶项、`(1, -created_at_timestamp)` 给未置顶项，元组第一位保证置顶分组整体排在未置顶分组之前，第二位再分别按各自规则细排。`save_chat_history` 新建文档时不写入 `pinned_at`（缺省即未置顶），`get_chat_history_list` 读到缺失该字段的置顶项（理论上不该出现，但作为防御性兜底）会退化为时间戳 `0`，排最前而不会抛异常。
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
  - **两栏应用布局（`.app-layout`，2026-07-02 新增）**：`<nav>` 与可选的 `.guest-banner` 之后，页面主体由 `.app-layout`（`display:flex`）包裹，左侧 `<aside class="left-sidebar">`（固定 260px、深色 `#171717` 背景、ChatGPT/Claude 风格）+ 右侧 `.main-content`（`flex:1`，内部仍是原有的 `.container`，表单/结果/互评面板等既有功能未改动）。`.left-sidebar` 与 `.main-content` 是 `.app-layout` 的两个 flex 子项，`align-items:stretch` 让两者互相匹配高度——但这**只保证两者彼此相等**，不保证达到视口高度：结果区为空、页面本身内容矮于视口时，两者会一起矮下去，侧边栏的深色背景就会在视口底部之前截断，露出下方的浅色页面背景（2026-07-02 用户报告的 Bug，已修复）。修复方案是让 `body { display:flex; flex-direction:column; min-height:calc(100vh / var(--page-zoom)); }`，再给 `.app-layout { flex:1; }`，使其吃掉 body 除 `<nav>`/`.guest-banner` 之外的全部剩余高度，即便结果区为空也有一个视口高度的"地板"可以撑满；提交 Prompt 后结果区变高，`.app-layout` 的高度会自然随内容超过这个地板值继续增长，无需任何 JS 干预。**`--page-zoom`（`:root` 上定义，当前值 `0.8`，对应 `body { zoom: var(--page-zoom); }` 这个全屏 80% 缩放的开发者自定义项）是这里必须用 `calc(100vh / var(--page-zoom))` 而非直接 `100vh` 的原因**：`vh` 单位始终按真实浏览器视口计算，不会随 `zoom` 一起放大，所以一个 `min-height:100vh` 的盒子套上 `zoom:0.8` 后，视觉上只会占到 `100vh × 0.8` 的高度，视口底部仍会露出 20% 的空白，和 flex 本身是否生效无关；预先把 `min-height` 放大 `1/0.8` 倍，缩放渲染后才会刚好还原成完整的 `100vh`。若以后调整 `zoom` 的数值，必须只改 `--page-zoom` 这一处，`zoom` 声明和 `min-height` 的 `calc` 会自动保持同步，不能各自硬编码。
  - **侧边栏结构**：`.sidebar-top`（"+ New Chat" 按钮，点击时触发 `clearBtn` 的既有清空逻辑并在移动端顺带关闭抽屉）→ `.sidebar-section-label`（"Recents" 静态标题）→ `#sidebarRecents`（动态区域，初始渲染 `#sidebarSkeleton` 骨架屏占位，JS 加载完成后整体替换为分组列表 / 空态提示）。
  - **`.sidebar-skeleton` 骨架屏**：4 条 `.skeleton-line`（末条 `.short` 宽度 60% 制造错落感），通过 CSS `linear-gradient` + `background-position` 动画（`@keyframes skeleton-loading`，1.4s 循环）模拟加载中的呼吸光效；`loadSidebarHistory()` 完成后会用 `recents.innerHTML = ...` 整体覆盖，骨架屏随之消失，不存在骨架屏卡死不消失的情况（无论请求成功、失败还是未登录都会走到覆盖分支）。
  - **Recents 时间分组**：不再展示条目下方的小字时间戳，改为按 `formatGroupLabel(created_at)` 计算出的分组名（`Today`/`Yesterday`/`Previous 7 Days`/`Older`，顺序固定，无数据的分组不渲染空标题）包裹一组 `.history-item`；`groupHistoryByDate(items)` 负责分桶。`created_at` 是后端 `/api/history` 返回的 Firestore Timestamp，经 Flask 默认 JSON encoder 序列化为 ISO 字符串，前端用 `new Date(...)` 解析；解析失败（`isNaN`）时归入 `Older`，不抛异常。
  - **条目行 Hover 交互 + Pin 的持久图标（2026-07-02 仿照 Gemini 网页版重构）**：`.history-item` 内部现在是"标题 + `.history-item-right`"两栏布局，`.history-item-right` 包裹两类图标：(1) `.history-item-actions`（重命名、删除按钮，未置顶时还包含"去 Pin"按钮）——沿用原有 hover-only 行为，默认 `opacity:0` 且 `translateX(4px)` 轻微右偏，`:hover`/`:focus-within` 时平滑淡入并归位；(2) `.pin-indicator`——**只在条目已置顶时渲染**，是一个独立于 `.history-item-actions` 之外、`opacity:1` 恒定可见的按钮（品牌绿 `#06c167`，沿用 `.pin-btn.is-pinned` 配色），固定停在行的最右侧，不需要 hover 才能看到，再次点击它即可取消置顶（`data-action="pin"`，走同一个 `toggleHistoryPin` 处理函数）。`renderHistoryItem(item)` 据此二选一渲染：`isPinned` 为真时只渲染 `.pin-indicator`（不渲染 hover-only 的 Pin 按钮，避免同一条目出现两个可点击的置顶控件）；为假时只在 `.history-item-actions` 内渲染 hover-only 的 Pin 按钮。键盘用户 Tab 到 `.history-item-actions` 内的按钮时 `:focus-within` 同样会显现，不依赖鼠标；`.pin-indicator` 本就恒定可见，不受此规则影响。
  - **Bug 修复：Pin 和 Unpin 没有图标区分（2026-07-02 用户报告，仿照 Gemini 网页版逻辑）**：修复前，Pin 按钮无论置顶与否都待在同一个 hover-only 的 `.history-item-actions` 组里，颜色靠 `.is-pinned` class 区分，但**必须先 hover 才能看到**——也就是说一个已置顶的条目在鼠标移开后，肉眼完全看不出它被置顶过，"图标区分"形同虚设。修复为上一条描述的持久 `.pin-indicator`：已置顶条目的绿色大头针图标脱离 hover 分组、常驻显示在行最右侧，未置顶条目则仍是 hover 才出现的灰色大头针图标，两种状态在不 hover 的情况下也能一眼区分。
  - **`currentHistoryItems`（2026-07-02 步骤四新增）**：侧边栏当前渲染内容的唯一数据源（一个 JS 数组）。已登录时是 `loadSidebarHistory()` 从 `/api/history` 拉取的那一页数据；游客时**直接等于 `window.guestHistory` 的数组引用本身**（不是拷贝），因此对 `currentHistoryItems` 里条目对象的原地修改（`item.is_pinned = ...`、`item.title = ...`）本身就是"保存"到 `window.guestHistory`，无需额外同步步骤。`renderCurrentHistory()` 是唯一的渲染入口：先用 `sortHistoryItems(currentHistoryItems)`（2026-07-02 新增，见下方 Bug 说明）**返回一份新数组**——置顶项整体排在未置顶项之前，置顶组内部按 `pinned_at` **升序**（最早置顶的排最前）、未置顶组内部按 `created_at` 降序，与后端 `get_chat_history_list` 排序契约保持一致——再按 `groupHistoryByDate(...)` 分组后整体覆盖 `#sidebarRecents.innerHTML`；`sortHistoryItems` 绝不原地修改 `currentHistoryItems`/`window.guestHistory` 本身（这两者的顺序必须保持插入顺序，供分页/回滚逻辑使用原始 index）。当 `currentHistoryItems` 为空且 `isLoggedIn === false` 时特殊渲染"Log in to save and view your conversation history."提示（游客一旦有条目就正常渲染列表，不再恒为登录提示）。
  - **Bug 修复：Pin 后条目未移动到 Recents 顶部，且第二个 Pin 会顶替第一个（2026-07-02 用户报告）**：这实际是两层问题的叠加。第一层（初次修复）：`toggleHistoryPin(id)` 原实现只翻转 `item.is_pinned` 再调用 `renderCurrentHistory()`，但渲染函数当时直接对 `currentHistoryItems` 做 `groupHistoryByDate`，同桶内保持数组原有顺序，从不因 pin 状态变化重排——条目图标切换了但位置没变。第二层（本轮修复，用户进一步指出）：即使加上了排序，`sortHistoryItems` 最初仍是按 `is_pinned` 降序 + 全局统一按 `created_at` 降序排的，这意味着"哪个条目排在置顶区最上面"取决于**对话创建时间**而非**置顶操作发生的时间**——先置顶一个较早创建的对话，再置顶一个创建时间更新的对话，后者会因为 `created_at` 更大而排到前者上面，表现为"第二个 Pin 顶替了第一个 Pin 的位置"。修复方式见上方 `pinned_at` 字段说明：`toggleHistoryPin` 置顶时在本地记录 `item.pinned_at = new Date().toISOString()`（客户端时间戳，纯粹用于本次会话内的相对排序；取消置顶时清为 `null`），`sortHistoryItems` 的置顶组改按 `pinned_at` 升序排序，与后端 `auth/db.py` 的 `toggle_pin_chat_history`/`get_chat_history_list` 保持同一套"先置顶的稳定在上、后置顶的排在下面"的语义。
  - **Bug 修复：点击 Pin 后条目快速闪烁两下（2026-07-02 用户报告）**：`toggleHistoryPin` 的乐观更新会立即 `renderCurrentHistory()` 一次（整体替换 `#sidebarRecents.innerHTML`，重建所有 DOM 节点）；修复前，`/api/history/<id>/toggle-pin` 请求成功返回后，`.then()` 回调里只要 `data.is_pinned` 是布尔值就**无条件**再 `renderCurrentHistory()` 一次——而这次网络请求通常在几十毫秒内就返回，且 `data.is_pinned` 几乎总是和刚才乐观设置的值相同，等于短时间内把整个列表的 DOM 完全重建了两遍。每次重建后，鼠标下的新按钮节点要等浏览器重新计算 `:hover` 状态、`.history-item-actions` 的 `opacity 0.15s` 过渡才会重新淡入，两次重建各触发一次这个淡入过程，肉眼看就是"闪烁两下"。修复为只有当 `data.is_pinned !== item.is_pinned`（服务端返回的真实状态与本地乐观值确实不一致，例如另一个标签页并发操作了同一条目）时才触发这次确认性重渲染，绝大多数情况下点击一次 Pin 只会重渲染一次。
  - **游客对话历史前端模拟（`window.guestHistory`，2026-07-02 步骤四新增）**：声明于顶层脚本、挂在 `window` 上（`window.guestHistory = window.guestHistory || []`）。`POST /api/compare` 成功且响应体 `history_id` 为空（即当前是游客）时，前端在 `.unshift()` 一条本地构造的记录（`id: 'guest-' + Date.now() + '-' + 随机后缀`、`title: prompt.length > 15 ? prompt.slice(0,15)+'...' : prompt`（2026-07-02 修复，见下方 Bug 说明）、`created_at: new Date().toISOString()`、`is_pinned: false`）后调用 `loadSidebarHistory()`，模拟"新对话立刻出现在 Recents 顶部"的已登录体验。游客的增删改查全部只操作这一内存数组，**从不触碰任何 `/api/history*` 端点**——`toggleHistoryPin`/`deleteHistoryItem`/`commitRename` 内部均以 `if (!isLoggedIn) return;` 在乐观更新之后短路，跳过网络请求分支（乐观更新本身对游客而言就是最终状态，无需回滚）。关闭标签页或刷新页面后 `window.guestHistory` 丢失，这是有意为之，与"游客数据不持久化"的身份不变量一致。
  - **Bug 修复：短 prompt 的标题被无意义地追加省略号（2026-07-02 用户报告）**：原逻辑无条件 `prompt.slice(0, 15) + '...'`，导致像 `"hi"` 这样明显短于 15 字符的 prompt 也会被显示成 `"hi..."`，暗示内容被截断，实际并未截断。修复为 `prompt.length > 15 ? prompt.slice(0, 15) + '...' : prompt`，只有真正超长时才追加省略号；同一规则的后端实现见 `auth/db.py` 的 `save_chat_history`。
  - **乐观更新（Optimistic Updates，2026-07-02 步骤四新增）**：`toggleHistoryPin(id)`、`deleteHistoryItem(id)`、`commitRename(id, newTitle)` 三者统一遵循"先改 `currentHistoryItems` 里的对象/数组 + 立即 `renderCurrentHistory()`，再异步发请求"的模式——用户点击后 DOM 瞬间更新，不等网络往返。已登录用户请求失败（`!response.ok` 或 `fetch` 抛异常）时，各自把改动的字段/条目**精确回滚**到操作前的值（pin 回滚 `is_pinned`，delete 用 `splice(index, 0, removedItem)` 插回原位置而非追加到末尾，rename 回滚 `item.title`），再 `renderCurrentHistory()` 复原 DOM，并调用 `showHistoryErrorToast(message)` 弹出一条 3 秒后自动消失的红色提示条（`.history-error-toast`，`opacity` 过渡，`z-index:400` 高于侧边栏抽屉的 300）。`toggleHistoryPin` 成功后额外用服务端返回的 `data.is_pinned` 覆盖本地值并重渲染，防止并发场景下客户端乐观值与服务端真实状态不一致。游客分支（`!isLoggedIn`）没有网络请求，因此也没有失败/回滚路径——本地写入即成功。
  - **原地重命名（`startInlineRename`，2026-07-02 步骤四起取代原 `prompt()` 弹窗方案）**：点击 ✏️ 图标或双击 `.history-item-title` 均触发 `startInlineRename(itemEl, id)`，把该行的 `<span class="history-item-title">` 就地替换为 `<input class="history-rename-input">`（浅色配色、品牌绿边框，2026-07-02 由深色配色改为浅色，见下方 Bug 说明）并自动 `focus()` + `select()` 全选原文字。
  - **Bug 修复：重命名输入框文字不可读（2026-07-02 用户报告）**：`.history-rename-input` 最初写成深色主题（`color:#ececec` 浅灰文字 + `background:#262626` 深色底），意图匹配侧边栏深色背景。但全局 `input:focus, textarea:focus` 规则（本文件 CSS 靠后位置）会把 `background-color` 强制改为 `#ffffff`，且该规则的选择器特异性（类型选择器 `input` + 伪类 `:focus` = (0,1,1)）高于仅有单个类选择器的 `.history-rename-input`（(0,1,0)），因此每次 `startInlineRename` 调用 `input.focus()`（几乎总是立即触发，因为聚焦发生在元素刚创建之后）后，背景立刻被抢占为白色，而文字仍是浅灰 `#ececec`，浅灰字几乎糊在白底上不可读。修复为 `.history-rename-input` 直接改用浅色配色（`color:#1a1a1a` 深色文字 + `background:#ffffff` 白底），并新增 `.history-rename-input:focus` 规则显式锁定同样的配色——它的特异性（类选择器 + 伪类 = (0,2,0)）无条件高于全局 `input:focus` 的 (0,1,1)，因此无论声明顺序如何都稳赢，聚焦态和非聚焦态文字颜色保持一致可读。`Enter` 触发 `input.blur()` 间接提交，`blur` 事件本身绑定 `commit`（新标题为空或与原标题相同时视为放弃，直接 `renderCurrentHistory()` 还原，不发请求）；`Escape` 走独立的 `cancel()` 路径，不提交也不触发 `blur` 的 commit（用一个 `settled` 布尔位在两条路径间互斥，防止 Escape 后又被随之而来的 blur 事件重复提交一次）。真正的提交逻辑在 `commitRename(id, newTitle)` 中，遵循与 pin/delete 相同的"本地先改、失败再回滚"乐观更新模式。
  - **事件委托而非内联 `onclick`（2026-07-02 步骤四重构）**：`renderHistoryItem(item)` 生成的按钮不再带 `onclick="...('${id}')"` 内联属性（旧写法在标题含单引号/双引号时有转义风险，且不支持双击标题触发重命名），改为纯 `data-action="pin|rename|delete"` 属性；`#sidebarRecents` 上各绑定一个委托的 `click`（读 `e.target.closest('[data-action]')` 分发到三个处理函数，未命中 `[data-action]` 时改为下方 `loadHistorySnapshot` 分支，2026-07-03 新增）和 `dblclick`（读 `e.target.closest('.history-item-title')` 触发重命名）监听器，只挂载一次，不随每次 `renderCurrentHistory()` 重新绑定，因为委托监听器挂在容器 `#sidebarRecents` 本身（该节点不会被 `innerHTML` 替换整体移除）而非条目节点上。
  - **`loadHistorySnapshot(id)`（2026-07-03 新增，点击 Recents 条目加载历史快照）**：`#sidebarRecents` 的委托 `click` 监听器里，若点击目标不落在 `[data-action]`（固定/重命名/删除图标）也不落在 `.history-rename-input`（重命名中的输入框，避免点击光标定位被误判为加载）上，则视为点击了条目本身，调用 `loadHistorySnapshot(itemEl.dataset.id)`。该函数从 `currentHistoryItems` 里按 `id` 找到对应条目，把 `#prompt` 文本框整体替换为 `item.prompt`（该条目当初提交时的原始完整提示词，与可能已被 `commitRename` 修改过的 `item.title` 是两个独立字段），并把 `item.results`（`/api/compare` 保存时的完整 8-key result 数组快照，含 `peer_reviews`）通过 `displayResults({ results, total_providers: results.length, successful_providers: results.filter(r => r.success).length })` 整体渲染进结果区——**完全替换**掉当前屏幕上任何正在显示的对比结果，不是追加或合并；`total_providers`/`successful_providers` 两个字段在 Firestore `history` 文档里从未存储（`save_chat_history` 只存 `results`），因此在前端按当前快照的 `results` 数组重新计算，而非直接从历史文档读取。若该条目的 `results` 为空数组（正常流程下不应发生——每次保存都带完整结果列表，但作为防御性分支处理），则隐藏结果区并清空 `#resultsContainer`/`#stats`，而不是让上一次对比的结果继续挂在屏幕上、被误认为属于当前点击的这条历史。移动端点击后额外调用 `closeSidebar()` 收起抽屉，与 "+ New Chat" 按钮的既有行为一致。`.history-item` 的 `cursor` 同步由 `default` 改为 `pointer`，提示整行可点击。游客点击走同一套逻辑，因为 `currentHistoryItems` 就是 `window.guestHistory` 本身，无需网络请求。**已知的与双击重命名的交互**：浏览器在派发 `dblclick` 之前必然先派发两次 `click`，因此双击标题触发重命名之前，`loadHistorySnapshot` 会先被调用（最多）两次——这是无害的（只是把当前条目的快照重复加载了一次/两次，随后立刻进入重命名编辑态，不影响侧边栏 DOM），不视为 Bug。
  - **删除确认改为自定义居中模态框（`showDeleteConfirmModal(entryTitle)`，2026-07-03 新增，纯前端）**：`deleteHistoryItem(id)` 原先直接调用浏览器原生 `confirm('Delete this conversation?')`——一个无法定制样式、且不点名具体是哪条记录的蓝白系统弹窗。现改为 `async function deleteHistoryItem(id)`，先从 `currentHistoryItems` 找到目标条目，`await showDeleteConfirmModal(targetItem.title)` 拿到用户的选择（`Promise<boolean>`），`false` 时直接 `return`（不做任何后续的乐观删除/请求逻辑）。`showDeleteConfirmModal` 惰性创建并复用一个 `#confirmModalOverlay`（`.confirm-modal-overlay`，`position:fixed;inset:0`，半透明黑色背景 `rgba(0,0,0,0.55)`，`display:flex` 居中，`z-index:500`——高于历史操作失败的 `.history-error-toast`（`z-index:400`）和移动端侧边栏抽屉（`z-index:300`），确保不会被二者遮挡）+ 内部 `.confirm-modal` 对话框（深色 `#171717` 背景、`#ececec` 文字，配色与 `.left-sidebar` 一致），结构固定为：标题 `Delete this chat?` → 正文 `This will delete `+`<strong>`包裹的条目标题（`textContent` 赋值，不用 `innerHTML`，条目标题即使含 `<script>`/引号等字符也不会被解析为标签或破坏结构；标题为空字符串时兜底显示 `this conversation`）→ 底部 Cancel（`.confirm-modal-btn-cancel`，透明背景+浅色描边，样式与 `.new-chat-btn` 同源）和 Delete（`.confirm-modal-btn-delete`，纯色红 `#d93b3b`，为侧边栏在深色主题下少数的破坏性操作强调色，与 `.history-error-toast` 的红色系 `rgba(255,100,100,...)` 呼应但不完全相同，因为一个是纯色实心按钮、一个是暗底浅字提示条）两个按钮。函数返回一个 `Promise<boolean>`：点击 Delete → `resolve(true)`；点击 Cancel、点击遮罩背景（`e.target === overlay`，区别于点击对话框本身冒泡到 overlay 但 `target` 是内部元素的情况）、或按下 `Escape` 键（监听在 `document` 上，因为焦点可能不在对话框内部）→ 均 `resolve(false)`；四条路径共享同一个 `cleanup(result)` 收尾函数，负责隐藏遮罩（移除 `.visible` class）、解绑全部四个监听器（避免每次调用都在 `document` 上残留一个新的 `keydown` 监听器）、`resolve`。对话框打开时把焦点设到 Delete 按钮上（`deleteBtn.focus()`），便于键盘用户直接按 Enter 确认或 Tab 到 Cancel。`deleteHistoryItem` 之后的乐观删除 + 回滚逻辑（`splice`/`showHistoryErrorToast`）完全未变，只是从"同步执行、由 `confirm()` 挡在最前面"改为"由 `await` 挡在最前面"，游客分支（`!isLoggedIn` 时 `return`，无网络请求）同样未变。
  - **`clearBtn` 清空时重置 Model 下拉为"使用 Provider 默认模型"（2026-07-03 新增，纯前端）**：`clearBtn` 的 `click` 监听器原本只清空结果区、Prompt 输入框、Provider 勾选框，不触碰 Model 下拉——而 `updateModelDropdown()` 内部有"尽量保留刷新前已选中项"的逻辑（`if (availableModels.has(currentSelectedValue) && currentSelectedValue !== '')`），由于清空 Provider 勾选后 `selectedProviders.length === 0` 会退化为"显示全部 Provider 的模型并集"，用户之前选中的具体模型（如 `gpt-4`）大概率仍在这个并集里，于是清空/新建对话后 Model 下拉会诡异地保留着上一次对话选的模型，而不是回到"使用 Provider 默认模型"这个符合"新对话"语义的初始状态。修复为在调用 `updateModelDropdown()` 之前先执行 `modelSelect.value = ''`：这样 `updateModelDropdown()` 内部读到的 `currentSelectedValue` 就是空字符串，天然走进 `else` 分支，把 `modelSelect.value` 和 `customTrigger.textContent` 都重置为默认态，不需要额外重复设置自定义下拉组件的显示文本。`newChatBtn`（"+ New Chat"）通过 `document.getElementById('clearBtn').click()` 触发同一个监听器，因此同步获得这个重置行为，无需单独处理。
  - **`deleteHistoryItem` 确认删除后联动重置界面（2026-07-03 新增，纯前端）**：用户反馈删除一条历史记录后，屏幕上如果正显示着某次对比的结果（无论是刚提交的还是通过 `loadHistorySnapshot` 加载出的历史快照），残留内容与"这条记录已经不存在了"的状态不符，容易造成困惑。修复为在 `showDeleteConfirmModal(...)` 返回 `true`（用户点击 Delete 确认）之后、原有的乐观删除 `splice`/`renderCurrentHistory()`/`fetch DELETE` 逻辑之前，插入一行 `document.getElementById('clearBtn').click()`——复用 `newChatBtn` 已经在用的"以编程方式点击 clearBtn"手法，等价于用户手动点了一次"Clear results / + New Chat"，隐藏结果区、清空 Prompt、取消所有 Provider 勾选、把 Model 下拉重置为"使用 Provider 默认模型"（含上一条新增的重置行为）。取消删除（点击 Cancel/背景/`Escape`）时函数已在 `if (!confirmed) return;` 提前返回，不会触发这次界面重置。原有的历史记录删除逻辑（本地 `splice` 乐观移除、`DELETE /api/history/<id>` 请求、失败时按原 index `splice` 回滚并 `showHistoryErrorToast`）完全未改动，这次重置只是在其之前追加的一个独立副作用。
  - **`loadSidebarHistory()`**：读取顶层常量 `isLoggedIn`（由 Jinja `{{ 'true' if session.user_id else 'false' }}` 注入，游客与匿名均为 `false`）。`false` 时将 `currentHistoryItems` 指向 `window.guestHistory` 并调用 `renderCurrentHistory()`，**不发起任何 `/api/history` 请求**（避免必然 401 的无意义网络调用，也避免控制台报错噪音）；`true` 时 `fetch('/api/history?page=1&limit=20')`，成功时把 `data.history` 赋给 `currentHistoryItems` 再渲染，失败或响应非 2xx 时渲染"Could not load history."兜底提示，不抛出未捕获异常。首次调用发生在脚本末尾（页面加载完立即执行一次）；此外 `/api/compare` 成功后，已登录用户在 `history_id` 非空时、游客在把新记录 `unshift` 进 `window.guestHistory` 后，都会再次调用它刷新 Recents。
  - **移动端抽屉式导航（`@media (max-width: 520px)`，复用既有断点）**：`.hamburger-btn`（三条 `<span>` 画的汉堡图标，桌面端 `display:none`，仅在该断点内 `display:flex`）新增于 `.nav-container` 左侧（包在新增的 `.nav-left` 容器内，与 `.nav-logo` 分组，避免破坏原有 `justify-content:space-between` 两端对齐布局）。该断点内 `.left-sidebar` 切换为 `position:fixed`（`top:0; left:0; height:100vh; z-index:300`）+ `transform:translateX(-100%)` 默认滑出屏幕外，JS 通过 `openSidebar()`/`closeSidebar()` 切换 `.sidebar-open` class（`transform:translateX(0)`，`transition:transform 0.25s ease`）实现平滑滑入滑出；`#sidebarOverlay`（`position:fixed;inset:0`，半透明黑色遮罩，`z-index:250` 低于侧边栏的 300）在该断点被点击时同步关闭侧边栏，桌面端遮罩恒为 `display:none`（无 `.visible` class 时）不占用交互层。桌面端（`>520px`）`.left-sidebar` 保持默认的 flex 静态子项，`.sidebar-open`/`.visible` 两个 class 即使被意外打上也不会有任何视觉效果（这两条规则只在移动端媒体查询内生效）。
  - 顶部新增三态导航栏：已登录时显示用户名、Profile、Logout；游客时显示 Guest Mode 徽章、Login、Register。
  - **移动端响应式导航栏**：`@media (max-width: 520px)` 将 Logo 从"LLM Aggregator"切换为"G4F"（通过 `.logo-full` / `.logo-short` 双 span 实现），隐藏 `.nav-welcome`，压缩 nav 间距与字号（`Guest Mode` 徽章、Login、Register 字号均缩至 `0.82rem`/`0.72rem`），确保导航栏在手机竖屏单行显示。`.guest-badge` 始终设置 `white-space: nowrap` 防止"Guest Mode"被截断为两行。
  - **移动端响应式页面标题**：`@media (max-width: 520px)` 将 `.header h1` 中"G4F LLM Aggregator"切换为"G4F"（通过 `.header-full` / `.header-short` 双 span 实现，手机端隐藏 `.header-full`，显示 `.header-short`）。
  - 游客状态下在导航栏下方显示黑色提示条（`.guest-banner`），引导注册或登录；文案 2026-07-02 起由中文改为英文（"You are currently browsing as a guest and your data will not be saved. Log in or sign up to save your history."），链接文案同步改为 "Log in" / "sign up"。
  - 已登录用户的 header 区域展示个性化欢迎语（`Welcome back, {{ session.username }}`）。
  - 新增 Flash 消息显示区（位于 `.container` 内、`.header` 之上），确保注册成功等提示在此处被立即消费。Flash 消息在渲染后 3 秒自动淡出消失（opacity 渐变 0.4s，淡出后从 DOM 移除，若 `.flash-messages` 容器变空则一并移除）。
  - `escapeHtml(str)`: provider 名、model 名、error 均通过此函数转义后注入 DOM，防止 XSS。`response` 经 `marked.parse()` 渲染为 Markdown HTML，不经过 `escapeHtml`（内容来自受信 LLM，Markdown 渲染为有意为之）。
  - `renderPeerReviews(reviews, uid)`: 将 `peer_reviews` 数组渲染为可折叠面板，展示"Blind review from [Provider] [N pts]: [comment]"（2026-07-02 起面板文案由中文"来自 [Provider] 的盲评 [N分]：[comment]"改为英文，折叠按钮文案同步由"查看其他 AI 盲评 (N)"改为"View other AI blind reviews (N)"）。面板默认折叠，通过 `togglePeerReview(uid)` 切换显示状态。
  - `displayResults(data)` 在每个成功结果的 `.provider-response` 下方附加互评面板；失败结果不展示互评。
  - **Provider 勾选框自绘实现**：`<input type="checkbox" class="provider-trigger">` 本身被完全隐藏（`position: absolute; width: 0; height: 0; opacity: 0`），因为 `<input>` 属于替换元素（replaced element），规范上不保证支持 `::before`/`::after`，早期直接在 `input:checked::after` 上画对勾在部分浏览器下会残缺（只剩一条边，视觉上像斜线）。视觉勾选框改由紧跟其后的 `<span class="checkbox-box">`（普通非替换元素）承载：18×18 正方形、`border-radius: 2px`，通过兄弟选择器 `.provider-trigger:checked + .checkbox-box` 联动状态；对勾本身是 `.checkbox-box::after` 用 `border-width: 0 3px 3px 0` + `rotate(45deg)` 画出的黑色（`#1a1a1a`）对勾，未选中时 `opacity: 0` 隐藏、选中时 `opacity: 1` 显示。
  - **Provider 勾选框选中态配色**：选中时 `.checkbox-box` 背景从白色变为品牌绿 `#06c167`（与边框同色）；同时整张 `.provider-checkbox` 卡片的边框也变为与 `:hover` 态相同的 `#06c167`，通过 JS 维护的 `.is-checked` class 实现（`.provider-checkbox.is-checked { border-color: #06c167 }`），未采用 CSS `:has()` 选择器以规避旧版浏览器兼容问题。`syncCardChecked(card)` 函数负责让该 class 与 `input.checked` 保持同步，在以下所有路径中都会被调用：卡片初始化时（含浏览器 bfcache 回退恢复已勾选状态的情况）、直接点击原生 input（含键盘 Tab+Space 触发）、点击卡片其他区域触发的手动 toggle、以及 Clear 按钮批量清空所有勾选时（`clearBtn` 监听器在 `providerTriggers.forEach(cb => cb.checked = false)` 之后必须紧跟 `providerCards.forEach(syncCardChecked)`，否则卡片边框会在清空后残留绿色）。
- **depends_on**: 后端路由 `/` 传来的 `providers`、`provider_models_json` 以及 `session` 全局对象（新增 `session.user_id` 用于注入 `isLoggedIn` JS 常量）；`/api/compare` 返回的 `peer_reviews` 字段与 `history_id` 字段（2026-07-02 新增）；`/api/history` 系列 4 个端点（`GET`/`PATCH .../title`/`DELETE`/`POST .../toggle-pin`，2026-07-02 新增）。

### `templates/auth/base.html` [NEW]

- **role**: 认证模块所有页面的通用布局基础模板。
- **key logic**: 导航栏根据 `session.user_id` 和 `session.is_guest` 进行三态切换：已登录显示 Profile + Logout；游客显示 Guest Mode 徽章 + Login + Register；未认证显示 Login + Register。Flash 消息（`.alert` 类）统一在 `.card` 容器顶部渲染，3 秒后自动淡出消失（opacity 渐变 0.4s，淡出后从 DOM 移除）。**移动端响应式导航栏**：`@media (max-width: 520px)` 压缩 nav-container padding、Logo 字号（`1.05rem`）、nav-links 间距与 a 标签字号（`0.82rem`）、`.guest-badge` 字号（`0.72rem`），与 `index.html` 保持一致。`.guest-badge` 始终设置 `white-space: nowrap` 防止"Guest Mode"换行。

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

### Firestore 对话历史文档结构（`history` 集合，2026-07-02 新增）

```python
{
    'user_id': str,           # 所属用户的 Firestore 文档 ID（users 集合），归属校验字段
    'title': str,              # save_chat_history 写入时固定为 prompt[:15] + ('...' if len(prompt) > 15 else '')（2026-07-02 修复：短 prompt 不再被强行加省略号），支持后续手动重命名
    'prompt': str,              # 原始完整提示词
    'results': list,            # /api/compare 返回的完整聚合结果列表，严格保持现有 8-key result DTO 契约不变（含 peer_reviews）
    'created_at': Timestamp,    # Firestore SERVER_TIMESTAMP
    'is_pinned': bool,          # 默认 False，由 toggle_pin_chat_history 翻转
    'pinned_at': Timestamp,      # 仅置顶项存在（2026-07-02 新增）；置顶时写入 SERVER_TIMESTAMP，取消置顶时整个字段被 DELETE_FIELD 删除；新建文档（save_chat_history）不写入此字段
}
```

文档 ID 由 Firestore 自动生成。**游客（`is_guest`）的对话历史不写入此集合**，仅在前端内存中维护，随会话结束丢失；只有 `session['user_id']` 存在的已登录用户的历史才持久化。

### 对话历史 CRUD 函数契约（`auth/db.py`，2026-07-02 新增）

| 函数 | 参数 | 成功返回 | Firebase 不可用 / 校验失败时返回 |
|---|---|---|---|
| `save_chat_history` | `user_id, prompt, results` | 含 `id` 键的完整历史 dict | `None`（仅 Firebase 不可用；无归属校验，因为是新建） |
| `get_chat_history_list` | `user_id, limit=20, offset=0` | 历史 dict 列表，排序规则：先按 `is_pinned` 分组（置顶在前），置顶组内按 `pinned_at` **升序**（最早置顶的排最前，2026-07-02 由 `created_at` 降序改为此规则，见下方 System Risks 条目），未置顶组内按 `created_at` 降序；每项含 `id` 键（排序与分页均在 Python 应用层完成，Firestore 查询本身只做单字段等值过滤，见下方 9. SYSTEM RISKS 的"复合索引"条目） | `[]` |
| `delete_chat_history` | `user_id, history_id` | `True` | `False` |
| `update_chat_history_title` | `user_id, history_id, new_title` | `True` | `False` |
| `toggle_pin_chat_history` | `user_id, history_id` | 翻转后的 `is_pinned` 布尔值（`True`/`False`）；同步写入/清除 `pinned_at`（置顶时 `SERVER_TIMESTAMP`，取消置顶时 `DELETE_FIELD`），但该字段本身不在返回值里，只影响 Firestore 文档 | `None` |

`delete_chat_history`、`update_chat_history_title`、`toggle_pin_chat_history` 三者返回 `False`/`None` 时无法区分"Firebase 不可用"和"文档不属于该用户/不存在"两种原因（均只 `logger.warning` 不抛异常），调用方若需要区分需自行先查询。这是有意简化：路由层目前只需要知道操作是否成功即可决定 HTTP 状态码/Flash 消息。

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
- `POST /api/compare`：接收 `prompt`、`providers`、`model`、`max_workers`，返回并发测试后的聚合排序结果；响应体新增顶层字段 `history_id`（2026-07-02 新增，已登录且保存成功时为字符串，游客/匿名/保存失败时为 `null`）。
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

**对话历史接口（2026-07-02 新增，均要求 `session['user_id']` 存在，否则 401）：**

- `GET /api/history?page=1&limit=20`：分页查询当前用户的历史记录，返回 `{"history": [...], "page": int, "limit": int}`；`limit` 上限 100，`page` 下限 1，非数字参数回退默认值。
- `PATCH /api/history/<history_id>/title`：接收 JSON `{"new_title": str}` 重命名；`new_title` 为空返回 400；目标不存在或不属于当前用户返回 404。
- `DELETE /api/history/<history_id>`：删除指定历史记录；不存在或不属于当前用户返回 404。
- `POST /api/history/<history_id>/toggle-pin`：翻转置顶状态，返回 `{"is_pinned": bool}`；不存在或不属于当前用户返回 404。

### 第三方集成

- **g4f 库**：通过模拟浏览器或逆向接口，无凭证调用各大免费 AI 渠道（如 `Yqcloud`、`OperaAria`、`PollinationsAI`）。
- **Firebase Admin SDK**：连接 Google Cloud Firestore，管理 `users` 集合的读写。本地开发使用 `firebase-key.json`，GAE 生产环境使用 Application Default Credentials（ADC）。

## 9. ⚠️ SYSTEM RISKS / CODE QUALITY AUDIT

- ~~**超时机制不一致**~~（**已修复**）：`future.result()` 的外层等待超时已从 `25` 秒调整为 `21` 秒，与内部 `g4f.ChatCompletion.create(timeout=20)` 保持一致，仅预留 1 秒线程调度缓冲。

- ~~**前端异常捕获漏洞**~~（**已修复**）：`index.html` 的 Fetch 处理逻辑已在调用 `response.json()` 前检查 `response.ok`。非 2xx 响应时先尝试解析 JSON `error` 字段，若 body 非 JSON 则回退到 `Server error: 状态码`，不再导致前端崩溃。

- ~~**异常回滚伪造**~~（**已修复**）：`compare_providers` 的 `except` 块已改为复用 `determine_actual_model()` 和 `init_result_object()` 两个辅助函数，模型决策规则与正常流程完全一致，key 集合严格统一。

- ~~**Flash 消息堆积 Bug**~~（**已修复**）：`home.html` 和 `index.html` 原本没有 Flash 消息显示区，导致退出登录等操作产生的 Flash 消息滞留 session，在下一个 auth 页面（使用 `auth/base.html`）上集中出现，引发"注册成功 + 已退出登录"同时显示的假象。两个文件均已加入 Flash 消息显示区。

- ~~**侧边栏空结果区时背景截断**~~（**已修复，2026-07-02 用户报告**）：`.app-layout` 原来只有 `display:flex; align-items:stretch;`，这只能让 `.left-sidebar` 与 `.main-content` 两者**互相**等高，不能让二者一起达到视口高度。结果区为空（未提交 Prompt）时 `.main-content` 只有表单那么高，`.left-sidebar` 的深色背景也就只延伸到表单底部，视口剩余部分露出页面浅色背景，视觉上呈现"黑色边栏被从下方截断"；提交 Prompt 出结果后 `.main-content` 变高，`.left-sidebar` 才跟着"延展"下去——这正是用户观察到的现象。修复为 `body` 改成 `display:flex; flex-direction:column;` 并给 `.app-layout` 加 `flex:1`，使其吃掉 `<nav>`/`.guest-banner` 之外的全部剩余高度，为空结果区场景提供一个视口高度的"地板"。由于 `body` 本身还叠了一个非标准的 `zoom: var(--page-zoom)`（当前 `0.8`，纯粹是页面自带的 80% 缩放效果，与本次 Bug 无关但会干扰修复），`min-height` 必须写成 `calc(100vh / var(--page-zoom))` 而不能直接 `100vh`，否则缩放后仍会在视口底部留出约 20% 的空白（`vh` 按真实视口计算、不随 `zoom`放大，这是两者叠加时的已知陷阱）。此问题不涉及任何 Python 代码，纯 CSS 修复；由于 jsdom 不做真实布局渲染（无法产出高度数值），本次改用真实 headless Chromium（Playwright）测量渲染后的像素高度做验证，详见第 11 节。

- ~~**`get_chat_history_list` 缺失 Firestore 复合索引导致 500**~~（**已修复，2026-07-02 生产事故**）：原实现对 `history` 集合链式调用 `.where('user_id', '==', ...).order_by('is_pinned', ...).order_by('created_at', ...)`。这是一个 Firestore 复合查询（一个等值过滤 + 两个排序字段），Firestore 要求为其**手动**在控制台创建复合索引——该索引不属于代码库的一部分，也不会随部署自动创建，每个 Firebase 项目（本地、GAE 生产等）都需要各自单独创建一次。索引缺失时 `query.stream()` 直接抛出 `google.api_core.exceptions.FailedPrecondition: 400 The query requires an index`，导致 `GET /api/history` 恒定返回 500（前端表现为 Recents 侧栏卡在"Could not load history."，且 `/api/compare` 成功保存后侧栏也不会刷新，因为刷新同样要走这条 500 的查询）。修复方式：`get_chat_history_list` 现在只对 Firestore 发起单字段等值查询（`.where('user_id', '==', user_id)`，Firestore 对每个字段都有自动单字段索引，无需额外配置），取回该用户的全部历史记录后，排序（`is_pinned` 降序、`created_at` 降序）和分页（`offset`/`limit` 切片）改为在 Python 应用层完成。这样彻底消除了对手动创建复合索引这一外部环境配置步骤的依赖，新 Firebase 项目开箱即可用。代价是每次调用都会取回该用户的全部历史文档而非只取一页，但单用户对话历史量级很小，可忽略。

- ~~**Recents 短 prompt 标题被强行加省略号**~~（**已修复，2026-07-02 用户报告**）：`save_chat_history`（`auth/db.py`）与前端游客模拟逻辑（`templates/index.html`）原先都无条件执行 `prompt[:15] + '...'` / `prompt.slice(0, 15) + '...'`，导致像 `"hi"` 这种远短于 15 字符的 prompt 也被显示成 `"hi..."`，误导用户以为标题被截断。两处均已改为条件判断：仅当 `len(prompt) > 15` 时才追加省略号，否则标题就是 prompt 原文。

- ~~**重命名输入框文字不可读（浅灰字叠白底）**~~（**已修复，2026-07-02 用户报告**）：`.history-rename-input` 最初设计为深色主题（`color:#ececec` + `background:#262626`），但全局 `input:focus` 规则的选择器特异性 `(0,1,1)` 高于仅有单一类选择器的 `.history-rename-input` 的 `(0,1,0)`，聚焦时（`startInlineRename` 创建后立即 `input.focus()`）背景被强制改为白色而文字仍是浅灰，导致输入框视觉上一片空白难以辨认文字。修复为直接改用浅色配色（`color:#1a1a1a` + `background:#ffffff`），并新增特异性 `(0,2,0)` 的 `.history-rename-input:focus` 规则显式锁定同样配色，确保稳赢全局 `input:focus`。

- ~~**Pin 置顶后条目未移动到 Recents 顶部**~~（**已修复，2026-07-02 用户报告，第一轮**）：`toggleHistoryPin` 只翻转 `item.is_pinned` 字段就调用 `renderCurrentHistory()`，但渲染函数直接对 `currentHistoryItems` 做 `groupHistoryByDate`——分组只按日期落桶，桶内顺序沿用数组原有顺序，从不因 `is_pinned` 变化而重排，因此图标切换成"已固定"样式后条目仍停在原位，用户观察到"闪烁两下但没真的置顶"。修复为新增 `sortHistoryItems(items)`，在分组前对**拷贝**按 `is_pinned` 降序重新排序，不改变 `currentHistoryItems`/`window.guestHistory` 自身的元素顺序（分页/回滚逻辑仍依赖原始 index）。

- ~~**第二个 Pin 会顶替第一个 Pin 的位置**~~（**已修复，2026-07-02 用户报告，第二轮**）：第一轮修复只解决了"置顶后完全不移动"的问题，但当时置顶组内部仍沿用未置顶组的排序字段 `created_at` 降序——这意味着置顶区内谁排最上面，取决于**对话创建时间**而不是**用户点击 Pin 的先后顺序**：先置顶一个较早创建的对话，再置顶一个创建时间更新的对话，后者会因为 `created_at` 更大反而排到前者上面，表现为"第二个 Pin 顶替了第一个"。修复为引入 `pinned_at` 字段（专门记录"这次置顶操作发生的时刻"，与 `created_at` 语义分离）：后端 `toggle_pin_chat_history` 置顶时写入 `firestore.SERVER_TIMESTAMP`、取消置顶时用 `firestore.DELETE_FIELD` 删除该字段；`get_chat_history_list` 的排序改为置顶组内按 `pinned_at` 升序（最早置顶的排最前）；前端 `toggleHistoryPin` 同步在本地记录 `item.pinned_at`（客户端时间戳，用于本次会话内的乐观排序），`sortHistoryItems` 同样按 `pinned_at` 升序排置顶组。此后第一个被置顶的对话会稳定停留在最上面，后续每次新置顶依次排在其下方，仿照 Gemini 网页版的置顶行为。

- ~~**Pin/Unpin 无图标区分，必须 hover 才能看出置顶状态**~~（**已修复，2026-07-02 用户报告，仿照 Gemini 网页版**）：修复前 Pin 按钮无论置顶与否都待在同一个 hover-only 的 `.history-item-actions` 组里，仅靠 `.is-pinned` class 变色区分——但整个组默认 `opacity:0`，鼠标移开后已置顶条目和未置顶条目在视觉上完全一样，"图标区分"形同虚设。修复为新增 `.pin-indicator`：已置顶条目改渲染一个独立于 hover 分组之外、`opacity:1` 恒定可见的绿色大头针按钮，固定停在行最右侧，不需要 hover 即可看到置顶状态，再次点击它触发取消置顶；未置顶条目保持原有的 hover-only 灰色 Pin 按钮。两种状态互斥渲染（`renderHistoryItem` 依据 `isPinned` 二选一），不会同一条目同时出现两个 Pin 控件。

- ~~**点击 Pin 后条目快速闪烁两下**~~（**已修复，2026-07-02 用户报告**）：`toggleHistoryPin` 的乐观更新会立即 `renderCurrentHistory()`（整体替换 `#sidebarRecents.innerHTML`，重建所有 DOM 节点）一次；修复前，`/api/history/<id>/toggle-pin` 请求成功返回后，`.then()` 回调只要 `data.is_pinned` 是布尔值就**无条件**再 `renderCurrentHistory()` 一次——该请求通常几十毫秒内就返回，且 `data.is_pinned` 几乎总是和刚设置的乐观值相同，等于短时间内把整个列表的 DOM 完全重建两遍，每次重建后 `.history-item-actions` 的 `opacity 0.15s` 过渡都要重新走一遍淡入动画，肉眼可见"闪烁两下"。修复为只有当 `data.is_pinned !== item.is_pinned`（服务端真实状态与本地乐观值确实不一致，例如另一个标签页并发操作了同一条目）时才触发这次确认性重渲染。

- ~~**点击 Recents 条目不会加载对应的历史快照**~~（**已修复，2026-07-03 用户报告**）：侧边栏 Recents 列表此前只支持 pin/rename/delete 三个悬浮图标的操作，点击条目本身（标题或行内空白处）没有绑定任何行为——用户点开一条历史记录，页面上的 Prompt 输入框和结果区完全没有反应，看起来像是"点了没用"。修复为在 `#sidebarRecents` 既有的委托 `click` 监听器里新增一个 fallback 分支：点击目标未命中 `[data-action]` 图标、也未命中重命名中的 `.history-rename-input` 时，调用新增的 `loadHistorySnapshot(id)`，把 `#prompt` 替换为该条目的原始 `prompt` 字段、把结果区整体替换为该条目保存时的 `results` 快照（通过 `displayResults()` 复用现有渲染逻辑，`total_providers`/`successful_providers` 在前端按快照的 `results.length`/成功数现算，因为 Firestore 历史文档从未存储这两个统计字段）。若该条目 `results` 为空数组，结果区被清空/隐藏而不是保留上一次对比的残留内容。详见第 4 节 `templates/index.html` 条目下的 `loadHistorySnapshot(id)` 说明。纯前端修复，未改动任何 Python 代码。

- ~~**互评阶段对 Yqcloud 等较慢 Provider 频繁误判超时**~~（**已修复，2026-07-03 用户报告**）：修复前 `run_peer_review` 内部 `g4f.ChatCompletion.create` 的 advisory `timeout=15`、外层 `future.result(timeout=25)`。互评 prompt 远长于普通请求（额外拼接了被评价者的完整回答文本），比首轮直接提问更容易超过 15s 的 advisory 上限；一旦 g4f 内部因 advisory 超时抛出异常，`run_peer_review` 只在命中 429/queue 关键词时才重试，纯粹的耗时超限不会重试，直接进入兜底文案分支或被外层 25s 硬截断打断，日志表现为 `Peer review for <provider> timed out after 25s`，用户报告 Yqcloud 经常在互评阶段拿不到分数/点评。修复为把内部 advisory 上调到 `timeout=25`、外层 `future.result` 同步上调到 `timeout=32`（约 7 秒调度缓冲）。**429 重试时序**同步重新核算：429 通常在 0.1s 内即返回，重试等待 2-3s，第二次尝试的 advisory 上限是新的 25s，两次尝试总耗时上限约 2-3s + 25s ≈ 28s，仍安全在新的 32s outer timeout 以内。纯 Python 改动，`TestPeerReviewOuterTimeoutValue`（`test_main_graybox.py`，2026-07-03 新增）直接 spy `concurrent.futures.Future.result` 断言互评阶段确实以 `timeout=32` 调用（且不再出现旧值 `25`），另配一个 `TimeoutError` 场景验证单条互评超时不影响其余结果的 200 返回。

- ~~**Provider 底层内容审查（如 Azure OpenAI 内容过滤）报出裸异常文本**~~（**已修复，2026-07-03 用户报告**）：修复前 `PollinationsAI`（`openai-fast`，底层走 Azure OpenAI）遇到内容审查拦截时，`g4f.ChatCompletion.create` 抛出的原始异常（`Error 400: ... azure-openai error: The response was filtered due to the prompt triggering Azure OpenAI's content management policy...`）未命中 `NETWORK_ERROR_KEYWORDS` 中任何关键词，落入 `else` 分支被原样透传给前端，用户直接看到一段带内部实现细节（Azure、BAD_REQUEST、文档链接）的裸英文报错，且这类错误重试没有意义（同一 prompt 大概率再次被拦截）。修复为新增模块级常量 `CONTENT_POLICY_ERROR_KEYWORDS`（`content management policy`、`content_filter`、`content filtering polic`、`response was filtered`、`responsible ai`），`test_g4f_provider` 和 `run_peer_review` 的异常分支均**优先**检查此列表（早于网络类关键词判定），命中时分别返回 `"This provider's content filter blocked the response. Try rephrasing your prompt."` / `"This provider's content filter blocked the review. Try rephrasing your prompt."`，不再暴露 Azure/g4f 的内部实现细节，也不再误导用户"重试"一个注定被拦截的请求。纯 Python 改动，白盒新增 4 个用例覆盖友好文案本身、内容策略关键词与网络关键词同时出现时的优先级判定、以及互评路径下重试逻辑不会因内容策略错误而被触发（详见 §11）。

- **互评阶段双层保护**：互评阶段采用两层独立 try-except。外层兜住整个阶段（任务构建崩溃、Executor 初始化失败等），内层兜住单条 `future.result(timeout=32)`（2026-07-03 由 `25` 上调，见上方"互评阶段对 Yqcloud 等较慢 Provider 频繁误判超时"条目）的超时或执行异常。任何一层失败均不影响第一轮 LLM 结果返回，`peer_reviews` 字段在互评 try 块之前已初始化为 `[]`。`run_peer_review` 内部 `g4f.ChatCompletion.create(timeout=25)`（2026-07-03 由 `15` 上调）为 advisory 超时（非硬截断）；外层硬截断留有约 7 秒调度缓冲。互评 prompt 远长于普通请求（含完整回答文本），若再次调整任一超时值须同步评估另一侧的缓冲余量。**429 重试时序**：429 通常在 0.1s 内即返回，重试等待 2-3s，第一个并发请求通常已完成，因此第二次尝试几乎不会再遇到队列满。两次尝试总耗时上限约 2-3s + 25s ≈ 28s，安全在新的 32s outer timeout 以内。

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
5. 在 `PEER_REVIEW_PROMPTS_MAP` 中为该 Provider 使用的模型名称添加互评裁判提示词（可选；缺失时使用默认值 `'Please evaluate the quality of the following answer, noting its strengths and weaknesses.'`，2026-07-02 起该默认值已由中文改为英文）。新增条目必须要求模型输出 `{"score": int, "comment": str}` JSON 格式，否则 `parse_peer_review_json` 将 fallback 为 80 分；新增条目也应使用英文撰写，与表内既有条目保持语言一致。
6. 前端具备完全动态的联动机制，无需修改任何 HTML/JS 代码。

### 🟢 已完成：对话历史 HTTP 路由（`main.py`，2026-07-02）

`auth/db.py` 的 5 个对话历史 CRUD 函数已全部由 `main.py` 的路由暴露：

| 路由 | 方法 | 函数 | 认证要求 |
|---|---|---|---|
| `/api/compare` | POST | `compare_providers`（已登录时自动附带保存） | 可选（游客/匿名不保存） |
| `/api/history` | GET | `get_history` | 必须 `session['user_id']` |
| `/api/history/<history_id>/title` | PATCH | `update_history_title` | 必须 `session['user_id']` |
| `/api/history/<history_id>` | DELETE | `delete_history` | 必须 `session['user_id']` |
| `/api/history/<history_id>/toggle-pin` | POST | `toggle_history_pin` | 必须 `session['user_id']` |

后续若要继续扩展这批路由（如批量删除、搜索历史），沿用以下已验证的模式：

1. 路由体首行调用 `_get_authenticated_user_id()`，拿到 `err_response` 非空立即 `return err_response`（401）；游客（只有 `is_guest`）会被这一层拦下，不会碰到 CRUD 函数。
2. 调用 `delete_chat_history`/`update_chat_history_title`/`toggle_pin_chat_history` 时，`user_id` 参数必须取自 `session['user_id']`，不能相信前端传来的 `user_id`（否则数据层的归属校验形同虚设）。
3. 这 5 个函数内部已各自守卫 `FIREBASE_AVAILABLE`，路由层不必重复检查，但仍需把 fallback 返回值（`None`/`False`/`[]`）转换为合适的 HTTP 状态码（当前统一转换为 404，理由见 `auth/db.py` 一节的契约表）。
4. **前端 UI 已实现（2026-07-02）**：`templates/index.html` 的左侧边栏 Recents 列表消费这 4 个端点，`/api/compare` 返回的 `history_id` 用于触发保存后即时刷新侧栏。详见下方 `templates/index.html` 条目的完整前端逻辑说明。

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
- 不要把 `test_g4f_provider`/`run_peer_review` 异常分支里 `CONTENT_POLICY_ERROR_KEYWORDS` 的判定顺序挪到 `NETWORK_ERROR_KEYWORDS`（或 `PEER_REVIEW_NETWORK_ERROR_KEYWORDS`）之后。Azure OpenAI 等 Provider 的内容审查报错文本有时会与网络类关键词同时出现在同一条错误信息里，内容策略必须优先判定——这类拦截重试没有意义，一旦被误判成"系统正忙"文案会诱导用户重试一个注定再次被拦截的相同请求（2026-07-03 用户报告的原始问题）。
- 不要把 `run_peer_review` 内部 `g4f.ChatCompletion.create` 的 advisory `timeout`（当前 `25`）和 `compare_providers` 第二阶段 `future.result` 的外层超时（当前 `32`）改成不同步的数值。外层必须始终留有约 7 秒的调度缓冲，且要同步核算 429 重试场景的总耗时上限（2-3s 等待 + 一次完整 advisory timeout）是否仍小于外层超时，否则会重新引入 2026-07-03 修复的"较慢 Provider 在互评阶段被误判超时"问题（`Peer review for <provider> timed out after Ns`）。
- 不要在 session 中同时设置 `user_id` 和 `is_guest`。两个键必须互斥。任何改变身份状态的路由都必须在写入新键的同时清除旧键。
- 不要在 auth 路由中直接调用 CRUD 函数而不先检查 `FIREBASE_AVAILABLE`。若 Firebase 未初始化，`db` 对象为 `None`，直接调用会触发 `AttributeError`。
- 不要修改 `GET /home` 路由的行为（即不要让它清除 `user_id`）。该路由专为"返回欢迎页"设计，已登录用户误触不应导致退出登录。
- 不要将 `run_peer_review` 的返回结构改回含 `response_time` 字段。互评阶段不计入前端展示的耗时统计，两者混用会使前端数据语义混乱。
- 不要移除 `delete_chat_history`/`update_chat_history_title`/`toggle_pin_chat_history` 内部的归属校验（`doc.to_dict().get('user_id') == user_id`）。这是防止跨用户操作他人对话历史的唯一防线，路由层不会重复校验。
- 不要让游客（`session['is_guest'] == True`）路径调用任何对话历史 CRUD 函数。游客数据不持久化是身份状态三层不变量的一部分。
- 不要在 `toggle_history_pin()` 中把 `toggle_pin_chat_history` 的返回值用 `if not new_pinned:` 判断失败。它的返回值是"翻转后的新状态"，`False` 是合法的成功结果（表示刚取消置顶），只有 `None` 才代表失败（不存在/不属于该用户/Firebase 不可用），必须用 `is None` 判断。
- 不要把 `history_id` 塞进 8-key result DTO（`test_g4f_provider` 返回值）或互评 4-key DTO 里。它是 `/api/compare` 响应体的顶层字段，与这两个既有契约无关。
- 不要让 `loadSidebarHistory()` 在 `isLoggedIn === false`（游客/匿名）时仍然调用 `fetch('/api/history...')`。后端会正确返回 401，但前端应主动跳过这次必然失败的请求——移除这个短路判断只会让游客控制台里多一条无意义的失败请求，不会造成安全问题，但会破坏"游客不触碰对话历史"这一体验层面的一致性。
- 不要移除 `.history-item-actions` 的 `:focus-within` 显现规则、只保留 `:hover`。键盘用户 Tab 到固定/重命名/删除按钮时若图标仍不可见，会显示一个看不见但可点击的"幽灵按钮"，破坏可访问性。
- 不要在游客分支（`!isLoggedIn`）把 `loadSidebarHistory()` 里的 `currentHistoryItems = window.guestHistory` 改成拷贝（如 `[...window.guestHistory]`）。乐观更新（pin/rename/delete）依赖 `currentHistoryItems` 与 `window.guestHistory` 是**同一个数组引用**，对条目对象的原地修改才能直接等价于"保存"；一旦拷贝，游客的操作会在下次 `loadSidebarHistory()` 时被真正的 `window.guestHistory` 覆盖丢失。
- 不要把 `toggleHistoryPin`/`deleteHistoryItem`/`commitRename` 里 "先本地改数据 + `renderCurrentHistory()`，再发请求" 的顺序颠倒成"先等待 fetch 成功再改 DOM"。这三个函数的乐观更新语义（点击后 UI 立即响应，失败才回滚）是 2026-07-02 步骤四的核心要求，颠倒顺序会让点击操作在网络慢时出现明显卡顿，且与 `showHistoryErrorToast` 的失败提示设计脱节（回滚逻辑假设 DOM 已经乐观更新过）。
- 不要恢复 `renameHistoryItem` 曾经使用的原生 `prompt()` 弹窗方案。`startInlineRename` 已在 2026-07-02 步骤四取代它，改为原地 `<input>` 编辑；混用两套机制会导致同一行同时存在旧的 `data-id` onclick 和新的 `data-action` 委托监听，重复触发或互相覆盖。
- 不要移除 `deleteHistoryItem` 失败回滚时的 `splice(index, 0, removedItem)` 精确插回位置，改成简单的 `push`/`unshift`。用户删除列表中间的条目失败后，该条目应该原地复位，而不是跳到列表首尾造成视觉跳动。
- 不要把 `body` 的 `min-height: calc(100vh / var(--page-zoom))` 改回 `min-height: 100vh`，也不要移除 `.app-layout { flex: 1; }` 或把 `body` 改回默认的块级布局（去掉 `display:flex; flex-direction:column;`）。三者任一被移除都会让结果区为空时 `.left-sidebar` 的深色背景在视口底部之前被截断（2026-07-02 用户报告的回归 Bug）。若以后要调整页面的 `zoom` 缩放比例，只改 `:root` 上的 `--page-zoom` 变量本身，不要绕开它直接给 `zoom` 或这里的 `calc` 硬编码新数值，否则两处会分叉。
- 不要在 `get_chat_history_list` 的 Firestore 查询上重新链式调用第二个 `.order_by()`（例如把 `is_pinned` 排序挪回 Firestore 端）。`.where()` + 两个 `.order_by()` 是复合查询，需要在每个 Firebase 项目的控制台里手动创建复合索引，缺失时会直接 500（`FAILED_PRECONDITION: The query requires an index`），2026-07-02 已在生产环境实际触发过一次。排序必须继续留在 Python 应用层完成。
- 不要移除 `renderCurrentHistory()` 里对 `sortHistoryItems(currentHistoryItems)` 的调用，也不要让它改成原地排序（`Array.prototype.sort` 直接作用于 `currentHistoryItems`/`window.guestHistory` 本身而非拷贝）。前者会让 pin/unpin 后条目不移动（2026-07-02 用户报告的回归 Bug，因为 `groupHistoryByDate` 只按日期分桶、桶内顺序完全依赖调用方传入数组的既有顺序）；后者会打乱 `deleteHistoryItem` 回滚时依赖的原始 index、以及 `window.guestHistory` 理应保持的插入顺序。
- 不要把 `save_chat_history`（`auth/db.py`）或前端游客模拟逻辑（`templates/index.html`）里的标题截断改回无条件 `prompt[:15] + '...'`。必须判断 `len(prompt) > 15`（前端对应 `prompt.length > 15`）才追加省略号，否则短 prompt（如 `"hi"`）会被误显示成加了省略号的样子，暗示了并不存在的截断（2026-07-02 用户报告的回归点）。
- 不要把 `.history-rename-input` 的配色改回深色文字配深色背景（或任何依赖全局 `input:focus` 规则自然生效的写法）而不同时显式声明 `.history-rename-input:focus`。全局 `input:focus` 会强制 `background-color:#ffffff`，其选择器特异性 `(0,1,1)` 高于单一类选择器 `(0,1,0)`；只有同时用 `.history-rename-input` 和 `.history-rename-input:focus` 两条规则显式锁定配色（特异性 `(0,2,0)`，稳赢 `input:focus`），聚焦态和非聚焦态才会保持一致可读，否则会复现 2026-07-02 用户报告的"文字看不清"回归。
- 不要把 `sortHistoryItems`（前端）或 `get_chat_history_list` 的 `sort_key`（后端，`auth/db.py`）里置顶组的排序字段从 `pinned_at` 改回 `created_at`。这是 2026-07-02 用户报告的第二次回归："第二个 Pin 顶替了第一个 Pin"——用 `created_at` 排置顶组会让排序结果取决于对话创建时间而不是置顶操作发生的时间，先置顶的条目可能被后置顶但创建时间更新的条目挤到下面。必须继续用 `pinned_at`（前端本地时间戳 + 后端 `firestore.SERVER_TIMESTAMP`）表示"这次置顶操作发生的时刻"，且是升序（最早置顶的在最上面）。
- 不要把已置顶条目的 Pin 图标重新塞回 `.history-item-actions`（hover-only 分组）里，或者反过来把未置顶条目的 Pin 按钮也做成常驻可见。已置顶条目必须用独立于 hover 分组之外、`opacity:1` 恒定可见的 `.pin-indicator`（2026-07-02 仿照 Gemini 网页版新增），未置顶条目必须保持 hover 才出现的灰色 Pin 按钮——这是"Pin/Unpin 图标要有区分"这一需求的核心，混在一起会导致置顶状态在不 hover 时又变得看不出来（复现用户报告的原始 Bug）。同一条目也不要同时渲染两个 Pin 控件（`renderHistoryItem` 里 `isPinned` 为真渲染 `.pin-indicator`、为假渲染 hover 分组内的 Pin 按钮，二选一，不可都渲染）。
- 不要恢复 `toggleHistoryPin` 的 `.then()` 回调里"只要 `data.is_pinned` 是布尔值就无条件 `renderCurrentHistory()`"的写法。必须加上 `data.is_pinned !== item.is_pinned` 的判断，只有服务端返回值和本地乐观值确实不同才二次渲染，否则每次点击 Pin 都会把整个 Recents 列表的 DOM 重建两遍，造成 2026-07-02 用户报告的"点击后条目快速闪烁两下"。
- 不要把 `loadHistorySnapshot(id)` 的结果渲染改成"追加/合并"到当前结果区，也不要在 `item.results` 为空数组时保留结果区原有内容不清空。用户点击一条历史记录，预期是页面完全切换成那条记录当时的快照（prompt + results），而不是新旧数据混在一起；空快照时清空/隐藏结果区是刻意设计，为的是不让"点了没反应"误判为"点了但结果没变"（2026-07-03 用户报告的原始 Bug 就是点击完全没反应）。
- 不要把 `#sidebarRecents` 委托 `click` 监听器里 `loadHistorySnapshot` 的 fallback 分支挪到 `[data-action]` 判断之前，或者反过来让它也响应对 `[data-action]` 图标、`.history-rename-input` 的点击。这三类点击目标必须严格互斥：pin/rename/delete 图标触发对应操作、重命名输入框内的点击不触发任何侧边栏级联行为（只是普通的光标定位/选中文字）、其余点击（含标题文字和行内空白）才加载快照。合并或颠倒判断顺序会导致点击图标时同时误触发快照加载，或者点击快照时误触发图标操作。
- 不要把 `deleteHistoryItem` 恢复成直接调用浏览器原生 `confirm()`。2026-07-03 起删除确认已改为自定义居中模态框 `showDeleteConfirmModal(entryTitle)`（见第 4 节 `templates/index.html` 条目），原生 `confirm()` 样式无法定制、且不会点名具体条目标题，与本项目其余弹层（历史操作失败 Toast、侧边栏抽屉）风格不一致。同理不要让 `showDeleteConfirmModal` 的 Delete 按钮改用 `innerHTML` 注入条目标题——必须继续用 `textContent`，否则标题中含 `<script>`/引号等字符的历史记录会造成 DOM 结构破坏或（在更宽松的场景下）XSS。
- 不要移除 `showDeleteConfirmModal` 里 Cancel 按钮、遮罩背景点击（`e.target === overlay`）、`Escape` 键三条等价的"取消"路径中的任何一条，也不要让它们在解绑监听器（`cleanup()`）之外各自重复一份收尾逻辑。四条收尾路径（Cancel/Delete/背景点击/Escape）必须共享同一个 `cleanup(result)`，否则容易出现某条路径忘记移除 `document` 上的 `keydown` 监听器，导致多次打开模态框后监听器越叠越多。
- 不要移除 `clearBtn` 监听器里 `updateModelDropdown()` 调用之前的 `modelSelect.value = '';`。这一行是 2026-07-03 新增的修复：`updateModelDropdown()` 有"尽量保留刷新前已选中模型"的逻辑，清空 Provider 勾选后它会退化成"显示全部 Provider 模型并集"，用户之前选中的具体模型大概率仍在并集里，若不先清空 `modelSelect.value`，清空/新建对话后 Model 下拉会诡异地保留上一次选择而不是回到"使用 Provider 默认模型"。也不要把这一行改成只重置 `customTrigger.textContent` 而不重置 `modelSelect.value` 本身（或反之）——两者必须一起交给 `updateModelDropdown()` 的 `else` 分支统一处理，单独改一个会让原生 `<select>` 的实际值和自定义下拉组件显示的文本不一致。
- 不要移除 `deleteHistoryItem` 里 `showDeleteConfirmModal(...)` 确认通过之后新增的 `document.getElementById('clearBtn').click()`。这是 2026-07-03 新增的行为：确认删除一条历史记录后，屏幕上任何正在显示的结果（当次提交的或 `loadHistorySnapshot` 加载的历史快照）都应联动清空，避免残留内容与"记录已删除"的状态矛盾。这一行必须放在 `if (!confirmed) return;` 之后（取消删除不应重置界面），且必须在原有的乐观删除 `splice`/`fetch DELETE`/失败回滚逻辑之前或独立于其后都可以，但不能删除这一行本身或把它替换成手写的、与 `clearBtn` 监听器逻辑不同步的重置代码——复用 `clearBtn.click()`（`newChatBtn` 已验证过的同款手法）能保证两处"清空界面"的语义永远一致，不会随 `clearBtn` 未来逻辑变化而分叉。

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

- **test_main_whitebox.py**：直接测试 `main.py` 的核心内部函数。测试内容包括模型降级规则 A/B/C 的全部边界条件、结果字典的 key 完整性、`test_g4f_provider` 的成功路径与异常路径、`detect_and_truncate` 的句级与滑动窗口重复检测、`ROUTE_PROMPTS_MAP` 路由注入行为、`parse_peer_review_json` 的 JSON 解析/夹值/容错 fallback 全部边界条件，以及 `run_peer_review` 的 429 重试逻辑（`TestRunPeerReview`，429 触发重试且成功、queue-full 同样触发重试、非 429 不重试、重试后仍失败返回友好消息、结果 4-key 契约始终完整）。`TestTestG4fProvider` 2026-07-03 新增 2 个用例：`test_content_policy_error_shows_friendly_message`（模拟 Azure OpenAI `content management policy` 拦截报错，断言 `result['error']` 含 `'content filter'` 友好文案而非原始异常文本）、`test_content_policy_error_takes_priority_over_network_wording`（异常文本同时含内容策略关键词与网络关键词 `'remote'`，断言最终文案是内容策略版本，验证判定顺序未颠倒）。`TestRunPeerReview` 2026-07-03 同步新增 2 个用例：`test_content_policy_error_does_not_retry_and_returns_friendly_comment`（断言内容策略错误只调用 1 次 `g4f.ChatCompletion.create`、不触发 429 重试逻辑、`comment` 含 `'content filter'` 友好文案）、`test_advisory_timeout_passed_to_g4f_is_25_seconds`（断言 `g4f.ChatCompletion.create` 实际收到的 `timeout` 关键字参数已从 `15` 上调为 `25`，锁定"互评超时调优"的具体数值不被意外改回）。
- **test_main_blackbox.py**：通过 Flask `test_client()` 以 HTTP 协议测试 `main.py` 暴露的全部对外 API 端点，包含互评触发条件、`peer_reviews` 字段存在性、互评 DTO key 集合（`{reviewer_provider, reviewer_model, score, comment}`）以及 `score` 类型为 `int` 的断言。新增对 `/health` 接口的 `routing_rules_loaded` 和 `peer_review_rules_loaded` 两个字段的存在性与类型断言；新增对 `/api/test-single` 的 `ROUTE_PROMPTS_MAP` 隐形路由生效验证（检查 g4f 实际收到的 prompt 含 style suffix）以及 `detect_and_truncate` 被调用验证。`TestPeerReview` 新增 `test_default_judge_prefix_used_when_model_not_in_peer_review_map`（2026-07-02 新增）：将 `PEER_REVIEW_PROMPTS_MAP` patch 为空字典，断言两个成功 Provider 互评时实际发给 g4f 的 prompt 均以英文默认裁判前缀 `'Please evaluate the quality of the following answer'` 开头，覆盖该默认值由中文改为英文后的 fallback 分支。新增对话历史相关测试（2026-07-02，共 36 个用例）：`TestCompareHistoryPersistence`（4 个用例）验证已登录用户触发 `save_chat_history` 且传参正确、响应含 `history_id`、游客/匿名均不触发保存；`TestHistoryAuthGuard`（6 个用例）验证全部 4 个历史路由（含游客）在未登录时统一返回 401；`TestGetHistoryEndpoint`（8 个用例）覆盖分页参数换算为 `offset`、`limit` 夹到 100 上限、`page` 夹到 1 下限、非数字参数回退默认值、500 兜底；`TestUpdateHistoryTitleEndpoint`（6 个用例）覆盖成功、`user_id` 取自 session 而非请求体、404、空标题 400、500 兜底；`TestDeleteHistoryEndpoint`（4 个用例）与 `TestTogglePinEndpoint`（5 个用例，含关键用例 `test_response_reflects_new_is_pinned_false_not_confused_with_failure` 断言翻转为 `False` 时仍返回 200 而非误判为 404）。新增 `TestIndexPageSidebarMarkup`（2026-07-02，7 个用例）：验证 `GET /` 在已登录/游客身份下渲染出侧边栏关键标记（`left-sidebar`、`sidebar-overlay`、`main-content`、`app-layout`、`hamburger-btn`、`sidebarRecents`、`newChatBtn`、`sidebar-skeleton`）、匿名身份仍正确路由到无侧边栏的 `home.html`（防止身份路由回归）、以及 `isLoggedIn` JS 常量根据 `session.user_id` 正确注入 `true`/`false`。**侧边栏的运行时交互（骨架屏替换、hover 淡入、汉堡抽屉动画、时间分组算法 `groupHistoryByDate`/`formatGroupLabel`、乐观更新的 pin/rename/delete 及其失败回滚、原地重命名的 `<input>` 替换与 Enter/Escape 分支、游客 `window.guestHistory` 全流程模拟、事件委托的 `data-action` 分发）不在 Python `unittest` 覆盖范围内**——本项目没有 JS 测试框架（无 `package.json`/Jest/Vitest），这部分逻辑通过 Node.js + jsdom 手动验证过（模拟 DOM 加载 `page_guest.html`/`page_loggedin.html`、mock `fetch`、断言 class 切换与分组渲染结果；2026-07-02 步骤四新增验证覆盖：pin/delete/rename 的乐观更新在 mock 请求成功/失败两条路径下的 DOM 状态与 `showHistoryErrorToast` 弹出情况、编辑图标与双击标题两种方式触发的原地重命名、Enter 提交与 Escape 取消、游客提交 `/api/compare` 后 `window.guestHistory` 被正确写入且全部后续 pin/rename/delete 操作零网络请求），但不是可重复运行的自动化测试套件；后续若引入前端测试框架，应把这些验证转成正式用例。步骤四未修改任何 Python 代码（`main.py`/`auth/db.py`/`auth/routes.py` 均未触碰），因此未新增/修改任何 black/white/grey box 测试用例。2026-07-02 的侧边栏"空结果区背景截断"修复同样未改动任何 Python 代码，纯 CSS（`body`/`.app-layout` 的 flex 与高度声明），因此也未新增/修改 black/white/grey box 测试用例；但这类问题本质是**渲染后的像素高度**是否正确，而 jsdom **不做真实布局计算**（没有实现 CSS 盒模型渲染，`getBoundingClientRect`/`offsetHeight` 等在 jsdom 下不返回真实数值），此前几轮验证一直只能靠 jsdom 断言 class 切换和 DOM 结构，无法发现这类高度截断问题。这次改用真实 headless Chromium（Playwright）加载运行中的 Flask 页面，直接测量 `.left-sidebar`/`.app-layout` 的 `boundingBox()` 高度，分别在"未提交 Prompt"与"提交后出结果"两种状态下断言侧边栏底部到达视口底部，并截图核对视觉效果（桌面 1400×900 与移动 400×800 两种视口，含汉堡抽屉展开/收起）。此沙箱环境没有预装浏览器可执行文件所需的系统共享库（`libnspr4`/`libnss3`/`libasound2`），且没有 root 权限跑 `apt-get install`；通过 `apt-get download` 单独拉取这几个 `.deb` 包、`dpkg -x` 解压到用户目录、再设置 `LD_LIBRARY_PATH` 指向解压出的 `.so` 文件，绕开了这一限制，未使用任何持久化的系统级安装。这一验证方式没有留下可重复运行的自动化测试文件（纯手动脚本 + 截图核对），后续如果引入前端测试框架并接入真实浏览器环境，应把这类"渲染高度是否正确"的断言转成正式的视觉回归测试用例。2026-07-02 的三个 Recents 小 Bug 修复（短 prompt 标题误加省略号、重命名输入框文字不可读、Pin 后条目未移动到顶部）中，第一个是纯 Python 逻辑（有正式 whitebox 用例覆盖，见 `test_auth_whitebox.py` 条目），后两个是纯前端逻辑：重命名输入框的可读性问题用 jsdom 的 `window.getComputedStyle(input)` 读取 `.history-rename-input` 在 `focus()` 之后的实际 `color`/`backgroundColor`，验证不再是浅灰字叠白底；Pin 排序问题用 jsdom 构造三个同日期分组的条目，触发 `toggleHistoryPin` 后断言 `#sidebarRecents` 内 `.history-item` 的 DOM 顺序（而非仅断言 class 是否切换）确实把目标条目移到了最前面，取消置顶后又跌出置顶位置——这类"DOM 元素相对顺序"和"computed style"断言 jsdom 是可以正确处理的（不同于此前 CSS 布局高度截断那个 Bug，那类问题才需要真实 Chromium），所以延用 jsdom 手动脚本验证即可，未使用 Playwright。这三个修复均未改动 Python 侧的对话历史 CRUD 契约或路由，因此除 `TestSaveChatHistory` 新增的两个用例外，未新增/修改其余 black/grey box 测试用例。2026-07-02 本轮进一步修复的三个 Pin 相关 Bug（第二个 Pin 顶替第一个、Pin/Unpin 无图标区分、点击 Pin 后闪烁两下）中，排序逻辑（`pinned_at` 升序）同时改了前端 `sortHistoryItems` 和后端 `get_chat_history_list`/`toggle_pin_chat_history`——后端部分有正式 whitebox 用例覆盖（`TestGetChatHistoryList`/`TestTogglePinChatHistory` 新增的 4 个用例，见上方 `test_auth_whitebox.py` 条目），`main.py` 的 `/api/history/<id>/toggle-pin` 路由本身未改动（仍只是把 `toggle_pin_chat_history` 的返回值包一层 JSON），因此 `TestTogglePinEndpoint`（`test_main_blackbox.py`）无需更新。图标持久化显示和防闪烁两个纯前端修复继续用 jsdom 手动脚本验证：用三个同日期分组的条目模拟"先 Pin 一个、间隔后再 Pin 另一个"，断言 `#sidebarRecents` 内 `.history-item` 的 DOM 顺序（先置顶的稳定在最前，后置顶的排在其后而非顶替）；用 `MutationObserver` 监听 `#sidebarRecents` 的 `childList` 变化次数，断言一次点击 Pin 只触发一次整体 DOM 重建（而非修复前的两次），验证"闪烁两下"确实消失；用选择器断言未置顶条目只有 hover 分组内的 Pin 按钮、置顶条目只有独立的 `.pin-indicator`，且取消置顶后二者会互相替换。这类"DOM 元素相对顺序""DOM 变更次数"和"元素是否存在"的断言 jsdom 可以正确处理（不同于此前 CSS 布局像素高度那个 Bug，那类问题才需要真实 Chromium），因此继续沿用 jsdom 手动脚本，未使用 Playwright。2026-07-03 新增的 `loadHistorySnapshot(id)`（点击 Recents 条目加载历史快照到 Prompt 输入框与结果区）同样是纯前端修复，未改动任何 Python 代码：jsdom 脚本构造带完整 `results` 快照的历史条目，先在 `#prompt`/`#resultsContainer` 里塞入"当前正在显示的、与历史条目无关"的占位数据，再触发点击，断言 `#prompt` 的值、`.stat-value`（重算的 `total_providers`/`successful_providers`）、`.provider-name` 列表被整体替换为该条目的快照内容而非追加或保留旧值；点击第二个条目时断言第一个条目的结果不再残留（验证"完全替换"而非"合并"）；点击 pin/rename/delete 图标时断言 `#prompt` 未被触碰（验证三类点击目标互斥）；`results` 为空数组的条目点击后断言结果区被隐藏且 `#resultsContainer.innerHTML` 清空；游客身份下重复以上关键断言并确认零 `fetch` 调用。这类"表单值/DOM 内容是否被替换"的断言同样是 jsdom 能正确处理的范畴，未使用 Playwright。2026-07-03 新增的删除确认自定义模态框（`showDeleteConfirmModal`，取代原生 `confirm()`）同样是纯前端修复，未改动任何 Python 代码：jsdom 脚本通过真实的 `loadSidebarHistory()` 流程（mock `fetch('/api/history?...')` 返回种子数据，而非直接对 `let currentHistoryItems` 赋值——该变量是 `<script>` 顶层的词法作用域绑定而非 `window` 属性，从测试脚本外部赋值不会影响闭包内读到的值）渲染出历史条目，点击 🗑️ 图标后断言：`#confirmModalOverlay` 被创建且带有 `.visible`；标题为 `"Delete this chat?"`；正文里的 `<strong>` 通过 `textContent`（而非 `innerHTML`）承载条目标题，用一个标题里含 `<script>` 字样的条目验证 `innerHTML` 中并未出现可执行的 `<script>` 标签；点击 Cancel、点击遮罩背景（构造 `target === overlay` 的 `MouseEvent`）、按 `Escape` 键三条路径均关闭模态框且不触发任何 `DELETE` 请求、条目仍在 DOM 中；点击 Delete 按钮才会真正移除该条目并且只对该条目的 `id` 发出一次 `DELETE /api/history/<id>` 请求；标题为空字符串时模态框正文回退显示 `"this conversation"`。游客身份下重复关键路径（打开模态框可点名条目标题、确认删除后 `window.guestHistory` 被原地移除、全程零 `fetch` 调用）。这类"元素是否存在/`textContent` 内容/请求是否被触发及触发几次"的断言同样是 jsdom 能正确处理的范畴（不涉及像素级布局计算），未使用 Playwright。2026-07-03 新增的两处联动重置行为（`clearBtn` 重置 Model 下拉、`deleteHistoryItem` 确认后联动清空界面）同样是纯前端修复，未改动任何 Python 代码：这次 jsdom 脚本不再用手写的最小化 HTML 片段，而是先用 `main.app.test_client()`（`session_transaction` 写入 `user_id`）实际渲染 `GET /` 拿到真实的 `index.html` 输出，再喂给 jsdom（`beforeParse` 钩子里预置 `window.fetch` 桩，因为页面脚本末尾会同步调用 `loadSidebarHistory()`，若不在解析最外层 `<script>` 执行前就注入 fetch 会报 `ReferenceError: fetch is not defined`——jsdom 对内联 `<script>` 是在 `new JSDOM(html, ...)` 构造函数内同步执行的，构造完成后再赋值 `window.fetch` 为时已晚）；断言点包括：选中一个非默认 Provider 模型后点击 `#clearBtn`，`#modelSelect` 的 `value` 变回 `''` 且自定义下拉组件 `#customSelectTrigger` 的文本变回 `"-- Use Provider's Default Model --"`；以及弄脏界面（结果区可见、Prompt 有值、Provider 勾选、Model 选中非默认值）后 stub `showDeleteConfirmModal` 直接 `resolve(true)` 并调用 `deleteHistoryItem(id)`，断言这四项状态全部被重置、同时该历史条目对应的 `.history-item` DOM 节点确实被移除（验证联动重置没有影响原有的删除行为本身）。同样因为 `let currentHistoryItems` 是脚本顶层词法绑定、不是 `window` 属性，测试改为通过 `beforeParse` 里的 fetch 桩让页面自己的 `loadSidebarHistory()` 加载种子数据，而非从测试代码外部直接赋值。
- **test_main_graybox.py**：感知后端全局状态与线程池行为。测试内容包括排序契约、fallback 路径、`max_workers` 双重上限、`G4F_AVAILABLE` 降级响应，以及三 Provider 场景下 1 个失败时互评仅在 2 个成功者之间双向触发的行为（`TestPeerReviewPartialFailure`）。排序测试（`TestSortOrderInvariant`）在 setUp 中 mock `run_peer_review`，避免 2 个成功者触发真实网络调用。新增 `TestPeerReviewPhaseRobustness`（3 个用例）：通过令 `PEER_REVIEW_PROMPTS_MAP.get` 在任务构建阶段抛出 `RuntimeError`，模拟互评阶段整体崩溃，断言接口仍返回 200、第一轮结果完整、`peer_reviews` 字段为空列表。新增 `TestTestSingleRobustness`（1 个用例）：令 `test_g4f_provider` 直接抛出异常以触发 `test_single_provider` 外层 except，断言返回 500 英文友好消息（`Service temporarily unavailable...`，2026-07-02 起由中文改为英文）。新增 `TestHealthConfigFlags`（2 个用例）：分别将 `ROUTE_PROMPTS_MAP` 和 `PEER_REVIEW_PROMPTS_MAP` patch 为空字典，断言 `/health` 对应标志字段为 `False`。新增 `TestSaveHistoryRobustness`（2026-07-02，3 个用例）：令 `save_chat_history` 抛出异常或返回 `None`，断言 `/api/compare` 仍返回 200 且 `results` 完整，`history_id` 安全回退为 `null`，验证该调用的独立 try-except 隔离与互评阶段的隔离设计一致。新增 `TestPeerReviewOuterTimeoutValue`（2026-07-03，2 个用例，对应"互评阶段对较慢 Provider 频繁误判超时"的修复）：`test_peer_review_future_result_waits_up_to_32_seconds` 通过 `patch.object(concurrent.futures.Future, 'result', spy_result)` 包一层记录调用参数的 spy（内部仍调用原始 `result` 完成真实等待，不影响其他测试断言），驱动一次真实的两 Provider 互评流程，断言互评阶段确实以 `timeout=32` 调用 `future.result()`、且不再出现修复前的旧值 `25`（防止外层超时被意外改回）；`test_peer_review_timeout_error_does_not_crash_and_leaves_that_review_missing` 沿用 `TestThreadTimeoutFallback` 同款手法——让 `main.run_peer_review` 直接 `side_effect=concurrent.futures.TimeoutError(...)`（模拟"函数本身抛出超时异常"而非真的等待 32 秒），断言路由仍返回 200、第一轮结果的 `success` 与 `peer_reviews: []` 不受影响。
- **test_auth_whitebox.py**：使用 `unittest.mock.patch` 绕过真实 Firebase 网络连接。测试内容包括 Werkzeug 密码哈希生成与校验，`get_user_by_username` 和 `get_user_by_email` 两个函数在"用户存在"和"用户不存在"两个分支下的行为，以及（2026-07-02 新增）5 个对话历史 CRUD 函数的完整白盒测试（`TestSaveChatHistory`、`TestGetChatHistoryList`、`TestDeleteChatHistory`、`TestUpdateChatHistoryTitle`、`TestTogglePinChatHistory`，共 35 个用例）：覆盖 `title` 截断规则（`prompt[:15] + ('...' if len(prompt) > 15 else '')`；`TestSaveChatHistory` 含 `test_title_equals_prompt_verbatim_when_not_longer_than_limit` 和 `test_title_has_no_ellipsis_when_prompt_exactly_15_chars` 两个用例，2026-07-02 因短 prompt 误加省略号的 Bug 修复新增，断言 `len(prompt) <= 15` 时标题就是 prompt 原文、不追加省略号）、`is_pinned` 默认 `False`、`results` 原样保留、以及 delete/update/toggle 三者的归属校验分支（文档不存在、文档属于其他用户两种拒绝场景）与 `FIREBASE_AVAILABLE=False` 时各自的 fallback 返回值。`TestGetChatHistoryList`（2026-07-02 因生产事故重写，共 12 个用例）断言查询链**只**调用一次 `.where('user_id', '==', ...)`、**不**调用 `.order_by()`（`test_query_does_not_chain_order_by_avoiding_composite_index`，防止未来又串回需要复合索引的写法），并直接在 Python 侧验证排序正确性：置顶项无论新旧都排在未置顶项之前（`test_sorts_pinned_items_before_unpinned_regardless_of_recency`）、未置顶组内按 `created_at` 降序（`test_sorts_by_created_at_descending_within_unpinned_block`）、`offset`/`limit` 在排序后的完整列表上做 Python 切片而非交给 Firestore（`test_applies_limit_and_offset_in_python_after_sorting`）、缺失 `created_at` 字段时排序不崩溃（`test_missing_created_at_does_not_crash_sort`）；2026-07-02 本轮新增 `test_sorts_pinned_items_by_pinned_at_ascending_oldest_pin_first`（两个置顶项，先置顶的 `pinned_at` 更早但 `created_at` 反而更晚，断言排序仍以先置顶的排最前——直接复现并验证修复了"第二个 Pin 顶替第一个"这一用户报告的 Bug）和 `test_missing_pinned_at_for_pinned_item_does_not_crash_sort`（置顶项缺失 `pinned_at` 字段时的防御性兜底）。`TestTogglePinChatHistory`（共 7 个用例）2026-07-02 本轮新增 `test_pin_sets_server_timestamp_sentinel_not_a_python_datetime` 和 `test_unpin_clears_pinned_at_with_delete_field_sentinel`，断言置顶时 `update()` 调用参数里的 `pinned_at` 是 `firestore.SERVER_TIMESTAMP` 哨兵值（而非 Python 端计算的 `datetime.now()`）、取消置顶时是 `firestore.DELETE_FIELD` 哨兵值（而非设为 `None`）；原有的 `test_toggle_pins_when_currently_unpinned`/`test_toggle_unpins_when_currently_pinned` 两个用例的 `update.assert_called_once_with(...)` 断言也同步更新为包含 `pinned_at` 键。
- **test_auth_blackbox.py**：通过 Flask `test_client()` 测试 auth 蓝图的全部路由。测试内容包括登录成功后 `session['user_id']` 的写入与重定向、注册成功后 `session['is_guest']` 的清除、`/profile` 路由的未登录重定向以及已登录用户的正常访问。对话历史路由的黑盒测试放在 `test_main_blackbox.py`（而非本文件），因为这些路由挂载于 `main.py` 而非 `auth_bp` 蓝图。

```bash
# 发现并运行 tests/ 目录下的全部测试（共 274 个用例，5 个测试文件）
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

- `requirements.txt` 采用完全锁版本策略（`pip freeze` 输出格式），所有间接依赖均固定。例外：`gunicorn` 仅写包名不含版本（GAE 运行时负责安装，本地开发环境通常不安装）。
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
- 游客切换相关的 Fetch（`/api/auth/guest`）：`home.html` 中的专属游客按钮实现完整交互（disabled + "Loading..." 文案 + 失败时恢复原始文案 + 显示 `#guestError`）。`login.html` 和 `register.html` 中的 "Continue as guest" 是简化实现（仅在 `res.ok` 时跳转，无 Loading 状态），因为这两处是辅助入口而非主路径。

### 提交规范

- 提交信息用中文或英文均可，项目历史两者混用。
- 保持原子提交：一次提交只做一件事。

## 13. 🧠 MEMORY ANCHORS (FOR CLAUDE CODE)

- **LLM 核心调用链路（两阶段 + 可选持久化）**：`index.html (Form Submit)` + `POST /api/compare` + `ThreadPoolExecutor[1]` + `test_g4f_provider()` + `g4f.ChatCompletion.create()` → 收集结果 → `ThreadPoolExecutor[2]` + `run_peer_review()` + `parse_peer_review_json()` → 挂载 `peer_reviews` → 排序 → 若已登录调用 `save_chat_history()`（独立 try-except，2026-07-02 新增）→ 挂载 `history_id` → 返回 JSON。
- **认证核心调用链路**：`home.html (Login/Register/Guest)` + `auth Blueprint` + `Firebase Firestore` + `session 写入` + `redirect url_for('index')`。
- **身份状态入口**：根路由 `/` 的 `index()` 函数是唯一的身份状态路由器，所有重定向最终都回到这里。
- **关键不变量 1**：LLM 对比结果的排序始终是"成功在前，耗时短在前"。
- **关键不变量 2**：`session['user_id']` 和 `session['is_guest']` 永远不同时存在。
- **关键不变量 3**：所有作为 Flash + redirect 目标的页面，必须包含 Flash 消息显示区，否则会产生消息堆积 Bug。
- **关键不变量 4**：`test_g4f_provider` 的返回值严格为 7-key 契约；`peer_reviews` 字段由 `compare_providers` 在外层追加，使最终 result 共有 8 个 key；`run_peer_review` 的返回值严格为 `{reviewer_provider, reviewer_model, score, comment}` 4-key 契约。
- **异常文案判定顺序（2026-07-03 新增，`test_g4f_provider` 与 `run_peer_review` 共用）**：`CONTENT_POLICY_ERROR_KEYWORDS` 必须先于 `NETWORK_ERROR_KEYWORDS`/`PEER_REVIEW_NETWORK_ERROR_KEYWORDS` 判定，因为 Provider 底层内容审查（如 Azure OpenAI `content management policy`）报错重试无意义，误判成"系统正忙"会诱导用户做无效重试。
- **互评超时调优（2026-07-03）**：`run_peer_review` 内部 `g4f.ChatCompletion.create` 的 advisory `timeout` 由 `15` 上调到 `25`，`compare_providers` 第二阶段 `future.result` 的外层等待由 `25` 上调到 `32`（约 7 秒调度缓冲），解决 Yqcloud 等较慢 Provider 在互评阶段（prompt 含完整被评回答、比首轮更慢）频繁被误判超时（`Peer review for <provider> timed out after 25s`）的问题。两值必须同步调整，且需重新核算 429 重试场景的总耗时上限仍小于外层超时。
- **互评触发条件**：`tested >= 2` 且 `success >= 2`，缺一不触发。只有成功者相互点评，失败者不参与任何方向。
- **`parse_peer_review_json` 容错行为**：任何解析失败（格式错误、缺失 score、异常）均返回 `(80, raw_text)`，不抛出异常，接口永不因互评解析失败而崩溃。
- **核心文件**：LLM 后端入口为 `main.py`，认证后端为 `auth/routes.py` 和 `auth/db.py`，三个前端入口分别为 `templates/home.html`（未认证）、`templates/index.html`（已认证或游客）、`templates/auth/` 目录（认证流程页）。
- **自适应策略**：指定模型不匹配时，自动降级为映射表中的第一个模型，最终兜底为 `"gpt-3.5-turbo"`。
- **Firebase 初始化顺序**：key 文件优先于 ADC，不能反转，因为 ADC 的构造函数不立即抛出异常。
- **`get_chat_history_list` 复合索引事故修复（2026-07-02）**：曾经的实现对 `history` 集合做 `.where(user_id).order_by(is_pinned).order_by(created_at)` 复合查询，这需要在 Firebase 控制台手动创建复合索引（不随代码库提交），本地/生产各环境若未创建就会让 `GET /api/history` 恒定 500（`FAILED_PRECONDITION: The query requires an index`），表现为侧栏卡在"Could not load history."且新对话存档后侧栏也刷新不出来。现已修复为只做单字段等值查询 + Python 应用层排序分页，不再依赖任何手动创建的 Firestore 索引，新环境开箱即用。详见第 9 节风险审计。
- **对话历史持久化现状（2026-07-02，全栈落地）**：数据层 `auth/db.py` 实现 5 个 CRUD 函数（`save_chat_history`、`get_chat_history_list`、`delete_chat_history`、`update_chat_history_title`、`toggle_pin_chat_history`），操作 `history` 集合；路由层 `main.py` 挂载对应端点（`/api/compare` 自动保存 + `/api/history` 系列 4 个端点）；前端 `templates/index.html` 的左侧边栏（`.left-sidebar`，260px 深色 `#171717`）消费这些端点，按 `Today`/`Yesterday`/`Previous 7 Days`/`Older` 时间分组展示 Recents，条目 hover/focus-within 时淡入固定/重命名/删除三个操作图标。三个写操作类函数（delete/update/toggle）内部自带归属校验，路由层调用时 `user_id` 必须取自 `session['user_id']`，不可信任前端传入值。`toggle_pin_chat_history` 返回值判空须用 `is None`，`False` 是合法成功结果不是失败（前端与后端路由两处都遵守这条）。`save_chat_history` 的调用在 `compare_providers()` 中被独立 try-except 包裹，失败不影响 `/api/compare` 本次结果返回，`history_id` 安全回退为 `None`。移动端（`<=520px`）侧边栏切换为 `position:fixed` 抽屉，由 `.hamburger-btn`（嵌在 `.nav-left` 内，与 `.nav-logo` 分组）通过 `sidebar-open`/`visible` class 驱动 `transform:translateX()` 滑入滑出，桌面端这两个 class 不产生任何视觉效果。
- **侧边栏交互层：乐观更新 + 游客内存模拟（2026-07-02 步骤四，纯前端，未改动任何 Python 代码）**：`currentHistoryItems` 是渲染 `#sidebarRecents` 的唯一数据源——已登录时是从 `/api/history` 拉取的数组，游客时**直接是 `window.guestHistory` 的同一引用**（非拷贝），因此原地改条目字段即等价于写入 `window.guestHistory`。`toggleHistoryPin`/`deleteHistoryItem`/`commitRename` 均先本地改数据 + `renderCurrentHistory()`（DOM 立即响应），已登录用户的请求再异步发出；失败时精确回滚到操作前状态并 `showHistoryErrorToast()` 弹出 3 秒提示，游客分支因为没有网络请求所以没有回滚路径。原地重命名 `startInlineRename` 取代了旧的 `prompt()` 弹窗方案，点击 ✏️ 或双击标题都会把 `.history-item-title` 换成 `<input>`，`Enter` 走 `blur` 提交、`Escape` 走独立取消路径（用 `settled` 标志位防止两者重复触发）。游客提交 `/api/compare` 成功且 `history_id` 为空时，前端会把结果 `unshift` 进 `window.guestHistory` 并重渲染，完整模拟"新对话出现在 Recents 顶部"的已登录体验，且游客的任何后续 pin/rename/delete 都不会碰 `/api/history*` 端点。条目按钮从内联 `onclick` 重构为 `data-action` 属性 + `#sidebarRecents` 上的委托 `click`/`dblclick` 监听器（只挂载一次，不受 `renderCurrentHistory()` 反复覆盖 `innerHTML` 影响）。此层逻辑无 Python 测试覆盖，通过 jsdom 手动验证（详见第 11 节）。
- **侧边栏空结果区背景截断修复（2026-07-02）**：`.app-layout` 的 `align-items:stretch` 只能让 `.left-sidebar` 与 `.main-content` 彼此等高，不能让二者一起撑满视口——结果区为空时两者一起矮下去，深色侧边栏就在视口底部之前截断。修复为 `body{display:flex;flex-direction:column;min-height:calc(100vh / var(--page-zoom))}` + `.app-layout{flex:1}`，为空结果区提供一个视口高度的地板；`--page-zoom`（`:root` 定义，当前 `0.8`）必须参与这个 `calc`，因为 `body` 本身还叠了 `zoom:var(--page-zoom)` 这个非标准缩放，`vh` 不会跟着 `zoom` 一起放大，直接用 `100vh` 会在视口底部留出约 20% 空白。纯 CSS 修复，无 Python 改动；用真实 headless Chromium（而非 jsdom，jsdom 不做真实布局渲染）测量 `boundingBox()` 高度验证。
- **Recents 三个小 Bug 修复（2026-07-02，用户报告）**：(1) 短 prompt 标题误加省略号——`save_chat_history`（`auth/db.py`）与前端游客模拟均已改为 `len(prompt) > 15` 才追加 `'...'`，短 prompt（如 `"hi"`）直接用原文做标题；(2) 重命名输入框文字不可读——`.history-rename-input` 原本深色配色（`#ececec` 字 + `#262626` 底）在 `input.focus()` 触发全局 `input:focus` 规则（选择器特异性 `(0,1,1)` 高于单一类选择器）后背景被强制变白、浅灰字糊在白底上，修复为改用浅色配色（`#1a1a1a` 字 + `#ffffff` 底）并新增 `.history-rename-input:focus`（特异性 `(0,2,0)`，稳赢全局规则）显式锁定；(3) Pin 后条目未移到顶部——`toggleHistoryPin` 只翻转 `is_pinned` 就重渲染，但 `renderCurrentHistory()` 此前直接对 `currentHistoryItems` 分组，数组元素顺序从不因 pin 状态变化而重排，修复为新增 `sortHistoryItems(items)` 在分组前对拷贝重新排序。三者中只有第一项涉及 Python 改动（`TestSaveChatHistory` 新增 2 个用例），后两项是纯前端修复，通过 jsdom 的 `getComputedStyle` 和 DOM 顺序断言验证（不需要真实 Chromium，因为验证的是 computed style 和元素相对顺序，不是像素级布局高度）。
- **Pin 逻辑第二轮修复：仿照 Gemini 网页版（2026-07-02，用户进一步报告）**：上一条修复的 `sortHistoryItems` 仍用 `created_at` 降序排置顶组，导致"后置顶但对话创建时间更新的条目"会顶替"先置顶但对话创建时间更早的条目"。本轮引入 `pinned_at` 字段专门记录"置顶操作发生的时刻"（后端 `toggle_pin_chat_history` 置顶写 `firestore.SERVER_TIMESTAMP`、取消置顶用 `firestore.DELETE_FIELD` 删除；前端 `toggleHistoryPin` 本地记录 `new Date().toISOString()`），置顶组改按 `pinned_at` **升序**排序（最早置顶的稳定在最上面，后续新置顶依次排在下面）。同时仿照 Gemini 网页版新增持久化置顶图标：已置顶条目的绿色大头针图标脱离 hover-only 的 `.history-item-actions` 分组，改用独立、`opacity:1` 恒定可见的 `.pin-indicator`（固定在行最右侧），未置顶条目则仍是 hover 才出现的灰色 Pin 按钮，二者互斥渲染，解决了"Pin/Unpin 无图标区分"的问题。另外修复了点击 Pin 后条目闪烁两下的 Bug：`toggleHistoryPin` 的服务端确认回调之前无条件二次 `renderCurrentHistory()`（哪怕状态没变也重建整个 DOM），改为只有 `data.is_pinned !== item.is_pinned` 时才二次渲染。后端排序 + `pinned_at` 写入逻辑有 whitebox 用例覆盖（`TestGetChatHistoryList`/`TestTogglePinChatHistory` 各新增 2 个用例），`main.py` 路由未改动故黑盒测试无需更新；图标持久化与防闪烁是纯前端修复，通过 jsdom（DOM 顺序断言 + `MutationObserver` 计数）手动验证。
- **点击 Recents 条目加载历史快照（2026-07-03，用户报告，纯前端）**：修复前，侧边栏 Recents 列表只有 pin/rename/delete 三个悬浮图标响应点击，条目本身（标题文字、行内空白）点击没有任何效果，用户点开一条历史记录后页面毫无反应。新增 `loadHistorySnapshot(id)`，挂在 `#sidebarRecents` 委托 `click` 监听器里 `[data-action]`/`.history-rename-input` 都未命中时的 fallback 分支：把 `#prompt` 替换为该条目的 `prompt` 字段（不是可能被改过的 `title`），把结果区通过既有的 `displayResults()` 整体替换为该条目 `results` 字段（`/api/compare` 保存时的完整 8-key result 数组快照）对应的渲染结果，`total_providers`/`successful_providers` 两个统计字段现算（Firestore 历史文档从未存这两个字段）。是"完全替换"语义，不是追加/合并——点击第二条会让第一条的结果彻底消失。`results` 为空数组时清空/隐藏结果区而非保留残留内容。游客点击复用同一套逻辑（`currentHistoryItems === window.guestHistory`），零网络请求。`.history-item` 的 `cursor` 同步从 `default` 改为 `pointer`。未改动任何 Python 代码，纯前端修复，通过 jsdom 验证（表单值/结果区内容替换断言、点击图标不触发加载、空快照清空、游客路径）。
- **删除确认改为自定义居中模态框（2026-07-03，用户要求，纯前端）**：`deleteHistoryItem` 原先用浏览器原生 `confirm('Delete this conversation?')` 拦截删除操作——样式无法定制（系统默认的蓝白弹窗）、且只有一句通用文案，不点名具体删除的是哪条记录。新增 `showDeleteConfirmModal(entryTitle)`，惰性创建并复用一个居中的 `.confirm-modal-overlay` + `.confirm-modal`（深色配色，与 `.left-sidebar` 同源），标题固定 `Delete this chat?`，正文 `This will delete ` + `<strong>`（`textContent` 承载条目标题，标题为空时兜底 `this conversation`），底部 Cancel/Delete 两个按钮；返回 `Promise<boolean>`，Cancel、点击遮罩背景、`Escape` 键三条路径均 `resolve(false)` 且不触碰任何数据，只有点击 Delete 才 `resolve(true)`。`deleteHistoryItem` 相应改为 `async function`，在原来 `confirm()` 所在位置改为 `await showDeleteConfirmModal(...)`，为 `false` 时直接 `return`；其后的乐观删除+失败回滚逻辑完全未变。未改动任何 Python 代码，纯前端修复，通过 jsdom 验证（模态框文案与转义、Cancel/背景点击/Escape 三条取消路径均不触发 `DELETE` 请求、确认删除只对目标 `id` 发一次 `DELETE` 请求、空标题兜底文案、游客路径零网络请求）。
- **清空/新建对话联动重置 Model 下拉，删除历史后联动清空界面（2026-07-03，用户要求，纯前端）**：两处独立增强。(1) `clearBtn` 的 click 监听器新增 `modelSelect.value = '';`（在调用 `updateModelDropdown()` 之前），解决"清空 Provider 勾选后 Model 下拉仍保留上次选中的具体模型"的问题——`updateModelDropdown()` 有一段"尽量保留刷新前已选中项"的逻辑，清空勾选后 `selectedProviders` 为空会退化成"全部 Provider 模型并集"，用户之前选的模型大概率仍在这个并集里，若不预先清空 `modelSelect.value`，`updateModelDropdown()` 就会误判为"应该保留"。预先设为 `''` 后，函数内部读到的 `currentSelectedValue` 是空字符串，自然走进重置分支，Model 下拉和它的自定义显示组件（`customTrigger.textContent`）都会回到"使用 Provider 默认模型"。`newChatBtn` 通过 `clearBtn.click()` 复用同一监听器，无需单独处理。(2) `deleteHistoryItem` 在 `showDeleteConfirmModal` 确认通过（`confirmed === true`）之后，新增 `document.getElementById('clearBtn').click()`，等价于用户手动点了一次"Clear results / + New Chat"：隐藏结果区、清空 Prompt、取消 Provider 勾选、重置 Model 下拉（含上一条的行为）。原有的历史记录删除本身（本地乐观 `splice`、`DELETE /api/history/<id>` 请求、失败按原 index 回滚并 `showHistoryErrorToast`）完全未改动，这次界面重置只是在确认删除之后追加的一个独立副作用；取消删除时函数在 `if (!confirmed) return;` 提前返回，不会触发这次重置。两处均未改动任何 Python 代码，纯前端修复，通过 jsdom 加载真实渲染出的页面（Flask `test_client()` 渲染 + `beforeParse` 注入 `fetch` 桩）验证：(1) 选中一个非默认模型后点击 `clearBtn`，断言 `modelSelect.value` 变回 `''` 且 `customTrigger.textContent` 变回默认文案；(2) 弄脏界面状态（结果区可见、Prompt 有内容、Provider 勾选、Model 选中非默认值）后，stub `showDeleteConfirmModal` 直接 `resolve(true)`、stub `fetch` 的 `DELETE` 请求成功，调用 `deleteHistoryItem(id)`，断言界面四项状态全部被重置，且该历史条目仍然从 DOM 中被正确移除（验证删除本身的行为未被这次改动影响）。
