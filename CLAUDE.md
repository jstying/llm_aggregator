# claude.md

## 1. 🧠 SYSTEM OVERVIEW (Cognitive Summary)

这是一个基于 Flask 框架开发的轻量级大语言模型（LLM）聚合与性能对比 Web 应用程序。该系统的核心目的是允许用户输入提示词，同时或单独调用不同的 g4f Provider（大模型接口通道），并实时对比它们的响应内容和响应时间。系统属于 Web 应用工具，采用了典型的后端路由结合前端单页异步交互（AJAX/Fetch）的架构样式。

## 2. 🧬 ARCHITECTURE MAP (MOST IMPORTANT SECTION)

系统由两个核心子系统构成：Flask 后端服务与 HTML5/JavaScript 前端交互界面。

### 后端服务（Flask）

- **路由层**：负责提供页面渲染路由（`/`）以及三个核心 API 接口（`/api/providers`、`/api/compare`、`/api/test-single`）。
    
- **多线程并发调度器**：利用 `ThreadPoolExecutor` 并发调用多个 Provider 的请求，防止单点阻塞。
    
- **g4f 适配层**：封装对 `g4f.ChatCompletion` 的底层调用，处理模型匹配逻辑和异常捕获。
    

### 前端界面（Jinja2 + JS）

- **状态与视图同步**：接收后端注入的结构化数据，动态渲染可用的 Provider 列表和对应模型。
    
- **异步交互控制器**：通过 Fetch API 与后端进行非阻塞通信，并动态更新 DOM（如渲染统计图表和响应卡片）。
    

```
[前端用户界面 (HTML/JS)] --(Fetch API / JSON)--> [Flask 后端路由]
                                                        |
                                             [ThreadPoolExecutor 线程池]
                                                        |
                                             [g4f Provider 适配层]
                                                        |
                                             (外部 LLM APIs / Providers)
```

### 耦合风险与设计缺陷

- **硬编码映射**：`PROVIDER_MODELS_MAP` 在代码中属于硬编码。若 g4f 库更新了底层支持的模型，后端代码必须手动同步修改。
    
- **全局状态依赖**：系统依赖 `G4F_AVAILABLE` 全局布尔标志。若初始化失败，整个核心业务直接进入降级状态。
    

## 3. 🧰 TECHNICAL STACK (EVIDENCE-BASED ONLY)

- **编程语言**：Python, JavaScript
    
- **后端框架**：Flask
    
- **并发库**：`concurrent.futures.ThreadPoolExecutor`
    
- **核心依赖库**：g4f (GPT4Free)
    
- **前端技术**：HTML5, CSS3 (Linear Gradients, Grid 布局, Flex 布局), Vanilla JavaScript
    
- **模板引擎**：Jinja2
    
- **运行环境配置**：通过 `os.environ.get('PORT')` 读取环境变量
    
- **部署平台**：Google App Engine (GAE Standard / Flexible compatible)

## 4. 📁 CODEBASE STRUCTURE (WITH INTENT)

```
llm_aggregator/
├── main.py                          # Flask 后端入口
├── templates/
│   └── index.html                   # 前端单页模板
├── tests/                           # 自动化测试目录（基于 unittest，不部署）
│   ├── test_whitebox.py             # 白盒单元测试（直接测试内部函数）
│   ├── test_blackbox.py             # 黑盒集成测试（通过 HTTP 接口驱动）
│   └── test_graybox.py             # 灰盒测试（感知全局状态与线程池行为）
├── availability_g4f/                # Provider 可用性探测工具（开发辅助，不部署）
│   ├── find_providers_models.py     # 扫描 g4f 所有 working Provider 及其模型列表
│   └── test_providers.py            # 手动逐一测试候选 Provider 是否真正可用
├── available_providers_models.txt   # find_providers_models.py 的输出结果
├── provider_test_results.txt        # test_providers.py 第一轮测试结果
├── provider_test_results_v2.txt     # test_providers.py 第二轮测试结果
├── requirements.txt                 # 所有依赖的锁定版本（pip freeze 风格）
├── app.yaml                         # Google App Engine 部署配置
└── env/                             # 本地 Python 虚拟环境（不提交 git）
```

### `main.py`

