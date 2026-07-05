# claude.md

## 1. 🧠 SYSTEM OVERVIEW

基于 Flask 的大语言模型（LLM）聚合与性能对比 Web 应用。用户输入 Prompt，系统并发调用多个 g4f Provider，实时对比响应内容、响应时间，并让成功的模型互相盲评打分。系统集成 Firebase 认证，支持三种身份：匿名访客、游客（Guest）、已登录用户。已登录用户的对话历史持久化到 Firestore，并通过 ChatGPT/Claude 风格的左侧 Recents 侧边栏（时间分组、懒加载分页、pin/rename/delete 乐观更新、只读详情页）管理；游客历史仅存于 `sessionStorage`，不落库。系统同时支持文生图（text-to-image）聚合对比，走与文本对话完全独立的 g4f 调用链路。文生图结果自 2026-07-04 起持久化到独立的 `image_history` Firestore 集合，并有自己的 Recents 侧边栏（点击"🎨 Generate Image"切换进入、点击"+ New Chat"切换回对话历史）——**但仅限已登录用户使用**，游客与匿名访客既不落库也不提供任何客户端临时记录，这一点与对话历史对游客的处理（`sessionStorage` 临时镜像）刻意不同。

**Chat 功能自 2026-07-04 起额外集成官方 Anthropic API（Claude）**，与上述两条 g4f 调用链路完全独立的第三条链路：直接用官方 `anthropic` SDK 调用 `claude-sonnet-5`/`claude-haiku-4-5-20251001` 两个前沿模型。这是本项目首个"付费/有配额"的 Provider，因此配套一整套权限拦截与开发者成本保护机制——游客/匿名一律被拦截（前端置灰 + 后端 401 双重防御）；每个注册用户可免费消耗开发者账户额度调用 1 次，超限后前端弹窗引导去 `/apikey-config` 配置个人 Key；一旦携带个人 Key（`X-User-Claude-Key` 请求头），后续调用完全绕开开发者额度与免费次数计数。详见第 6 节"Claude 权限控制与防滥用策略"与第 10 节危险区。

后端：Flask + Blueprint（`auth/`）。前端：Jinja2 + Vanilla JS（无框架/构建工具），页面级 `zoom` 缩放通过 `:root` 的 `--page-zoom`（当前 `0.88`）统一控制。

## 2. 🧬 ARCHITECTURE MAP

系统由三个核心子系统构成：Flask 后端服务、Firebase 认证模块、HTML5/JS 前端。

### 后端服务（Flask，`main.py`）

- **路由层**：页面路由（`/`、`/home`、`/history/<history_id>`、`/image-history/<history_id>`、`/apikey-config`，2026-07-04 新增）、LLM API（`/api/providers`、`/api/compare`、`/api/test-single`）、Claude 官方 API（`POST /api/claude-chat`，2026-07-04 新增，见下）、文生图 API（`/api/image-providers`、`/api/generate-images`）、生成媒体静态文件路由（`GET /media/<filename>`）、认证 API（`/api/auth/guest`）、对话历史 API（`/api/history` 系列，仅登录用户）、文生图历史 API（`/api/image-history` 系列，仅登录用户，2026-07-04 新增）。
- **多线程并发调度器**：`ThreadPoolExecutor` 并发调用多个 Provider，防止单点阻塞；文生图路由复用同一套调度骨架（单阶段，无互评）。
- **g4f 适配层**：`g4f.ChatCompletion`（文本对话）与 `g4f.client.Client().images.generate()`（文生图）两条完全独立的调用链路，各自的模型匹配逻辑和异常捕获互不共享。`images.generate()` 在返回前会把生成的图片同步下载到本地 `get_media_dir()` 目录（`./generated_images` 优先，否则 `./generated_media`），Result DTO 的 `url` 字段是形如 `/media/<filename>?url=<原始外部地址>` 的相对路径——这是 g4f 自带 GUI/API 服务器的路由约定，本项目未运行那套服务器，因此 `main.py` 自行注册了 `GET /media/<filename>` 静态文件路由（`serve_generated_media`）来提供这些本地文件，否则前端 `<img>` 与下载按钮都会 404。
- **Anthropic (Claude) 适配层**（2026-07-04 新增）：`call_claude_model()` 直接用官方 `anthropic` SDK 的 `client.messages.create()` 调用，是与上面两条 g4f 调用链路完全独立的第三条链路——不经过 `ThreadPoolExecutor` 并发调度（`/api/claude-chat` 单次只调用一个模型，不做多 Provider 并发对比），不参与 `run_peer_review()` 互评（Claude 从不出现在 `providers_to_test`/`G4F_PROVIDERS` 名字空间里），也不复用 `PROVIDER_MODELS_MAP`/`ROUTE_PROMPTS_MAP`。前端把它作为 `/`（对话对比表单）里视觉上并列的一个 Provider 卡片，但点击"Compare"提交时会额外单独发起一次 `POST /api/claude-chat` 请求，把返回结果在渲染层并入同一份 `results` 数组——即"UI 上同台对比，后端调度完全隔离"。`CLAUDE_AVAILABLE` 是独立于 `G4F_AVAILABLE` 的全局布尔标志。

### 认证子系统（`auth/`）

- `auth_bp` 以无前缀方式挂载：`/login`、`/register`、`/logout`、`/profile`。
- `auth/db.py` 初始化时尝试连接 Firebase Firestore，设置 `FIREBASE_AVAILABLE`；为 `False` 时认证路由返回 503，不崩溃。
- 用户身份通过 Flask `session` 跨请求传递，密钥来自 `SECRET_KEY` 环境变量。

### 前端界面（Jinja2 + JS）

- 三态导航栏（`auth/base.html`、`index.html`）根据 `session.user_id`/`session.is_guest` 联动切换。
- 通过 Fetch API 与后端非阻塞通信。

```
[浏览器]
   |-- GET /         --> [index() 身份检查] --> home.html / index.html
   |-- GET /home     --> [home() 清除 is_guest] --> 重定向 /
   |-- POST /login   --> [auth Blueprint] --> Firebase 验证 --> session 写入
   |-- POST /api/auth/guest --> session['is_guest']=True
   |-- POST /api/compare    --> ThreadPoolExecutor --> g4f Provider 适配层 --> (外部 LLM APIs)
   |-- POST /api/generate-images --> ThreadPoolExecutor --> g4f.client.Client().images.generate() --> (外部文生图 APIs) --> 已登录时 save_image_history()
   |-- GET /media/<filename> --> serve_generated_media() --> get_media_dir() 本地文件（g4f 已提前下载好）
   |-- GET /history/<id>       --> view_history() --> history.html（只读，游客有 sessionStorage 空壳）
   |-- GET /image-history/<id> --> view_image_history() --> image_history.html（只读，仅登录用户，游客/匿名重定向 /）
   |-- POST /api/claude-chat   --> _get_authenticated_user_id() 守卫 --> 免费额度/自带 Key 判定 --> call_claude_model() --> (Anthropic 官方 API) --> 成功且未自带 Key 时 increment_claude_free_tier_usage()
   |-- GET /apikey-config      --> apikey_config() --> apikey-config.html（纯客户端 localStorage 绑定，无登录态守卫）
```

### 耦合风险与设计注意事项

- **硬编码映射**：`PROVIDER_MODELS_MAP`/`IMAGE_PROVIDER_MODELS_MAP`/`CLAUDE_MODELS` 属于硬编码，g4f 库/Anthropic 官方模型目录升级后必须手动同步。
- **全局状态依赖**：`G4F_AVAILABLE`、`FIREBASE_AVAILABLE`、`CLAUDE_AVAILABLE`（2026-07-04 新增）三个全局布尔标志，任一失败都会导致对应功能降级，互不影响。
- **Flash 消息消费规则**：任何作为重定向目标的页面必须包含 Flash 消息显示区，否则消息会在 session 中堆积、在下一个 auth 页面集中出现。

## 3. 🧰 TECHNICAL STACK

- **语言**：Python, JavaScript
- **后端框架**：Flask
- **并发**：`concurrent.futures.ThreadPoolExecutor`
- **核心依赖**：g4f (GPT4Free)、firebase-admin、python-dotenv、`anthropic`（官方 Claude SDK，2026-07-04 新增）
- **认证**：Werkzeug (`generate_password_hash`/`check_password_hash`)、Flask `session`
- **数据库**：Google Cloud Firestore（Firebase Admin SDK）
- **前端**：HTML5, CSS3 (Grid/Flex), Vanilla JS，无框架/无构建工具
- **模板引擎**：Jinja2
- **运行环境**：`os.environ.get('PORT')`/`SECRET_KEY`；本地 `.env`（python-dotenv）；`ANTHROPIC_API_KEY`（开发者默认 Claude Key，2026-07-04 新增，未设置时 `CLAUDE_AVAILABLE` 仍为 `True`——`anthropic.Anthropic()` 零参构造不会立即报错，缺 Key 只会在真正调用 `messages.create()` 时才失败）
- **部署平台**：Google App Engine (GAE Standard/Flexible)

## 4. 📁 CODEBASE STRUCTURE

```
llm_aggregator/
├── main.py                          # Flask 入口：auth 蓝图注册、LLM 路由、文生图路由、历史路由
├── auth/
│   ├── __init__.py                  # auth_bp 蓝图定义
│   ├── db.py                        # Firebase 初始化、用户 CRUD、对话历史 CRUD（6 函数）、图片历史 CRUD（6 函数，2026-07-04 新增）
│   └── routes.py                    # /login /register /logout /profile
├── templates/
│   ├── home.html                    # 未认证且非游客的唯一入口
│   ├── index.html                   # 主功能页：对比表单 + 文生图表单 + Recents 侧边栏（chat/image 双模式）
│   ├── history.html                 # 只读对话历史详情页（index.html 裁剪版，无任何表单元素）
│   ├── image_history.html           # 只读文生图历史详情页（2026-07-04 新增，仅登录用户可达）
│   ├── apikey-config.html            # 个人 API Key 配置页（2026-07-04 新增，见下方专属小节）
│   └── auth/
│       ├── base.html                # 认证页通用布局，三态导航栏，`.card-title` 复用类
│       ├── login.html / register.html / profile.html
├── tests/                           # unittest，584 个用例，不部署
│   ├── test_main_whitebox.py
│   ├── test_main_blackbox.py
│   ├── test_main_graybox.py
│   ├── test_auth_whitebox.py
│   ├── test_auth_blackbox.py
│   ├── test_image_history_whitebox.py   # 图片历史 auth/db.py CRUD（2026-07-04 新增）
│   ├── test_image_history_blackbox.py   # 图片历史 main.py 路由（2026-07-04 新增）
│   ├── test_sidebar_ui_blackbox.py      # 模式感知侧边栏按钮 + G4F 文案移除的标记/文案断言（2026-07-04 新增，同日晚些时候补充 Compare 按钮改名/Recents 空状态文案断言，见下方说明；主要覆盖 index.html，两个新增断言类同时覆盖 history.html）
│   ├── test_html_structure_blackbox.py  # 渲染页面的 HTML 标签配对结构性回归测试（2026-07-04 新增，见下方"HTML 结构完整性"事故记录）
│   ├── test_history_mode_toggle_blackbox.py  # history.html/image_history.html 的 .sidebar-top 按钮改版（2026-07-04 新增，见下方说明）
│   ├── test_claude_integration.py       # 官方 Claude 集成（2026-07-04 新增，见下方专属小节）：计数器/Key 路由白盒测试 + 游客拦截/免费额度/余额耗尽转发黑盒测试
│   ├── test_english_only_blackbox.py    # English-only UI text 政策回归测试（2026-07-04 当天晚些时候新增，见下方专属小节）
│   ├── test_apikey_config_blackbox.py   # apikey-config.html 导航入口 + 逐字段清空按钮（2026-07-04 当天晚些时候新增，见下方专属小节）
│   └── test_scrollbar_dropdown_overflow_blackbox.py  # .custom-options 关闭态零溢出回归测试（2026-07-04 新增，见下方"关闭态的自定义下拉面板"事故记录）
├── availability_g4f/                 # Provider 可用性探测工具（开发辅助，不部署，不被 main.py/tests 引用）
│   ├── find_providers_models.py / test_providers.py          # 文本 Provider 探测
│   ├── find_image_providers.py / test_image_providers.py      # 文生图 Provider 探测
│   └── available_*.txt / provider_test_results*.txt           # 探测产物（多为 gitignored）
├── firebase-key.json                 # 本地 Firebase 服务账号密钥（严禁提交）
├── .env                               # 本地环境变量（严禁提交）
├── requirements.txt / app.yaml       # 依赖锁定 / GAE 部署配置
└── env/                               # 本地虚拟环境（不提交）
```

### `main.py` 关键点

- `load_dotenv()`；`app.secret_key` 来自 `SECRET_KEY`，缺失时 fallback 为随机值（重启后 session 失效）。
- `index()`：`session.user_id`/`is_guest` 均无 → `home.html`；否则 → `index.html`（同时注入文本与图片两套 `provider_models_json`）。
- `home()`（`GET /home`）：清除 `is_guest`，重定向 `/`，不动 `user_id`。
- `view_history(history_id)`（`GET /history/<id>`）：匿名重定向 `/`；已登录经 `get_chat_history_by_id` 校验归属渲染，未找到则 flash + 重定向；游客渲染空壳，由前端从 `sessionStorage` 自行查找。
- `view_image_history(history_id)`（`GET /image-history/<id>`，2026-07-04 新增）：**游客与匿名一律重定向 `/`**（不像 `view_history` 那样为游客渲染空壳）——文生图历史对游客不提供任何形式的记录，连客户端临时记录都没有，所以没有东西可渲染。已登录经 `get_image_history_by_id` 校验归属渲染，未找到则 flash + 重定向。
- `guest_login()`（`POST /api/auth/guest`）：`session['is_guest']=True`。
- `_get_authenticated_user_id()`：对话历史/图片历史路由共用的守卫，游客与匿名一律视为未认证（401）。
- `determine_actual_model(provider, requested_model)` / `determine_actual_image_model(...)`：模型降级纯函数（规则见第 6 节）。
- `init_result_object()` / `init_image_result_object()`：标准 Result 字典初始化。
- `detect_and_truncate(text)`：句级+滑动窗口重复检测，触发时截断；敏感词命中时返回拦截提示。
- `parse_peer_review_json(text)`：从互评响应中提取 `(score, comment)`，任何解析失败均 fallback `(80, raw_text)`。
- `test_g4f_provider()` / `test_g4f_image_provider()`：核心测试函数，分别对应文本/图片两条完全独立的调用链路，各自有独立的异常判定顺序（见第 6 节）。`test_g4f_image_provider()`（2026-07-04 更新）现与 `run_peer_review()` 同构：对 429/queue 类瞬时限流错误重试一次；`advisory timeout` 通过 `get_image_timeouts(provider_name)` 按 Provider 取值而非硬编码常量；异常文案判定顺序为 `GPU_QUOTA_ERROR_KEYWORDS` → `PEER_REVIEW_NETWORK_ERROR_KEYWORDS`（含 429/queue，重试耗尽后兜底）→ 原始异常文本。
- `run_peer_review()`：互评单次请求，429/queue 错误重试一次。
- `compare_providers()`：两阶段并发（测试 + 互评），已登录时调用 `save_chat_history()`（独立 try/except，失败不影响主结果）。
- `generate_images()`：单阶段并发（无互评），Provider 名字空间严格限定在 `IMAGE_PROVIDERS`。请求开始时（`G4F_AVAILABLE` 检查之后、筛选 Provider 之前）先调用一次 `cleanup_old_generated_media()`（2026-07-04 新增），惰性清理 `get_media_dir()` 里的过期文件，防止本地磁盘随请求量无限增长。已登录用户持久化到独立的 `image_history` 集合（`save_image_history()`，2026-07-04 新增，独立 try/except，失败不影响主结果），返回体挂载 `history_id`；游客与匿名不落库，`history_id` 为 `None`——与 `compare_providers()` 对 `save_chat_history()` 的处理同构，但**没有**对游客的 `sessionStorage` 客户端回退（图片版 Recents 侧边栏仅限登录用户使用）。
- `serve_generated_media(filename)`（`GET /media/<filename>`，2026-07-04 新增）：把请求路径里的 `filename` 经 `os.path.basename()` 去掉任何目录穿越片段后，用 `send_from_directory(get_media_dir(), safe_filename)` 提供本地已生成的图片/音视频文件。只读取本地磁盘上已存在的文件，**不**依据 URL 查询参数发起任何服务端抓取——与"下载按钮不做服务端代理"的 SSRF 规避原则一致（见第 9/10 节）。`get_media_dir` 从 `g4f.image.copy_images` 导入，`g4f` 不可用时 fallback 为返回字面量 `'./generated_media'` 的 lambda。
- 模块级常量：`NETWORK_ERROR_KEYWORDS`、`PEER_REVIEW_NETWORK_ERROR_KEYWORDS`、`CONTENT_POLICY_ERROR_KEYWORDS`、`GPU_QUOTA_ERROR_KEYWORDS`（文生图专属，判定顺序见第 9 节）、`ROUTE_PROMPTS_MAP`、`PEER_REVIEW_PROMPTS_MAP`、`SENSITIVE_KEYWORDS`、`IMAGE_GENERATION_ADVISORY_TIMEOUT`(40)/`IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER`(5)/`IMAGE_GENERATION_OUTER_TIMEOUT`(85 = `2*ADVISORY+BUFFER`，2026-07-04 从"advisory+固定 10s"公式改为"2*advisory+固定 5s"公式，见下方 `get_image_timeouts` 说明与第 9 节)、`IMAGE_PROVIDER_TIMEOUT_OVERRIDES`（单 Provider 超时覆盖表，当前仅 `AnyProvider`: advisory 70，outer 由公式推出为 145）、`GENERATED_MEDIA_MAX_AGE_SECONDS`(3600，2026-07-04 新增，见 `cleanup_old_generated_media`)、`CLAUDE_MODELS`/`CLAUDE_MAX_TOKENS`(2048)/`CLAUDE_FREE_TIER_LIMIT`(1)（Claude 专属，2026-07-04 新增，见下）。
- `get_image_timeouts(provider_name)`：查 `IMAGE_PROVIDER_TIMEOUT_OVERRIDES`，命中则用该 Provider 专属的 advisory，否则用默认的 `IMAGE_GENERATION_ADVISORY_TIMEOUT`；outer 永远由 `_compute_outer_timeout(advisory) = 2*advisory + IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER` 从取到的 advisory 现算，**不再**有单独的 per-Provider outer 覆盖值（2026-07-04 起，`IMAGE_PROVIDER_TIMEOUT_OVERRIDES` 里的条目只允许写 `advisory`）。`test_g4f_image_provider()` 和 `generate_images()` 均通过它取超时值，不再直接引用模块常量——这样 `AnyProvider` 可以有更宽松的预算而不影响同批次其他 Provider（outer timeout 是每个 `future.result()` 独立计算的）。
- `cleanup_old_generated_media(max_age_seconds=GENERATED_MEDIA_MAX_AGE_SECONDS)`（2026-07-04 新增）：`generate_images()` 请求开始时惰性调用一次，扫描 `get_media_dir()`，删除 `mtime` 早于 `max_age_seconds`（默认 3600 秒=1 小时）的**文件**（`os.path.isfile()` 守卫，跳过子目录）。目录不存在（`os.listdir` 抛 `OSError`）直接返回；单个文件删除失败（如权限错误）只记 `logger.warning`，不中断其余文件的清理，也不向上抛出——清理失败绝不能阻塞本次图片生成请求。**刻意不做**"每次生成前清空整个目录"：图片结果不落库（见第 6 节"文生图并发调度规则"），浏览器里正在展示的 `<img>`/下载按钮引用的本地文件是唯一副本，无差别清空会把其他并发请求（另一个标签页、另一个恰好落在同一 GAE 实例上的用户）尚未来得及查看/下载的图片一并删除且无法恢复；按年龄清理只处理"足够旧、大概率已经没人在看"的文件。
- `call_claude_model(prompt, model_key, user_api_key=None)`（2026-07-04 新增）：官方 Claude 调用链路的核心函数。`model_key` 是 `CLAUDE_MODELS` 的 key（同时也是前端 `<select>` 的 value/展示名），映射到真正的官方 API model ID（`claude-sonnet-5`/`claude-haiku-4-5-20251001`）。`user_api_key` 非空时用它实例化 `anthropic.Anthropic(api_key=user_api_key)`；为空时用零参 `anthropic.Anthropic()`（从 `ANTHROPIC_API_KEY` 环境变量读取开发者 Key）——**Key 路由的唯一分支点，两个调用方（`claude_chat()` 路由与测试）都不应该绕开这个函数自己实例化客户端**。非流式请求，`max_tokens=CLAUDE_MAX_TOKENS`（2048，远低于官方 SDK 对非流式请求约 16000 token 的超时保护阈值），不设置 `thinking` 参数（省略即可：Sonnet 5 上省略等价于走 adaptive thinking，Haiku 4.5 本就不支持该参数，两者都不需要额外判断模型再决定要不要传）。异常分类：`anthropic.APIStatusError` 且（`error.message` 含 `"credit balance"` 关键词，**或** `error.type == 'billing_error'` 作为兼容性兜底）→ 翻译为 `error_code: 'SERVER_CREDITS_EXHAUSTED'`（判断依据不绑定某个具体 `status_code`——**已用一个真实余额为 0 的 Anthropic 账户直接调用官方 API 验证过**，实测返回的是 `400` + `error.type == 'invalid_request_error'` + message 含 `"Your credit balance is too low..."`，既不是最初任务描述设想的"429 + insufficient_funds"这一并不存在的组合，也不是通用文档字面暗示的"403 + billing_error"——`insufficient_funds` 不是 Anthropic 使用的错误类型字符串，都在集成时按实测行为做了修正，见第 6 节与第 9 节风险记录）；`anthropic.APIConnectionError` → 与 g4f 路径一致的"系统正忙"友好文案；其余异常原样透出 `str(e)`。
- `claude_chat()`（`POST /api/claude-chat`，2026-07-04 新增）：见第 6 节"Claude 权限控制与防滥用策略"完整流程。
- `apikey_config()`（`GET /apikey-config`，2026-07-04 新增）：纯渲染 `apikey-config.html`，无登录态守卫——页面本身不发起任何需要权限的请求，只是把 Claude API Key 存进浏览器 `localStorage`；真正的权限/额度校验发生在 `/api/claude-chat` 路由里。
- **depends_on**：`flask`（含 `send_from_directory`）、`g4f`、`g4f.client.Client as G4FImageClient`、`g4f.image.copy_images.get_media_dir`、`anthropic`（2026-07-04 新增）、`concurrent.futures`、`auth.auth_bp`、`auth.db`（6 个对话历史 CRUD 函数 + 6 个图片历史 CRUD 函数 + 2 个 Claude 免费额度计数器函数）。

