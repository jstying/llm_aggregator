# claude.md

## 0. 未来更新规范（硬性要求）

后续任何修改必须简明扼要。不要写调试过程，不要写事故回顾，不要写"最初以为...后来发现..."这种叙事。只写结论和当前状态。

请遵循以下格式追加更新记录：

`[模块名] 更新原因：简短说明。调整内容：1. 2. 3.`

举例：

`[Claude 额度] 更新原因：用户反馈额度太少。调整内容：1. CLAUDE_FREE_TIER_LIMIT 从 10 改成 20。2. 前端弹窗文案同步改数字。`

写完新内容后，检查一下这份文档是不是又开始变长。如果某段历史记录已经超过 3 个月，或者对应的功能已经彻底稳定，就把细节删掉，只保留最终结论。这份文档是给未来的 Claude 看的操作手册，不是项目日志。

## 1. 系统概览

这是一个基于 Flask 的大语言模型聚合和对比网页应用。用户输入一个 prompt，系统会同时调用多个 g4f provider，展示每个 provider 的回答内容和响应时间，然后让回答成功的几个模型互相打分评价。

系统支持三种身份：匿名访客、游客、登录用户。登录用户的对话记录存在 Firestore 里，用左侧的 Recents 侧边栏管理，支持分组、分页、置顶、改名、删除。游客的记录只存在浏览器的 sessionStorage 里，关掉标签页就没了。

系统同时支持文生图对比，用的是完全独立的一套 g4f 调用链路。图片生成结果存在独立的 `image_history` 集合里，也有自己的 Recents 侧边栏，但只有登录用户能用，游客和匿名用户完全用不了这个功能。

聊天功能还接入了官方 Anthropic API（Claude），图片生成功能接入了官方 Google Gemini API（"Nano Banana"系列）。这两个是项目里仅有的两个"真金白银有成本"的 provider，所以配了一整套额度控制和防滥用机制，比其他免费 provider 严格很多。具体规则见第 6 节。

导航栏顶部有个 Trial Quota 徽章，显示用户 Claude 和 Gemini 各自还剩多少次免费额度。聊天模式显示 Claude 的数字，图片模式显示 Gemini 的数字。每次调用完这两个 provider 之后，前端都会重新问后端一次真实的额度数字，不会自己在本地猜。

后端用 Flask 加 Blueprint（`auth/`）搭建。前端用 Jinja2 加原生 JS，没有用任何前端框架或构建工具。页面整体缩放靠 `:root` 上的 `--page-zoom` 变量控制，现在是 0.88。

## 2. 架构地图

系统分三块：Flask 后端、Firebase 认证模块、HTML5/JS 前端。

### 后端（`main.py`）

路由分几类：页面路由（`/`、`/home`、`/history/<id>`、`/image-history/<id>`、`/apikey-config`）、g4f 聊天 API（`/api/providers`、`/api/compare`、`/api/test-single`）、Claude API（`/api/claude-chat`）、Gemini API（`/api/gemini-image`）、额度查询 API（`/api/quota-status`）、文生图 API（`/api/image-providers`、`/api/generate-images`）、生成图片的静态文件路由（`/media/<filename>`）、认证 API（`/api/auth/guest`）、对话历史 API（`/api/history` 系列）、图片历史 API（`/api/image-history` 系列，仅登录用户）。

并发调度靠 `ThreadPoolExecutor`，用来同时调用多个 provider，防止一个卡住拖慢全部。文生图路由复用同一套调度骨架，但只有一个阶段，没有互评步骤。

g4f 那边有两条完全独立的调用链路：`g4f.ChatCompletion` 管文本对话，`g4f.client.Client().images.generate()` 管文生图。两条链路的模型匹配逻辑和异常处理完全不共用。图片生成时 g4f 会自动把图片下载到本地的 `get_media_dir()` 目录，返回的 `url` 字段是 `/media/<filename>?url=...` 这种相对路径。这是 g4f 自带 GUI 服务器的路由约定，但本项目没跑那套服务器，所以自己补了一个 `GET /media/<filename>` 静态文件路由（`serve_generated_media`）来提供这些文件。这些本地文件现在不会自动清理，会一直攒着，是刻意接受的代价，换来历史图片能永久看到。

Claude 走一条完全独立的第三条链路：`call_claude_model()` 直接用官方 `anthropic` SDK 的 `client.messages.create()`。它不进 `ThreadPoolExecutor` 并发调度，不参与互评，也不复用任何 g4f 的映射表。前端把它做成一张视觉上并列的 Provider 卡片放在对话表单里，点击 Compare 提交时会额外单独发一次 `POST /api/claude-chat` 请求，渲染层再把结果合并进同一份结果列表。这个请求可以携带 `/api/compare` 已经返回的 `history_id`，让这次 Claude 调用的结果追加进那条对话历史记录。

Gemini（"Nano Banana"系列）走第四条完全独立的链路：`call_gemini_image_model()` 用官方 `google-genai` SDK 的 `client.interactions.create()`。作用场景是图片生成表单，跟 Claude 是完全平行的关系，一个管聊天场景一个管图片场景。三档模型都已接入：`Nano Banana Pro` 对应 `gemini-3-pro-image`，`Nano Banana 2` 对应 `gemini-3.1-flash-image`，`Nano Banana Lite` 对应 `gemini-3.1-flash-lite-image`，前端用一个下拉框单选，选哪一档都只消耗一次额度。这个请求同样可以携带 `history_id`，追加进已有的图片历史记录。