- **role**: 系统的核心后端驱动程序，负责初始化配置、定义路由、管理并发请求以及处理外部大模型通信。
    
- **key logic**:
    
    - `determine_actual_model(provider_name, requested_model)`: 纯函数辅助，封装规则 A/B/C 的模型决策逻辑，返回最终使用的模型名称字符串。
        
    - `init_result_object(provider_name, model)`: 纯函数辅助，统一初始化标准 Result 字典，确保正常流程与异常兜底的 Key 集合严格一致。
        
    - `test_g4f_provider()`: 核心测试函数，调用上述两个辅助函数完成模型决策与结果初始化，并计算响应耗时。
        
    - `compare_providers()`: 通过线程池并发执行测试，并根据成功状态和耗时对结果进行排序。
        
- **depends_on**: `flask`, `g4f`, `concurrent.futures`, `time`, `logging`
    
- **affects**: `index.html` (通过 Jinja2 注入变量), 所有的前端 API 请求。
    

### `templates/index.html`

- **role**: 用户交互界面，负责动态展示后端支持的通道、过滤可选模型并渲染对比结果。
    
- **key logic**:
    
    - `updateModelDropdown()`: 前端交互核心，根据用户勾选的 Provider 动态计算并过滤出并集模型列表。
        
    - `displayResults()`: 解析后端返回的 JSON 数据，计算平均耗时和成功率，动态生成响应卡片。
        
- **depends_on**: 后端路由 `/` 传来的 `providers` 和 `provider_models_json`。
    
- **affects**: 用户视图展示及向 `/api/compare` 发送的请求负载。

### `availability_g4f/`

- **role**: 开发期专用的 Provider 探测工具目录，**不部署到 GAE**。用于在 g4f 库升级后重新筛选可用通道。
    
- `find_providers_models.py`：遍历 `g4f.Provider.__providers__`，筛出 `working=True` 的通道并记录其支持模型，结果写入 `available_providers_models.txt`。
    
- `test_providers.py`：对候选通道发送真实的 `"What is 2+2?"` 请求验证可用性，每次调用间隔 5 秒避免限速，结果写入 `provider_test_results_v2.txt`。
    

### `tests/`

- **role**: 自动化测试目录，包含基于 Python 内置 `unittest` 框架的三类测试文件，**不部署到 GAE**。
    
- `test_whitebox.py`：直接导入并测试 `main.py` 的三个核心内部函数（`determine_actual_model`、`init_result_object`、`test_g4f_provider`）。三个测试类分别覆盖：模型降级规则 A/B/C 的全部边界条件（10 个用例），结果字典的 key 完整性与默认值（10 个用例），以及 `test_g4f_provider` 的成功路径、异常路径和模型降级逻辑（8 个用例）。所有用例通过 `unittest.mock.patch` 替换 `PROVIDER_MODELS_MAP` 和 `g4f` 模块，不启动 Flask 服务器，不发出任何真实网络请求。
    
- `test_blackbox.py`：通过 Flask 内置的 `test_client()` 以 HTTP 协议驱动四个 API 端点（`/health`、`/api/providers`、`/api/test-single`、`/api/compare`），不直接调用任何内部函数。`TestTestSingleEndpoint` 和 `TestCompareEndpoint` 两个类使用 `@patch` 屏蔽真实 g4f 网络调用。若运行环境中 `G4F_AVAILABLE` 为 `False`，这两个类整体跳过，其余测试类不受影响。
    
- `test_graybox.py`：结合接口输入输出与后端内部感知，覆盖四个测试类。`TestSortOrderInvariant` 通过 Mock 控制 `test_g4f_provider` 的返回值，验证 `/api/compare` 的二次排序规则。`TestThreadTimeoutFallback` 模拟线程抛出 `concurrent.futures.TimeoutError`，验证 `except` 块通过 `init_result_object` 组装 fallback 数据、接口不崩溃。`TestMaxWorkersConstraint` 包装真实 `ThreadPoolExecutor` 以捕获构造参数，验证双重上限约束（全局不超过 5，且不超过 provider 数量）。`TestGlobalDegradationState` 直接修改 `main.G4F_AVAILABLE = False`，验证各路由的降级响应（503 或空列表），`tearDown` 恢复原始全局状态。
    