### `auth/db.py` 关键点

- 初始化：`firebase-key.json` 存在则优先用它（本地），否则 `ApplicationDefault()`（GAE）。**必须先检查 key 文件**——`ApplicationDefault()` 的凭据解析是惰性的，不能靠它的构造异常做 fallback 判断。
- `FIREBASE_AVAILABLE`：全局布尔标志，任何异常（含 `ImportError`）均设 `False`。
- 4 个用户 CRUD 函数（`get_user_by_username`/`get_user_by_email`/`create_user`/`get_user_by_id`）：无内部守卫，调用方负责检查 `FIREBASE_AVAILABLE`。
- 6 个对话历史 CRUD 函数（`save_chat_history`/`get_chat_history_list`/`get_chat_history_by_id`/`delete_chat_history`/`update_chat_history_title`/`toggle_pin_chat_history`）：**各自内部**检查 `FIREBASE_AVAILABLE`，不依赖调用方。除 `save_chat_history` 外均先读文档校验 `doc.to_dict().get('user_id') == user_id`，不匹配/不存在则拒绝并返回 fallback 值（`None`/`False`）。三种失败原因（Firebase 不可用/不存在/越权）不区分。
- 6 个图片历史 CRUD 函数（`save_image_history`/`get_image_history_list`/`get_image_history_by_id`/`delete_image_history`/`update_image_history_title`/`toggle_pin_image_history`，2026-07-04 新增）：与上面 6 个对话历史函数逐一同构（同样的归属校验、fallback 值、`pinned_at` 语义），但读写独立的 `image_history` 集合，而不是复用 `history`——两者的 result DTO 分别是 7-key（+ peer_reviews）和 8-key，混进同一个集合需要额外的判别字段，且历史上 `history` 集合已经积累了纯聊天记录，不应该被图片文档污染。
- `pinned_at` 字段：置顶时写 `firestore.SERVER_TIMESTAMP`，取消置顶时用 `firestore.DELETE_FIELD` 整体删除（不是设 `None`）。排序：置顶组内按 `pinned_at` 升序（最早置顶的排最前），未置顶组按 `created_at` 降序。对话历史与图片历史两套函数完全一致地遵循这条规则。
- **`get_chat_history_list`/`get_image_history_list` 都只做单字段等值查询**（`.where(filter=FieldFilter('user_id','==',...))`），排序和分页在 Python 应用层完成——避免依赖需要手动创建的 Firestore 复合索引（见第 9 节风险）。
- **查询一律用 `.where(filter=FieldFilter(field, op, value))`，不用已废弃的位置参数形式 `.where(field, op, value)`（2026-07-04 修复）**：本文件 4 处单字段查询（`get_user_by_username`/`get_user_by_email`/`get_chat_history_list`/`get_image_history_list`）原先都写的是 `.where('user_id', '==', user_id)` 这种位置参数调用——这是较新版本 `google-cloud-firestore`（本项目锁定的 2.28.0）里已废弃的写法，每次真实查询 Firestore（包括对生产库的手动排查、Claude 集成的浏览器端到端测试等）都会在日志里打印一条 `UserWarning: Detected filter using positional arguments. Prefer using the 'filter' keyword argument instead.`——纯噪音日志，不影响查询结果，但会污染生产日志、掩盖真正需要关注的 WARNING/ERROR。修复为从 `google.cloud.firestore_v1.base_query` 导入 `FieldFilter`，改写成 `.where(filter=FieldFilter(field, op, value))`。`FieldFilter` 的导入放在 `auth/db.py` 顶部已有的 `try/except ImportError` 块内（与 `firebase_admin`/`credentials`/`firestore` 一起），沿用同一套"导入失败则 `FIREBASE_AVAILABLE=False`"的降级路径，不需要新增异常处理分支。**`FieldFilter` 没有实现 `__eq__`**，因此白盒测试里原先用 `mock.assert_called_once_with('user_id', '==', 'uid1')` 直接断言调用参数的写法失效（`FieldFilter` 实例两两比较恒为 `False`）；改为提取 `call_args` 里 `kwargs['filter']`，再逐一断言其 `.field_path`/`.op_string`/`.value` 三个属性（`tests/test_auth_whitebox.py`/`tests/test_image_history_whitebox.py` 新增的 `_assert_where_called_with_field_filter()` 辅助函数）。
- `get_claude_free_tier_usage(user_id)`/`increment_claude_free_tier_usage(user_id)`（2026-07-04 新增）：Claude 免费额度计数器，读写的是 `users` 集合文档上的 `claude_free_tier_usage` 整型字段，而不是独立集合——这个字段是账户级别的持久状态，天然属于用户文档本身。字段**不需要**在 `create_user()` 里预先写入初始值，读取时用 `doc.to_dict().get('claude_free_tier_usage', 0)` 兜底默认 0 即可（与 `is_pinned` 等字段的既有处理方式一致）。`increment_claude_free_tier_usage()` 用 `firestore.Increment(1)` 原子递增（而不是"先读再写"两步），避免高并发下的竞态；两个函数各自内部检查 `FIREBASE_AVAILABLE`（与其余 CRUD 函数一致），不依赖调用方。**已知的非原子性**：`main.py` 里"检查是否超限"（`get_claude_free_tier_usage`）与"调用成功后递增"（`increment_claude_free_tier_usage`）是两次独立的 Firestore 读写，中间没有事务包裹——极端并发下（同一账号在毫秒级内发起两个并发的 `/api/claude-chat` 请求）理论上可能让免费额度被多用 1 次。这是刻意的简化：防薅羊毛的目标是提高滥用成本、不是做到金融级精确扣费，且本项目其余地方也没有使用 Firestore 事务的先例，引入事务的复杂度收益比不划算。
- 与图片历史 CRUD 一样，Claude 计数器函数没有归属校验的概念——`user_id` 直接来自 `session['user_id']`，不存在"越权访问别人计数器"的攻击面（不像历史记录那样需要用一个外部传入的 `history_id` 反查 `user_id` 字段）。

### `auth/routes.py`

- 每条路由顶层 `try/except`，错误通过 Flash 反馈。
- 登录/注册成功：写 `session['user_id']`/`username`，清 `is_guest`。
- 退出：清除全部三个 session 键，重定向 `/`。
- `/profile`：先检查 `session['user_id']`，否则重定向 `/login`。

### `templates/index.html` 关键点