前沿模型结果的历史持久化：`claude_chat()`/`gemini_image_chat()` 都接受一个可选的请求体字段 `history_id`。只要这次调用真的发生了（没被额度拦截），无论成功还是失败，都会把结果追加进这个 `history_id` 对应的、已经存在的历史记录里。负责追加的函数是 `auth/db.py` 里的 `append_chat_history_result()` 和 `append_image_history_result()`，追加时会做归属校验，追加完还会按"成功优先、耗时短优先"重新排序整个结果数组再写回。这两个函数只能往已有记录里追加，不能自己创建新记录，创建新记录的入口永远只有 g4f 那条链路的 `save_chat_history()`/`save_image_history()`。

### 认证子系统（`auth/`）

`auth_bp` 挂载在根路径下，没有前缀：`/login`、`/register`、`/logout`、`/profile`。`auth/db.py` 启动时会尝试连 Firebase Firestore，连不上就把 `FIREBASE_AVAILABLE` 设成 `False`，这时候认证路由返回 503，不会崩溃。用户身份靠 Flask 的 `session` 在请求之间传递，密钥来自 `SECRET_KEY` 环境变量。

### 前端（Jinja2 + JS）

导航栏有三种状态，根据 `session.user_id`/`is_guest` 来切换，`auth/base.html` 和 `index.html` 里各维护一份。所有跟后端的通信都走 Fetch API，非阻塞。

## 3. 技术栈

语言用 Python 和 JavaScript。后端框架是 Flask，并发靠 `concurrent.futures.ThreadPoolExecutor`。核心依赖有 g4f、firebase-admin、python-dotenv、官方 `anthropic` SDK、官方 `google-genai` SDK（导入路径是 `from google import genai`，不要跟已经废弃的旧 SDK `google-generativeai` 搞混）。

认证用 Werkzeug 的密码哈希函数加 Flask 的 `session`。数据库是 Google Cloud Firestore，通过 Firebase Admin SDK 访问。前端是纯 HTML5、CSS3、原生 JS，没有框架也没有构建工具。模板引擎是 Jinja2。部署平台是 Google App Engine。

环境变量方面，`SECRET_KEY` 和 `PORT` 是基础配置，本地开发用 `.env` 文件加 python-dotenv 加载。`ANTHROPIC_API_KEY` 是开发者的默认 Claude Key，不设置也能启动，只有真正调用时才会报错。`GEMINI_API_KEY` 是开发者的默认 Gemini Key，行为跟 Claude 不一样：`google_genai.Client()` 一构造就会立刻检查这个环境变量，缺了直接抛 `ValueError`，不会等到真正调用的时候才报错。不过用户最终看到的效果是一样的：请求返回失败,不影响其他功能。

## 4. 代码结构

```
llm_aggregator/
├── main.py                  # Flask 入口，所有路由
├── auth/
│   ├── __init__.py          # auth_bp 蓝图
│   ├── db.py                 # Firebase 初始化、用户 CRUD、历史 CRUD、额度计数器
│   └── routes.py             # /login /register /logout /profile
├── templates/
│   ├── home.html             # 未登录且非游客的唯一入口
│   ├── index.html            # 主功能页，对比表单加文生图表单加 Recents 侧边栏
│   ├── history.html           # 只读对话历史详情页
│   ├── image_history.html     # 只读文生图历史详情页
│   ├── apikey-config.html      # 个人 API Key 配置页
│   └── auth/
│       ├── base.html          # 认证页通用布局
│       ├── login.html / register.html / profile.html
├── tests/                    # unittest 测试，不部署
├── availability_g4f/          # provider 可用性探测脚本，开发辅助用，不部署
├── firebase-key.json           # 本地 Firebase 密钥，严禁提交
├── .env                        # 本地环境变量，严禁提交
├── requirements.txt / app.yaml
└── env/                         # 虚拟环境，不提交
```

### `main.py` 关键点

`load_dotenv()` 先加载环境变量。`app.secret_key` 来自 `SECRET_KEY`，没设置就用随机值（重启后所有 session 失效）。

`index()` 是身份路由的核心：没登录也不是游客就渲染 `home.html`，否则渲染 `index.html`，同时会通过 `_get_frontier_quota_context()` 算出 Trial Quota 的上下文传给模板。这个函数只在用户登录时才去查 Claude 和 Gemini 的额度使用情况，游客和匿名用户拿到的两个值都是 `None`,不是"降级展示 0/10"。

`home()`（`GET /home`）只清除 `is_guest`，不动 `user_id`，然后重定向到 `/`。

`view_history(id)` 处理对话历史详情页：匿名用户重定向到首页，登录用户校验归属后渲染，游客渲染一个空壳让前端自己去 `sessionStorage` 里找。`view_image_history(id)` 处理图片历史详情页，规则不太一样：游客和匿名用户都直接重定向到首页,没有空壳这一说，因为图片历史对游客压根不提供任何形式的记录。

`_get_authenticated_user_id()` 是对话历史、图片历史、Claude、Gemini、额度查询这几类路由共用的守卫函数，游客和匿名一律当成未认证处理，返回 401。

模型降级的纯函数是 `determine_actual_model()`（文本）和 `determine_actual_image_model()`（图片），规则写在第 6 节。`init_result_object()`/`init_image_result_object()` 负责初始化标准的结果字典。`detect_and_truncate()` 做重复检测和敏感词拦截。`parse_peer_review_json()` 从互评回答里提取分数和评语，解析失败就统一 fallback 成 `(80, 原始文本)`。