## 5. 🔄 EXECUTION & DATA FLOW (CRITICAL)

### 1. 初始化阶段

- Flask 应用启动，尝试导入 `g4f`。
    
- 若成功，注册 `Yqcloud` 和 `OperaAria` 两个通道，并配置其对应的模型映射表。
    

### 2. 页面加载与渲染

- 用户访问根路由 `/`。
    
- 后端将 `G4F_PROVIDERS` 转换为包含默认模型的结构化列表，连同映射字典一同注入 `index.html`。
    
- 前端 JavaScript 解析 `provider_models_json`，并初始化模型下拉选择框。
    

### 3. 多通道对比触发

- 用户输入 Prompt，勾选目标 Provider，点击对比按钮。
    
- 前端阻断表单默认提交，收集数据并向 `/api/compare` 发送 POST 请求。
    
- 后端接收到请求，验证 Prompt 合法性，根据名称匹配出对应的 Provider 实例。
    
- 后端启动 `ThreadPoolExecutor` 线程池（最大线程数限制在 3 到 5 之间）。
    

### 4. 数据转换与返回

- 各子线程并发执行 `test_g4f_provider`。
    
- `test_g4f_provider` 内部校验用户指定的模型。若不在支持列表内，则强制降级为该 Provider 的默认模型。
    
- 调用 `g4f.ChatCompletion.create` 并设置 20 秒超时。
    
- 利用 `time.time()` 计算精确到小数点后两位的 `response_time`。
    
- 主线程收集所有线程结果，按 `(失败状态, 耗时升序)` 进行二次排序。
    
- 返回 JSON 给前端，前端重新计算 `Success Rate` 和 `Avg Response Time` 并刷新页面。
    

## 6. 🧠 CORE LOGIC / DOMAIN RULES

### 模型自适应降级规则

当用户发起请求时，系统遵循以下决策树来决定最终传递给 g4f 的模型名称。该逻辑已提取为纯函数 `determine_actual_model(provider_name, requested_model)`，在 `test_g4f_provider` 和 `compare_providers` 的异常兜底块中共用：

- **规则 A**：若用户指定了特定模型，且该模型包含在当前 Provider 的支持映射表内，则使用该指定模型。
    
- **规则 B**：若用户指定的模型不被该 Provider 支持（或者用户未指定模型），则自动选取该 Provider 模型列表中的第一个作为默认模型。
    
- **规则 C**：若该 Provider 没有配置任何模型列表，则强制兜底降级为 `"gpt-3.5-turbo"`。
    

### 结果排序权重规则

后端返回的对比结果不是无序的，而是经过了多维度排序：

- **第一优先级**：`success` 状态。成功的请求必须排在失败的请求前面。
    
- **第二优先级**：`response_time`。在同样成功或同样失败的情况下，响应耗时越短的 Provider 排在越前面。
    

## 7. 🧾 DATA MODELS / STATE DESIGN

### 核心数据传输对象 (DTO)

系统未定义持久化数据库模型，所有状态均在内存中流转，核心结构为 `Result` 字典：

Python

```
{
    'provider': str,       # Provider 类的名称
    'success': bool,       # 是否请求成功
    'response': str,       # 模型返回的文本内容 (成功时)
    'error': str,          # 异常堆栈或错误信息简述 (失败时)
    'response_time': float,# 响应耗时，单位秒，保留两位小数
    'model': str,          # 实际使用的模型名称
    'type': 'g4f'          # 固定类型标识
}
```

### 全局状态

- `G4F_AVAILABLE`：全局单例布尔值，用于标识当前环境中 g4f 库是否可用，决定了系统是正常工作还是全面降级。
    

## 8. 🔌 EXTERNAL INTERFACES

### 后端 API 接口规范

- `GET /api/providers`：返回所有可用 Provider 的元数据列表（包含名称、支持的模型、默认模型、类型和状态）。
    
- `POST /api/compare`：接收 JSON 载荷（`prompt`、`providers` 数组、`model`、`max_workers`），返回并发测试后的聚合排序结果。
    
- `POST /api/test-single`：接收 JSON 载荷（`prompt`、`provider`、`model`），单独测试某一个通道并返回标准结果对象。
    