- **两栏应用布局**（`.app-layout`）：`.left-sidebar`（260px 深色 `#171717`）+ `.main-content`（`flex:1`）。`body{display:flex;flex-direction:column;min-height:calc(100vh/var(--page-zoom))}` + `.app-layout{flex:1}` 保证结果区为空时侧边栏背景仍铺满视口（见第 9 节不变量）。
- `.left-sidebar` 用 `position:sticky` + `height:calc(100vh/var(--page-zoom) - var(--sidebar-header-height) + 2px)`（纯 CSS，不依赖 JS 对 `window.innerHeight` 的手工运算），使 `#sidebarRecents` 能真正溢出滚动。
- **文生图模式**：`#compareModeContainer`/`#imageModeContainer` 两个互斥容器，`switchToImageMode()`/`switchToCompareMode()` 切换。图片 Provider 勾选框**必须用独立 class**（`.image-provider-checkbox`/`.image-provider-trigger`），不能复用文本表单的 `.provider-checkbox`/`.provider-trigger`（后者被全局无容器限定的 `querySelectorAll` 查询，同名会互相污染）。结果区 `.image-results-grid`（固定两列），下载按钮 `downloadImage()` 全程浏览器端 `fetch()`+Blob 完成，**刻意不做服务端代理**（避免 SSRF）。下载文件名的扩展名从 `result.url` 路径部分动态提取（`?` 之前的最后一段 `.ext`），不再硬编码 `.png`——不同 Provider 实际产出的格式不同（如 `PollinationsImage` 是 `.jpg`），扩展名与真实字节格式不符会导致部分系统看图工具拒绝打开（2026-07-04 修复，与 `/media/<filename>` 路由缺失是同一次报告里的两个症状，但属独立小问题）。
- **Recents 侧边栏双模式**（2026-07-04 新增）：`#sidebarRecents` 是唯一的物理容器，`let sidebarMode`（`'chat'`|`'image'`）决定当前渲染/交互走哪一套逻辑；`switchToImageMode()`/`switchToCompareMode()` 除了切换 `#compareModeContainer`/`#imageModeContainer` 外，还分别把 `sidebarMode` 设为 `'image'`/`'chat'` 并调用 `loadSidebarImageHistoryList()`/`loadSidebarHistory()` 刷新侧边栏内容。共用的 `click`/`dblclick`/`scroll` 委托监听器（挂在 `#sidebarRecents` 上）内部按 `sidebarMode` 分流到 `toggleHistoryPin`/`toggleImageHistoryPin` 等对应的一对函数——两套函数除了各自的状态数组（`currentHistoryItems`/`currentImageHistoryItems`）、API 前缀（`/api/history`/`/api/image-history`）、详情页路径（`/history/<id>`/`/image-history/<id>`）不同外，逻辑完全对称。支持 `?mode=image` URL 查询参数：页面加载时若命中则直接调用 `switchToImageMode()`（供 `image_history.html` 的"Generate Image"侧边栏按钮跳转回来时使用，见其小节）。
- **`.sidebar-top` 双按钮改为"模式感知"而非固定文案**（2026-07-04 更新）：`#newChatBtn`/`#generateImageBtn` 不再是两个语义固定的按钮（旧版分别永远是"+ New Chat"和"🎨 Generate Image"，点击后者进入图片模式后两个按钮文案却原地不动，读起来仍像"回到聊天"和"进入图片"，容易让用户误以为点 `#newChatBtn` 也会离开当前模式）。现在：`#newChatBtn` 始终显示"+ New"，点击时按 `sidebarMode` 分流点击 `#clearBtn`（聊天模式）或 `#clearImagesBtn`（图片模式）——**不再**附带 `switchToCompareMode()` 强制跳回聊天模式，即"在当前模式下清空重来"，而不是"离开当前模式回到聊天"。`#generateImageBtn`（沿用旧 id，但语义已变成"切换到另一个模式"）的图标/文案由 `switchToImageMode()`/`switchToCompareMode()` 内部写入固定 id 的 `#modeToggleBtnIcon`/`#modeToggleBtnLabel` 两个 `<span>`：聊天模式下显示"🎨"+"Generate Image"（点击进入图片模式），图片模式下显示"✍️"+"Generate Text"（点击回到聊天模式）；点击处理器本身也按 `sidebarMode` 分流调用这两个切换函数，而不是像旧版一样固定绑定 `switchToImageMode`。两个按钮共用一个 `.btn-icon` 定宽（`1.3em`）flex 列 + `.btn-label`，让"+"和表情符号宽度不同也不影响"New"/"Generate Image"/"Generate Text" 三种文案的左侧起始位置对齐。
- **图片版 Recents 仅限已登录用户**：`renderCurrentImageHistoryList()` 在 `!isLoggedIn` 时直接渲染"Log in to save and view your image generation history."锁定文案，**不发起任何 `/api/image-history` 请求**——游客/匿名连懒加载分页、pin/rename/delete 的入口都不存在（因为根本没有渲染任何 `.history-item`）。这与对话历史对游客的处理（`window.guestHistory` 客户端临时记录）刻意不同：图片生成历史对游客完全不可用，不是"降级为客户端记录"而是"完全锁定"。
- **对话历史 Recents 侧边栏**：`#sidebarRecents` 骨架屏 → 按 `Today`/`Yesterday`/`Previous 7 Days`/`Older` 时间分组渲染；`currentHistoryItems` 是渲染唯一数据源（游客时直接是 `window.guestHistory` 的同一引用，非拷贝）；懒加载分页（滚动到底部 48px 内触发 `loadMoreSidebarHistory()`）；pin/rename/delete 均为乐观更新（先改本地数据+重渲染，再异步请求，失败精确回滚 + `showHistoryErrorToast`）；`sortHistoryItems()` 渲染前重排（置顶组按 `pinned_at` 升序，不改变原数组顺序）；删除确认走自定义 `showDeleteConfirmModal()`（非原生 `confirm()`）。图片历史侧边栏复用 `groupHistoryByDate`/`sortHistoryItems`/`renderHistoryGroups`/`renderHistoryItem` 这几个通用渲染辅助函数（两种历史条目共享同样的 `{id, title, is_pinned, pinned_at, created_at}` 形状），只有数据获取/持久化/导航目标是各自独立的一套。`renderHistoryGroups()` 空列表时的占位文案（2026-07-04 当天晚些时候）从 "No conversations yet." 缩短为 **"Empty."**（`history.html` 里的同一份函数同步改动），图片历史侧边栏的占位文案（"No images generated yet."）未变、仍是独立的一份，不共用这条文案。
- **对比表单主提交按钮**（2026-07-04 当天晚些时候）：`#compareBtn` 的文案从 "Compare Providers" 改为 **"Compare Responses"**——按钮提交后对比的是各 Provider 返回的*回答*，而不是 Provider 本身，新文案更准确。回归测试见 `tests/test_sidebar_ui_blackbox.py::TestCompareButtonRenamed`。
- **点击 Recents 条目**：`openHistoryEntry(id)`/`openImageHistoryEntry(id)` 整页导航到 `/history/<id>`/`/image-history/<id>`（只读页面），不再内联加载进可编辑表单。
- **游客历史**：`window.guestHistory` 持续镜像进 `sessionStorage`（`persistGuestHistory()`/`loadGuestHistoryFromStorage()`），支持跨页面导航存活，标签页关闭即清空。**仅适用于对话历史**——图片历史没有对应的游客镜像机制。
- **自绘可拖拽滚动指示器**：`#sidebarRecentsScrollThumb`/`#pageScrollThumb`（`setupThumbDrag()`），两处原生滚动条彻底隐藏（`scrollbar-width:none`+`::-webkit-scrollbar{display:none}`，恒为 0 宽度）——Chrome 的原生 overlay 滚动条无法被 `::-webkit-scrollbar` 样式可靠压制，且会阻挡自绘指示器的 `mousedown`，因此改为完全自绘。
- **`#pageScrollThumb` 曾在用户交互前"乱动"/需要先点击 Compare 或勾选一个 Provider 才会"锁住"（2026-07-04 修复，见第 9 节"关闭态下拉面板"事故记录）**：根因不在 `updatePageScrollIndicator()` 自身的计算，而在 `.custom-options`（`#customOptions`/`#imageCustomOptions` 共用同一个 class）——关闭状态下只用 `opacity:0`/`visibility:hidden` 隐藏，但盒子本身仍是 `display:block`/`position:absolute`/`max-height:250px`，这个不可见的盒子依然计入 `document.documentElement.scrollHeight`，而它的实际尺寸又随 `updateModelDropdown()`/`updateImageModelDropdown()` 当前汇总的模型数量变化（未勾选任何 Provider 时汇总全部 Provider 的模型，是最大的那种状态）。修复：关闭态改为 `max-height:0`+`overflow:hidden`（`transition` 用显式属性列表 + 对 `max-height` 加 `0s linear 0.15s` 的 transition-delay，让塌陷动作延后到淡出动画结束、镜像 `visibility` 本身的原生离散过渡行为），`.open` 态恢复 `max-height:250px`+`overflow-y:auto`。回归测试见 `tests/test_scrollbar_dropdown_overflow_blackbox.py`。
- **导航栏对齐**：`.nav-container` 边到边铺满（无 `max-width`），`.nav-left` 定宽 260px 与 `.left-sidebar` 对齐，logo 居中在侧边栏正上方。
- **页面标题/导航栏彻底去除 "G4F" 用户可见文案**（2026-07-04）：`<title>`（`index.html`/`history.html`/`image_history.html` 三处均为 `LLM Aggregator`/`History - LLM Aggregator`/`Image History - LLM Aggregator`）、`.nav-logo` 的移动端缩写 `.logo-short`（原先窄屏下显示字面量 "G4F"，现与桌面端 `.logo-full` 一样统一显示"LLM Aggregator"）、两个模式各自的 `<h1>` 大标题均不再提及"G4F"——`.header-full` 从"G4F LLM Aggregator"/"G4F Image Generator"分别改名为**"Text Generator"**/**"Image Generator"**，两者共用的移动端缩写 `.header-short` 也从各自独立（聊天模式"LLM Aggregator"、图片模式"Image Gen"）统一改成同一句"LLM Aggregator"。图片模式副标题同时从"Generate images with free, no-key g4f providers and compare them side by side."改为不提 g4f 实现细节的"Generate images with providers and compare them side by side."。`g4f` 仍是内部实现依赖（见第 2/3 节），只是不再作为面向用户的产品名称出现。
- 移动端（`<=520px`）：`.left-sidebar` 变为 `position:fixed` 抽屉，由 `.hamburger-btn` 驱动。
- Model 自绘下拉框：高亮切换延迟到面板收起动画结束后执行（`syncSelectedOptionHighlight()` + 150ms `setTimeout`），避免视觉跳变。
- `escapeHtml()` 转义 provider/model/error 后注入 DOM；`response` 经 `marked.parse()` 渲染 Markdown（内容来自受信 LLM，不转义）。
- **Provider/Model 选择区四段式布局**（2026-07-04 重排）：`compareForm` 内自上而下依次是 `#frontierProviderSelection`（"Select frontier providers:"，付费/官方 API Provider，当前仅 Claude 一张卡片，预留位置给未来的 ChatGPT/Gemini 前沿模型卡片）→ `#claudeModelSelect` 所在的 `.claude-model-select-group`（原样不动，位置不变）→ `#providerSelection`（"Select free providers (leave all unchecked to test all):"，原文案"Select Providers (leave all unchecked to test all):"改名，内容仍是纯 g4f Provider 勾选框循环，不含 Claude）→ 免 Key 模型下拉（"Select free models (single selection):"，原文案"Select Model (Single Selection):"改名，`#customSelectWrapper`/`#modelSelect` 本身不变）。**四段分离是刻意的**：`#frontierProviderSelection`/`#providerSelection` 是两个独立的同级 `<div class="provider-selection">` 容器（class 名相同以复用视觉样式，但 id 不同），Claude 卡片只存在于前者——这样"付费前沿模型"与"免 Key 模型"在视觉与 DOM 结构上都是两个独立分组，而不是像旧版那样把 Claude 卡片追加在 g4f Provider 循环之后、挤在同一个 `#providerSelection` 网格里。**图片生成表单（`#imageModeContainer`）不受影响**：它自己的"Select Image Providers (leave all unchecked to test all):"/"Select Model (Single Selection):"（图片模型）两处文案与 id（`#imageProviderSelection`）均未改名，与本次重排是两个独立的表单/命名空间。回归测试：`tests/test_main_blackbox.py::TestProviderSelectionSectionMarkup`（2026-07-04 新增，见下方测试小节）断言四段的文案、id、DOM 顺序、Claude 卡片不再落在 `#providerSelection` 容器内，并显式排除图片表单里同名旧文案造成的误判。
- **Claude Provider 卡片**（2026-07-04 新增；同日晚些时候随上一条改为渲染在 `#frontierProviderSelection` 而非 `#providerSelection` 内）：`#claudeProviderCard`/`#claudeProviderTrigger` **必须用独立 class**（`.claude-provider-checkbox`/`.claude-provider-trigger`），不能复用 `.provider-checkbox`/`.provider-trigger`——原因与图片 Provider 勾选框那条规则完全一致：`providerTriggers`/`providerCards` 是全局无容器限定的 `document.querySelectorAll('.provider-trigger'/'.provider-checkbox')`，同名会让 Claude 被一并纳入提交给 `/api/compare` 的 `providers` 数组（而 Claude 根本不在后端 `G4F_PROVIDERS` 名字空间里），也会让 `syncCardChecked()` 在它身上 `querySelector('.provider-trigger')` 返回 `null` 而崩溃——这条判定与 Claude 卡片当前位于哪个容器无关（这些查询本就不 scope 到 `#providerSelection`），所以把卡片挪到 `#frontierProviderSelection` 不影响这条隔离规则，这也是这次重排能做到"只挪位置、不动行为"的原因。`#claudeModelSelect` 是独立于共享 `#modelSelect`/`providerModelsMap` 的原生 `<select>`（Claude 的两个模型不进入 g4f 的模型映射表），仅在 `#claudeProviderTrigger` 勾选且未被 `disabled` 时启用（`syncClaudeModelSelectEnabled()`）。游客/匿名访客：卡片渲染 `.is-locked` class（整卡置灰）+ `<input disabled>` + `title` 属性浏览器原生 tooltip + 卡片内 `<small>` 文案，三处同时提示 `"Log in to unlock frontier models"`（原为中文"请登录以解锁前沿模型"，2026-07-04 当天晚些时候随"English-only UI text 政策"改为英文，见第 6 节该小节）（第 3 节"Guest 模式拦截"要求"置灰区域旁或悬浮提示"二选一，这里两者都做了）。**提交流程**：`compareForm` 的 `submit` 处理器在拿到 `/api/compare` 的结果后，若 Claude 勾选框被选中，**额外单独**调用 `fetchClaudeResult()` 发起 `POST /api/claude-chat`（携带 `X-User-Claude-Key` 请求头，值来自 `localStorage.getItem('user_claude_key')`），成功/失败都构造一个与 g4f Result DTO 同形状的对象（`peer_reviews` 恒为空数组）`push` 进 `data.results` 后重新按"成功优先、耗时短优先"排序，再统一调用 `displayResults(data)`——即两次独立的网络请求，但渲染层合并成一份结果网格。`FREE_TIER_EXHAUSTED` 响应触发 `showClaudeUpgradeModal()`（复用 `.confirm-modal-overlay`/`.confirm-modal` 视觉样式，懒创建 DOM，与 `showDeleteConfirmModal()` 同构但是独立的 overlay 节点），点击 `"Configure API Key"`（原中文"去配置 API Key"）跳转 `/apikey-config`；其余错误（含 `SERVER_CREDITS_EXHAUSTED`）不弹窗，只作为一张失败的 "Claude" 结果卡片渲染在网格里。`clearBtn` 的点击处理器同步重置 Claude 勾选框/模型 `<select>` 状态。

### `templates/history.html`

- `index.html` 的裁剪版：无 `#compareForm`/Provider 勾选/Model 下拉/Compare-Clear 按钮，只有只读 `#promptDisplayContainer` + 原样保留的 `#results`。
- 三态数据来源：游客从 `sessionStorage` 重建的 `currentHistoryItems` 查找；已登录直接用服务端注入的 `serverHistoryEntry`（无需二次请求）。
- **`.sidebar-top` 按钮对齐 `index.html` 最新的模式感知样式**（2026-07-04 更新）：`#newChatBtn` 从纯文案"+ New Chat"改为 `<span class="btn-icon">+</span><span class="btn-label">New</span>` 结构，与 `index.html`/`image_history.html` 共用同一套 `.btn-icon`（`1.3em` 定宽）+ `.btn-label` 布局。**此前这个页面没有任何模式切换入口**——`#generateImageBtn` 是本次新增，之前只有 `#newChatBtn` 单一按钮，与 `image_history.html`（一直有两个按钮）不对称。由于这个页面是纯只读展示、没有实时的 `sidebarMode`/表单可以就地清空，两个按钮都只是跳转到别处，不像 `index.html` 里那样按 `sidebarMode` 分流：`#newChatBtn` 固定跳 `/`（这个页面永远代表 chat 语境，"+ New" 就是"离开只读详情页、去开始新的对比"）；`#generateImageBtn` 图标/文案固定为"🎨"+"Generate Image"（不像 `index.html` 那样运行时翻转），点击跳 `/?mode=image`，让 `index.html` 加载时直接进入图片模式。
- 删除当前正在查看的条目会跳转离开。
- 侧边栏这里只渲染对话历史（Recents 一直是 chat 模式），因为这个页面本身就是在展示一条对话记录。

### `templates/image_history.html`（2026-07-04 新增）

- `history.html` 的图片版兄弟页面，但**没有游客分支**——`main.py` 的 `view_image_history()` 已经在渲染前把游客/匿名访客重定向到 `/`，所以这个模板到达时 `serverHistoryEntry` 必然非空，不需要 `isGuestView`/`targetHistoryId` 查 `sessionStorage` 那一套逻辑。
- 只读区域展示 `entry.prompt`（"Original Image Prompt"）+ 8-key 图片 DTO 网格（`.image-results-grid`/`.image-result-card`，样式与 `index.html` 图片模式的结果区一致），下载按钮复用同一套浏览器端 `downloadImage()`（不做服务端代理）。
- 侧边栏 Recents 一直是图片模式：`loadSidebarImageHistoryList()`/`renderCurrentHistory()` 等函数是 `index.html` 图片侧边栏逻辑的独立副本（拉取 `/api/image-history`，导航到 `/image-history/<id>`），没有 `sidebarMode` 变量——这个页面永远只展示图片历史。
- **`.sidebar-top` 按钮同一次更新（2026-07-04）改为 `.btn-icon`/`.btn-label` span 结构，且语义随之调转**：旧版"+ New Chat"→`/`、"🎨 Generate Image"→`/?mode=image`——即"跳转到别处"的那个按钮反而是固定文案的"进入图片模式"，"+ New Chat"才是离开当前（图片）语境的那个。这与 `index.html` 已经确立的规则（`#newChatBtn`/"+ New" 永远**不**强制切换模式，只在当前语境下清空重来；模式切换是 `#generateImageBtn` 专属的职责）互相矛盾。改为：`#newChatBtn` 固定跳 `/?mode=image`（这个页面永远代表图片语境，"+ New" 留在图片模式、开始新一批生成）；`#generateImageBtn` 图标/文案改为固定的"✍️"+"Generate Text"（不再是"🎨"+"Generate Image"），点击跳 `/`（切到对话模式）——与 `history.html` 的 `#generateImageBtn`（固定"🎨"+"Generate Image"→`/?mode=image`）方向刚好相反，两个只读页面的模式切换按钮互为镜像。
- 删除当前正在查看的条目会跳转到 `/`（同 `history.html`）。

### `templates/auth/*`

- `base.html`：三态导航栏 + Flash 显示区 + `.card-title` 复用类（登录/注册/资料页标题统一样式）。
- `login.html`/`register.html`：Back to welcome / Continue as guest 快捷入口。

### `templates/apikey-config.html`（2026-07-04 新增，同日追加导航入口 + 逐字段清空按钮，随后又随 English-only 政策翻译成英文）

- 继承 `auth/base.html`（复用其导航栏/`.card`/`.form-group`/`.btn` 样式），**无登录态守卫**——`main.py` 的 `apikey_config()` 路由不检查 `session`，任何人（含匿名访客）都能访问；这是刻意的，因为页面本身不发起任何需要权限的请求，纯粹是"把一个字符串存进浏览器本地存储"。
- 3 个输入框：`#chatgptKeyInput`/`#geminiKeyInput`/`#claudeKeyInput`。**只有 Claude 一个字段真正接入业务逻辑**——页面加载时用 `localStorage.getItem('user_claude_key')` 回填输入框，提交表单时用 `localStorage.setItem('user_claude_key', ...)`（值为空则 `removeItem`）写回。ChatGPT/Gemini 两个输入框按第 3 节要求渲染为 `disabled` 占位符，**不接入任何存储**——未来要实现时应该各自新增独立的 `localStorage` key（如 `user_chatgpt_key`/`user_gemini_key`），不应该复用 `user_claude_key`。
- **顶部导航栏永久入口（2026-07-04 新增）**：所有渲染 `session.user_id` 登录态导航分支的模板（`templates/auth/base.html`、`templates/index.html`、`templates/history.html`、`templates/image_history.html`）都在 "Profile" 与 "Logout" 之间插入了一条 `<li><a href="{{ url_for('apikey_config') }}">API Keys</a></li>`。**只在已登录分支插入**——游客/匿名访客本来就看不到 Profile/Logout，因此也看不到这条链接，与"Profile/Logout 只在登录态渲染"这条既有规则保持一致；这不是新的访问限制，游客/匿名依旧可以像之前一样通过直接访问 `/apikey-config` URL 到达页面（路由本身仍然没有登录态守卫），只是导航栏里不会出现这个入口。四处 markup 各自维护（`auth/base.html` 供 login/register/profile/apikey-config 四个页面共用；`index.html`/`history.html`/`image_history.html` 各自有独立的一份导航栏 markup，历史上就是分别维护、不共享同一个 Jinja include），因此每处都需要同步插入，不能只改一处。
- **逐字段"Clear"按钮（2026-07-04 新增）**：每个 API Key 输入框右侧新增一个同宽的 `.btn-clear-key` 按钮（`#clearChatgptKeyBtn`/`#clearGeminiKeyBtn`/`#clearClaudeKeyBtn`，包在新增的 `.key-input-row` flex 容器里，与输入框并排）。**只有 `#clearClaudeKeyBtn` 是可用的**——点击后立即 `claudeKeyInput.value = ''` + `localStorage.removeItem('user_claude_key')`，不等待/不需要用户再点提交按钮；这是刻意的即时生效设计，服务于"用户不想再被继续用自己的 Key 扣 credits"这个场景——如果清空必须先手动清空输入框再点提交才会生效，中途关闭标签页会让清空意图落空（用户以为已经清空，实际上 `localStorage` 里的旧 Key 仍在，下一次请求依然会带着它发出去）。`#clearChatgptKeyBtn`/`#clearGeminiKeyBtn` 与它们各自的输入框一样渲染为 `disabled`——**不接入任何存储**，只是让"每个字段都有一个清空按钮"在视觉上一致，不违反上一条"ChatGPT/Gemini 不接入任何存储"的既有规则；未来这两个 Provider 真正接入时，应该同时解锁输入框和清空按钮，参照 Claude 字段的模式各自独立接线，而不是共用 Claude 的清空逻辑。
- **本页曾经是全项目用户可见中文最集中的一处**（`<title>`、`<h2>` 标题、说明段落、ChatGPT/Gemini 占位提示、3 个"清空"按钮、"保存"提交按钮、"← 返回应用"链接、JS 里的保存/清空提示），2026-07-04 当天晚些时候随"English-only UI text 政策"（见第 6 节）全部改为英文：`<title>API Key Configuration - LLM Aggregator</title>`、`<h2>Personal API Key Configuration</h2>`、`(Not supported yet, coming soon)`、`Clear`/`Save`/`← Back to App`、`Saved`/`Cleared. Future requests will no longer include this key.`。回归测试见 `tests/test_english_only_blackbox.py::TestApikeyConfigPageIsEnglishOnly`（页面级英文断言）与 `tests/test_apikey_config_blackbox.py`（导航链接位置 + 三个清空按钮的 disabled/enabled 状态 + Claude 清空按钮的 `localStorage.removeItem` 接线，2026-07-04 新增，与英文化改造各自独立提交、互不覆盖）。
- 提交表单不发起任何网络请求——纯客户端 `localStorage` 读写，`e.preventDefault()` 后本地展示 "Saved" 提示（`#saveStatus`，2.5 秒后自动隐藏）。真正消费这个 Key 的地方是 `index.html` 里 `fetchClaudeResult()` 每次调用 `/api/claude-chat` 时从 `localStorage` 读出、放进 `X-User-Claude-Key` 请求头——`apikey-config.html` 本身与 `/api/claude-chat` 没有任何直接耦合。

## 5. 🔄 EXECUTION & DATA FLOW

1. **初始化**：`load_dotenv()` → `secret_key` 加载 → `auth_bp` 注册 → Firebase 初始化（key 文件优先于 ADC）→ `g4f` 导入探测 → `anthropic` 导入探测（2026-07-04 新增，独立于 g4f 的探测结果）。
2. **身份路由**：访问 `/`，`index()` 检查 `session.user_id`/`is_guest`，渲染 `home.html` 或 `index.html`。
3. **身份建立**：游客走 `POST /api/auth/guest`；登录/注册走对应表单，成功后写入/清除对应 session 键。
4. **LLM 聚合**：`POST /api/compare` → 第一阶段并发 `test_g4f_provider`（应用 `ROUTE_PROMPTS_MAP` 隐形路由）→ 满足条件时第二阶段并发互评 → 排序 → 已登录时 `save_chat_history` → 返回 JSON（含 `history_id`）。
5. **文生图**：`POST /api/generate-images` → 单阶段并发 `test_g4f_image_provider` → 排序 → 已登录时 `save_image_history` → 返回 JSON（含 `history_id`，无互评）。
6. **退出登录**：清除三个 session 键 → flash → 重定向 `/` → `home.html` 消费 flash。
7. **文生图历史查看**（2026-07-04 新增）：点击图片版 Recents 条目 → `GET /image-history/<id>` → 游客/匿名重定向 `/`；已登录经 `get_image_history_by_id` 校验归属渲染 `image_history.html`（只读）。
8. **Claude 对话**（2026-07-04 新增）：前端提交 `compareForm` 时若 Claude 勾选框被选中 → 在拿到 `/api/compare` 结果后额外单独发起 `POST /api/claude-chat` → `_get_authenticated_user_id()` 守卫（游客/匿名 401）→ 有 `X-User-Claude-Key` 则直接用它调用，跳过额度检查；否则先查 `get_claude_free_tier_usage`，达到 `CLAUDE_FREE_TIER_LIMIT`（1）则 403 `FREE_TIER_EXHAUSTED` → `call_claude_model()` 调用官方 API → 遇余额耗尽（`error.message` 含 `"credit balance"`，实测形状；`error.type == 'billing_error'` 作为兼容兜底）翻译为 503 `SERVER_CREDITS_EXHAUSTED` → 成功且未用自带 Key 时 `increment_claude_free_tier_usage` → 返回结果，前端并入同一份 `results` 渲染。

## 6. 🧠 CORE LOGIC / DOMAIN RULES

### 身份三态不变量

| 状态 | session | 根路径行为 |
|---|---|---|
| 匿名 | 无 `user_id`/`is_guest` | 渲染 `home.html`，不存储数据 |
| 游客 | `is_guest=True` | 渲染 `index.html`+Guest 徽章；数据仅前端内存/sessionStorage，不持久化；可访问 login/register，不可访问 profile |
| 已登录 | `user_id` 存在 | `username` 同步存储；数据与 Firestore 同步；可访问 profile |

三键互斥：`user_id` 存在时 `is_guest` 必须已清除，反之亦然。

### Session 转换规范

- 任何完成身份切换的重定向目标页面必须含 Flash 显示区。
- 游客→登录：`session['user_id']`/`username` 写入 + `is_guest` 清除，三步同一请求内原子完成。
- 登出：三个键全部清除。
- `GET /home` 只清 `is_guest`，不动 `user_id`。

### 模型自适应降级规则（文本，`determine_actual_model`）

- 规则 A：指定模型在映射表内 → 用它。
- 规则 B：不支持/未指定 → 映射表第一个。
- 规则 C：Provider 无模型配置 → 兜底 `"gpt-3.5-turbo"`。

文生图版 `determine_actual_image_model` 只有规则 A/B，**没有规则 C**——Provider 不在映射表时返回 `None`，由调用方展示为 `'default'`。

### AI 盲评规则

- 触发：`len(providers_to_test) >= 2` 且 `len(successful_results) >= 2`。
- 每个成功者 A 由其余所有成功者 B 评价（B 不评自身）。
- `parse_peer_review_json` 容错：解析失败一律 fallback `(80, raw_text)`，score 夹入 [1,100]。
- 失败者不参与互评（既不被评也不当裁判）。

### 排序规则

成功优先，同状态下耗时升序。文本和图片两条路由共用同一排序表达式。

### 异常文案判定顺序（`test_g4f_provider`/`run_peer_review` 共用，**不可颠倒**）

`CONTENT_POLICY_ERROR_KEYWORDS` 必须优先于 `NETWORK_ERROR_KEYWORDS`/`PEER_REVIEW_NETWORK_ERROR_KEYWORDS` 判定——内容审查类错误（如 Azure OpenAI）重试无意义，误判成"系统正忙"会诱导用户做无效重试。

### 文生图异常文案判定顺序（`test_g4f_image_provider` 专属，**不可颠倒**，2026-07-04 新增）

`GPU_QUOTA_ERROR_KEYWORDS` 必须优先于 `PEER_REVIEW_NETWORK_ERROR_KEYWORDS` 判定——HuggingFace ZeroGPU Space 后端（`BlackForestLabs_Flux1Dev`/`StabilityAI_SD35Large`）的免费 GPU 配额耗尽错误（含 `"zerogpu"`/`"gpu token limit"`/`"gpu quota"` 关键词）需要独立于网络类错误单独给出"配额耗尽，请稍后再试或换个 Provider"的友好提示，不落入通用的"系统正忙"文案，也不重试（见下条重试规则）。

### 文生图重试规则（`test_g4f_image_provider` 专属，2026-07-04 新增，与 `run_peer_review` 同构）

- 仅 429 / queue-full 类瞬时限流错误重试一次（等待 2~3 秒随机抖动后重试）。
- GPU 配额耗尽错误、内容策略错误等**不重试**——重试对已耗尽的配额无意义，且会对本就紧张的免费资源造成额外压力。
- 重试耗尽后的错误分类需用 `PEER_REVIEW_NETWORK_ERROR_KEYWORDS`（含 429/queue）而非 `NETWORK_ERROR_KEYWORDS`，否则重试失败后的 "Error 429: ..." 原始文本会漏给前端。

### 文生图超时预算规则（2026-07-04 新增，同日内二次修订）

- 默认 `IMAGE_GENERATION_ADVISORY_TIMEOUT=40`；outer **不是**存出来的常量，而是由 `get_image_timeouts()` 内部的 `_compute_outer_timeout(advisory) = 2*advisory + IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER(5)` 现算得到，默认场景下算出 85。
- 公式是"2 倍 advisory + 小缓冲"而不是"1 倍 advisory + 大缓冲"：`test_g4f_image_provider` 的 429/queue 重试会跑最多两次尝试，每次尝试各自都可能跑到接近满 advisory_timeout 才结束（不是"第一次快速失败、重试后立刻成功"的理想情况），所以 outer 必须能覆盖两次满額尝试，而不是一次满額尝试加一点缓冲。这是 2026-07-04 第二次修订的核心改动——同日更早的版本用的是"advisory+固定 10s"公式，在 `PollinationsImage` 命中 429 重试、且重试后的第二次请求本身耗时较长时不够用（详见第 9 节），暴露出固定缓冲公式对"重试会让某次尝试单独耗时接近 advisory"这一场景覆盖不足，且这个场景对所有会重试的 Provider 都成立，不是单一 Provider 的特例。
- `AnyProvider` 是 g4f 的聚合型 Provider（内部依次尝试多个真实后端直到成功），实测耗时明显更长，通过 `IMAGE_PROVIDER_TIMEOUT_OVERRIDES` 单独给它 advisory 70 的预算（**只覆盖 advisory**，不再单独覆盖 outer）；它的 outer 同样由上面的公式从 70 现算得到 145，与默认 Provider 用同一套公式，不会出现"AnyProvider 的 outer 单独手写、可能与公式脱节"的问题。
- 未来若某个 Provider 实测比 advisory 70 还需要更长时间，应该给它加 `IMAGE_PROVIDER_TIMEOUT_OVERRIDES` 的 `advisory` 条目（outer 自动跟着公式算出来），而不是笼统调高全局 `IMAGE_GENERATION_ADVISORY_TIMEOUT` 默认值（会拖慢所有 Provider 批次的最坏情况等待时间）；但如果未来又出现"重试后仍被提前判超时"这种和某个具体 Provider 无关、纯粹是时序覆盖不足的问题，应该先检查是不是 `_compute_outer_timeout()` 公式本身的系数（2 倍 + 5s 缓冲）又不够了，而不是继续给单个 Provider 加 override——这正是本次修复要避免重复的模式。

### 文生图并发调度规则

- 无互评阶段。
- Provider 名字空间隔离：`IMAGE_PROVIDERS` 与 `G4F_PROVIDERS` 互不越界。
- **不触发 `save_chat_history`**——已登录用户改为触发独立的 `save_image_history()`（写入 `image_history` 集合，2026-07-04 新增），不是复用聊天历史的持久化路径；游客与匿名两者都不触发。

### 文生图 Recents 访问限制（2026-07-04 新增）

- 图片版 Recents 侧边栏与 `/image-history/<id>` 只读详情页**仅对已登录用户开放**——这是与对话历史刻意不同的设计决策，不是遗漏：对话历史对游客提供 `sessionStorage` 客户端临时记录（见"游客 Recents"不变量），图片历史对游客完全不提供任何形式的记录。
- 后端：`generate_images()` 只在 `session.get('user_id')` 时调用 `save_image_history()`；`/api/image-history` 系列路由复用 `_get_authenticated_user_id()`，游客与匿名一律 401；`view_image_history()` 对游客/匿名直接重定向 `/`，不像 `view_history()` 那样渲染游客空壳。
- 前端：`renderCurrentImageHistoryList()` 在 `!isLoggedIn` 时只渲染锁定文案，**不发起任何网络请求**，因此游客即使手动切到图片模式，侧边栏里也不会出现任何可点击的历史条目——三层防御（不落库/后端 401/前端不请求）共同保证这条限制。

### Claude 权限控制与防滥用策略（2026-07-04 新增）

Claude 是本项目第一个"调用有真实成本"的 Provider（其余 g4f Provider 都是免 Key 的第三方免费渠道），因此配套一整套权限拦截与开发者成本保护机制，比其余任何 Provider 都更严格。

**1. 游客/匿名一律拦截（三层防御，与图片历史 Recents 对游客的处理同构）：**

- **前端**：`index.html` 渲染时按 `session.user_id` 决定 `#claudeProviderTrigger` 是否 `disabled`，游客/匿名看到的是置灰卡片 + `title` 悬浮提示 + 卡片内 `<small>` 文案，三处同时展示 `"Log in to unlock frontier models"`（原为中文，见第 6 节"English-only UI text 政策"）。
- **后端**：`/api/claude-chat` 复用 `_get_authenticated_user_id()`（与对话/图片历史路由同一套守卫），游客（只有 `is_guest`，没有 `user_id`）与匿名一律 401——即使有人绕开前端直接 `curl` 这个接口也拦得住。
- **没有第三层"游客降级"**：不像对话历史那样给游客一个 `sessionStorage` 客户端镜像，也不像文生图历史那样"完全不可见"——Claude 对游客就是单纯地"不可调用"，没有任何形式的降级体验，这是本项目目前对成本最敏感的一个 Provider，故意不给游客开任何口子。

**2. 单账号 1 次免费额度（`CLAUDE_FREE_TIER_LIMIT = 1`）：**

- 计数器是 `users` 集合文档上的整型字段 `claude_free_tier_usage`（`auth/db.py` 的 `get_claude_free_tier_usage`/`increment_claude_free_tier_usage`，见第 4 节），初始隐式为 0，不需要在 `create_user()` 里预写。
- **仅在未携带自带 Key 时**才检查/消耗这个计数器：`claude_chat()` 路由先看请求头 `X-User-Claude-Key` 是否非空，非空则整个免费额度体系形同虚设（既不检查是否超限，也不递增）。
- **只有调用成功才递增**——`call_claude_model()` 返回 `success: False` 的失败调用（含网络错误、模型侧异常）不消耗用户的免费额度，避免用户因为瞬时故障"白白"损失试用机会。
- 超限（`usage >= CLAUDE_FREE_TIER_LIMIT`）时，后端**直接拦截、完全不调用 `call_claude_model()`**（不消耗开发者账户的任何 API 调用），返回 `{"error": "FREE_TIER_EXHAUSTED"}`（403）。前端收到后弹出 `showClaudeUpgradeModal()` 模态弹窗，引导跳转 `/apikey-config`。
- **已知的非原子性**：检查与递增是两次独立的 Firestore 读写，中间没有事务保护，极端并发下理论上可能被多用 1 次——这是刻意的简化（详见 `auth/db.py` 关键点小节的说明），不是疏漏。

**3. 用户自带 Key（`X-User-Claude-Key`）的流转架构：**

```
apikey-config.html（用户填入 Key）
   → localStorage.setItem('user_claude_key', key)   ← 纯客户端，不经过任何后端请求
   ↓（用户之后每次在 index.html 发起 Claude 请求）
index.html: fetchClaudeResult()
   → localStorage.getItem('user_claude_key')
   → fetch('/api/claude-chat', headers: {'X-User-Claude-Key': key})
   ↓
main.py: claude_chat()
   → request.headers.get('X-User-Claude-Key')  ← 每次请求都从 Header 读取，后端不持久化用户的 Key
   → 非空则 using_own_key=True：跳过额度检查/递增，call_claude_model(prompt, model, user_api_key=key)
   ↓
call_claude_model()
   → anthropic.Anthropic(api_key=user_api_key)  ← 用用户自己的 Key 实例化客户端，本次调用完全计入用户自己的 Anthropic 账户，不消耗开发者的 ANTHROPIC_API_KEY 额度
```

关键不变量：**后端从不持久化用户的个人 Key**——它只存在于浏览器 `localStorage` 和单次请求的 Header 里，每次请求都要求前端重新携带；服务器进程内也不缓存"这个 user_id 上次用过哪个 Key"。

**4. 开发者账户余额耗尽的错误转发（对开发者自己的保护）：**

- `call_claude_model()` 捕获 `anthropic.APIStatusError`，检查 `error.message` 是否含 `"credit balance"` 关键词（不区分大小写），命中即判定为余额耗尽；同时兼容性地保留 `error.type == 'billing_error'` 作为第二条判断线索。
- 命中时翻译为内部标记 `error_code: 'SERVER_CREDITS_EXHAUSTED'`，`claude_chat()` 路由据此返回 503 `{"error": "SERVER_CREDITS_EXHAUSTED", "message": "Developer account balance is insufficient. Please configure your personal API key to continue."}`——**不消耗用户的免费额度计数**（余额耗尽是开发者账户侧的问题，不该算在用户头上）。**该 message 字段最初是中文**（"开发者账户余额不足，请配置您的个人 API Key 继续使用"），2026-07-04 当天晚些时候随"English-only UI text 政策"（见第 6 节该小节末尾）统一改为上述英文文案；`tests/test_english_only_blackbox.py::TestClaudeServerCreditsExhaustedMessageIsEnglish` 锁定了这个字符串的精确值。
- **与任务描述的偏差、已实测修正（两次修正）**：最初任务描述设想的是"捕获 429 且错误类型为 `insufficient_funds`"，但 Anthropic 官方错误体系里并不存在 `insufficient_funds` 这个类型字符串。第一次修正参照通用文档字面含义，改成检查 `error.type == 'billing_error'`（文档暗示映射到 403）。**第二次修正来自真实验证**：把开发者的 `ANTHROPIC_API_KEY` 配置为一个真实余额为 0 的账户，直接用官方 `anthropic` SDK 调用 `client.messages.create()` 触发真实错误，观察到的实际形状是 **400 + `error.type == 'invalid_request_error'` + message 含 `"Your credit balance is too low to access the Anthropic API"`**——既不是 429/`insufficient_funds`，也不是 403/`billing_error`。因此最终判断依据改为检查 `error.message` 里的 `"credit balance"` 关键词（这是跨 `status_code`/`error.type` 都稳定出现的信号），`billing_error` 类型检查作为兼容性兜底保留。已在浏览器里端到端验证：登录用户勾选 Claude 发起真实请求，结果卡片正确显示当时的中文友好文案，而不是泄漏原始英文错误；且该失败调用没有递增用户的 `claude_free_tier_usage`（Firestore 读取确认为 0）。测试见 `tests/test_claude_integration.py::TestCallClaudeModelKeyRouting::test_real_world_credit_balance_error_maps_to_server_credits_exhausted`（默认参数即真实观测到的形状）与 `test_billing_error_type_also_recognized_defensively`（兼容性分支）。**第二轮真实验证（开发者账户充值到 $5 余额后，同日晚些时候）**：用同一个真实 `ANTHROPIC_API_KEY`（此时已有正余额）通过浏览器完整走了一遍成功路径——注册新账号登录、勾选 Claude（Haiku 4.5）、提交真实 prompt，结果卡片正确显示 `Success` + 真实模型回答（如"2+2 equals 4."），且 Firestore 里该用户的 `claude_free_tier_usage` 从 0 正确原子递增为 1；同账号发起第二次请求（仍未带自带 Key）被正确拦截为 403 `FREE_TIER_EXHAUSTED`，前端正确弹出 `showClaudeUpgradeModal()`；随后在 `/apikey-config` 页面填入同一个真实 Key 存入 `localStorage`，再次提交请求，验证 `X-User-Claude-Key` 请求头正确绕开额度检查、返回 200 成功结果，且计数器仍保持 1（未被消耗）。这一轮补齐了"正常成功路径 + 额度耗尽拦截 + 自带 Key 绕过"在真实账户下的端到端验证，与第一轮"开发者账户余额耗尽"的验证互补，两轮合起来覆盖了 Claude 集成里所有对外可观察的分支。测试过程中额外发现 `main.py` 里 `claude_chat()` 路由上方的一行说明注释仍停留在修正前的表述（"call_claude_model() 内部把余额耗尽（403 + billing_error）翻译成..."），与其正上方 `call_claude_model()` 自身的注释块（已经是修正后的表述）不一致——这只是文档性注释滞后，不影响实际逻辑（代码本身一直用的是 `"credit balance"` 关键词判断），已同步改为与实测行为一致的表述。

**5. English-only UI text 政策（2026-07-04 当天晚些时候新增）：**

用户明确要求"使用这个软件的时候不希望看到任何中文，必须全英文"。审计发现全项目面向用户的中文只集中在少数几处（其余渲染文案早已是英文），逐一改为英文：
- `templates/index.html`：Claude Provider 卡片的游客锁定提示（`title` 属性 + `<small>` 文案）"请登录以解锁前沿模型" → `"Log in to unlock frontier models"`；`showClaudeUpgradeModal()` 弹窗标题/正文/两个按钮（"前沿模型试用额度已用完"/"您的免费前沿模型试用额度（1次）已用完。请配置您个人的 API Key 以继续使用高级模型。"/"稍后再说"/"去配置 API Key"）全部改为英文（见该函数定义处）。
- `templates/apikey-config.html`：全页面唯一一处大量中文用户可见文案（`<title>`、`<h2>` 标题、说明段落、ChatGPT/Gemini 占位提示"(暂不支持，敬请期待)"、三个"清空"按钮、"保存"提交按钮、"← 返回应用"链接、JS 里的"已保存"/"已清空，后续请求将不再携带该 Key"提示）全部改为英文（"API Key Configuration"/"Personal API Key Configuration"/"(Not supported yet, coming soon)"/"Clear"/"Save"/"← Back to App"/"Saved"/"Cleared. Future requests will no longer include this key."）。
- `main.py`：`claude_chat()` 路由里 `SERVER_CREDITS_EXHAUSTED` 的 `message` 字段（唯一一处后端返回给前端、会被渲染到失败结果卡片上的中文字符串）改为英文（见上方"4. 开发者账户余额耗尽的错误转发"小节）。

**审计范围与刻意排除的部分**：审计覆盖了全部 `templates/*.html`（含 `auth/*`）与 `main.py`/`auth/db.py`/`auth/routes.py` 里所有加引号的字符串字面量（`flash()` 消息、JSON 错误体、`ROUTE_PROMPTS_MAP`/`PEER_REVIEW_PROMPTS_MAP` 里发给 LLM 的实际 prompt 文本、`SENSITIVE_KEYWORDS`/`NETWORK_ERROR_KEYWORDS` 等友好文案），确认除上述三处外均已是英文（flash 消息、prompt 文本、异常友好文案在本次审计前就已经是英文，不是这次新翻译的）。**刻意不翻译**的是 Python `#`/JS `//`/CSS 及 HTML `/* */`、`<!-- -->` 里的代码注释——这些内容只出现在源码/页面源码里，浏览器渲染的可见 UI 与用户实际会看到的报错文案都不会包含它们；CLAUDE.md 本身作为项目工程文档也一直是中文写就，中文仍是本项目的内部工程语言，这条政策只约束"用户使用软件时能看到的内容"，不要求把内部注释或本文档翻译成英文。回归测试见 `tests/test_english_only_blackbox.py`（渲染页面可见文本/属性扫描 + Claude 报错文案精确值 + prompt 工程文本扫描）。

## 7. 🧾 DATA MODELS

### LLM Result（7-key，严禁增删；`peer_reviews` 由 `compare_providers` 外层追加成 8-key）

```python
{
    'provider': str, 'success': bool, 'response': str, 'error': str,
    'response_time': float, 'model': str, 'type': 'g4f'
}
# 追加：
result['peer_reviews'] = [
    {'reviewer_provider': str, 'reviewer_model': str, 'score': int, 'comment': str}, ...
]
```

### Image Result（8-key，独立契约，严禁与文本 DTO 混用）

```python
{
    'provider': str, 'success': bool, 'url': str | None, 'b64_json': str | None,
    'error': str, 'response_time': float, 'model': str, 'type': 'g4f_image'
}
```

`url`/`b64_json` 互斥（成功时只有一个非 None）。`/api/generate-images` 响应体无 `peer_reviews`；**有** `history_id`（2026-07-04 起，已登录用户为实际 id，游客/匿名为 `None`，与 `/api/compare` 同构）。

### Claude Result（2026-07-04 新增；`/api/claude-chat` 响应体本身即此形状，不额外包一层）

```python
{
    'provider': 'Claude', 'success': bool, 'response': str, 'error': str,
    'response_time': float, 'model': str,  # CLAUDE_MODELS 的 key，如 'claude-sonnet-5'
    'type': 'anthropic',
}
# 余额耗尽时额外携带（main.py 的 claude_chat() 路由据此转换成顶层 JSON 错误体，
# 不会原样出现在最终响应里）：
result['error_code'] = 'SERVER_CREDITS_EXHAUSTED'
```

与 LLM Result（7-key，`type: 'g4f'`）结构相似但**是独立的第三种 DTO**——`type` 字段值不同（`'anthropic'` vs `'g4f'`），且**没有** `peer_reviews`（Claude 不参与互评，见第 6 节）。前端渲染时会手动补一个空的 `peer_reviews: []` 字段以复用 `renderPeerReviews()`/`displayResults()` 的渲染逻辑，但这只是前端渲染层的兼容处理，后端从不写这个字段。

### Firestore `users` 集合

```python
{
    'username': str, 'email': str, 'password_hash': str, 'created_at': Timestamp,
    'claude_free_tier_usage': int,  # 2026-07-04 新增；字段不存在时按 0 处理，不需要在
                                     # create_user() 里预先写入（见 auth/db.py 关键点小节）
}
```

### Firestore `history` 集合（仅登录用户，游客不写入）

```python
{
    'user_id': str, 'title': str,  # prompt[:15]+'...' if len(prompt)>15 else prompt
    'prompt': str, 'results': list,  # 严格 8-key result DTO（含 peer_reviews）
    'created_at': Timestamp, 'is_pinned': bool,
    'pinned_at': Timestamp,  # 仅置顶项存在
}
```

### Firestore `image_history` 集合（2026-07-04 新增；仅登录用户，游客/匿名不写入）

```python
{
    'user_id': str, 'title': str,  # prompt[:15]+'...' if len(prompt)>15 else prompt
    'prompt': str, 'results': list,  # 严格 8-key image result DTO（无 peer_reviews）
    'created_at': Timestamp, 'is_pinned': bool,
    'pinned_at': Timestamp,  # 仅置顶项存在
}
```

与 `history` 集合结构上几乎一样（同样的 `title`/`is_pinned`/`pinned_at` 语义），**但是独立的集合**——`results` 里存的是 8-key 图片 DTO 而不是 7-key 文本 DTO（+ peer_reviews），两种 DTO 不应混进同一个集合（见第 10 节危险区）。

### 对话历史 CRUD 契约（`auth/db.py`）

| 函数 | 参数 | 成功 | 失败 |
|---|---|---|---|
| `save_chat_history` | `user_id, prompt, results` | 含 `id` 的 dict | `None` |
| `get_chat_history_list` | `user_id, limit=20, offset=0` | dict 列表（Python 层排序分页） | `[]` |
| `get_chat_history_by_id` | `user_id, history_id` | 含 `id` 的 dict | `None` |
| `delete_chat_history` | `user_id, history_id` | `True` | `False` |
| `update_chat_history_title` | `user_id, history_id, new_title` | `True` | `False` |
| `toggle_pin_chat_history` | `user_id, history_id` | 翻转后的 `is_pinned`（`True`/`False`） | `None` |

`toggle_pin_chat_history` 判空必须用 `is None`，`False` 是合法成功结果。

### 图片历史 CRUD 契约（`auth/db.py`，2026-07-04 新增）

与上表逐一同构，仅集合名不同（`image_history`）：`save_image_history`、`get_image_history_list`、`get_image_history_by_id`、`delete_image_history`、`update_image_history_title`、`toggle_pin_image_history`。同样的参数签名、同样的成功/失败返回值形状、同样的归属校验；`toggle_pin_image_history` 判空同样必须用 `is None`。

### Claude 免费额度计数器契约（`auth/db.py`，2026-07-04 新增）

| 函数 | 参数 | 成功 | 失败/Firebase 不可用 |
|---|---|---|---|
| `get_claude_free_tier_usage` | `user_id` | 当前计数（`int`，字段不存在或用户不存在时为 `0`） | `0` |
| `increment_claude_free_tier_usage` | `user_id` | `True` | `None` |

与上面两套历史 CRUD 的关键区别：**没有归属校验的概念**——`user_id` 直接来自 `session['user_id']`，不像历史记录那样需要拿一个外部传入的 `history_id` 反查文档的 `user_id` 字段是否匹配；也**没有独立集合**，读写的是 `users` 集合文档本身的一个字段。

### Session 键

```python
session['user_id']   # str，已登录独有
session['username']  # str
session['is_guest']  # bool=True，游客独有
```

## 8. 🔌 EXTERNAL INTERFACES

**LLM 聚合**：
- `GET /api/providers` — Provider 元数据列表
- `POST /api/compare` — `{prompt, providers, model, max_workers}` → 聚合结果 + `history_id`
- `POST /api/test-single` — 单通道测试
- `GET /health` — 状态、`g4f_available`、`image_providers`、`routing_rules_loaded`、`peer_review_rules_loaded`、`claude_available`/`claude_models`（2026-07-04 新增）

**Claude（官方 Anthropic API，2026-07-04 新增，均需 `session['user_id']`，游客/匿名 401）**：
- `POST /api/claude-chat` — `{prompt, model}`（`model` 必须是 `claude-sonnet-5`/`claude-haiku-4-5` 之一）+ 可选请求头 `X-User-Claude-Key`（非空时使用该 Key，跳过免费额度检查/递增）→ 成功时返回 Claude Result（见第 7 节）；额度耗尽返回 403 `{"error": "FREE_TIER_EXHAUSTED"}`；开发者账户余额不足返回 503 `{"error": "SERVER_CREDITS_EXHAUSTED", "message": "..."}`；`prompt`/`model` 缺失或非法返回 400 `{"error": "INVALID_REQUEST", ...}`；`anthropic` 库不可用返回 503 `{"error": "CLAUDE_UNAVAILABLE", ...}`。

**文生图**：
- `GET /api/image-providers`
- `POST /api/generate-images` — `{prompt, providers, model, max_workers}` → 聚合结果 + `history_id`（已登录用户为实际 id，游客/匿名为 `None`，2026-07-04 起）
- `GET /media/<filename>` — 提供 `get_media_dir()` 下 g4f 已生成的图片/音视频本地文件（Result DTO `url` 字段引用的静态资源，见第 2/9 节）

**页面**：
- `GET /` / `GET /home` / `GET /history/<history_id>` / `GET /image-history/<history_id>`（2026-07-04 新增，仅登录用户，游客/匿名重定向 `/`）/ `GET /apikey-config`（2026-07-04 新增，无登录态守卫，见第 4 节 `templates/apikey-config.html` 小节）

**认证（`auth_bp`）**：`/login` `/register` `/logout` `/profile`

**游客**：`POST /api/auth/guest`

**对话历史**（均需 `session['user_id']`，否则 401）：
- `GET /api/history?page=1&limit=20`
- `PATCH /api/history/<id>/title` — `{new_title}`
- `DELETE /api/history/<id>`
- `POST /api/history/<id>/toggle-pin`

**图片历史**（2026-07-04 新增，均需 `session['user_id']`，否则 401——游客同样被拒绝）：
- `GET /api/image-history?page=1&limit=20`
- `PATCH /api/image-history/<id>/title` — `{new_title}`
- `DELETE /api/image-history/<id>`
- `POST /api/image-history/<id>/toggle-pin`

### 第三方集成

- **g4f**：无凭证调用免费 AI 渠道（`Yqcloud`、`OperaAria`、`PollinationsAI` 等文本；`PollinationsImage`、`BlackForestLabs_Flux1Dev`、`AnyProvider`、`StabilityAI_SD35Large`、`OperaAria` 等图片）。**Gemini 生图不可免 Key 使用**（`AnyProvider`/`GeminiPro` 均已验证失败，需用户自备 API Key，与本项目"零凭证"设计原则冲突，暂不集成）。
- **Firebase Admin SDK**：本地用 `firebase-key.json`，GAE 用 ADC。
- **Anthropic 官方 API**（2026-07-04 新增）：官方 `anthropic` Python SDK，是本项目第一个"需要真实付费 Key"的第三方集成——与上面 g4f 系"零凭证"的设计原则刻意不同，因此配套了第 6 节"Claude 权限控制与防滥用策略"整套机制。开发者默认 Key 来自 `ANTHROPIC_API_KEY` 环境变量；用户可在 `/apikey-config` 配置个人 Key 完全绕开开发者额度。当前接入 `claude-sonnet-5`（官方 alias 本身即模型 ID）与 `claude-haiku-4-5`（UI 展示用 key，映射到官方精确 ID `claude-haiku-4-5-20251001`）两个模型。

## 9. ⚠️ SYSTEM RISKS (当前仍需注意)

- **超时同步要求**：互评阶段 `run_peer_review` 内部 advisory `timeout=25` 与 `compare_providers` 外层 `future.result(timeout=32)` 必须同步调整（约 7 秒调度缓冲）；文生图默认 `IMAGE_GENERATION_ADVISORY_TIMEOUT=40`，outer 由 `get_image_timeouts()` 现算为 `2*advisory+IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER(5) = 85`（2026-07-04 第二次调整，见下一条）。HuggingFace Space 类后端有真实冷启动延迟，不要凭直觉调低。
- **`AnyProvider` 曾因 outer timeout 过早判定超时而丢失已生成的图片（2026-07-04 首次修复）**：`AnyProvider` 是 g4f 的聚合型 Provider，内部依次尝试多个真实图片后端直到成功，耗时明显长于其余单一后端 Provider，且方差大。旧版所有 Provider 共用同一个 45s outer timeout 时，`AnyProvider` 经常在图片其实已经生成并下载到本地 `get_media_dir()` 之后才真正返回——但 `future.result(timeout=45)` 早已超时放弃该 future，前端只能看到 "system is busy" 的 Failed 兜底文案，生成的图片文件被静默丢弃（`ThreadPoolExecutor` 不会杀死已提交的线程，线程会继续跑完并写盘，只是结果无人收）。首次修复方式：新增 `IMAGE_PROVIDER_TIMEOUT_OVERRIDES` 单 Provider 超时覆盖表 + `get_image_timeouts(provider_name)` 辅助函数，给 `AnyProvider` 单独分配 advisory 70s/outer 80s 的预算。
- **`PollinationsImage` 同样因 outer timeout 过早判定超时而丢失已生成的图片，暴露出 outer 计算公式本身的缺陷（2026-07-04 第二次修复）**：日志显示 `PollinationsImage` 命中一次 429、按设计重试，但重试后的第二次请求本身耗时较长，图片其实已经生成并写入 `get_media_dir()`——但当时 outer timeout 只是"advisory(40) + 固定 10s 缓冲 = 50s"，这个固定 10s 缓冲只够覆盖"重试前的等待"，根本不够覆盖"第二次尝试本身也可能跑到接近满 advisory 才成功"的情况，于是 `future.result(timeout=50)` 依然提前放弃，前端展示为 Failed，同一类问题在 `AnyProvider` 之外的其他 Provider 上复现。这次没有再走"给这一个 Provider 单独加 override"的老路——因为 429 重试是 `test_g4f_image_provider` 里对**所有**图片 Provider 通用的逻辑，任何 Provider 都可能在重试后需要接近满 advisory 的时间才成功，是公式本身的问题，不是某个 Provider 特例。修复方式：把 outer 的计算公式从"advisory + 固定缓冲"改成"`2*advisory + IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER(5)`"（`_compute_outer_timeout()`），覆盖"两次尝试都跑满 advisory"的最坏情况；`IMAGE_PROVIDER_TIMEOUT_OVERRIDES` 里也相应只保留 `advisory` 键，`AnyProvider` 的 outer 不再是手写的 80，而是由同一公式从它的 advisory(70) 现算出 145，与默认 Provider 用同一套公式、不会互相脱节。默认 outer 因此从 50 变为 85；`AnyProvider` 的 outer 从 80 变为 145。回归测试：`tests/test_main_blackbox.py::TestGenerateImagesEndpoint::test_slow_retry_success_not_discarded_by_outer_timeout` 用缩小版的 advisory/buffer 复现了"重试后耗时接近 advisory 但仍应算成功"的场景，并验证了它在旧公式下确实会失败。（outer timeout 是每个 `future.result()` 独立计算的，不会因为某个 Provider 预算变长而拖慢同批次里其他 Provider 的等待时间——这条不变量没有变化。）
- **线程池**：`max_workers = min(请求值, 5)` 且不超过实际 Provider 数。
- **SECRET_KEY 持久化**：未设置时每次重启生成新密钥，所有 session 失效。生产环境必须在 `app.yaml` 固定设置。
- **Firebase 凭据惰性解析**：`ApplicationDefault()` 构造函数不立即验证凭据，必须优先检测 `firebase-key.json`。
- **Firestore 复合索引**：`get_chat_history_list` 故意只做单字段等值查询、排序放在 Python 层，避免依赖需要在 Firebase 控制台手动创建的复合索引（曾经的复合查询写法导致生产环境 500，不要恢复）。
- **`zoom` 与 `vh` 叠加陷阱**：`body` 有非标准的 `zoom:var(--page-zoom)`（当前 `0.88`）。`vh` 单位不随 `zoom` 缩放，因此涉及视口高度的 CSS 必须用 `calc(100vh/var(--page-zoom))`，JS 里从 `getBoundingClientRect()` 得到的物理像素赋回 `style.top/height` 前必须除以 `--page-zoom`（但 `scrollTop`/`scrollHeight`/`clientHeight` 等纯局部比例值不需要）。若调整缩放比例，只改 `--page-zoom` 这一处变量。
- **原生滚动条已彻底隐藏**：Recents 侧边栏与整页均用 JS 自绘可拖拽指示器（`#sidebarRecentsScrollThumb`/`#pageScrollThumb`）取代原生滚动条，因为 Chrome 的 overlay 滚动条无法被 `::-webkit-scrollbar` 样式可靠压制、且会阻挡自绘指示器的鼠标事件。
- **侧边栏高度不依赖 JS 手工计算**：`.left-sidebar` 用纯 CSS `calc()` 定高（`position:sticky`），不要改回 JS 对 `window.innerHeight` 做整数运算再注入字面量像素值——那会因缩放叠加取整误差导致侧边栏高度计算不准。
- **`/media/<filename>` 是本项目自行补的静态文件路由（2026-07-04），不是 g4f 自带的**：`g4f.client.Client().images.generate()` 返回的 `url` 字段（形如 `/media/<filename>?url=...`）遵循的是 g4f 自带 GUI/API 服务器的路由约定，但本项目只用了 g4f 的 client 库、并未启动那套服务器。曾经因为缺少这条路由，前端 `<img>` 和下载按钮请求 `/media/...` 全部 404（下载按钮会把 404 错误页当成图片字节保存，表现为"不支持的文件格式"）。若未来升级 g4f 导致其内部 media URL 格式变化，需要同步检查 `serve_generated_media()`/`get_media_dir()` 是否仍然兼容。
- **`generated_media/` 无用户/会话命名空间，本地磁盘曾无限堆积（2026-07-04 部分修复）**：文件名完全由 g4f 自身命名规则生成（`{timestamp}_{prompt}_{hash}.ext`），不带任何 user_id/session 标记——同一台机器上所有用户（含不同登录用户与游客）的生成结果都写进同一个共享目录，本地长时间运行/反复调试会导致该目录无限增长。已通过 `cleanup_old_generated_media()` 在 `generate_images()` 请求开始时惰性清理超过 `GENERATED_MEDIA_MAX_AGE_SECONDS`（1 小时）的旧文件来缓解，**但特意不做**"每次生成前清空整个目录"：图片结果不落库，浏览器里正在展示/等待下载的图片文件是唯一副本，无差别清空会让其他并发请求（另一个标签页、另一个恰好落在同一实例上的用户）当前正在看的图片被提前删除且无法找回；按年龄清理只处理"足够旧、大概率没人在看"的文件，不影响近期批次。
- **生产环境（GAE Standard，`automatic_scaling` 1-10 实例）下本地磁盘是 per-instance 的，`cleanup_old_generated_media()` 不解决跨实例的文件不一致问题**：`app.yaml` 未声明共享网络存储，每个实例的 `get_media_dir()` 是各自独立的本地磁盘。若一次 `POST /api/generate-images` 请求（图片写入实例 A 的本地磁盘）之后，前端 `<img>`/下载按钮对 `GET /media/<filename>` 的后续请求被负载均衡分配到实例 B，该文件在 B 上根本不存在，会直接 404——这与清理逻辑无关，是"本地磁盘 + 多实例"这一存储架构本身的已知限制，真正修复需要把生成图片迁移到共享存储（如 Cloud Storage），属于比本次清理更大的架构改动，当前不在范围内。
- **HTML 结构完整性：一次编辑意外删掉了开始标签、留下了配对的结束标签，导致已登录用户打开首页时只看到侧边栏（2026-07-04 事故，同日修复）**：`.sidebar-top` 双按钮改为"模式感知"（见 `templates/index.html` 关键点小节）那次编辑里，`<div class="sidebar-top">` 的开始标签被替换成了一段说明性 HTML 注释，但紧随两个按钮之后配对的 `</div>` 却原样保留了下来。`<aside class="left-sidebar">` 内部因此没有任何 `<div>` 能匹配这个多出来的 `</div>`——按 HTML5"未匹配的结束标签"解析算法，浏览器会沿着已打开标签栈往上找最近的同名标签，而这个 `div` 实际上是更外层的 `.app-layout`；于是浏览器把 `.app-layout`（连同其间的 `<aside>`）一并提前关闭，`.main-content`（表单、结果区）被挤出预期的 flex 容器之外，页面上只剩侧边栏可见。**这个 bug 完全没被发现，因为当时新增的 `test_sidebar_ui_blackbox.py` 全部是 `assertIn('某段文本', html)` 式的字符串子串断言**——被删掉标签之后，所有按钮 id、文案、内联 JS 片段依然逐字存在于响应体里，子串断言全部通过；子串匹配天然无法察觉标签配对/嵌套层级已经被破坏。修复：把 `<div class="sidebar-top">` 开始标签放回原位。根治：新增 `tests/test_html_structure_blackbox.py`，用 `html.parser.HTMLParser` 对渲染出的整页 HTML（`<script>`/`<style>` 内容会先被剥离，因为其中的 `<`/`>` 不是标记）做标签配对完整性检查——结束标签必须与最近打开的标签同名，且文档结束时打开标签栈必须清空；已用"重新引入这个具体 bug"的方式验证过它确实会让相关用例失败（而不仅仅是"写了但从没红过"）。**以后任何涉及 `templates/*.html` 结构性改动（增删标签、调整嵌套）的编辑，都应该让这个文件覆盖到改动的模板/路由，不能只依赖 `assertIn` 式的文案/id 断言**——后者验证"文字还在"，前者验证"骨架没散"，两者互补，缺一不可。
- **关闭态的自定义下拉面板悄悄撑大了整页可滚动区域，导致 `#pageScrollThumb` 在用户勾选 Provider 或点击 Compare 之前"乱动"/不"锁"（2026-07-04 事故，同日修复）**：用户报告"为什么一定要点 Compare 或者勾选一个 Provider，整页右侧的自绘滚动条才会稳定下来、不再跳动"。排查过程：先怀疑 `updatePageScrollIndicator()`/`setupThumbDrag()` 自身的计算或 `ResizeObserver`/`--page-zoom` 时序问题，用 headless Chromium（Playwright，本沙箱同样靠 `apt-get download` 单独拉取 `libnspr4`/`libnss3`/`libasound2t64` 解决无 root 限制）反复测量 `document.documentElement.scrollHeight`/`#pageScrollThumb` 的 `style.top`/`style.height`，发现页面刚加载、什么都不点时数值完全静止（4 秒采样无漂移），排除"时序/异步加载"类猜测；转而对比"勾选一个 Provider 前/后"的精确差值，命中 `docScrollHeight` 从 987 变为 968（少了 19px）。用 `opts.style.setProperty('display','none','important')` 直接对 `#customOptions` 做二分实验，确认单独隐藏这一个元素就能让 `scrollHeight` 从 987 掉到 968——与勾选前后的差值完全吻合，锁定根因：`.custom-options`（"Select Model" 自绘下拉面板，`#customOptions`/`#imageCustomOptions` 共用同一个 class）关闭时只靠 `opacity:0`+`visibility:hidden` 隐藏，盒子本身仍是 `display:block`+`position:absolute`+`max-height:250px`——不可见但仍然计入其滚动祖先（一路到 `document.documentElement`，因为中间没有任何元素做 `overflow:hidden` 裁剪）的可滚动溢出区域；而这个盒子的实际渲染高度又取决于 `updateModelDropdown()`/`updateImageModelDropdown()` 当下汇总进去的 `<option>` 数量——未勾选任何 Provider 是默认状态，此时汇总的是**全部** Provider 的模型（数量最多、盒子最高），因此"刚加载页面、什么都还没点"恰好是这个隐形溢出最大的时刻。之所以看起来"点 Compare 或勾选 Provider 后就'锁'住了"：勾选 Provider 会收窄汇总的模型集合（盒子变矮，多余溢出减少）；点 Compare 会在 `#results` 里塞入大量真实可见内容，让这份固定上限（`max-height:250px`）的隐形溢出相对占比变得可以忽略——两者殊途同归地让 `#pageScrollThumb`（其尺寸/位置直接读 `document.documentElement.scrollHeight`）看起来"终于稳定了"，但根因从来不是 thumb 自身的计算逻辑，而是这个"关闭时不是真的零高度"的下拉面板。修复：关闭态从 `max-height:250px`+`overflow-y:auto` 改为 `max-height:0`+`overflow:hidden`（`.open` 态照旧恢复 `250px`/`auto`），并把 `transition:all 0.15s ease` 拆成显式属性列表、给 `max-height` 单独加 `0s linear 0.15s` 的 transition-delay——效果是关闭动画期间面板保持原尺寸淡出（视觉上和修复前完全一样，因为 `visibility` 属性本身在 CSS 规范里就有"淡出结束才真正切换"的离散过渡语义，这里是照抄同一套技巧应用到 `max-height` 上），只有在完全淡出、已经不可见之后才真正塌陷到 0，从而杜绝"关闭态高度还跟着 Provider 选择走"这件事本身，而不是让骨架碰巧被后续交互掩盖掉。已用 headless Chromium 反复验证：刚加载、逐个勾选/取消勾选任意数量 Provider（含默认全选态），`document.documentElement.scrollHeight`/`#pageScrollThumb` 的 `style.top`/`style.height` 全程保持完全不变；打开/选中/关闭下拉面板本身的动画效果、模型选择功能均正常。**这类"关闭但未真正归零"的隐形溢出，在这个项目里没有自动化测试兜底**——之前的教训（见上方"HTML 结构完整性"事故）是"子串断言测不出结构性回归"，这次是同一类"看起来没问题、实际上有一个不可见元素在悄悄影响布局"的变体，只是这次影响的是滚动尺寸而不是标签配对；`tests/test_scrollbar_dropdown_overflow_blackbox.py`（2026-07-04 新增）把这个修复钉死在渲染出的 CSS 规则文本上（关闭态必须是 `max-height: 0`+`overflow: hidden`，不能是任何固定大数值；`.open` 态必须仍是 `max-height: 250px`+`overflow-y: auto`），但**这只是回归防线，不是完整验证**——像素级的"scrollHeight 是否真的不再随勾选变化"这类运行时行为验证，本项目仍然没有自动化 JS/浏览器测试框架（同"前端交互层"既有说明），依赖手动 Playwright 复现，本次已完整跑过。
- **Claude 免费额度以"注册账号"为单位，多账号注册可无限刷免费额度（2026-07-04 新增，已知局限，非 Bug）**：`CLAUDE_FREE_TIER_LIMIT`（每账号 1 次）的判定依据是 `session['user_id']`，任何人只要能不断注册新账号（`POST /register` 目前只要求用户名/邮箱唯一 + 密码 ≥ 6 位，没有任何速率限制或人机验证），就能无限次白嫖开发者账户额度调用 Claude——当前系统对此没有任何防护。**TODO（待评估，非本次范围）**：
  1. IP 级限流——对 `/register` 和/或 `/api/claude-chat` 按来源 IP 做速率限制（如"同一 IP 每小时最多注册 N 个账号"），但需要先确认部署环境（GAE）下能否拿到可信的客户端真实 IP（反向代理/负载均衡后 `request.remote_addr` 可能是内部地址，需要正确解析 `X-Forwarded-For`）。
  2. 图形验证码（CAPTCHA）——在 `/register` 表单上加验证码，提高批量注册的自动化成本。
  3. 邮箱验证——当前 `create_user()` 不校验邮箱真实性，接入邮箱验证环节可以提高小号注册门槛（但会改变现有"注册即登录"的即时体验，需要权衡）。
  这三项都不在本次集成范围内，留作后续独立评估的方向，不应该被误认为是当前实现的疏漏。