`test_g4f_provider()` 和 `test_g4f_image_provider()` 是两条链路各自的核心测试函数,内部的重试逻辑和错误分类顺序也是完全独立的，具体顺序见第 6 节。`run_peer_review()` 负责单次互评请求。`compare_providers()` 负责两阶段并发（先测试后互评），登录用户会调用 `save_chat_history()`。`generate_images()` 负责单阶段并发，登录用户调用 `save_image_history()`，不再有任何清理本地文件的步骤。

`call_claude_model(prompt, model_key, user_api_key=None)` 是官方 Claude 调用的核心函数。`user_api_key` 有值就用用户自己的 Key 实例化客户端，没有就用零参构造读环境变量里的开发者 Key。这是 Key 路由的唯一分支点，任何调用方都不应该绕开这个函数自己去实例化客户端。异常分类的关键规则：判断"开发者账户余额耗尽"不能看某个固定的 status_code，而是要看 `error.message` 里有没有"credit balance"这个关键词（这是经过真实账户验证过的稳定信号，官方文档字面暗示的 403+`billing_error` 组合作为兼容兜底保留，但不是主判断依据）。

`call_gemini_image_model(prompt, model_key, user_api_key=None)` 是官方 Gemini 调用的核心函数，跟 Claude 那个是同一种"Key 路由加错误分类"模式的另一份独立实现。判断配额耗尽看 `status_code == 429`，这也是真实验证过的稳定信号。这个函数用 `getattr()` 鸭子类型去读异常属性,不要改成 import 具体的异常类做判断，因为那些类在 google-genai 包里没有稳定的公开导入路径。

`_append_claude_result_to_history()` 和 `_append_gemini_result_to_image_history()` 是薄包装函数，负责把 Claude/Gemini 这次调用的结果追加进已有的历史记录。`history_id` 为空就直接跳过，追加失败只记日志，不会影响这次请求本身的响应。

`quota_status()`（`GET /api/quota-status`）返回 `{claude: {used, limit}, gemini: {used, limit}}`，用同一套认证守卫。`apikey_config()`（`GET /apikey-config`）只负责渲染页面,没有登录态守卫，因为页面本身不发起任何需要权限的请求，只是往浏览器 `localStorage` 里存东西。

### `auth/db.py` 关键点

初始化时优先找本地的 `firebase-key.json`,找不到才用 `ApplicationDefault()`（给 GAE 用）。必须先检查密钥文件是否存在，因为 `ApplicationDefault()` 的凭据解析是惰性的，不能靠它的构造异常来判断。

任何异常都会把 `FIREBASE_AVAILABLE` 设成 `False`。历史 CRUD 类的函数都在内部自己检查这个标志，不依赖调用方检查。除了创建操作之外，其他操作都会先读文档校验 `user_id` 字段是不是匹配,不匹配或者不存在就拒绝并返回 fallback 值。

查询一律用 `.where(filter=FieldFilter(field, op, value))` 这种新写法，不要用已经废弃的位置参数写法 `.where(field, op, value)`,后者会在日志里打一堆废弃警告。

`append_chat_history_result()`/`append_image_history_result()` 只能往一条已经存在的记录里追加，不能创建新记录。追加逻辑是：读出现有的结果列表，追加新结果，按"成功优先、耗时短优先"重新排序，整体写回。

`pinned_at` 字段用来控制置顶排序：置顶时写 `SERVER_TIMESTAMP`，取消置顶时用 `DELETE_FIELD` 整体删除这个字段（不是设成 `None`）。排序规则是置顶组内按 `pinned_at` 升序,未置顶组按 `created_at` 降序。对话历史和图片历史两套都遵循同一个规则。

`get_chat_history_list`/`get_image_history_list` 都只做单字段等值查询，排序和分页放在 Python 层做，这样可以避免依赖需要手动在 Firebase 控制台创建的复合索引。

`get_claude_free_tier_usage()`/`increment_claude_free_tier_usage()` 读写的是 `users` 集合文档上的 `claude_free_tier_usage` 整型字段，不需要在建用户的时候预先写入初始值，读的时候用 `.get('claude_free_tier_usage', 0)` 兜底就行。递增操作用 `firestore.Increment(1)` 做原子递增,不要先读再写两步走，避免并发下的竞态。检查额度和真正递增额度是两次独立的 Firestore 操作,中间没有事务保护，这是刻意的简化。`get_gemini_free_tier_usage()`/`increment_gemini_free_tier_usage()` 是完全同构的一套，读写独立的 `gemini_free_tier_usage` 字段，两个计数器互不共享。

### `auth/routes.py`

每条路由外层都有 `try/except`，出错就走 `flash()` 反馈。登录注册成功会写 `session['user_id']`/`username`，同时清掉 `is_guest`。退出登录清除所有三个 session 键。`/profile` 会先检查 `session['user_id']`,没有就重定向到登录页。

### 前端模板关键点

`templates/index.html` 用两栏布局：左边 260px 宽的深色侧边栏，右边是主内容区。侧边栏用 `position:sticky` 加纯 CSS `calc()` 算高度，不依赖 JS 手工计算像素值，这样可以避免缩放叠加导致的取整误差。

文生图模式和对话模式是两个互斥的容器，`switchToImageMode()`/`switchToCompareMode()` 负责切换。图片 Provider 的勾选框必须用独立的 class（`.image-provider-checkbox`/`.image-provider-trigger`），不能跟对话表单共用,因为项目里有些查询是全局无容器限定的 `querySelectorAll`，同名 class 会互相污染导致提交错误的数据或者代码崩溃。这条规则贯穿整个项目：任何新增的 provider 卡片都要用自己独立的 class,不能复用别人的。

