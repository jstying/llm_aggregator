# claude.md

## 1. 🧠 SYSTEM OVERVIEW

基于 Flask 的大语言模型（LLM）聚合与性能对比 Web 应用。用户输入 Prompt，系统并发调用多个 g4f Provider，实时对比响应内容、响应时间，并让成功的模型互相盲评打分。系统集成 Firebase 认证，支持三种身份：匿名访客、游客（Guest）、已登录用户。已登录用户的对话历史持久化到 Firestore，并通过 ChatGPT/Claude 风格的左侧 Recents 侧边栏（时间分组、懒加载分页、pin/rename/delete 乐观更新、只读详情页）管理；游客历史仅存于 `sessionStorage`，不落库。系统同时支持文生图（text-to-image）聚合对比，走与文本对话完全独立的 g4f 调用链路。文生图结果自 2026-07-04 起持久化到独立的 `image_history` Firestore 集合，并有自己的 Recents 侧边栏（点击"🎨 Generate Image"切换进入、点击"+ New Chat"切换回对话历史）——**但仅限已登录用户使用**，游客与匿名访客既不落库也不提供任何客户端临时记录，这一点与对话历史对游客的处理（`sessionStorage` 临时镜像）刻意不同。

后端：Flask + Blueprint（`auth/`）。前端：Jinja2 + Vanilla JS（无框架/构建工具），页面级 `zoom` 缩放通过 `:root` 的 `--page-zoom`（当前 `0.88`）统一控制。

## 2. 🧬 ARCHITECTURE MAP

系统由三个核心子系统构成：Flask 后端服务、Firebase 认证模块、HTML5/JS 前端。

### 后端服务（Flask，`main.py`）