## 10. 🧭 EXTENSION & MODIFICATION GUIDE

### 🟢 安全区：添加新文本 LLM Provider

1. 运行 `availability_g4f/find_providers_models.py` 扫描候选，更新 `test_providers.py` 验证。
2. 追加进 `G4F_PROVIDERS` 列表 + `PROVIDER_MODELS_MAP`。
3.（可选）`ROUTE_PROMPTS_MAP` 加隐形风格提示词、`PEER_REVIEW_PROMPTS_MAP` 加互评裁判提示词（英文撰写，要求输出 `{"score": int, "comment": str}` JSON）。
4. 前端联动机制已全自动，无需改 HTML/JS。

### 🟢 安全区：添加新图片 Provider

同上模式，运行 `find_image_providers.py`/`test_image_providers.py` 验证后追加进 `IMAGE_PROVIDERS`/`IMAGE_PROVIDER_MODELS_MAP`。**不要**让图片 Provider 与文本 Provider 共用映射表或名字空间。

### 🟢 安全区：添加新页面

若可能作为重定向目标，必须加入 Flash 消息显示代码块：

```jinja2
{% with messages = get_flashed_messages(with_categories=true) %}
    {% if messages %}
        {% for category, message in messages %}
            <div class="flash flash-{{ category }}">{{ message }}</div>
        {% endfor %}
    {% endif %}
{% endwith %}
```