Recents 侧边栏支持对话历史和图片历史两种模式，用 `sidebarMode` 变量区分，物理上共用同一个 `#sidebarRecents` 容器。图片版 Recents 只对登录用户开放,游客看到的是一句锁定文案,不会发起任何网络请求。对话历史对游客走 `sessionStorage` 镜像方案，图片历史对游客完全没有降级方案，这是刻意的差异化设计。

对话表单和图片表单都是"四段式"布局：先是前沿 provider 选择区（Claude 或 Gemini 卡片），然后是对应的模型下拉框，然后是免费 g4f provider 勾选区，最后是免费模型下拉框。前沿 provider 区和免费 provider 区是两个独立的容器，不要合并。

Claude 卡片（`#claudeProviderCard`）和 Gemini 卡片（`#geminiProviderCard`）都要用各自独立的 class，不能跟任何已有 class 混用。游客/匿名看到的都是置灰卡片加提示文案"Log in to unlock frontier models"。提交表单时,如果这些前沿卡片被勾选了，会在拿到 g4f 结果之后额外单独发一次请求，结果合并进同一份渲染列表。

页面上所有用户可见的文案必须是英文，不能出现中文。这条规则不管代码注释和 CLAUDE.md 本身，那些依然可以用中文写。

滚动条方面，项目自己画了可拖拽的滚动指示器，原生滚动条被彻底隐藏了。自定义下拉面板关闭的时候要用 `max-height:0` 加 `overflow:hidden`，不能只用 `opacity:0`/`visibility:hidden`,不然这个看不见的盒子还是会撑大页面的可滚动区域。

## 5. 核心执行流程

1. 启动时依次做：加载环境变量，注册认证蓝图，初始化 Firebase，探测 g4f/anthropic/google-genai 是否能正常导入。
2. 访问 `/`，`index()` 检查登录状态，决定渲染首页还是主功能页。
3. 游客走 `/api/auth/guest`，登录注册走对应表单。
4. 聊天对比走 `/api/compare`：先并发测试各 provider，满足条件就进行互评，排序后登录用户存历史，返回结果。
5. 文生图走 `/api/generate-images`：单阶段并发测试，排序后登录用户存历史，返回结果，没有互评。
6. Claude 调用走 `/api/claude-chat`：认证守卫,判断是否用自带 Key,判断额度,调用官方 API,分类错误,更新额度计数器,追加进历史记录。
7. Gemini 调用走 `/api/gemini-image`，流程跟 Claude 完全对称，只是场景换成图片生成。

## 6. 核心业务规则

### 身份三态

匿名用户没有 `user_id` 也没有 `is_guest`，访问首页看到 `home.html`，什么数据都不存。游客有 `is_guest=True`，看到主功能页加游客徽章，数据只存在前端内存和 sessionStorage 里,不会写进数据库。登录用户有 `user_id`，数据会跟 Firestore 同步。这三个键互斥：`user_id` 存在的时候 `is_guest` 必须已经清除，反过来也一样。

### 模型自适应降级规则

文本模型走三条规则：指定的模型在映射表里就直接用，不支持或者没指定就用映射表里第一个，provider 没有模型配置就兜底用 `gpt-3.5-turbo`。图片模型只有前两条规则，没有第三条兜底,provider 不在映射表里就返回 `None`,前端展示成 `default`。

### 互评规则

触发条件是测试的 provider 数量大于等于 2，而且成功的数量也大于等于 2。每个成功的回答会被其他所有成功的回答评价，但不评价自己。解析互评结果失败就统一 fallback 成 80 分加原始文本。失败的 provider 不参与互评，既不评别人也不被评。

### 排序规则

成功的排在前面，同样成功或者同样失败的情况下,耗时短的排前面。文本和图片两条链路共用这个排序表达式。

### 错误文案判定顺序

文本这边，内容审查类错误（比如 Azure OpenAI 的审查拦截）必须比网络类错误优先判定,因为审查类错误重试没有意义,误判成"系统繁忙"会诱导用户做无效重试。

图片这边，GPU 配额耗尽错误必须比网络类错误优先判定,配额耗尽不重试，重试对已经耗尽的配额没有意义，还会给紧张的免费资源加压。

### 图片生成重试规则

只有 429 或者排队已满这类瞬时限流错误才重试一次，等 2 到 3 秒随机抖动再试。GPU 配额耗尽和内容审查类错误不重试。

### 图片生成超时预算

默认 advisory timeout 是 40 秒，外层 timeout 不是写死的常量，而是用公式 `2*advisory + 5秒缓冲` 现算出来的,默认场景下算出 85 秒。用两倍是因为重试会跑最多两次尝试，每次都可能接近满 advisory 时间才结束，一倍时间加小缓冲不够用。`AnyProvider` 这种聚合型 provider 实测耗时更长,单独给它 advisory 70 秒的预算,外层时间同样用公式现算，不要给它手写一个独立的外层数值。

以后如果某个 provider 需要更长时间，应该单独给它加 advisory 覆盖值,而不是笼统调高全局默认值，那样会拖慢所有 provider 批次的最坏情况等待时间。

### Claude 权限控制

游客和匿名用户一律拦截，前端置灰卡片,后端 401,两层防御。没有第三层降级,Claude 对游客就是完全不可用,不像对话历史那样给个客户端临时记录。