- **路由层**：页面路由（`/`、`/home`、`/history/<history_id>`、`/image-history/<history_id>`）、LLM API（`/api/providers`、`/api/compare`、`/api/test-single`）、文生图 API（`/api/image-providers`、`/api/generate-images`）、生成媒体静态文件路由（`GET /media/<filename>`）、认证 API（`/api/auth/guest`）、对话历史 API（`/api/history` 系列，仅登录用户）、文生图历史 API（`/api/image-history` 系列，仅登录用户，2026-07-04 新增）。
- **多线程并发调度器**：`ThreadPoolExecutor` 并发调用多个 Provider，防止单点阻塞；文生图路由复用同一套调度骨架（单阶段，无互评）。
- **g4f 适配层**：`g4f.ChatCompletion`（文本对话）与 `g4f.client.Client().images.generate()`（文生图）两条完全独立的调用链路，各自的模型匹配逻辑和异常捕获互不共享。`images.generate()` 在返回前会把生成的图片同步下载到本地 `get_media_dir()` 目录（`./generated_images` 优先，否则 `./generated_media`），Result DTO 的 `url` 字段是形如 `/media/<filename>?url=<原始外部地址>` 的相对路径——这是 g4f 自带 GUI/API 服务器的路由约定，本项目未运行那套服务器，因此 `main.py` 自行注册了 `GET /media/<filename>` 静态文件路由（`serve_generated_media`）来提供这些本地文件，否则前端 `<img>` 与下载按钮都会 404。

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
```

### 耦合风险与设计注意事项

- **硬编码映射**：`PROVIDER_MODELS_MAP`/`IMAGE_PROVIDER_MODELS_MAP` 属于硬编码，g4f 库升级后必须手动同步。
- **全局状态依赖**：`G4F_AVAILABLE`、`FIREBASE_AVAILABLE` 两个全局布尔标志，任一失败都会导致对应功能降级。
- **Flash 消息消费规则**：任何作为重定向目标的页面必须包含 Flash 消息显示区，否则消息会在 session 中堆积、在下一个 auth 页面集中出现。

## 3. 🧰 TECHNICAL STACK

- **语言**：Python, JavaScript
- **后端框架**：Flask
- **并发**：`concurrent.futures.ThreadPoolExecutor`
- **核心依赖**：g4f (GPT4Free)、firebase-admin、python-dotenv
- **认证**：Werkzeug (`generate_password_hash`/`check_password_hash`)、Flask `session`
- **数据库**：Google Cloud Firestore（Firebase Admin SDK）
- **前端**：HTML5, CSS3 (Grid/Flex), Vanilla JS，无框架/无构建工具
- **模板引擎**：Jinja2
- **运行环境**：`os.environ.get('PORT')`/`SECRET_KEY`；本地 `.env`（python-dotenv）
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
│   └── auth/
│       ├── base.html                # 认证页通用布局，三态导航栏，`.card-title` 复用类
│       ├── login.html / register.html / profile.html
├── tests/                           # unittest，505 个用例，不部署
│   ├── test_main_whitebox.py
│   ├── test_main_blackbox.py
│   ├── test_main_graybox.py
│   ├── test_auth_whitebox.py
│   ├── test_auth_blackbox.py
│   ├── test_image_history_whitebox.py   # 图片历史 auth/db.py CRUD（2026-07-04 新增）
│   ├── test_image_history_blackbox.py   # 图片历史 main.py 路由（2026-07-04 新增）
│   ├── test_sidebar_ui_blackbox.py      # 模式感知侧边栏按钮 + G4F 文案移除的标记/文案断言（2026-07-04 新增；仅覆盖 index.html）
│   ├── test_html_structure_blackbox.py  # 渲染页面的 HTML 标签配对结构性回归测试（2026-07-04 新增，见下方"HTML 结构完整性"事故记录）
│   └── test_history_mode_toggle_blackbox.py  # history.html/image_history.html 的 .sidebar-top 按钮改版（2026-07-04 新增，见下方说明）
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
- 模块级常量：`NETWORK_ERROR_KEYWORDS`、`PEER_REVIEW_NETWORK_ERROR_KEYWORDS`、`CONTENT_POLICY_ERROR_KEYWORDS`、`GPU_QUOTA_ERROR_KEYWORDS`（文生图专属，判定顺序见第 9 节）、`ROUTE_PROMPTS_MAP`、`PEER_REVIEW_PROMPTS_MAP`、`SENSITIVE_KEYWORDS`、`IMAGE_GENERATION_ADVISORY_TIMEOUT`(40)/`IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER`(5)/`IMAGE_GENERATION_OUTER_TIMEOUT`(85 = `2*ADVISORY+BUFFER`，2026-07-04 从"advisory+固定 10s"公式改为"2*advisory+固定 5s"公式，见下方 `get_image_timeouts` 说明与第 9 节)、`IMAGE_PROVIDER_TIMEOUT_OVERRIDES`（单 Provider 超时覆盖表，当前仅 `AnyProvider`: advisory 70，outer 由公式推出为 145）、`GENERATED_MEDIA_MAX_AGE_SECONDS`(3600，2026-07-04 新增，见 `cleanup_old_generated_media`)。
- `get_image_timeouts(provider_name)`：查 `IMAGE_PROVIDER_TIMEOUT_OVERRIDES`，命中则用该 Provider 专属的 advisory，否则用默认的 `IMAGE_GENERATION_ADVISORY_TIMEOUT`；outer 永远由 `_compute_outer_timeout(advisory) = 2*advisory + IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER` 从取到的 advisory 现算，**不再**有单独的 per-Provider outer 覆盖值（2026-07-04 起，`IMAGE_PROVIDER_TIMEOUT_OVERRIDES` 里的条目只允许写 `advisory`）。`test_g4f_image_provider()` 和 `generate_images()` 均通过它取超时值，不再直接引用模块常量——这样 `AnyProvider` 可以有更宽松的预算而不影响同批次其他 Provider（outer timeout 是每个 `future.result()` 独立计算的）。
- `cleanup_old_generated_media(max_age_seconds=GENERATED_MEDIA_MAX_AGE_SECONDS)`（2026-07-04 新增）：`generate_images()` 请求开始时惰性调用一次，扫描 `get_media_dir()`，删除 `mtime` 早于 `max_age_seconds`（默认 3600 秒=1 小时）的**文件**（`os.path.isfile()` 守卫，跳过子目录）。目录不存在（`os.listdir` 抛 `OSError`）直接返回；单个文件删除失败（如权限错误）只记 `logger.warning`，不中断其余文件的清理，也不向上抛出——清理失败绝不能阻塞本次图片生成请求。**刻意不做**"每次生成前清空整个目录"：图片结果不落库（见第 6 节"文生图并发调度规则"），浏览器里正在展示的 `<img>`/下载按钮引用的本地文件是唯一副本，无差别清空会把其他并发请求（另一个标签页、另一个恰好落在同一 GAE 实例上的用户）尚未来得及查看/下载的图片一并删除且无法恢复；按年龄清理只处理"足够旧、大概率已经没人在看"的文件。
- **depends_on**：`flask`（含 `send_from_directory`）、`g4f`、`g4f.client.Client as G4FImageClient`、`g4f.image.copy_images.get_media_dir`、`concurrent.futures`、`auth.auth_bp`、`auth.db`（6 个对话历史 CRUD 函数 + 6 个图片历史 CRUD 函数）。

### `auth/db.py` 关键点

- 初始化：`firebase-key.json` 存在则优先用它（本地），否则 `ApplicationDefault()`（GAE）。**必须先检查 key 文件**——`ApplicationDefault()` 的凭据解析是惰性的，不能靠它的构造异常做 fallback 判断。
- `FIREBASE_AVAILABLE`：全局布尔标志，任何异常（含 `ImportError`）均设 `False`。
- 4 个用户 CRUD 函数（`get_user_by_username`/`get_user_by_email`/`create_user`/`get_user_by_id`）：无内部守卫，调用方负责检查 `FIREBASE_AVAILABLE`。
- 6 个对话历史 CRUD 函数（`save_chat_history`/`get_chat_history_list`/`get_chat_history_by_id`/`delete_chat_history`/`update_chat_history_title`/`toggle_pin_chat_history`）：**各自内部**检查 `FIREBASE_AVAILABLE`，不依赖调用方。除 `save_chat_history` 外均先读文档校验 `doc.to_dict().get('user_id') == user_id`，不匹配/不存在则拒绝并返回 fallback 值（`None`/`False`）。三种失败原因（Firebase 不可用/不存在/越权）不区分。
- 6 个图片历史 CRUD 函数（`save_image_history`/`get_image_history_list`/`get_image_history_by_id`/`delete_image_history`/`update_image_history_title`/`toggle_pin_image_history`，2026-07-04 新增）：与上面 6 个对话历史函数逐一同构（同样的归属校验、fallback 值、`pinned_at` 语义），但读写独立的 `image_history` 集合，而不是复用 `history`——两者的 result DTO 分别是 7-key（+ peer_reviews）和 8-key，混进同一个集合需要额外的判别字段，且历史上 `history` 集合已经积累了纯聊天记录，不应该被图片文档污染。
- `pinned_at` 字段：置顶时写 `firestore.SERVER_TIMESTAMP`，取消置顶时用 `firestore.DELETE_FIELD` 整体删除（不是设 `None`）。排序：置顶组内按 `pinned_at` 升序（最早置顶的排最前），未置顶组按 `created_at` 降序。对话历史与图片历史两套函数完全一致地遵循这条规则。
- **`get_chat_history_list`/`get_image_history_list` 都只做单字段等值查询**（`.where('user_id','==',...)`），排序和分页在 Python 应用层完成——避免依赖需要手动创建的 Firestore 复合索引（见第 9 节风险）。

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
- **对话历史 Recents 侧边栏**：`#sidebarRecents` 骨架屏 → 按 `Today`/`Yesterday`/`Previous 7 Days`/`Older` 时间分组渲染；`currentHistoryItems` 是渲染唯一数据源（游客时直接是 `window.guestHistory` 的同一引用，非拷贝）；懒加载分页（滚动到底部 48px 内触发 `loadMoreSidebarHistory()`）；pin/rename/delete 均为乐观更新（先改本地数据+重渲染，再异步请求，失败精确回滚 + `showHistoryErrorToast`）；`sortHistoryItems()` 渲染前重排（置顶组按 `pinned_at` 升序，不改变原数组顺序）；删除确认走自定义 `showDeleteConfirmModal()`（非原生 `confirm()`）。图片历史侧边栏复用 `groupHistoryByDate`/`sortHistoryItems`/`renderHistoryGroups`/`renderHistoryItem` 这几个通用渲染辅助函数（两种历史条目共享同样的 `{id, title, is_pinned, pinned_at, created_at}` 形状），只有数据获取/持久化/导航目标是各自独立的一套。
- **点击 Recents 条目**：`openHistoryEntry(id)`/`openImageHistoryEntry(id)` 整页导航到 `/history/<id>`/`/image-history/<id>`（只读页面），不再内联加载进可编辑表单。
- **游客历史**：`window.guestHistory` 持续镜像进 `sessionStorage`（`persistGuestHistory()`/`loadGuestHistoryFromStorage()`），支持跨页面导航存活，标签页关闭即清空。**仅适用于对话历史**——图片历史没有对应的游客镜像机制。
- **自绘可拖拽滚动指示器**：`#sidebarRecentsScrollThumb`/`#pageScrollThumb`（`setupThumbDrag()`），两处原生滚动条彻底隐藏（`scrollbar-width:none`+`::-webkit-scrollbar{display:none}`，恒为 0 宽度）——Chrome 的原生 overlay 滚动条无法被 `::-webkit-scrollbar` 样式可靠压制，且会阻挡自绘指示器的 `mousedown`，因此改为完全自绘。
- **导航栏对齐**：`.nav-container` 边到边铺满（无 `max-width`），`.nav-left` 定宽 260px 与 `.left-sidebar` 对齐，logo 居中在侧边栏正上方。
- **页面标题/导航栏彻底去除 "G4F" 用户可见文案**（2026-07-04）：`<title>`（`index.html`/`history.html`/`image_history.html` 三处均为 `LLM Aggregator`/`History - LLM Aggregator`/`Image History - LLM Aggregator`）、`.nav-logo` 的移动端缩写 `.logo-short`（原先窄屏下显示字面量 "G4F"，现与桌面端 `.logo-full` 一样统一显示"LLM Aggregator"）、两个模式各自的 `<h1>` 大标题均不再提及"G4F"——`.header-full` 从"G4F LLM Aggregator"/"G4F Image Generator"分别改名为**"Text Generator"**/**"Image Generator"**，两者共用的移动端缩写 `.header-short` 也从各自独立（聊天模式"LLM Aggregator"、图片模式"Image Gen"）统一改成同一句"LLM Aggregator"。图片模式副标题同时从"Generate images with free, no-key g4f providers and compare them side by side."改为不提 g4f 实现细节的"Generate images with providers and compare them side by side."。`g4f` 仍是内部实现依赖（见第 2/3 节），只是不再作为面向用户的产品名称出现。
- 移动端（`<=520px`）：`.left-sidebar` 变为 `position:fixed` 抽屉，由 `.hamburger-btn` 驱动。
- Model 自绘下拉框：高亮切换延迟到面板收起动画结束后执行（`syncSelectedOptionHighlight()` + 150ms `setTimeout`），避免视觉跳变。
- `escapeHtml()` 转义 provider/model/error 后注入 DOM；`response` 经 `marked.parse()` 渲染 Markdown（内容来自受信 LLM，不转义）。

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

## 5. 🔄 EXECUTION & DATA FLOW

1. **初始化**：`load_dotenv()` → `secret_key` 加载 → `auth_bp` 注册 → Firebase 初始化（key 文件优先于 ADC）→ `g4f` 导入探测。
2. **身份路由**：访问 `/`，`index()` 检查 `session.user_id`/`is_guest`，渲染 `home.html` 或 `index.html`。
3. **身份建立**：游客走 `POST /api/auth/guest`；登录/注册走对应表单，成功后写入/清除对应 session 键。
4. **LLM 聚合**：`POST /api/compare` → 第一阶段并发 `test_g4f_provider`（应用 `ROUTE_PROMPTS_MAP` 隐形路由）→ 满足条件时第二阶段并发互评 → 排序 → 已登录时 `save_chat_history` → 返回 JSON（含 `history_id`）。
5. **文生图**：`POST /api/generate-images` → 单阶段并发 `test_g4f_image_provider` → 排序 → 已登录时 `save_image_history` → 返回 JSON（含 `history_id`，无互评）。
6. **退出登录**：清除三个 session 键 → flash → 重定向 `/` → `home.html` 消费 flash。
7. **文生图历史查看**（2026-07-04 新增）：点击图片版 Recents 条目 → `GET /image-history/<id>` → 游客/匿名重定向 `/`；已登录经 `get_image_history_by_id` 校验归属渲染 `image_history.html`（只读）。

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

### Firestore `users` 集合

```python
{'username': str, 'email': str, 'password_hash': str, 'created_at': Timestamp}
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
- `GET /health` — 状态、`g4f_available`、`image_providers`、`routing_rules_loaded`、`peer_review_rules_loaded`

**文生图**：
- `GET /api/image-providers`
- `POST /api/generate-images` — `{prompt, providers, model, max_workers}` → 聚合结果 + `history_id`（已登录用户为实际 id，游客/匿名为 `None`，2026-07-04 起）
- `GET /media/<filename>` — 提供 `get_media_dir()` 下 g4f 已生成的图片/音视频本地文件（Result DTO `url` 字段引用的静态资源，见第 2/9 节）

**页面**：
- `GET /` / `GET /home` / `GET /history/<history_id>` / `GET /image-history/<history_id>`（2026-07-04 新增，仅登录用户，游客/匿名重定向 `/`）

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

### 运行

```bash
python main.py                    # 默认端口 8080
PORT=5000 python main.py
gunicorn -b :8080 main:app        # 模拟 GAE
```

**⚠️ 修改 `templates/*.html` 后必须重启开发服务器**：`app.run(debug=False, ...)`，Jinja2 模板自动重载默认跟随 `app.debug`（`False`），已运行进程会永久缓存旧模板，浏览器强刷无法解决。

访问 `http://localhost:8080`；`http://localhost:8080/health` 验证状态。

### 自动化测试

项目用 `unittest`，测试文件在 `tests/`，共 489 个用例：

- `test_main_whitebox.py`：内部函数（模型降级规则、DTO 完整性、`detect_and_truncate`、`parse_peer_review_json`、`run_peer_review` 429 重试、内容策略/网络错误文案判定、图片 Provider 相关纯函数、`test_g4f_image_provider` 的 GPU 配额友好文案/429-queue 重试/per-provider advisory timeout 转发，2026-07-04 新增）、`TestCleanupOldGeneratedMedia`——`cleanup_old_generated_media()` 纯函数断言：只删严格早于阈值的文件、边界值（恰好等于阈值）不删、混合新旧文件批次只删旧的、目录不存在不抛异常、跳过子目录、单个文件删除失败不影响其余文件清理、不传 `max_age_seconds` 时使用 `GENERATED_MEDIA_MAX_AGE_SECONDS` 模块常量（2026-07-04 新增，均用 `tempfile.TemporaryDirectory` + `patch('main.get_media_dir', ...)` 隔离）。
- `test_main_blackbox.py`：HTTP 接口驱动（`/api/compare`/`/api/generate-images`/`/api/history` 系列/`/history/<id>`/`/health`/`GET /media/<filename>`），含身份路由回归、互评契约、Provider 名字空间隔离断言、`serve_generated_media` 静态文件路由断言（存在文件 200、缺失文件 404、忽略 g4f 附带的 `?url=` 查询参数、路径穿越拦截、Content-Type 校验；用 `tempfile.TemporaryDirectory` + `patch('main.get_media_dir', ...)` 隔离，不触碰真实 `generated_media/` 目录）、GPU 配额错误端到端友好文案断言（2026-07-04 新增）、`test_slow_retry_success_not_discarded_by_outer_timeout`——端到端复现 `PollinationsImage` 429 重试且重试后耗时较长仍应算成功的场景（用 `patch('main.IMAGE_GENERATION_ADVISORY_TIMEOUT', ...)`/`IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER` 缩小成毫秒级以避免真实等待，用 `threading.Event().wait()` 而非 `time.sleep()` 模拟耗时，因为 `main.time` 和测试文件里 `import time` 是同一个模块对象，`patch('main.time.sleep')` 会连带影响测试自己的 `time.sleep()` 调用；2026-07-04 outer 超时公式修订新增）、`TestGenerateImagesTriggersMediaCleanup`——端到端验证 `POST /api/generate-images` 会触发 `cleanup_old_generated_media()`：过期文件被删除、近期文件（可能仍被其他并发请求展示）不受影响、`get_media_dir()` 尚不存在时清理内部吞掉 `OSError` 不导致整个请求 500（2026-07-04 新增）；`TestGenerateImagesEndpoint::test_history_id_is_null_for_anonymous_request`（2026-07-04 更新）——图片历史持久化上线后，匿名/游客请求必须仍然拿到 `history_id: None` 且不触发 `save_image_history`。
- `test_main_graybox.py`：全局状态与线程池行为（排序契约、超时 fallback、`max_workers` 约束、`G4F_AVAILABLE` 降级、互评/图片超时数值锁定、`IMAGE_PROVIDER_TIMEOUT_OVERRIDES`/`get_image_timeouts()` 的 `AnyProvider` 专属超时断言，2026-07-04 新增；`TestImageOuterTimeoutValue` 于 2026-07-04 outer 公式修订时更新——锁定 `IMAGE_GENERATION_OUTER_TIMEOUT==85` 及 `outer == 2*advisory+IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER` 的公式关系，并验证 `AnyProvider` 的 outer 同样由该公式推导而非单独硬编码）；`TestSaveImageHistoryRobustness`（2026-07-04 新增，`TestSaveHistoryRobustness` 的图片对应版本）——`save_image_history()` 抛异常或返回 `None` 都不能让 `/api/generate-images` 本身失败，`history_id` 精确 fallback 为 `None`。
- `test_auth_whitebox.py`：密码哈希、Firestore mock 下的用户/对话历史 CRUD 契约（含归属校验、排序、`pinned_at` 哨兵值断言）。
- `test_auth_blackbox.py`：auth 蓝图路由、session 读写、访问控制。
- `test_image_history_whitebox.py`（2026-07-04 新增）：`auth/db.py` 图片历史 6 个 CRUD 函数的 Firestore mock 契约测试，与 `test_auth_whitebox.py` 里对话历史的等价测试类逐一同构（`TestSaveImageHistory`/`TestGetImageHistoryList`/`TestGetImageHistoryById`/`TestDeleteImageHistory`/`TestUpdateImageHistoryTitle`/`TestTogglePinImageHistory`），额外断言读写的是 `image_history` 集合而非 `history`。
- `test_image_history_blackbox.py`（2026-07-04 新增）：`main.py` 图片历史路由的 HTTP 层测试——`TestViewImageHistoryPage`（含"游客直接重定向、不像 `/history/<id>` 那样渲染空壳"的断言）、`TestImageHistoryAuthGuard`（游客/匿名 401）、`TestGetImageHistoryEndpoint`/`TestUpdateImageHistoryTitleEndpoint`/`TestDeleteImageHistoryEndpoint`/`TestToggleImageHistoryPinEndpoint`（与对话历史等价路由同构）、`TestGenerateImagesHistoryPersistence`（`/api/generate-images` 触发 `save_image_history` 并挂载 `history_id`，游客不触发）、`TestIndexPageImageSidebarMarkup`（`/` 页面包含图片模式切换入口）。
- `test_sidebar_ui_blackbox.py`（2026-07-04 新增）：`index.html`/`history.html`/`image_history.html` 纯前端文案/标记层面的 HTTP 黑盒断言（Flask test client 走真实 `render_template` 渲染路径，而非解析 JS——项目无 JS 测试框架，这与 CLAUDE.md 第 11 节"前端交互层不在 unittest 覆盖范围"的既有说明一致，这里断言的是渲染出的 HTML/内联 JS 源码字符串本身，不是运行时交互行为）。三个测试类：`TestModeAwareSidebarButtonsMarkup`——`#newChatBtn` 渲染为固定的"+ New"（`.btn-icon`+`.btn-label` 双 span），`#generateImageBtn` 携带可变的 `#modeToggleBtnIcon`/`#modeToggleBtnLabel`（初始状态"🎨"+"Generate Image"），并断言内联脚本中 `switchToImageMode()`/`switchToCompareMode()` 分别把这两个 id 写成"✍️"+"Generate Text"/"🎨"+"Generate Image"、两个按钮的 `click` 监听器各自按 `sidebarMode` 分流（`#generateImageBtn` 在两个切换函数间二选一；`#newChatBtn` 在 `#clearBtn`/`#clearImagesBtn` 间二选一，且不再调用 `switchToCompareMode()` 强制切模式）。`TestG4FBrandingRemoved`——`/`（登录/游客）、`/history/<id>`、`/image-history/<id>` 四个响应体均不含字面量"G4F"，且三个页面的 `<title>` 均已改为不含"G4F"前缀的版本。`TestHeaderRenames`——`.header-full` 断言为"Text Generator"/"Image Generator"，图片模式副标题断言为新文案且不含"free, no-key g4f providers"，`.header-short` 在两个模式下统一断言为"LLM Aggregator"（不再是"Image Gen"）。
- `test_html_structure_blackbox.py`（2026-07-04 新增，见第 9 节"HTML 结构完整性"事故记录）：对 `GET /`（登录/游客/匿名）、`GET /history/<id>`（登录/游客）、`GET /image-history/<id>`（登录）渲染出的完整 HTML 做标签配对结构性检查，而不是文案/id 子串断言——`assert_html_tag_balance()` 辅助函数剥离 `<script>`/`<style>` 内容后用 `html.parser.HTMLParser` 验证每个结束标签都精确匹配最近打开的标签、且文档结束时打开标签栈已清空。这类测试专门用来兜住"文本子串断言全部通过、但标签配对/嵌套已经被破坏"的回归——已用"重新引入 2026-07-04 那次 `.sidebar-top` 开始标签丢失的 bug"验证过它确实会让相关用例失败。
- `test_history_mode_toggle_blackbox.py`（2026-07-04 新增）：`history.html`/`image_history.html` 的 `.sidebar-top` 按钮改版为对齐 `index.html` 最新的 `.btn-icon`/`.btn-label` span 结构（`test_sidebar_ui_blackbox.py` 的 `TestModeAwareSidebarButtonsMarkup` 只覆盖 `index.html`，不覆盖这两个只读页面，所以新开此文件而非往其中添加用例）。`TestHistoryPageModeAwareButtons`（游客+登录两态各验证一次，因为 `.sidebar-top` 对两态都渲染同一份 markup）：断言 `#newChatBtn` 渲染为"+ New"而非旧版"+ New Chat"；断言此前**完全不存在**的 `#generateImageBtn`（本次新增）已出现，固定图标/文案为"🎨"+"Generate Image"；断言两个按钮的 `click` 处理器分别导航到 `/`、`/?mode=image`。`TestImageHistoryPageModeAwareButtons`：断言 `#newChatBtn` 同样改为"+ New" span 结构；断言 `#generateImageBtn` 的图标/文案从旧版固定的"🎨"+"Generate Image"改为"✍️"+"Generate Text"；断言两个按钮的点击语义随文案调转——`#newChatBtn` 现在导航到 `/?mode=image`（旧版是 `/`），`#generateImageBtn` 现在导航到 `/`（旧版是 `/?mode=image`），因为两个只读页面各自固定代表一种语境，"+ New" 不应再触发模式切换（呼应 `index.html` 里 `#newChatBtn` 已经确立的"不强制切换模式"规则，见上方 `templates/index.html` 关键点小节）。

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
- **核心文件**：LLM/文生图/历史后端 `main.py`；认证后端 `auth/routes.py`+`auth/db.py`；前端 `templates/home.html`（未认证）、`templates/index.html`（主功能页，含 chat/image 双模式 Recents 侧边栏）、`templates/history.html`（对话历史只读详情页）、`templates/image_history.html`（图片历史只读详情页，2026-07-04 新增）、`templates/auth/`（认证流程）。
- **未完成/已知限制（非 Bug，待评估的未来方向）**：
  - 图片版 Recents/历史详情页已上线（2026-07-04），但仅限已登录用户；若未来要为游客也提供图片历史，需要重新设计（游客数据本来就不落库，不能简单照搬对话历史的 `sessionStorage` 镜像方案，因为 `image_history` 表结构本身就没有游客写入路径）。
  - Gemini 生图需要用户自备 API Key 才可行，涉及新的凭据管理机制设计，超出当前"零凭证"适配层范畴，应作为独立功能设计。
  - 前端交互层（乐观更新、滚动指示器、布局）无自动化回归测试，依赖手动 Playwright/jsdom 验证；若项目引入前端测试框架，应把已验证过的场景补成正式用例。
  - `generated_media/` 本地磁盘存储在生产环境（GAE Standard，多实例）下存在跨实例文件不一致的已知限制：`cleanup_old_generated_media()`（2026-07-04）只解决了单实例本地磁盘随时间无限增长的问题，没有解决"图片写入实例 A、后续 `GET /media/<filename>` 落到实例 B 时 404"这一架构性限制——真正修复需要迁移到共享存储（如 Cloud Storage），是比清理逻辑更大的独立改动。