- `GET /health`：健康检查接口，返回系统状态、g4f 可用性、Provider 列表及当前服务器时间戳。
    

### 第三方集成

- **g4f 库**：通过模拟浏览器或逆向接口，无凭证调用各大免费 AI 渠道（如 `Yqcloud`、`OperaAria`）。
    

## 9. ⚠️ SYSTEM RISKS / CODE QUALITY AUDIT

- ~~**超时机制不一致**~~（**已修复**）：`future.result()` 的外层等待超时已从 `25` 秒调整为 `21` 秒，与内部 `g4f.ChatCompletion.create(timeout=20)` 保持一致，仅预留 1 秒线程调度缓冲。
    
- ~~**前端异常捕获漏洞**~~（**已修复**）：`index.html` 的 Fetch 处理逻辑已在调用 `response.json()` 前检查 `response.ok`。非 2xx 响应时先尝试解析 JSON 中的 `error` 字段作为提示，若 body 非 JSON 则回退到 `Server error: 状态码` 提示，不再导致前端崩溃。

- ~~**异常回滚伪造**~~（**已修复**）：`compare_providers` 的 `except` 块已改为复用 `determine_actual_model()` 和 `init_result_object()` 两个辅助函数，模型决策规则与正常流程完全一致，Key 集合严格统一。
    
- **线程池潜在安全隐患**：`max_workers` 的计算逻辑为 `min(data.get('max_workers', 3), 5)`。在实际提交给线程池时，代码使用的是 `min(max_workers, len(providers_to_test))`。然而，由于可测试的 Provider 列表（`G4F_PROVIDERS`）目前在代码中总共只有 2 个（`Yqcloud`、`OperaAria`），前端即使传 `max_workers: 5`，实际工作的最大线程数也永远不会超过 2。
    

## 10. 🧭 EXTENSION & MODIFICATION GUIDE (VERY IMPORTANT)

### 🟢 安全区（Safe Zones）：如何安全地添加新通道

若需要引入新的 g4f 支持的 Provider，只需修改 `main.py` 的初始化部分：

1. 确保新通道已被 g4f 原生支持（例如 `g4f.Provider.Bing`）。
    
2. 将其追加进 `G4F_PROVIDERS` 列表中。
    
3. 在 `PROVIDER_MODELS_MAP` 中添加该 Provider 的名称作为 Key，并配置其支持的模型数组（第一个作为默认模型）。
    
4. 由于前端具备完全动态的联动机制，无需修改任何 HTML/JS 代码，前端会自动将其渲染出来并支持过滤。
    

### 🔴 危险区（Danger Zones）：严禁触碰的逻辑

- 不要随意修改 `test_g4f_provider` 的返回值字典结构。前端的 `displayResults()` 严格依赖该字典的 Key（如 `result.provider`、`result.success`、`result.response_time`）。一旦修改，前端报表和统计卡片将大面积瘫痪。
    
- 不要移除根路由 `/` 中的 `provider_models_json` 注入。它是前端 JavaScript 建立模型联动过滤机制的唯一数据源。
    

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

启动后访问 `http://localhost:8080` 进入界面，`http://localhost:8080/health` 验证服务状态。

### 自动化测试框架

项目使用 Python 内置的 `unittest` 框架，测试文件存放于 `tests/` 目录。测试集覆盖三个层面：

- **白盒测试**（`test_whitebox.py`）：直接测试 `main.py` 的三个核心内部函数，验证模型降级规则 A/B/C 的全部边界条件、结果字典的 key 完整性，以及 `test_g4f_provider` 的成功路径与异常路径。
    
- **黑盒测试**（`test_blackbox.py`）：通过 Flask `test_client()` 以 HTTP 协议测试所有对外 API 端点，验证响应结构、状态码、字段类型及结果排序规则。
    
- **灰盒测试**（`test_graybox.py`）：同时感知后端全局状态与线程池行为。通过 Mock 控制 `test_g4f_provider` 返回值验证排序契约，模拟线程 `TimeoutError` 验证 fallback 组装路径，包装 `ThreadPoolExecutor` 构造函数验证 `max_workers` 双重上限，以及直接修改 `main.G4F_AVAILABLE` 验证全系统降级响应。