每个账号有 `CLAUDE_FREE_TIER_LIMIT`（现在是 10）次免费额度,只在没带自己的 Key 时才检查和消耗，而且只有调用成功才会消耗,失败的调用不算。额度用完之后后端直接拦截,完全不会去调用官方 API，不消耗开发者账户的任何调用次数。检查和递增额度中间没有事务保护，是刻意的简化。

一次点击最多消耗一次额度,这个不变量在当前架构下是自动成立的,因为聊天场景只有 Claude 一个前沿 provider。

用户可以在 `/apikey-config` 页面填入自己的 Key，存在浏览器 `localStorage` 里，之后每次请求都带在 `X-User-Claude-Key` 请求头里。后端从来不会持久化用户的个人 Key,它只活在浏览器本地和单次请求的生命周期里。

开发者账户余额耗尽的时候会转换成 `SERVER_CREDITS_EXHAUSTED` 错误码，返回 503,并且不算用户的免费额度消耗。

### Gemini 权限控制

跟 Claude 完全同构，只是把"对话"换成"图片生成"。额度字段是 `gemini_free_tier_usage`，跟 Claude 的额度完全独立，互不共享。开发者账户配额耗尽转换成 `SERVER_QUOTA_EXHAUSTED` 错误码。个人 Key 走 `X-User-Gemini-Key` 请求头，同样不持久化在后端。

Gemini 的验证覆盖范围比 Claude 小一些：只用真实但零配额的账户验证过配额耗尽这一种场景，"配额充足时的成功路径"和"无效 Key 的 403 场景"还只靠文档推断和 mock 数据验证,没有做过真实账户的端到端验证。这是已知的验证缺口,以后拿到有正配额的真实 Key 时应该补上。

## 7. 数据模型

### LLM Result（文本，7 个字段，互评结果会额外挂一个 `peer_reviews` 数组变成 8 个字段）

```python
{
    'provider': str, 'success': bool, 'response': str, 'error': str,
    'response_time': float, 'model': str, 'type': 'g4f'
}
```

### Image Result（图片，8 个字段，独立契约,不跟文本 DTO 混用）

```python
{
    'provider': str, 'success': bool, 'url': str | None, 'b64_json': str | None,
    'error': str, 'response_time': float, 'model': str, 'type': 'g4f_image'
}
```

`url` 和 `b64_json` 互斥,成功时只有一个非空。

### Claude Result

```python
{
    'provider': 'Claude', 'success': bool, 'response': str, 'error': str,
    'response_time': float, 'model': str, 'type': 'anthropic',
}
```

跟 LLM Result 结构相似但是独立的第三种 DTO，`type` 字段值不一样，而且没有 `peer_reviews`。

### Gemini Image Result

```python
{
    'provider': 'Gemini', 'success': bool, 'url': None, 'b64_json': str | None,
    'error': str, 'response_time': float, 'model': str, 'type': 'google_genai',
}
```

跟 Image Result 结构相似但是独立的第四种 DTO，`url` 恒为 `None`,因为官方 API 直接返回 base64 编码的图片字节，不落地成本地文件。

### Firestore 集合结构

`users` 集合存用户名、邮箱、密码哈希、创建时间，还有 `claude_free_tier_usage` 和 `gemini_free_tier_usage` 两个额度字段。

`history` 集合（仅登录用户）存对话历史，字段有 `user_id`、`title`、`prompt`、`results`、`created_at`、`is_pinned`、`pinned_at`。`results` 数组里可能混有 g4f 结果和 Claude 结果两种形状,渲染时要用防御性写法处理 `peer_reviews` 可能不存在的情况。

`image_history` 集合（仅登录用户）结构类似,但是独立的集合,存图片类 DTO。`results` 数组里可能混有 g4f 图片结果和 Gemini 结果。

这两个集合永远不要合并,也不要互相混用判别字段。

### CRUD 契约表

| 函数 | 参数 | 成功 | 失败 |
|---|---|---|---|
| `save_chat_history` | `user_id, prompt, results` | 含 id 的 dict | `None` |
| `get_chat_history_list` | `user_id, limit=20, offset=0` | dict 列表 | `[]` |
| `get_chat_history_by_id` | `user_id, history_id` | 含 id 的 dict | `None` |
| `delete_chat_history` | `user_id, history_id` | `True` | `False` |
| `update_chat_history_title` | `user_id, history_id, new_title` | `True` | `False` |
| `toggle_pin_chat_history` | `user_id, history_id` | 翻转后的布尔值 | `None` |
| `append_chat_history_result` | `user_id, history_id, result` | `True` | `False` |

图片历史那一套函数命名和契约完全一样，只是集合名不同。判断 `toggle_pin` 是否失败必须用 `is None`,因为 `False` 是合法的成功结果。

Claude 和 Gemini 的额度计数器函数（`get_claude_free_tier_usage`/`increment_claude_free_tier_usage` 等）没有归属校验的概念，因为 `user_id` 直接来自 session,不需要反查文档。

## 8. 对外接口

聊天类：`GET /api/providers`，`POST /api/compare`，`POST /api/test-single`，`GET /health`。

Claude（需要登录）：`POST /api/claude-chat`，请求体是 `{prompt, model, history_id}`，可选带 `X-User-Claude-Key` 请求头。

Gemini（需要登录）：`POST /api/gemini-image`，请求体是 `{prompt, model, history_id}`，可选带 `X-User-Gemini-Key` 请求头。

额度查询（需要登录）：`GET /api/quota-status`，返回 `{claude: {used, limit}, gemini: {used, limit}}`。