### 🟢 安全区：添加新 Claude 模型

1. 在 `CLAUDE.md`（本文件）与 `/claude-api` skill 里确认官方 model ID 是否发生变化（Anthropic 会不定期发布新模型/退役旧模型）。
2. 在 `CLAUDE_MODELS` 追加一条 `{key: 官方 model ID}` 映射。
3. `templates/index.html` 的 `#claudeModelSelect` 加一个 `<option>`（value 用新 key）。
4. 无需改动权限/额度逻辑——`claude_chat()`/`call_claude_model()` 对模型是透明的，任何在 `CLAUDE_MODELS` 里的 key 都自动享有同一套免费额度/自带 Key/余额耗尽处理。

### 🟢 安全区：添加新的前沿（Frontier）Provider（如 ChatGPT/Gemini）

`templates/index.html` 的 `#frontierProviderSelection` 容器（"Select frontier providers:"，2026-07-04 重排新增）就是为此预留的位置——`apikey-config.html` 里 `#chatgptKeyInput`/`#geminiKeyInput` 两个占位输入框也已经预留好了 UI，接入时大致遵循 Claude 集成的既有模式：

1. 后端仿照 `call_claude_model()`/`CLAUDE_MODELS`/`CLAUDE_FREE_TIER_LIMIT` 建一套独立的调用函数/模型映射/额度常量（不要复用 Claude 的），走独立的 `/api/<provider>-chat` 路由，同样复用 `_get_authenticated_user_id()` 守卫（游客/匿名 401，无降级）。
2. 前端在 `#frontierProviderSelection` 内追加一张新的 Provider 卡片，**必须用独立 class**（不能是 `.claude-provider-checkbox`/`.claude-provider-trigger`，也不能是 `.provider-checkbox`/`.provider-trigger`），原因与 Claude/图片 Provider 那条隔离规则完全一致。
3. 若该 Provider 有多个模型可选，仿照 `.claude-model-select-group`/`#claudeModelSelect` 加一个独立的 `<select>`，不要复用 `#modelSelect`/`providerModelsMap`。
4. `apikey-config.html` 里对应的 Key 输入框接入 `localStorage`（独立 key，如 `user_chatgpt_key`/`user_gemini_key`，不要复用 `user_claude_key`），仿照 `fetchClaudeResult()` 写一个提交时机相同的独立 fetch 函数，结果同样在渲染层 push 进 `data.results` 后重排。
5. 不要让新 Provider 落入 `G4F_PROVIDERS`/`IMAGE_PROVIDERS` 名字空间或参与 `ThreadPoolExecutor`/`run_peer_review()`——前沿 Provider 与 g4f 系是并列但互相隔离的调用链路。