```bash
# 发现并运行 tests/ 目录下的全部测试
python -m unittest discover -s tests

# 带详细输出运行（显示每条用例名称与通过状态）
python -m unittest discover -s tests -v

# 单独运行某一测试文件
python -m unittest tests.test_whitebox
python -m unittest tests.test_blackbox
python -m unittest tests.test_graybox
```

### 快速冒烟测试（手动 curl）

服务启动后，可通过以下命令快速验证各端点的可用性：

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

- `requirements.txt` 采用 **完全锁版本**策略（`pip freeze` 输出格式），所有间接依赖均固定。
- 更新依赖时：在虚拟环境中 `pip install <package>`，然后 `pip freeze > requirements.txt`。
- **不要手动编辑 `requirements.txt`** 中的版本号，避免依赖冲突。

### 部署到 Google App Engine

```bash
# 需已安装并配置 Google Cloud SDK (gcloud)
gcloud app deploy app.yaml

# 查看实时日志
gcloud app logs tail -s default
```

- GAE 使用 `app.yaml` 中的 `entrypoint: gunicorn -b :$PORT main:app` 启动服务。
- Runtime 为 `python312`，自动缩放 1 到 10 个实例，CPU 目标利用率 60%。

---

## 12. ✏️ CODE STYLE & CONVENTIONS

### Python 代码规范

- **命名约定**：
  - 全局常量：`UPPER_SNAKE_CASE`（如 `G4F_AVAILABLE`、`PROVIDER_MODELS_MAP`）
  - 函数/变量：`lower_snake_case`（如 `test_g4f_provider`、`provider_name`）
  - Flask 路由函数名与路径语义对齐（如 `compare_providers` 对应 `/api/compare`）

- **日志规范**：
  - 使用模块级 `logger = logging.getLogger(__name__)`，**不使用 `print`**。
  - `INFO` 记录正常流程节点（如请求开始、Provider 完成）。
  - `ERROR` 用于异常，**必须带 `exc_info=True`** 以打印完整堆栈（参见 `compare_providers` 中的用法）。
  - 日志消息中截断长字符串：`prompt[:50]`，避免日志膨胀。

- **错误处理模式**：
  - 路由函数顶层用 `try/except Exception` 兜底，返回标准 JSON 错误体 `{'error': '...'}` 和对应 HTTP 状态码。
  - `test_g4f_provider` 内部用 `try/except/finally`，在 `finally` 中计算 `response_time`，确保耗时字段始终有值。
  - g4f 初始化用**两层 except**（`ImportError` 和 `Exception`）分别处理库缺失和初始化异常。

- **结果字典结构不变原则**：`test_g4f_provider` 返回的字典 key 集合（`provider`, `success`, `response`, `error`, `response_time`, `model`, `type`）为前后端契约，**严禁增删 key**。

### JavaScript / 前端规范

- 使用 **Vanilla JS**，不引入任何前端框架或构建工具。
- 前端通过 Jinja2 变量 `{{ provider_models_json | tojson }}` 接收后端数据，在页面初始化时解析为 JS 对象。
- Fetch 请求必须先检查 `response.ok` 再调用 `response.json()`。非 2xx 响应时先尝试解析 JSON `error` 字段，解析失败则回退到 `Server error: 状态码`，防止 body 为非 JSON 时前端崩溃。

### 提交规范

- 提交信息用**中文或英文均可**，项目历史两者混用。
- 保持原子提交：一次提交只做一件事（参见 git log 中的 `add model selection`、`add cn comments` 等）。

## 13. 🧠 MEMORY ANCHORS (FOR CLAUDE CODE)

- **核心调用链路**：`index.html (Form Submit)` → `POST /api/compare` → `ThreadPoolExecutor` → `test_g4f_provider()` → `g4f.ChatCompletion.create()`。
    
- **关键不变量**：对比结果的排序始终是“成功在前，耗时短在前”。
    
- **核心文件**：后端入口为 `main.py`，前端单页模板为 `templates/index.html`。
    
- **自适应策略**：指定模型不匹配时，自动降级为映射表中的第一个模型，最终兜底为 `"gpt-3.5-turbo"`。