文生图类：`GET /api/image-providers`，`POST /api/generate-images`，`GET /media/<filename>`。

页面路由：`GET /`，`GET /home`，`GET /history/<id>`，`GET /image-history/<id>`（仅登录，游客/匿名重定向），`GET /apikey-config`（无登录守卫）。

认证：`/login`、`/register`、`/logout`、`/profile`。游客：`POST /api/auth/guest`。

对话历史（需要登录）：`GET /api/history`，`PATCH /api/history/<id>/title`，`DELETE /api/history/<id>`，`POST /api/history/<id>/toggle-pin`。图片历史路由结构一样，前缀换成 `/api/image-history`。

### 第三方集成

g4f 用来无凭证调用免费渠道。Firebase Admin SDK 本地用密钥文件，GAE 用 ADC。Anthropic 官方 API 是项目第一个需要真实付费 Key 的集成，接入了 `claude-sonnet-5` 和 `claude-haiku-4-5` 两个模型。Google Gemini 官方 API 是第二个需要付费 Key 的集成，接入了三档 Nano Banana 模型。g4f 免 Key 路径下的 Gemini 生图依然不可用，跟官方付费 API 这条路径是两回事，不要混淆。

## 9. 已知风险和限制

超时数值必须保持同步：互评阶段的内部超时和外层 `future.result` 超时要一起调，图片生成的 advisory 和 outer 也要通过公式联动，不要手动改其中一个。

`generated_media/` 目录下的文件不区分用户，会一直堆积，不会自动清理。这是为了保证历史图片能永远看到而接受的代价。真正的长期方案是换成 Cloud Storage 之类的共享存储，加上用户配额限制，但现在还没做。

生产环境（GAE 多实例）下本地磁盘是每个实例独立的。如果图片写在实例 A，后续请求分配到实例 B 就会 404。这是本地磁盘存储架构本身的限制，跟清不清理无关，要修复得换共享存储。

Claude 和 Gemini 的免费额度都是按注册账号计数的，只要一直注册新账号就能无限刷。现在没有任何 IP 限流或者验证码防护。这个是已知的、还没解决的问题，待评估的方向有三个：IP 级限流（需要先确认 GAE 部署下能不能拿到可信的客户端 IP）、图形验证码、邮箱验证。

Gemini 的错误分类只用一种真实场景（零配额）验证过，配额充足的成功路径和无效 Key 的 403 场景还没有真实账户验证过。

前端交互层（乐观更新、动画、布局）没有自动化测试，项目里没有引入任何前端测试框架，靠手动测试验证。如果以后要加前端测试框架，应该把已经验证过的场景补成正式用例。

ChatGPT 的 API Key 输入框目前只是占位符,没有接入任何后端逻辑。Claude 和 Gemini 都已经接入了真实的存储和调用链路。

Claude 和 Gemini 现在都只能往已有的历史记录里追加结果，不能自己创建新记录，也没有参与各自领域的互评或者多 provider 并发对比。如果以后要支持这些功能，需要重新设计，不应该直接复用 g4f 专属的调度逻辑。

## 10. 扩展和修改指南

### 安全区：新增文本 provider

跑一遍探测脚本确认可用之后，加进 `G4F_PROVIDERS` 列表和 `PROVIDER_MODELS_MAP`。可选：给它加隐形风格提示词和互评裁判提示词。前端联动是全自动的，不用改 HTML 或 JS。

### 安全区：新增图片 provider

流程一样，加进 `IMAGE_PROVIDERS`/`IMAGE_PROVIDER_MODELS_MAP`。不要让图片 provider 跟文本 provider 共用映射表或者名字空间。

### 安全区：新增页面

如果这个页面可能是重定向目标，必须加上 Flash 消息显示代码块，不然消息会在 session 里堆积。

### 安全区：新增 Claude/Gemini 模型

先确认官方 model ID 有没有变化，然后在 `CLAUDE_MODELS`/`GEMINI_IMAGE_MODELS` 里加一条映射，前端下拉框加个选项。不需要改额度或者权限逻辑，这两个函数对模型是透明的。

### 安全区：新增前沿 provider（比如 ChatGPT）

模板里 `#frontierProviderSelection`/`#frontierImageProviderSelection` 就是为这个预留的位置。接入时参照 Claude 或 Gemini 的既有模式：后端建一套独立的调用函数、模型映射、额度常量，走独立路由，复用认证守卫。前端加一张新卡片，必须用独立 class。如果有多个模型可选，加一个独立的下拉框，不要复用已有的。`apikey-config.html` 里对应输入框接入独立的 `localStorage` key。不要让新 provider 落进 g4f 的名字空间或者参与 g4f 的并发调度。

### 危险区：不要碰的逻辑

不要改文本 7 个字段或者互评 4 个字段的契约，也不要改图片 8 个字段的契约。不要让文本和图片 provider 共用映射表或者调度路径。不要给图片 provider 勾选框复用文本表单同名的 class。

不要把图片历史混进对话历史集合，也不要让 `generate_images()` 调用 `save_chat_history()`。不要让游客或者匿名用户使用图片版 Recents 或者图片历史详情页。

不要给图片下载功能加服务端代理接口,这会引入 SSRF 风险，下载必须在浏览器端完成。`/media/<filename>` 这个路由本身没问题,因为它只读取本地已经下载好的文件，不要给它加"文件不存在就按 URL 参数回源下载"的逻辑。