### 🔴 危险区：严禁触碰的逻辑

- 不要修改 `test_g4f_provider` 的 7-key 契约或互评 4-key 契约（`reviewer_provider`/`reviewer_model`/`score`/`comment`）。
- 不要修改 `test_g4f_image_provider` 的 8-key 图片 DTO。
- 不要让文本 Provider 和图片 Provider 共用映射表/调度路径/名字空间——两者是 g4f 里完全不同的接口。
- 不要给图片 Provider 勾选框复用文本表单同名 class（全局无容器限定的 `querySelectorAll` 会互相污染）。
- 不要把 `save_chat_history` 的 `results` 参数混入图片 DTO，也不要让 `generate_images()` 调用 `save_chat_history`——图片历史必须走独立的 `save_image_history()`/`image_history` 集合（2026-07-04 新增），不要把两个集合合并或给 `history` 集合加判别字段来兼容图片 DTO。
- 不要让游客或匿名访问者使用图片版 Recents 侧边栏或 `/image-history/<id>`——这是与对话历史刻意不同的限制（对话历史对游客有 `sessionStorage` 客户端记录，图片历史对游客完全不提供）。具体：不要给 `generate_images()` 加"游客也临时记一下"的逻辑；不要给 `/api/image-history` 系列路由换成允许游客的守卫；不要让 `view_image_history()` 改回像 `view_history()` 那样为游客渲染空壳；不要让 `renderCurrentImageHistoryList()` 在 `!isLoggedIn` 时仍然发起 `/api/image-history` 请求。
- 不要给图片下载或任何"按客户端 URL 抓取"的功能加服务端代理接口（SSRF 风险）——下载必须留在浏览器端完成。`GET /media/<filename>`（`serve_generated_media`）不违反这条：它只读取 `get_media_dir()` 下已经由 g4f 提前下载好的本地文件，不依据请求里的 `url` 查询参数发起任何服务端抓取；不要给它加上"本地文件不存在时按 `url` 参数回源下载"的 fallback 逻辑，那样会引入新的 SSRF 面。
- 不要把 `cleanup_old_generated_media()` 改成"每次生成前无差别清空整个 `get_media_dir()`"——图片结果不落库，浏览器里正在展示/等待下载的图片是唯一副本，无差别清空会删掉其他并发请求（另一个标签页、另一个用户）尚未查看的文件且无法恢复；必须保持"只删 `mtime` 早于 `GENERATED_MEDIA_MAX_AGE_SECONDS` 的文件"这一按年龄清理的语义。
- 不要把互评/文生图的 advisory 超时与外层超时改成不同步的数值。
- 不要移除根路由 `/` 中的 `provider_models_json`/`image_provider_models_json` 注入。
- 不要颠倒 `CONTENT_POLICY_ERROR_KEYWORDS` 与 `NETWORK_ERROR_KEYWORDS` 的判定顺序。
- 不要颠倒 `GPU_QUOTA_ERROR_KEYWORDS` 与 `PEER_REVIEW_NETWORK_ERROR_KEYWORDS` 在 `test_g4f_image_provider` 里的判定顺序，也不要对 GPU 配额耗尽错误加重试——配额耗尽时重试无意义，且会给本就紧张的免费资源加压。
- 不要移除 `test_g4f_image_provider`/`generate_images` 对 `get_image_timeouts(provider_name)` 的调用、改回直接引用 `IMAGE_GENERATION_ADVISORY_TIMEOUT`/`IMAGE_GENERATION_OUTER_TIMEOUT` 常量——那会让 `IMAGE_PROVIDER_TIMEOUT_OVERRIDES` 里的 `AnyProvider` 特殊预算失效，重新引入"图片已生成但因超时被判 Failed"的问题。
- 不要把 `get_image_timeouts()` 里 outer 的计算公式从 `_compute_outer_timeout(advisory) = 2*advisory + IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER` 改回"advisory + 固定小缓冲"——固定缓冲不够覆盖"429 重试后第二次尝试本身也跑到接近满 advisory 才成功"的情况，这正是 `PollinationsImage` 2026-07-04 那次故障的根因，回归测试见 `tests/test_main_blackbox.py::test_slow_retry_success_not_discarded_by_outer_timeout`。
- 不要给 `IMAGE_PROVIDER_TIMEOUT_OVERRIDES` 的条目加回单独的 `outer` 键——outer 必须始终由 `_compute_outer_timeout()` 从该 Provider 的 advisory 推导，否则该 Provider 的 outer 会和公式脱节，未来公式调整时容易漏改。
- 不要在 session 中同时设置 `user_id` 和 `is_guest`。
- 不要在 auth 路由中跳过 `FIREBASE_AVAILABLE` 检查直接调用 CRUD 函数。
- 不要修改 `GET /home` 的行为（不能清除 `user_id`）。
- 不要移除历史 CRUD 函数（对话历史或图片历史）内部的归属校验（`doc.to_dict().get('user_id') == user_id`）。
- 不要让游客路径调用任何对话历史或图片历史 CRUD 函数。
- 不要用 `if not new_pinned` 判断 `toggle_pin_chat_history`/`toggle_pin_image_history` 是否失败——必须用 `is None`。
- 不要把 `history_id` 塞进 result DTO 或互评 DTO。
- 不要让 `get_chat_history_list` 恢复 Firestore 端的复合 `.order_by()` 查询——排序必须留在 Python 层，避免需要手动创建的复合索引。
- 不要把 `sortHistoryItems`/`get_chat_history_list` 的置顶排序字段从 `pinned_at` 改回 `created_at`。
- 不要恢复 `loadHistorySnapshot`（就地渲染进可编辑表单）这一已废弃模式——点击 Recents 条目必须整页导航到只读的 `history.html`。
- 不要把游客历史存储介质从 `sessionStorage` 换成 `localStorage`（后者没有"标签页关闭即清空"语义，违反身份不变量）。
- 不要移除 Recents 懒加载分页的任何部分，也不要引入除 `currentHistoryItems` 之外的额外客户端缓存层。
- 不要移除 `.left-sidebar` 的 `position:sticky`/CSS `calc()` 定高，或改回 JS 手工计算高度。
- 不要恢复原生滚动条（`overflow-y:scroll`/`scrollbar-width:thin` 等）替代自绘指示器——两者必须保持彻底隐藏，自绘指示器是唯一的滚动条视觉/交互层。
- 不要把 `.nav-container` 恢复成 `max-width:1200px;margin:0 auto` 的居中窄条写法。
- 不要给 `history.html`/`image_history.html` 加回任何形式的"再次提交"表单入口。
- 不要恢复 `deleteHistoryItem` 使用原生 `confirm()`。
- 不要把 `body`/`--page-zoom` 相关的 `calc()` 改回硬编码字面量或绕开 CSS 变量。
- 不要让游客或匿名访问者调用 `/api/claude-chat`——不要给这个路由换成允许游客的守卫，也不要在前端给 `#claudeProviderTrigger` 移除 `disabled` 属性的登录态判断；Claude 对游客是"完全不可用"，没有任何形式的降级体验，这是刻意的成本保护决策，不是遗漏。
- 不要给 `#claudeProviderCard`/`#claudeProviderTrigger` 复用 `.provider-checkbox`/`.provider-trigger`（或 `.image-provider-checkbox`/`.image-provider-trigger`）class——全局无容器限定的 `querySelectorAll` 会把 Claude 一并纳入 `/api/compare` 的 `providers` 数组，而 Claude 根本不在 `G4F_PROVIDERS` 名字空间里。
- 不要把 `#frontierProviderSelection`（"Select frontier providers:"，Claude 及未来 ChatGPT/Gemini 卡片所在容器）与 `#providerSelection`（"Select free providers..."，纯 g4f Provider 循环）合并回同一个容器，或把 Claude 卡片重新塞进 g4f Provider 循环之后——2026-07-04 的重排就是为了把"付费前沿模型"与"免 Key 模型"在 DOM 上彻底分开成两个同级容器；未来新增 ChatGPT/Gemini 前沿模型卡片时应追加进 `#frontierProviderSelection`，不要放进 `#providerSelection`。回归测试见 `tests/test_main_blackbox.py::TestProviderSelectionSectionMarkup`。
- 不要让 Claude 参与 `compare_providers()` 的 `ThreadPoolExecutor` 并发调度或 `run_peer_review()` 互评——Claude 是完全独立的第三条调用链路，走自己的 `/api/claude-chat`/`call_claude_model()`，不应该被塞进 `providers_to_test`/`G4F_PROVIDERS`。
- 不要把 `claude_free_tier_usage` 的检查顺序改成"先调用 `call_claude_model()` 再检查额度"——必须在调用开发者 API 之前就判断是否超限并直接拦截，超限的请求绝不能消耗一次真实的 Anthropic API 调用。
- 不要让 `X-User-Claude-Key` 请求头非空时仍然检查或递增 `claude_free_tier_usage`——自带 Key 的调用必须完全绕开免费额度体系。
- 不要在后端持久化用户通过 `X-User-Claude-Key` 传入的个人 Key（不要写入 Firestore、不要写入 session、不要缓存在进程内存里）——它只应该活在浏览器 `localStorage` 和单次请求的生命周期内。
- 不要把 `call_claude_model()` 里判断余额耗尽的依据改回具体的 `status_code`（如"只在 429 时才算余额耗尽"或"只在 403 时才算"）——**已用真实余额为 0 的账户验证过**，实际返回的是 400 + `error.type == 'invalid_request_error'`，`status_code` 并不是稳定信号；`error.message` 里的 `"credit balance"` 关键词才是。`insufficient_funds` 不是 Anthropic 实际使用的错误类型字符串，不要在代码里出现对这个字符串的匹配。
- 不要把余额耗尽（`SERVER_CREDITS_EXHAUSTED`）算作用户的免费额度消耗——`claude_chat()` 里必须在返回 503 之前跳过 `increment_claude_free_tier_usage()` 调用。
- 不要把 Claude 的 `type: 'anthropic'` 结果混入 `save_chat_history()`/`history` 集合——Claude 请求当前完全不落库（无论是否登录），不要给它加历史持久化，这是本次集成范围之外的功能（如需支持，应参照 `image_history` 集合的模式新建独立集合，而不是复用 `history`）。
- 不要给 `apikey-config.html` 的 ChatGPT/Gemini 占位输入框接入真实业务逻辑或 `localStorage` 持久化——第 3 节明确要求它们"暂不实现业务逻辑"，实现时也不应该复用 `user_claude_key` 这个 key。这条规则同样适用于它们各自新增的"Clear"按钮（`#clearChatgptKeyBtn`/`#clearGeminiKeyBtn`）——保持 `disabled`，不要接入任何存储；只有 `#clearClaudeKeyBtn` 允许调用 `localStorage.removeItem`。
- 不要把 `.custom-options`（`#customOptions`/`#imageCustomOptions` 共用）关闭态的 `max-height`/`overflow` 改回 `250px`/`auto` 这类会随内容变化的固定尺寸，也不要单纯依赖 `opacity:0`+`visibility:hidden` 来"隐藏"这类 `position:absolute` 且中间没有任何祖先做 `overflow:hidden` 裁剪的浮层——那样它依然会按当前内容量计入 `document.documentElement.scrollHeight`，重新引入"整页可滚动区域/`#pageScrollThumb` 随下拉框里当前有多少个 `<option>` 悄悄变化"的 bug（2026-07-04 事故，见第 9 节）。关闭态必须让这类浮层对滚动祖先的贡献恒为 0（与内容多少无关），需要保留开合动画时用 `transition-delay` 让塌陷延后到淡出结束（照抄 `visibility` 属性本身的离散过渡语义），而不是让塌陷和淡出同时发生。回归测试见 `tests/test_scrollbar_dropdown_overflow_blackbox.py`。
- 不要从 `templates/index.html`/`templates/history.html`/`templates/image_history.html`/`templates/auth/base.html` 任何一处的登录态导航分支里移除 "API Keys" 链接（`url_for('apikey_config')`），也不要把它挪到 "Profile" 之前或 "Logout" 之后——四处 markup 各自独立维护，修改导航栏结构时必须同步改全部四处，回归测试见 `tests/test_apikey_config_blackbox.py`。
- 不要在任何用户可见的位置（渲染出的 HTML 文本/属性、`flash()` 消息、JSON 错误体里的 `message`/`error` 字段、发给 LLM 的 `ROUTE_PROMPTS_MAP`/`PEER_REVIEW_PROMPTS_MAP` prompt 文本）重新引入中文——见第 6 节"English-only UI text 政策"（2026-07-04）。新增页面/新增错误分支时，文案一律直接写英文，不要先写中文再等着"以后再翻译"。这条规则**不**约束 Python `#`/JS `//`/CSS 及 HTML 注释、也不约束 CLAUDE.md 本身——内部代码注释与本文档一直是中文，且用户不会在正常使用软件的过程中看到它们，无需翻译。回归测试见 `tests/test_english_only_blackbox.py`。