不要重新引入任何形式的 `get_media_dir()` 自动清理机制,无论是按时间的惰性清理还是无差别清空整个目录。本地磁盘持续增长是刻意接受的代价。

不要把互评和文生图的超时改成不同步的数值。不要移除根路由里 `provider_models_json`/`image_provider_models_json` 的注入。不要颠倒内容审查错误和网络错误的判定顺序，图片那边也不要颠倒 GPU 配额错误和网络错误的判定顺序，也不要给 GPU 配额错误加重试。

不要把图片超时的计算公式从"两倍 advisory 加缓冲"改回"一倍 advisory 加固定缓冲"，固定缓冲不够覆盖重试后第二次尝试也跑到接近满时长的情况。不要给单个 provider 的超时覆盖表加单独的 outer 键，outer 必须始终由公式从 advisory 推导。

不要在 session 里同时设置 `user_id` 和 `is_guest`。不要跳过 `FIREBASE_AVAILABLE` 检查直接调用 CRUD 函数。不要改 `GET /home` 的行为,不能清除 `user_id`。不要移除历史 CRUD 函数内部的归属校验。不要让游客路径调用任何历史 CRUD 函数。不要用 `if not new_pinned` 判断置顶操作是否失败,必须用 `is None`。

不要让 `get_chat_history_list` 恢复成 Firestore 端的复合排序查询,排序必须留在 Python 层。不要把置顶排序字段从 `pinned_at` 改回 `created_at`。不要恢复"点击历史条目就地渲染进可编辑表单"这种已经废弃的模式,必须整页导航到只读页面。不要把游客历史存储介质从 `sessionStorage` 换成 `localStorage`。

不要移除 `.left-sidebar` 的 `sticky` 定位或者 CSS `calc()` 定高。不要恢复原生滚动条。不要给历史详情页加回任何"再次提交"的表单入口。

不要让游客或者匿名用户能调用 `/api/claude-chat` 或 `/api/gemini-image`,这两个功能对他们必须是完全不可用,没有任何降级体验。不要给 Claude/Gemini 卡片复用其他 provider 的 class。不要把前沿 provider 选择区和免费 provider 选择区合并回同一个容器。不要让 Claude/Gemini 参与 g4f 的并发调度或者互评。

不要把额度检查顺序改成"先调用官方 API 再检查额度",必须先拦截超限请求,不能浪费一次真实的 API 调用。不要让带了自己 Key 的请求还去检查或者递增免费额度。不要在后端持久化用户的个人 Key。不要把余额耗尽或者配额耗尽算作用户的免费额度消耗。

不要让 `claude_chat()`/`gemini_image_chat()` 自己调用 `save_chat_history()`/`save_image_history()` 创建新记录,它们只能追加进已有记录。不要让追加函数跳过归属校验，也不要让它们变成能接受客户端直接提交任意内容的接口。不要忘记在余额耗尽或者配额耗尽的分支追加历史之前,把内部错误码换成对外展示的友好文案。

不要在任何用户可见的地方重新引入中文,包括页面文案、flash 消息、JSON 错误体里的消息字段、发给 LLM 的 prompt 文本。这条规则不管代码注释和这份文档本身。

不要让 Trial Quota 徽章对游客或者匿名用户渲染。不要让两个额度常量互相引用同一个值,它们的语义和字段是独立的,必须能分别调整。不要让前端本地猜测这次调用有没有消耗额度,必须靠后端的真实查询接口。不要给额度查询接口换成允许游客的守卫。

## 11. 构建、运行、测试命令

### 环境搭建

```bash
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

### 本地运行前置条件

需要项目根目录下有 `firebase-key.json`，`.env` 里要有固定的 `SECRET_KEY`。`ANTHROPIC_API_KEY` 和 `GEMINI_API_KEY` 是可选的，不设置也能正常启动，只是对应功能调用时会失败。

### 运行

```bash
python main.py                    # 默认端口 8080
PORT=5000 python main.py
gunicorn -b :8080 main:app        # 模拟 GAE
```

修改 `templates/*.html` 之后必须重启开发服务器，因为模板自动重载没有开启，已经运行的进程会一直缓存旧模板，浏览器强刷解决不了。

访问 `http://localhost:8080`，用 `/health` 验证状态。

### 自动化测试

项目用 `unittest`，测试文件都在 `tests/` 目录下。主要分这几类：内部函数的白盒测试（模型降级规则、DTO 完整性、错误分类等），HTTP 接口的黑盒测试（各个路由的请求响应），认证相关的测试，Claude 和 Gemini 集成的专项测试，英文文案政策的回归测试，HTML 结构完整性的回归测试。

跑测试的命令：

```bash
python -m unittest discover -s tests
python -m unittest discover -s tests -v
python -m unittest tests.test_main_whitebox
```

前端交互层（动画、乐观更新、滚动指示器）不在 unittest 覆盖范围内,项目没有前端测试框架,这类验证靠手动测试。

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

Claude 和 Gemini 的接口需要登录态 session,示例：

```bash
curl -X POST http://localhost:8080/api/claude-chat -H "Content-Type: application/json" \
  -H "Cookie: session=<登录后的 session cookie>" \
  -d '{"prompt": "What is 2+2?", "model": "claude-sonnet-5"}'

curl -X POST http://localhost:8080/api/gemini-image -H "Content-Type: application/json" \
  -H "Cookie: session=<登录后的 session cookie>" \
  -d '{"prompt": "A single red apple", "model": "nano-banana-pro"}'
```

### Provider 可用性探测脚本

只有在 g4f 库升级之后,怀疑现有结论过期时才需要重跑：

```bash
cd availability_g4f
python find_providers_models.py
python test_providers.py
python find_image_providers.py
python test_image_providers.py
```

### 依赖管理

`requirements.txt` 用 `pip freeze` 格式锁死版本，唯一例外是 `gunicorn`。更新依赖用 `pip install <package>` 再 `pip freeze > requirements.txt`，不要手动改版本号。

### 部署到 GAE

```bash
gcloud app deploy app.yaml
gcloud app logs tail -s default
```

`entrypoint` 用 `gunicorn -b :$PORT main:app`，runtime 是 python312，自动缩放 1 到 10 个实例。`SECRET_KEY` 必须在 `app.yaml` 里设置。`firebase-key.json` 不部署，GAE 用 ADC。`ANTHROPIC_API_KEY` 和 `GEMINI_API_KEY` 也应该在 `app.yaml` 里设置，但现在还没配上，部署前需要手动补,而且真实 Key 不应该明文提交到仓库。

## 12. 代码规范

### Python

全局常量用大写加下划线，函数和变量用小写加下划线，路由函数名要跟路径语义对齐。

日志用模块级的 `logger`，不要用 `print`。正常流程节点用 INFO 级别，报错必须带上 `exc_info=True`。日志里长字符串要截断,比如只打印 prompt 的前 50 个字符。

LLM 和文生图路由外层要有 `try/except`，返回统一格式的 JSON 错误体。认证路由外层的 `try/except` 通过 `flash()` 反馈。结果字典的字段集合不能随便增删。

### JavaScript 和前端

用原生 JS，没有框架也没有构建工具。后端数据通过 Jinja2 的 `tojson` 过滤器注入页面，页面加载时解析。发请求必须先检查 `response.ok` 再解析 JSON,非 2xx 状态码先尝试读 `error` 字段，读不到就用状态码兜底。

### 提交规范

中英文都可以，保持原子提交,一次提交只做一件事。

## 13. 关键路径速查

聊天核心链路：前端提交表单，调 `/api/compare`，并发测试各 provider，收集结果，满足条件就并发互评，排序，登录用户存历史，返回 JSON。

文生图核心链路：调 `/api/generate-images`，并发测试各图片 provider（按 provider 各自的超时预算），排序，登录用户存历史，返回 JSON,没有互评。图片会同步下载到本地,通过 `/media/<filename>` 路由提供访问，这些文件现在永久保留不清理。

Claude 核心链路：Claude 卡片被勾选时,先完成正常的 `/api/compare` 请求拿到 `history_id`，再额外发一次 `/api/claude-chat`（带上这个 `history_id`），经过认证守卫和额度检查，调用官方 API，处理余额耗尽的情况，成功且没用自己 Key 就递增额度计数器，`history_id` 非空就追加进历史记录，最后前端把结果合并进同一份列表渲染。全程不经过并发调度器,也不会自己创建新的历史记录。

Gemini 核心链路：跟 Claude 完全对称，只是把聊天场景换成图片场景。

认证核心链路：登录表单提交，走 auth 蓝图，查 Firestore，写 session，重定向到首页。

关键不变量列表：结果排序永远是成功优先、耗时短优先。`user_id` 和 `is_guest` 永远不同时存在。重定向目标页面必须有 Flash 显示区。文本 7 字段和图片 8 字段的契约不能破坏。错误文案判定顺序不能颠倒。互评触发条件是测试数和成功数都大于等于 2。文本 provider 和图片 provider 名字空间严格隔离。历史列表查询只做单字段查询加 Python 层排序分页。置顶判空必须用 `is None`。游客数据不持久化,对话历史镜像进 sessionStorage,图片历史完全不提供。页面缩放靠 `--page-zoom` 变量,涉及视口高度的 CSS 要用 `calc(100vh/var(--page-zoom))`。`history` 和 `image_history` 是两个独立集合,不要合并。Claude 和 Gemini 都是独立于 g4f 的调用链路,不能自己创建新的历史记录,只能追加进已有记录。所有用户可见文案必须是英文。导航栏额度徽章只对登录用户渲染,数字必须来自后端真实查询,不能前端猜测。

核心文件：后端逻辑都在 `main.py`，认证逻辑在 `auth/routes.py` 和 `auth/db.py`，前端模板在 `templates/` 目录下,主功能页是 `index.html`。

## 14. 更新记录

`[Stop Generating] 更新原因：需要在开发者账户余额/配额耗尽但用户试用额度仍有剩余时给出正确文案，并支持中途取消生成。调整内容：1. claude_chat()/gemini_image_chat() 里 SERVER_CREDITS_EXHAUSTED/SERVER_QUOTA_EXHAUSTED 的文案按 using_own_key 分两支：自带 Key 耗尽提示检查自己的账户，开发者 Key 耗尽（试用额度仍有剩余）提示联系 developer。2. 新增前端 Stop Generating 按钮（Compare/Generate Images 各一个），用 AbortController 中断 /api/compare、/api/generate-images、/api/claude-chat、/api/gemini-image 的请求，不清空 prompt 和勾选。3. 新增 request_id 一次性退款账本（main.py 的 _PENDING_FRONTIER_REFUNDS + /api/claude-chat/refund、/api/gemini-image/refund）和 auth/db.py 的 decrement_claude_free_tier_usage()/decrement_gemini_free_tier_usage()，用于中断时把已经递增的免费额度退回，账本只认真实发生过的递增,不能被反复调用刷额度。`