## 11. 🛠️ BUILD, RUN & TEST COMMANDS

### 环境搭建

```bash
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

### 本地运行前置条件

1. `firebase-key.json` 存在于项目根目录。
2. `.env` 含固定 `SECRET_KEY`。
3. `firebase-admin`/`python-dotenv` 已安装（见 `requirements.txt`）。
4. （可选，2026-07-04 新增）`.env` 或环境变量含 `ANTHROPIC_API_KEY`——不设置也能正常启动（`CLAUDE_AVAILABLE` 仍为 `True`，`anthropic.Anthropic()` 零参构造不检查凭据），只有真正发起 `/api/claude-chat` 请求且未携带用户自带 Key 时才会因缺 Key 报错（表现为该请求返回失败结果，不影响其余功能）。

### 运行

```bash
python main.py                    # 默认端口 8080
PORT=5000 python main.py
gunicorn -b :8080 main:app        # 模拟 GAE
```

**⚠️ 修改 `templates/*.html` 后必须重启开发服务器**：`app.run(debug=False, ...)`，Jinja2 模板自动重载默认跟随 `app.debug`（`False`），已运行进程会永久缓存旧模板，浏览器强刷无法解决。

访问 `http://localhost:8080`；`http://localhost:8080/health` 验证状态。

### 自动化测试

项目用 `unittest`，测试文件在 `tests/`，共 584 个用例：

- `test_main_whitebox.py`：内部函数（模型降级规则、DTO 完整性、`detect_and_truncate`、`parse_peer_review_json`、`run_peer_review` 429 重试、内容策略/网络错误文案判定、图片 Provider 相关纯函数、`test_g4f_image_provider` 的 GPU 配额友好文案/429-queue 重试/per-provider advisory timeout 转发，2026-07-04 新增）、`TestCleanupOldGeneratedMedia`——`cleanup_old_generated_media()` 纯函数断言：只删严格早于阈值的文件、边界值（恰好等于阈值）不删、混合新旧文件批次只删旧的、目录不存在不抛异常、跳过子目录、单个文件删除失败不影响其余文件清理、不传 `max_age_seconds` 时使用 `GENERATED_MEDIA_MAX_AGE_SECONDS` 模块常量（2026-07-04 新增，均用 `tempfile.TemporaryDirectory` + `patch('main.get_media_dir', ...)` 隔离）。
- `test_main_blackbox.py`：HTTP 接口驱动（`/api/compare`/`/api/generate-images`/`/api/history` 系列/`/history/<id>`/`/health`/`GET /media/<filename>`），含身份路由回归、互评契约、Provider 名字空间隔离断言、`serve_generated_media` 静态文件路由断言（存在文件 200、缺失文件 404、忽略 g4f 附带的 `?url=` 查询参数、路径穿越拦截、Content-Type 校验；用 `tempfile.TemporaryDirectory` + `patch('main.get_media_dir', ...)` 隔离，不触碰真实 `generated_media/` 目录）、GPU 配额错误端到端友好文案断言（2026-07-04 新增）、`test_slow_retry_success_not_discarded_by_outer_timeout`——端到端复现 `PollinationsImage` 429 重试且重试后耗时较长仍应算成功的场景（用 `patch('main.IMAGE_GENERATION_ADVISORY_TIMEOUT', ...)`/`IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER` 缩小成毫秒级以避免真实等待，用 `threading.Event().wait()` 而非 `time.sleep()` 模拟耗时，因为 `main.time` 和测试文件里 `import time` 是同一个模块对象，`patch('main.time.sleep')` 会连带影响测试自己的 `time.sleep()` 调用；2026-07-04 outer 超时公式修订新增）、`TestGenerateImagesTriggersMediaCleanup`——端到端验证 `POST /api/generate-images` 会触发 `cleanup_old_generated_media()`：过期文件被删除、近期文件（可能仍被其他并发请求展示）不受影响、`get_media_dir()` 尚不存在时清理内部吞掉 `OSError` 不导致整个请求 500（2026-07-04 新增）；`TestGenerateImagesEndpoint::test_history_id_is_null_for_anonymous_request`（2026-07-04 更新）——图片历史持久化上线后，匿名/游客请求必须仍然拿到 `history_id: None` 且不触发 `save_image_history`；`TestProviderSelectionSectionMarkup`（2026-07-04 新增，8 个用例）——`compareForm` 内 Provider/Model 四段式重排（见 `templates/index.html` 关键点小节"Provider/Model 选择区四段式布局"）的渲染断言：`#frontierProviderSelection` 容器与"Select frontier providers:"文案存在、Claude 卡片位于该容器内而非 `#providerSelection`、`#providerSelection`/免 Key 模型下拉分别改名为"Select free providers (leave all unchecked to test all):"/"Select free models (single selection):"、`#claudeModelSelect` 本身文案/选项不变、四段在 DOM 中按"frontier → claude model → free providers → free models"顺序出现、游客态同样成立；断言时特意把检查范围限定在 `#compareModeContainer` 内（`_get_compare_form_html()` 辅助方法），避免 `#imageModeContainer` 里同名的旧文案"Select Model (Single Selection):"（图片模型下拉，未改名）造成误判。
- `test_main_graybox.py`：全局状态与线程池行为（排序契约、超时 fallback、`max_workers` 约束、`G4F_AVAILABLE` 降级、互评/图片超时数值锁定、`IMAGE_PROVIDER_TIMEOUT_OVERRIDES`/`get_image_timeouts()` 的 `AnyProvider` 专属超时断言，2026-07-04 新增；`TestImageOuterTimeoutValue` 于 2026-07-04 outer 公式修订时更新——锁定 `IMAGE_GENERATION_OUTER_TIMEOUT==85` 及 `outer == 2*advisory+IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER` 的公式关系，并验证 `AnyProvider` 的 outer 同样由该公式推导而非单独硬编码）；`TestSaveImageHistoryRobustness`（2026-07-04 新增，`TestSaveHistoryRobustness` 的图片对应版本）——`save_image_history()` 抛异常或返回 `None` 都不能让 `/api/generate-images` 本身失败，`history_id` 精确 fallback 为 `None`。
- `test_auth_whitebox.py`：密码哈希、Firestore mock 下的用户/对话历史 CRUD 契约（含归属校验、排序、`pinned_at` 哨兵值断言）。查询相关断言（`test_query_filters_on_username_field`/`test_query_filters_on_email_field`/`test_query_filters_by_user_id`）用模块级 `_assert_where_called_with_field_filter()` 辅助函数验证 `.where()` 调用（2026-07-04 更新，见 `auth/db.py` 关键点小节的 `FieldFilter` 修复说明）——直接 `assert_called_once_with('user_id', '==', 'uid1')` 断言调用参数的旧写法在改用 `FieldFilter` 后失效（`FieldFilter` 无 `__eq__`），辅助函数改为从 `call_args` 里取出 `kwargs['filter']` 再断言其 `field_path`/`op_string`/`value` 三个属性。
- `test_auth_blackbox.py`：auth 蓝图路由、session 读写、访问控制。
- `test_image_history_whitebox.py`（2026-07-04 新增）：`auth/db.py` 图片历史 6 个 CRUD 函数的 Firestore mock 契约测试，与 `test_auth_whitebox.py` 里对话历史的等价测试类逐一同构（`TestSaveImageHistory`/`TestGetImageHistoryList`/`TestGetImageHistoryById`/`TestDeleteImageHistory`/`TestUpdateImageHistoryTitle`/`TestTogglePinImageHistory`），额外断言读写的是 `image_history` 集合而非 `history`。查询断言同样用本文件自带的一份 `_assert_where_called_with_field_filter()`（与 `test_auth_whitebox.py` 里那份逻辑相同但各自独立定义，两个测试文件不共享模块间导入）验证 `get_image_history_list` 的 `.where(filter=FieldFilter(...))` 调用形状（2026-07-04 更新，同一次 `FieldFilter` 修复）。
- `test_image_history_blackbox.py`（2026-07-04 新增）：`main.py` 图片历史路由的 HTTP 层测试——`TestViewImageHistoryPage`（含"游客直接重定向、不像 `/history/<id>` 那样渲染空壳"的断言）、`TestImageHistoryAuthGuard`（游客/匿名 401）、`TestGetImageHistoryEndpoint`/`TestUpdateImageHistoryTitleEndpoint`/`TestDeleteImageHistoryEndpoint`/`TestToggleImageHistoryPinEndpoint`（与对话历史等价路由同构）、`TestGenerateImagesHistoryPersistence`（`/api/generate-images` 触发 `save_image_history` 并挂载 `history_id`，游客不触发）、`TestIndexPageImageSidebarMarkup`（`/` 页面包含图片模式切换入口）。
- `test_sidebar_ui_blackbox.py`（2026-07-04 新增）：`index.html`/`history.html`/`image_history.html` 纯前端文案/标记层面的 HTTP 黑盒断言（Flask test client 走真实 `render_template` 渲染路径，而非解析 JS——项目无 JS 测试框架，这与 CLAUDE.md 第 11 节"前端交互层不在 unittest 覆盖范围"的既有说明一致，这里断言的是渲染出的 HTML/内联 JS 源码字符串本身，不是运行时交互行为）。三个测试类：`TestModeAwareSidebarButtonsMarkup`——`#newChatBtn` 渲染为固定的"+ New"（`.btn-icon`+`.btn-label` 双 span），`#generateImageBtn` 携带可变的 `#modeToggleBtnIcon`/`#modeToggleBtnLabel`（初始状态"🎨"+"Generate Image"），并断言内联脚本中 `switchToImageMode()`/`switchToCompareMode()` 分别把这两个 id 写成"✍️"+"Generate Text"/"🎨"+"Generate Image"、两个按钮的 `click` 监听器各自按 `sidebarMode` 分流（`#generateImageBtn` 在两个切换函数间二选一；`#newChatBtn` 在 `#clearBtn`/`#clearImagesBtn` 间二选一，且不再调用 `switchToCompareMode()` 强制切模式）。`TestG4FBrandingRemoved`——`/`（登录/游客）、`/history/<id>`、`/image-history/<id>` 四个响应体均不含字面量"G4F"，且三个页面的 `<title>` 均已改为不含"G4F"前缀的版本。`TestHeaderRenames`——`.header-full` 断言为"Text Generator"/"Image Generator"，图片模式副标题断言为新文案且不含"free, no-key g4f providers"，`.header-short` 在两个模式下统一断言为"LLM Aggregator"（不再是"Image Gen"）。**同日晚些时候新增两个测试类**（随 `#compareBtn` 改名与 Recents 空状态文案改动一起加入，未新开文件，因为都是这个文件已有的"纯文案/标记断言"套路）：`TestCompareButtonRenamed`——`#compareBtn` 渲染文案为"Compare Responses"、旧文案"Compare Providers"不再出现在整页响应体里。`TestSidebarRecentsEmptyStateText`——`index.html`（登录态）与 `history.html`（游客态）里 `renderHistoryGroups()` 的空状态占位 markup 均为 `<div class="sidebar-empty-state">Empty.</div>`，旧文案"No conversations yet."不再出现（注意图片历史侧边栏的占位文案"No images generated yet."是独立的一份，未在这次改动范围内，见 `templates/index.html` 关键点小节）。
- `test_html_structure_blackbox.py`（2026-07-04 新增，见第 9 节"HTML 结构完整性"事故记录）：对 `GET /`（登录/游客/匿名）、`GET /history/<id>`（登录/游客）、`GET /image-history/<id>`（登录）渲染出的完整 HTML 做标签配对结构性检查，而不是文案/id 子串断言——`assert_html_tag_balance()` 辅助函数剥离 `<script>`/`<style>` 内容后用 `html.parser.HTMLParser` 验证每个结束标签都精确匹配最近打开的标签、且文档结束时打开标签栈已清空。这类测试专门用来兜住"文本子串断言全部通过、但标签配对/嵌套已经被破坏"的回归——已用"重新引入 2026-07-04 那次 `.sidebar-top` 开始标签丢失的 bug"验证过它确实会让相关用例失败。
- `test_history_mode_toggle_blackbox.py`（2026-07-04 新增）：`history.html`/`image_history.html` 的 `.sidebar-top` 按钮改版为对齐 `index.html` 最新的 `.btn-icon`/`.btn-label` span 结构（`test_sidebar_ui_blackbox.py` 的 `TestModeAwareSidebarButtonsMarkup` 只覆盖 `index.html`，不覆盖这两个只读页面，所以新开此文件而非往其中添加用例）。`TestHistoryPageModeAwareButtons`（游客+登录两态各验证一次，因为 `.sidebar-top` 对两态都渲染同一份 markup）：断言 `#newChatBtn` 渲染为"+ New"而非旧版"+ New Chat"；断言此前**完全不存在**的 `#generateImageBtn`（本次新增）已出现，固定图标/文案为"🎨"+"Generate Image"；断言两个按钮的 `click` 处理器分别导航到 `/`、`/?mode=image`。`TestImageHistoryPageModeAwareButtons`：断言 `#newChatBtn` 同样改为"+ New" span 结构；断言 `#generateImageBtn` 的图标/文案从旧版固定的"🎨"+"Generate Image"改为"✍️"+"Generate Text"；断言两个按钮的点击语义随文案调转——`#newChatBtn` 现在导航到 `/?mode=image`（旧版是 `/`），`#generateImageBtn` 现在导航到 `/`（旧版是 `/?mode=image`），因为两个只读页面各自固定代表一种语境，"+ New" 不应再触发模式切换（呼应 `index.html` 里 `#newChatBtn` 已经确立的"不强制切换模式"规则，见上方 `templates/index.html` 关键点小节）。
- `test_claude_integration.py`（2026-07-04 新增，31 个用例）：官方 Claude 集成的完整测试，按任务要求分白盒/黑盒两部分。**白盒/单元**：`TestCallClaudeModelKeyRouting`——`call_claude_model()` 的 Key 路由（自带 Key 时 `anthropic.Anthropic(api_key=user_key)`，否则零参 `anthropic.Anthropic()`）、成功结果形状、模型 key→官方 ID 映射转发、余额耗尽→`SERVER_CREDITS_EXHAUSTED` 的判定（`test_real_world_credit_balance_error_maps_to_server_credits_exhausted` 的默认参数即**实测形状**——400+`invalid_request_error`+"credit balance"，用一个真实余额为 0 的账户直接调用官方 API 验证过；`test_billing_error_type_also_recognized_defensively` 覆盖文档字面暗示的 403/`billing_error` 兼容分支；`test_plain_rate_limit_error_is_not_credits_exhausted` 确认普通限流不会误判；均用真实构造的 `anthropic.APIStatusError`+`httpx.Response` 而非字符串近似）、连接错误友好文案。`TestClaudeFreeTierCounterDb`——`auth/db.py` 两个新函数的 Firestore mock 契约（字段缺失/用户不存在时默认 0、`firestore.Increment` 哨兵类型断言、`FIREBASE_AVAILABLE=False` 时的 fallback）。`TestClaudeChatRouteKeyRoutingAndCounter`——`/api/claude-chat` 路由层面验证"成功且未自带 Key → 递增计数器"、"失败 → 不递增"、"自带 Key → 完全跳过计数器读写且 Key 正确转发给 `call_claude_model`"。**黑盒/集成**：`TestClaudeChatAuthGuard`（游客/匿名 401）、`TestClaudeChatFreeTierFlow`（首次成功、第二次无自带 Key 时 403 `FREE_TIER_EXHAUSTED`、自带 Key 绕过超限限制）、`TestClaudeChatServerCreditsExhausted`（mock 掉 `anthropic.Anthropic` 客户端抛出实测形状的 `APIStatusError`，验证端到端转发为 503 `SERVER_CREDITS_EXHAUSTED` 且不消耗免费额度）、`TestClaudeChatValidation`（缺 prompt/非法 model/`CLAUDE_AVAILABLE=False` 时的 400/503）、`TestApikeyConfigPage`/`TestHealthCheckReportsClaudeAvailability`（页面渲染与 `/health` 字段）。**已在真实浏览器里做过两轮端到端人工验证**（均为 Playwright 驱动 headless Chromium，测试账号验证后即从 Firestore 删除，不遗留在生产库里）：第一轮（开发者 Key 余额为 0 时）：注册新账号登录 → 勾选 Claude 卡片 → 提交真实 prompt → 用一个真实余额为 0 的开发者 Key 触发实际请求，确认结果卡片正确显示友好的 `SERVER_CREDITS_EXHAUSTED` 文案而非原始英文错误，且未消耗该用户的免费额度（读取 Firestore 确认 `claude_free_tier_usage` 仍为 0）。第二轮（开发者账户充值到 $5 正余额后）：同一套浏览器流程改为验证"正常路径"——首次请求返回真实模型的成功回答（`Success` 状态卡片），且 Firestore 里 `claude_free_tier_usage` 正确原子递增为 1；同账号第二次请求（未带自带 Key）被正确拦截为 403 `FREE_TIER_EXHAUSTED` 并弹出升级引导弹窗；随后在 `/apikey-config` 填入同一个真实 Key 存入 `localStorage`，第三次请求携带 `X-User-Claude-Key` 正确绕开额度检查、返回 200 成功，且计数器仍保持 1 未被消耗。两轮合起来覆盖了本项目 Claude 集成里所有对外可观察的分支（成功、余额耗尽、额度耗尽拦截、自带 Key 绕过）。第二轮测试中还发现 `main.py` 里 `claude_chat()` 路由上方的说明注释有一处滞后未跟着第一轮的修正同步更新（仍写着"403 + billing_error"），已订正为与 `call_claude_model()` 自身注释块一致的实测表述——这是纯文档性遗漏，运行时逻辑本身从一开始就是对的（一直用 `"credit balance"` 关键词判断），不影响任何已通过的测试结果。
- `test_english_only_blackbox.py`（2026-07-04 当天晚些时候新增）：锁定"English-only UI text 政策"（见第 6 节）的回归测试，防止未来的编辑无意中把中文重新引入用户可见的界面。核心是 `_VisibleTextChineseScanner`（`html.parser.HTMLParser` 子类）+ `assert_no_chinese_in_visible_html()` 辅助函数——跳过 `<script>`/`<style>` 标签体内容后扫描文本节点，并检查 `title`/`placeholder`/`value`/`alt`/`aria-label` 这几个用户能直接看到的属性值是否含 CJK 字符；HTML 注释天然不会进入 `handle_data()`，无需额外剥离。**故意不**对整份原始 HTML（含 `<script>`/`<style>` 源码与注释）做全量扫描——那样会把纯粹面向开发者的代码注释（本项目的既有工程语言就是中文，CLAUDE.md 本身也是中文）也判定为"违规"，而这条政策只约束用户实际会看到的内容。测试类：`TestIndexPageIsEnglishOnly`/`TestHistoryPageIsEnglishOnly`/`TestImageHistoryPageIsEnglishOnly`（登录/游客/匿名各态）、`TestApikeyConfigPageIsEnglishOnly`（该页面曾经是中文最集中的一处，额外精确断言 `Save`/`Clear` 按钮文案）、`TestAuthPagesAreEnglishOnly`（`/login`/`/register`/`/profile`，这三个页面审计前就已全英文，此处是防回归）、`TestClaudeServerCreditsExhaustedMessageIsEnglish`（mock `main.call_claude_model` 直接返回 `error_code: 'SERVER_CREDITS_EXHAUSTED'` 的结果字典，断言 `/api/claude-chat` 响应体 `message` 字段既不含 CJK 字符、也精确等于文档里记录的英文原文，不依赖真实构造 `anthropic.APIStatusError` 那一套——该错误分类逻辑本身已经在 `test_claude_integration.py` 里覆盖，这里只关心转发出去的文案语言）、`TestPromptEngineeringIsEnglish`（遍历 `ROUTE_PROMPTS_MAP`/`PEER_REVIEW_PROMPTS_MAP` 的每一条 prompt 文本本身，而不是描述它们的中文注释，确认发给 LLM 的实际指令不含中文——这两张表在本次审计前就已经是英文，此处同样是防回归；`G4F_AVAILABLE=False` 的环境下这两张表为空字典，用 `skipTest` 而非误判通过）。
- `test_apikey_config_blackbox.py`（2026-07-04 当天晚些时候新增）：`templates/apikey-config.html` 顶部导航入口 + 逐字段清空按钮这两处改动的黑盒测试。`TestNavApikeyLinkLoggedIn`——已登录态渲染的 `/`、`/history/<id>`、`/image-history/<id>`、`/profile`（mock `auth.routes.get_user_by_id`/`FIREBASE_AVAILABLE`，与 `test_auth_blackbox.py` 里 profile 相关测试同样的 mock 方式）、`/apikey-config` 本页自身共 5 个页面，用 `_assert_link_between_profile_and_logout()` 辅助函数断言 "API Keys" 链接出现且严格位于 "Profile" 之后、"Logout" 之前（而不只是"出现在页面某处"）。`TestNavApikeyLinkAbsentForOthers`——游客/匿名访客看不到这条链接（因为他们本来就看不到 Profile/Logout），但 `/apikey-config` 路由本身仍然可以被匿名直接访问（无登录态守卫这条既有规则未变）。`TestApikeyConfigClearButtons`——三个清空按钮均存在；`#clearChatgptKeyBtn`/`#clearGeminiKeyBtn` 渲染为 `disabled`；`#clearClaudeKeyBtn` 不带 `disabled` 且其 `click` 监听器精确调用了 `localStorage.removeItem('user_claude_key')`；额外的危险区回归断言页面不会因为新增清空按钮而顺带给 ChatGPT/Gemini 接入 `user_chatgpt_key`/`user_gemini_key` 这类真实存储（呼应第 10 节危险区"不要给 ChatGPT/Gemini 占位输入框接入真实业务逻辑"）；以及原有的"保存"提交流程未被清空按钮的新增破坏。与 `tests/test_html_structure_blackbox.py` 新增的 `TestApikeyConfigPageHtmlTagBalance`（本次同步新增，验证该页面新增的 `<style>` 块 + `.key-input-row` 包裹 div 没有破坏标签配对）互补，一个管标签配对完整性，一个管具体元素的可见文案/属性。
- `test_scrollbar_dropdown_overflow_blackbox.py`（2026-07-04 新增，见第 9 节"关闭态的自定义下拉面板"事故记录）：锁定 `.custom-options`（`#customOptions`/`#imageCustomOptions` 共用）关闭态零溢出这个修复本身的 CSS 声明，防止未来的样式调整无意中把它改回一个非零固定值、重新引入"整页可滚动区域随 Provider 选择悄悄变化"的 bug。`_extract_rule_body()` 辅助函数用锚定行首（忽略前导空白）的正则从渲染出的 `<style>` 文本里精确取出目标规则的花括号内容，避免"`.custom-options`"这个子串同时出现在更具体的 `.custom-select-wrapper.open .custom-options` 选择器里造成误匹配。`test_closed_state_clamps_max_height_to_zero`——基础（关闭）态必须含 `max-height: 0`+`overflow: hidden`，且不再含旧的 `max-height: 250px`。`test_open_state_restores_scrollable_max_height`——`.open` 态必须仍然恢复 `max-height: 250px`+`overflow-y: auto`（否则模型很多时下拉面板打开后反而不能滚动，是功能倒退）。`test_both_chat_and_image_dropdown_panels_share_the_fixed_class`——确认 `#customOptions`/`#imageCustomOptions` 仍然共用同一个 `class="custom-options"`，这正是"改一处 CSS 规则、两个下拉框同时修好"的前提，如果两者以后各自拆出独立 class（参照本文件里 `.provider-checkbox`/`.image-provider-checkbox` 因为全局 `querySelectorAll` 而必须拆分的先例）则会静默跳出这个修复的覆盖范围。**这个文件只锁定 CSS 规则文本本身**——实际的运行时验证（`document.documentElement.scrollHeight`/`#pageScrollThumb` 在勾选任意数量 Provider 前后是否真的保持不变）用 headless Chromium 手动跑过，没有对应的自动化用例（同"前端交互层不在 unittest 覆盖范围"的既有说明）。

**前端交互层**（骨架屏、hover 淡入、抽屉动画、时间分组、乐观更新回滚、原地重命名、游客历史模拟、自绘滚动指示器、像素级布局、`sidebarMode` 双模式切换）**不在 `unittest` 覆盖范围**——项目无 JS 测试框架。涉及像素级渲染/布局的验证用真实 headless Chromium（Playwright）手动跑（本沙箱环境无 root，用 `apt-get download` 单独拉取 `libnspr4`/`libnss3`/`libasound2t64`/`libasound2-data` 等 `.deb` 包、`dpkg -x` 解压到用户目录、设置 `LD_LIBRARY_PATH` 绕开系统库依赖）；纯 DOM 结构/事件/class 切换用 jsdom 手动脚本验证。两者均未留下可重复运行的自动化测试文件——引入前端测试框架后应将其转为正式回归测试。图片版 Recents 侧边栏的 `sidebarMode` 切换、锁定文案渲染、`?mode=image` 深链接同样只做了 JS 语法检查（`node --check`）和 Jinja/DOM 结构层面的黑盒断言，没有交互层回归测试——引入前端测试框架后应补上。

```bash
python -m unittest discover -s tests
python -m unittest discover -s tests -v
python -m unittest tests.test_main_whitebox   # 可替换为其他模块名
```

### 冒烟测试

```bash
curl http://localhost:8080/health
curl http://localhost:8080/api/providers
curl -X POST http://localhost:8080/api/test-single -H "Content-Type: application/json" \
  -d '{"prompt": "What is 2+2?", "provider": "Yqcloud"}'
curl -X POST http://localhost:8080/api/compare -H "Content-Type: application/json" \
  -d '{"prompt": "Hello", "providers": ["Yqcloud", "OperaAria"]}'
curl -X POST http://localhost:8080/api/auth/guest

# Claude（2026-07-04 新增）：需要先登录态 session（游客/匿名会 401），示例假设已用浏览器
# 登录并从 Cookie 里取到 session；也可以带自带 Key 跳过免费额度检查：
curl -X POST http://localhost:8080/api/claude-chat -H "Content-Type: application/json" \
  -H "Cookie: session=<登录后的 session cookie>" \
  -d '{"prompt": "What is 2+2?", "model": "claude-sonnet-5"}'
curl -X POST http://localhost:8080/api/claude-chat -H "Content-Type: application/json" \
  -H "Cookie: session=<登录后的 session cookie>" -H "X-User-Claude-Key: sk-ant-..." \
  -d '{"prompt": "What is 2+2?", "model": "claude-haiku-4-5"}'
```

### Provider 可用性探测脚本（g4f 库升级后使用）

```bash
cd availability_g4f
python find_providers_models.py       # 结果写入当前目录 available_providers_models.txt
python test_providers.py              # 结果写入 ../provider_test_results_v2.txt

python find_image_providers.py        # 结果写入当前目录 available_image_providers_models.txt
python test_image_providers.py        # 结果写入 ../image_provider_test_results.txt（单次约需数分钟）
```

已确认的 5 个免 Key 图片 Provider 组合见第 8 节；仅在 g4f 升级后怀疑结论过期时才需重跑。

### 依赖管理

- `requirements.txt` 完全锁版本（`pip freeze` 格式），例外仅 `gunicorn`。
- 更新：`pip install <package>` → `pip freeze > requirements.txt`。不要手动改版本号。

### 部署到 GAE

```bash
gcloud app deploy app.yaml
gcloud app logs tail -s default
```

- `entrypoint: gunicorn -b :$PORT main:app`，runtime `python312`，自动缩放 1-10 实例。
- `SECRET_KEY` 必须在 `app.yaml` 的 `env_variables` 设置；`firebase-key.json` 不部署，GAE 用 ADC。
- `ANTHROPIC_API_KEY`（2026-07-04 新增）应在 `app.yaml` 的 `env_variables` 设置为开发者的官方 Claude API Key——**不设置也能部署成功**（`CLAUDE_AVAILABLE` 只探测 `anthropic` 包是否可导入，不检查 Key），但未设置时任何未携带自带 Key 的 `/api/claude-chat` 请求都会在真正调用官方 API 时失败。**当前 `app.yaml` 尚未包含这一变量**，部署前需要手动补上，且真实 Key 不应该以明文提交到仓库（可考虑 Secret Manager 或部署时单独注入，而不是照搬 `SECRET_KEY` 目前"直接写在 `app.yaml` 里"的做法）。

## 12. ✏️ CODE STYLE & CONVENTIONS

### Python

- 命名：全局常量 `UPPER_SNAKE_CASE`；函数/变量 `lower_snake_case`；路由函数名与路径语义对齐。
- 日志：模块级 `logger = logging.getLogger(__name__)`，不用 `print`；`INFO` 记录正常流程节点；`ERROR` 必须带 `exc_info=True`；日志中截断长字符串（`prompt[:50]`）。
- 错误处理：LLM/文生图路由顶层 `try/except` 返回标准 JSON 错误体；auth 路由顶层 `try/except` 通过 `flash()` 反馈；`test_g4f_provider`/`test_g4f_image_provider` 用 `try/except/finally` 在 `finally` 计算 `response_time`。
- **结果字典 key 集合严禁增删**（文本 7-key、图片 8-key、互评 4-key）。

### JavaScript / 前端

- Vanilla JS，无框架/构建工具。
- 后端数据通过 Jinja2 `{{ ... | tojson }}` 注入页面初始化时解析。
- Fetch 请求必须先检查 `response.ok` 再 `response.json()`；非 2xx 先尝试解析 `error` 字段，失败则回退 `Server error: 状态码`。

### 提交规范

- 中英文均可，保持原子提交。

## 13. 🧠 MEMORY ANCHORS

- **LLM 核心调用链路**：`index.html Form Submit` → `POST /api/compare` → `ThreadPoolExecutor[1]` + `test_g4f_provider()` → 收集结果 → `ThreadPoolExecutor[2]` + `run_peer_review()` + `parse_peer_review_json()` → 挂载 `peer_reviews` → 排序 → 已登录时 `save_chat_history()` → 挂载 `history_id` → 返回 JSON。
- **文生图核心调用链路**：`generate_images()` → `cleanup_old_generated_media()`（惰性清理 `get_media_dir()` 里过期文件，2026-07-04 新增）→ `ThreadPoolExecutor` + `test_g4f_image_provider()`（`get_image_timeouts(provider_name)` 按 Provider 取 advisory/outer 超时 → `G4FImageClient().images.generate()`，该调用内部已把图片同步下载到本地 `get_media_dir()`；429/queue 类瞬时错误重试一次）→ 排序 → 已登录时 `save_image_history()`（写入独立的 `image_history` 集合，2026-07-04 新增）→ 挂载 `history_id` → 返回 JSON（无互评）→ 前端渲染时 `<img src>`/下载按钮请求 `GET /media/<filename>` → `serve_generated_media()` 提供本地文件。
- **文生图历史查看链路**（2026-07-04 新增）：图片版 Recents 条目点击 → `openImageHistoryEntry(id)` → `GET /image-history/<id>` → `view_image_history()`（游客/匿名重定向 `/`；已登录经 `get_image_history_by_id` 校验归属）→ `image_history.html`（只读，展示 prompt + 8-key 图片 DTO 网格）。
- **Claude 核心调用链路**（2026-07-04 新增）：`index.html compareForm Submit`（Claude 勾选框被选中时）→ 先完成 `/api/compare` 一样的请求 → 额外 `fetchClaudeResult()` → `POST /api/claude-chat` → `_get_authenticated_user_id()` 守卫（游客/匿名 401）→ 有 `X-User-Claude-Key` 则跳过额度检查直接调用；否则 `get_claude_free_tier_usage()` 判断是否 `>= CLAUDE_FREE_TIER_LIMIT`（超限 403 `FREE_TIER_EXHAUSTED`，前端弹 `showClaudeUpgradeModal()`）→ `call_claude_model()`（官方 `anthropic.Anthropic().messages.create()`；余额耗尽——实测为 400/`invalid_request_error`/message 含"credit balance"——转 503 `SERVER_CREDITS_EXHAUSTED`）→ 成功且未用自带 Key 时 `increment_claude_free_tier_usage()` → 返回 Claude Result → 前端并入同一份 `results` 数组重排后统一 `displayResults()`。**全程不经过 `ThreadPoolExecutor`/`run_peer_review()`，不落库。**
- **认证核心调用链路**：`home.html` → `auth Blueprint` → Firebase Firestore → session 写入 → `redirect url_for('index')`。
- **身份状态入口**：根路由 `/` 的 `index()` 是唯一身份路由器。
- **关键不变量**：
  1. 结果排序始终"成功在前，耗时短在前"。
  2. `session['user_id']` 与 `session['is_guest']` 永不同时存在。
  3. Flash + redirect 目标页面必须含 Flash 显示区。
  4. `test_g4f_provider` 7-key 契约；`peer_reviews` 外层追加成 8-key；`run_peer_review` 4-key 契约；`test_g4f_image_provider` 独立 8-key 契约。
  5. 异常文案判定：`CONTENT_POLICY_ERROR_KEYWORDS` 优先于网络类关键词；文生图路径额外有 `GPU_QUOTA_ERROR_KEYWORDS` 优先于 `PEER_REVIEW_NETWORK_ERROR_KEYWORDS`（见第 6 节"文生图异常文案判定顺序"）。
  6. 互评触发：`tested>=2` 且 `success>=2`；`parse_peer_review_json` 解析失败一律 fallback `(80, raw_text)`。
  7. 文本 Provider（`G4F_PROVIDERS`）与图片 Provider（`IMAGE_PROVIDERS`）名字空间严格隔离，不共用映射表/调度路径。
  8. `get_chat_history_list` 只做单字段查询+Python 层排序分页，不依赖 Firestore 复合索引。
  9. `toggle_pin_chat_history` 用 `is None` 判空，`False` 是合法成功结果。
  10. 游客数据不持久化到 Firestore；`window.guestHistory` 镜像进 `sessionStorage`（非 `localStorage`），随标签页关闭清空。**仅适用于对话历史**。
  11. 图片生成结果自 2026-07-04 起持久化到独立的 `image_history` 集合（已登录用户），**但游客/匿名依旧完全不可用**——不落库、无客户端镜像、`/api/image-history*` 一律 401、`/image-history/<id>` 直接重定向；Gemini 生图无免 Key 路径，暂不集成。
  12. `--page-zoom`（当前 `0.88`）驱动 `body{zoom:...}`；涉及视口高度的 CSS 用 `calc(100vh/var(--page-zoom))`；JS 中 `getBoundingClientRect()` 结果赋回 CSS 前需除以该变量。
  13. `image_history` 与 `history` 是两个独立的 Firestore 集合，CRUD 函数、Recents 侧边栏状态（`currentHistoryItems`/`currentImageHistoryItems`）、API 前缀（`/api/history`/`/api/image-history`）、详情页路径均不共用，未来也不应该合并。
  14. Claude（2026-07-04 新增）是与两条 g4f 调用链路完全独立的第三条链路——不进 `G4F_PROVIDERS`/`IMAGE_PROVIDERS` 名字空间，不参与 `ThreadPoolExecutor` 并发调度或 `run_peer_review()` 互评，不落库（无论是否登录）。游客/匿名一律 401，无任何降级体验；每账号 1 次免费额度（`claude_free_tier_usage` 字段，`users` 集合），仅在未携带 `X-User-Claude-Key` 时检查/递增；余额耗尽判定依据是 `error.message` 含 `"credit balance"`（**已用真实余额为 0 的账户实测验证**，实际形状是 400+`invalid_request_error`，不是 429+`insufficient_funds`，也不是文档字面暗示的 403+`billing_error`——后者仅作为兼容兜底保留），且不计入用户免费额度消耗。
  15. **English-only UI text 政策**（2026-07-04 当天晚些时候新增）：任何用户在正常使用软件过程中会看到的文本（渲染出的 HTML 文本/属性、`flash()` 消息、JSON 错误体的 `message`/`error` 字段、发给 LLM 的 prompt 工程文本）一律使用英文，不允许出现中文字符。审计时发现的三处遗留中文（Claude 卡片的游客锁定提示、`showClaudeUpgradeModal()` 弹窗、`apikey-config.html` 全页大部分文案、`SERVER_CREDITS_EXHAUSTED` 的 `message` 字段）均已改为英文，其余 UI 文案/prompt 工程文本在审计前就已是英文。此规则**不**约束内部代码注释与 CLAUDE.md 本身——中文仍是本项目的工程文档语言。回归测试见 `tests/test_english_only_blackbox.py`。
- **核心文件**：LLM/文生图/历史/Claude 后端 `main.py`；认证后端 `auth/routes.py`+`auth/db.py`；前端 `templates/home.html`（未认证）、`templates/index.html`（主功能页，含 chat/image 双模式 Recents 侧边栏 + Claude Provider 卡片）、`templates/history.html`（对话历史只读详情页）、`templates/image_history.html`（图片历史只读详情页，2026-07-04 新增）、`templates/apikey-config.html`（个人 API Key 配置页，2026-07-04 新增）、`templates/auth/`（认证流程）。
- **未完成/已知限制（非 Bug，待评估的未来方向）**：
  - 图片版 Recents/历史详情页已上线（2026-07-04），但仅限已登录用户；若未来要为游客也提供图片历史，需要重新设计（游客数据本来就不落库，不能简单照搬对话历史的 `sessionStorage` 镜像方案，因为 `image_history` 表结构本身就没有游客写入路径）。
  - Gemini 生图需要用户自备 API Key 才可行，涉及新的凭据管理机制设计，超出当前"零凭证"适配层范畴，应作为独立功能设计。
  - 前端交互层（乐观更新、滚动指示器、布局）无自动化回归测试，依赖手动 Playwright/jsdom 验证；若项目引入前端测试框架，应把已验证过的场景补成正式用例。
  - `generated_media/` 本地磁盘存储在生产环境（GAE Standard，多实例）下存在跨实例文件不一致的已知限制：`cleanup_old_generated_media()`（2026-07-04）只解决了单实例本地磁盘随时间无限增长的问题，没有解决"图片写入实例 A、后续 `GET /media/<filename>` 落到实例 B 时 404"这一架构性限制——真正修复需要迁移到共享存储（如 Cloud Storage），是比清理逻辑更大的独立改动。
  - **Claude 免费额度可被多账号注册无限薅取**（2026-07-04 新增，见第 9 节风险）：当前完全没有 IP 级限流或图形验证码防护，`CLAUDE_FREE_TIER_LIMIT` 只按 `user_id` 计数，注册新账号即可重置。TODO：评估 IP 级限流（需先确认 GAE 部署下能否拿到可信客户端 IP）、图形验证码、邮箱验证三个方向，均未实现。
  - Claude 请求结果当前完全不落库（无论是否登录），也没有加入互评——若未来要支持"Claude 对话历史持久化"或"Claude 参与盲评打分"，需要新的设计（不应该复用 `history`/`image_history` 集合或 `run_peer_review()` 的 g4f 专属调度逻辑），是本次集成范围之外的独立功能。
  - ChatGPT/Gemini 两个 API Key 占位输入框（`apikey-config.html`）尚无任何后端集成，纯 UI 占位。
