# 先加载环境变量
from dotenv import load_dotenv
load_dotenv()

# Flask Web框架相关模块
from flask import Flask, request, jsonify, render_template, redirect, session, url_for, flash, send_from_directory

# 计时模块（统计模型响应时间）
import time

# 日志模块
import logging

# 读取环境变量
import os
import secrets
import re
import json
import random

# 用于并发执行多个Provider请求
from concurrent.futures import ThreadPoolExecutor

# 配置日志级别
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 创建Flask应用
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

from auth import auth_bp  # noqa: E402
app.register_blueprint(auth_bp)

from auth.db import (  # noqa: E402
    save_chat_history,
    get_chat_history_list,
    get_chat_history_by_id,
    delete_chat_history,
    update_chat_history_title,
    toggle_pin_chat_history,
)


# =========================
# 初始化 g4f Provider
# =========================
try:
    import g4f
    from g4f.client import Client as G4FImageClient
    from g4f.image.copy_images import get_media_dir

    G4F_AVAILABLE = True
    logger.info("g4f imported successfully")

    # 当前支持的 Provider 列表
    G4F_PROVIDERS = [
        g4f.Provider.Yqcloud,
        g4f.Provider.OperaAria,
        g4f.Provider.PollinationsAI,
    ]

    # 配置映射表：一个 Provider 对应一个模型列表
    # 列表中的第一个模型会被当作该 Provider 的默认模型
    PROVIDER_MODELS_MAP = {
        'Yqcloud': ['gpt-3.5-turbo', 'gpt-4'],
        'OperaAria': ['aria'],
        'PollinationsAI': ['openai-fast'],
    }

    # 文生图（text-to-image）Provider 列表：2026-07-03 可用性调研（见
    # availability_g4f/available_free_image_providers.txt）实测确认的 5 个免 Key 组合，
    # 全部通过 g4f.client.Client().images.generate() 调用（与上方文本对话的
    # g4f.ChatCompletion.create() 是完全不同的两套 g4f 接口，不可混用）。
    IMAGE_PROVIDERS = [
        g4f.Provider.PollinationsImage,
        g4f.Provider.BlackForestLabs_Flux1Dev,
        g4f.Provider.AnyProvider,
        g4f.Provider.StabilityAI_SD35Large,
        g4f.Provider.OperaAria,
    ]

    # 文生图 Provider → 模型映射表。'auto' 是 PollinationsImage 的占位显示值，
    # 命中时调用 images.generate() 不传 model 参数（其自身默认走 default_image_model）。
    IMAGE_PROVIDER_MODELS_MAP = {
        'PollinationsImage': ['auto'],
        'BlackForestLabs_Flux1Dev': ['flux-dev'],
        'AnyProvider': ['flux'],
        'StabilityAI_SD35Large': ['sd-3.5-large'],
        'OperaAria': ['aria'],
    }

    # 隐形 Prompt 路由表：(provider_name, model) → 追加到用户 prompt 尾部的 Style Prompt
    # 设计原则：首句必须有"立刻"urgency指令（防超时）；其次凸显各模型的真实个性角色
    # gpt-4        → 严谨分析师：结论-依据-反思三层结构，300字
    # gpt-3.5      → 高效助手：TLDR一句话结论优先，口语化，150字
    # aria         → 实战顾问：跳过铺垫、直接给1-2个可操作动作，200字
    # openai-fast  → 极速速答者：一句结论+一句理由，英文输出，100字内
    ROUTE_PROMPTS_MAP = {
        ('Yqcloud', 'gpt-4'): '\n\n[System: Respond immediately. You are a rigorous analyst. Answer quickly using a three-part structure: "Core conclusion -> Key evidence -> Potential risks or reflection." Keep the entire response under 300 words.]',
        ('Yqcloud', 'gpt-3.5-turbo'): '\n\n[System: Give a TLDR immediately. You are an efficient assistant. State the single most important conclusion in one sentence first, then add up to two key points. Reply in a casual, conversational tone. Keep the entire response under 150 words. No filler.]',
        ('OperaAria', 'aria'): '\n\n[System: Give actionable advice immediately. You are a hands-on consultant. Skip the background and tell the user directly "here are the 1-2 things you can do right now," tailored to the current situation. Keep the entire response under 200 words.]',
        ('PollinationsAI', 'openai-fast'): '\n\n[System: Reply immediately. You are a speed-first assistant. Give ONE sentence answer then ONE sentence reason. English only. Max 100 words. No preamble.]',
    }

    # 互评裁判提示词配置表：model → 裁判专属提示词前缀（要求输出 JSON 格式）
    PEER_REVIEW_PROMPTS_MAP = {
        'gpt-4': 'You are now a blind review judge. Rigorously examine the following anonymous answer and point out any logical gaps, factual errors, or insufficiently supported arguments. Output ONLY this JSON, nothing else: {"score": integer(1-100), "comment": "one sharp sentence critique"}',
        'gpt-3.5-turbo': 'Quickly assess the following anonymous answer for organization and readability. Output ONLY this JSON, nothing else: {"score": integer(1-100), "comment": "one efficiency-focused sentence of editing feedback"}',
        'aria': 'Review the following anonymous answer from a practical standpoint, noting how down-to-earth and actionable it is. Output ONLY this JSON, nothing else: {"score": integer(1-100), "comment": "one blunt, plain-spoken sentence"}',
        'openai-fast': 'You are a blind reviewer. Rate the following answer for clarity and accuracy. Output ONLY this JSON, nothing else: {"score": integer(1-100), "comment": "one sharp sentence critique in English"}',
    }

except ImportError as e:
    G4F_AVAILABLE = False
    G4F_PROVIDERS = []
    PROVIDER_MODELS_MAP = {}
    ROUTE_PROMPTS_MAP = {}
    PEER_REVIEW_PROMPTS_MAP = {}
    IMAGE_PROVIDERS = []
    IMAGE_PROVIDER_MODELS_MAP = {}
    G4FImageClient = None
    get_media_dir = lambda: './generated_media'
    logger.warning(f"g4f not available: {e}")

except Exception as e:
    G4F_AVAILABLE = False
    G4F_PROVIDERS = []
    PROVIDER_MODELS_MAP = {}
    ROUTE_PROMPTS_MAP = {}
    PEER_REVIEW_PROMPTS_MAP = {}
    IMAGE_PROVIDERS = []
    IMAGE_PROVIDER_MODELS_MAP = {}
    G4FImageClient = None
    get_media_dir = lambda: './generated_media'
    logger.warning(f"g4f initialization failed: {e}")


# ==================================================
# 辅助函数：根据映射表和用户请求确定最终使用的模型
# 规则 A：用户指定的模型在支持列表中 → 直接使用
# 规则 B：不支持或未指定 → 降级为该 Provider 的默认模型（列表第一个）
# 规则 C：Provider 无模型配置 → 兜底为 "gpt-3.5-turbo"
# ==================================================
def determine_actual_model(provider_name, requested_model):
    supported_models = PROVIDER_MODELS_MAP.get(provider_name, [])
    if requested_model in supported_models:
        return requested_model
    return supported_models[0] if supported_models else "gpt-3.5-turbo"


# ==================================================
# 辅助函数：初始化标准 Result 字典
# 统一管理 Key 集合，确保正常流程与异常兜底结构严格一致
# ==================================================
def init_result_object(provider_name, model):
    return {
        'provider': provider_name,
        'success': False,
        'response': '',
        'error': '',
        'response_time': 0,
        'model': model,
        'type': 'g4f'
    }


# ==================================================
# 辅助函数：根据文生图映射表和用户请求确定最终使用的图片模型
# 与 determine_actual_model 同构（规则 A/B），但没有规则 C 式的通用兜底模型——
# 文生图 Provider 之间没有一个通用的、总能work的默认模型名，Provider 不在映射表
# 里时直接返回 None，交由调用方决定如何展示（见 init_image_result_object）。
# ==================================================
def determine_actual_image_model(provider_name, requested_model):
    supported_models = IMAGE_PROVIDER_MODELS_MAP.get(provider_name, [])
    if requested_model in supported_models:
        return requested_model
    return supported_models[0] if supported_models else None


# ==================================================
# 辅助函数：初始化标准图片 Result 字典
# 与文本的 7-key 契约（init_result_object）结构类似但字段不同：用 url/b64_json
# 两个字段分别承载 g4f ImagesResponse.data[0] 的两种可能返回形式，而不是单一的
# response 字符串字段，前端据此二选一渲染 <img src="...">。
# ==================================================
def init_image_result_object(provider_name, model):
    return {
        'provider': provider_name,
        'success': False,
        'url': None,
        'b64_json': None,
        'error': '',
        'response_time': 0,
        'model': model,
        'type': 'g4f_image'
    }


# ==================================================
# 辅助函数：解析互评 JSON 响应，提取 score 与 comment
# 容错策略：任何解析失败均返回默认分 80 + 原始文本作为 comment
# ==================================================
def parse_peer_review_json(text):
    try:
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end > start:
            data = json.loads(text[start:end + 1])
            score = data.get('score')
            comment = str(data.get('comment', ''))
            if isinstance(score, (int, float)):
                return max(1, min(100, int(score))), comment
    except Exception:
        pass
    return 80, text.strip()


# 敏感词列表（当前为占位空列表，按需填充关键词即可生效）
SENSITIVE_KEYWORDS = []

# 网络/限流类错误关键词：命中时返回统一的"系统正忙"友好提示，而非原始异常文本
NETWORK_ERROR_KEYWORDS = [
    'timeout', 'timed out', 'connection', 'network', 'remote',
    '502', '504', 'rate limit', 'too many requests', 'unavailable',
    'ssl', 'broken pipe', 'connection reset',
]

# 互评阶段的网络类错误判定额外把 429 / queue-full 计入（重试耗尽后兜底文案用）
PEER_REVIEW_NETWORK_ERROR_KEYWORDS = NETWORK_ERROR_KEYWORDS + ['429', 'queue']

# 内容策略类错误关键词：命中时说明是 Provider 底层供应商（如 Azure OpenAI）自身的
# 内容审查拦截了响应，重试无意义，需与网络类错误区分开单独给出友好提示
CONTENT_POLICY_ERROR_KEYWORDS = [
    'content management policy',
    'content_filter',
    'content filtering polic',
    'response was filtered',
    'responsible ai',
]

# GPU 配额类错误关键词（文生图专属，HuggingFace ZeroGPU Space 后端如
# BlackForestLabs_Flux1Dev / StabilityAI_SD35Large 命中）：命中时说明免费 GPU 配额
# 已被用尽，是该 Provider 自身资源限制，而非网络抖动——与网络类错误区分开单独给出
# 友好提示，否则前端会直接展示原始英文 JSON 报错（见 test_g4f_image_provider）
GPU_QUOTA_ERROR_KEYWORDS = [
    'zerogpu',
    'gpu token limit',
    'gpu quota',
]


# ==================================================
# 辅助函数：检测重复文本并截断，同时过滤敏感内容
# ==================================================
def detect_and_truncate(text):
    for kw in SENSITIVE_KEYWORDS:
        if kw in text:
            return "Content contains sensitive information and has been blocked."

    n = len(text)
    if n < 24:
        return text

    # --- 句级重复检测（以句末标点或换行为切分点）---
    parts = re.split(r'(?<=[。！？.!?\n])', text)
    parts = [p for p in parts if p.strip()]
    for i in range(len(parts) - 2):
        s = parts[i].strip()
        if s and s == parts[i + 1].strip() == parts[i + 2].strip():
            first_pos = text.find(parts[i])
            second_pos = text.find(parts[i + 1], first_pos + len(parts[i]))
            third_pos = text.find(parts[i + 2], second_pos + len(parts[i + 1]))
            if third_pos != -1:
                return text[:third_pos] + '... (truncated automatically due to repeated content)'

    # --- 滑动窗口短串重复检测（窗口 8~50 字符，覆盖短句/词组抽风）---
    for win in range(8, min(51, n // 3 + 1)):
        for i in range(n - win * 3 + 1):
            chunk = text[i:i + win]
            if (text[i + win:i + win * 2] == chunk and
                    text[i + win * 2:i + win * 3] == chunk):
                return text[:i + win * 2] + '... (truncated automatically due to repeated content)'

    return text


# ==================================================
# 测试单个Provider
# 功能：
# 1. 调用指定Provider
# 2. 动态匹配或校验用户传入的模型
# 3. 统计响应时间
# ==================================================
def test_g4f_provider(provider, prompt, requested_model=None):
    provider_name = provider.__name__
    actual_model = determine_actual_model(provider_name, requested_model)

    start_time = time.time()
    result = init_result_object(provider_name, actual_model)

    try:
        style_suffix = ROUTE_PROMPTS_MAP.get((provider_name, actual_model), '')
        routed_prompt = prompt + style_suffix

        # 调用大模型（传入含隐形路由的 prompt，result 中保留原始 prompt 无需修改）
        response = g4f.ChatCompletion.create(
            model=actual_model,
            messages=[
                {
                    "role": "user",
                    "content": routed_prompt
                }
            ],
            provider=provider,
            timeout=20
        )

        result['success'] = True
        result['response'] = detect_and_truncate(str(response))

    except Exception as e:
        err_str = str(e).lower()
        if any(kw in err_str for kw in CONTENT_POLICY_ERROR_KEYWORDS):
            result['error'] = "This provider's content filter blocked the response. Try rephrasing your prompt."
        elif any(kw in err_str for kw in NETWORK_ERROR_KEYWORDS):
            result['error'] = 'The system is busy and trying to reconnect. Please try again shortly.'
        else:
            result['error'] = str(e)

    finally:
        result['response_time'] = round(
            time.time() - start_time,
            2
        )

    return result


# 文生图 advisory 超时（传给 g4f images.generate() 的 timeout kwarg，非硬截断）。
# HuggingFace Space 后端（BlackForestLabs_Flux1Dev/StabilityAI_SD35Large）存在真实的冷启动/
# 排队延迟，比纯文本对话慢得多，因此取值明显高于文本路径的 20s；outer 硬截断
# （见 generate_images() 的 future.result(timeout=...)）必须留有缓冲，两者需同步调整。
# outer 相对 advisory 的缓冲从 5s 加宽到 10s：test_g4f_image_provider 在 429/queue 类
# 瞬时错误上会重试一次（见该函数内的重试循环），多出的一次网络往返 + 重试前的等待
# 需要计入 outer 预算，否则重试期间可能被 future.result() 提前判超时打断。
IMAGE_GENERATION_ADVISORY_TIMEOUT = 40
IMAGE_GENERATION_OUTER_TIMEOUT = 50

# 单个 Provider 的超时覆盖表：多数图片 Provider 用上面的默认值即可，但 AnyProvider
# 是 g4f 的"聚合再路由"型 Provider——内部会依次尝试多个真实图片后端直到成功或全部
# 耗尽，耗时明显更长、方差也更大（实测偶发超过默认 outer timeout 才真正返回，但那时
# 图片其实已经生成并下载到本地 get_media_dir()，只是因为 future.result() 提前超时、
# 结果被丢弃，前端展示为 Failed）。给它单独更宽松的预算；其余 Provider 不受影响，
# 因为 outer timeout 是每个 future 独立计算的，不会拖慢同批次里其他 Provider 的等待时间。
IMAGE_PROVIDER_TIMEOUT_OVERRIDES = {
    'AnyProvider': {'advisory': 70, 'outer': 80},
}


def get_image_timeouts(provider_name):
    override = IMAGE_PROVIDER_TIMEOUT_OVERRIDES.get(provider_name)
    if override:
        return override['advisory'], override['outer']
    return IMAGE_GENERATION_ADVISORY_TIMEOUT, IMAGE_GENERATION_OUTER_TIMEOUT


# ==================================================
# 测试单个文生图 Provider
# 与 test_g4f_provider() 是两套完全独立的调用链路：
# - 走 g4f.client.Client().images.generate()，不是 g4f.ChatCompletion.create()
# - 返回值遵循图片 8-key 契约（init_image_result_object），不是文本 7-key 契约
# - 不经过 ROUTE_PROMPTS_MAP 隐形路由 / detect_and_truncate 重复检测——两者都是
#   针对文本回答设计的，对图片 URL/base64 数据没有意义
# ==================================================
def test_g4f_image_provider(provider, prompt, requested_model=None):
    provider_name = provider.__name__
    actual_model = determine_actual_image_model(provider_name, requested_model)
    display_model = actual_model or 'default'
    advisory_timeout, _ = get_image_timeouts(provider_name)

    start_time = time.time()
    result = init_image_result_object(provider_name, display_model)

    # 与 run_peer_review() 同构的重试策略：仅 429 / queue-full 这类瞬时限流错误值得
    # 重试一次，其余异常（含 GPU 配额耗尽、内容策略等）重试无意义，直接跳出。
    for attempt in range(2):
        try:
            client = G4FImageClient()
            generate_kwargs = {
                'prompt': prompt,
                'provider': provider,
                'timeout': advisory_timeout,
            }
            # 'auto'（当前仅 PollinationsImage 使用）代表"不指定具体模型，让 Provider
            # 自己走它的 default_image_model"，因此故意不传 model 关键字参数。
            if actual_model and actual_model != 'auto':
                generate_kwargs['model'] = actual_model

            response = client.images.generate(**generate_kwargs)

            image_data = response.data[0] if response and response.data else None
            url = getattr(image_data, 'url', None) if image_data else None
            b64_json = getattr(image_data, 'b64_json', None) if image_data else None

            if url:
                result['success'] = True
                result['url'] = url
            elif b64_json:
                result['success'] = True
                result['b64_json'] = b64_json
            else:
                result['error'] = 'Provider returned no image data.'
            break

        except Exception as e:
            err_str = str(e).lower()
            if attempt == 0 and ('429' in err_str or 'queue' in err_str):
                wait = 2 + random.uniform(0, 1)
                logger.warning(
                    f"Image provider {provider_name} 429/queue, retrying in {wait:.1f}s"
                )
                time.sleep(wait)
                continue
            if any(kw in err_str for kw in GPU_QUOTA_ERROR_KEYWORDS):
                result['error'] = (
                    "This provider's free GPU quota is temporarily exhausted. "
                    "Try again later or pick a different provider."
                )
            # 用 PEER_REVIEW_NETWORK_ERROR_KEYWORDS（含 429/queue）而不是
            # NETWORK_ERROR_KEYWORDS：本函数和 run_peer_review 一样会对 429/queue
            # 重试一次，重试耗尽后仍需把这两类错误归为"系统正忙"友好文案，而不是原始
            # "Error 429: ..." 字符串漏给前端。
            elif any(kw in err_str for kw in PEER_REVIEW_NETWORK_ERROR_KEYWORDS):
                result['error'] = 'The system is busy and trying to reconnect. Please try again shortly.'
            else:
                result['error'] = str(e)
            break

    result['response_time'] = round(
        time.time() - start_time,
        2
    )

    return result


# ==================================================
# 辅助函数：执行单次互评请求（不经过隐形 Prompt 路由）
# ==================================================
def run_peer_review(reviewer_provider, reviewer_model, review_prompt):
    review_result = {
        'reviewer_provider': reviewer_provider.__name__,
        'reviewer_model': reviewer_model,
        'score': 80,
        'comment': '',
    }
    last_exc = None
    for attempt in range(2):
        try:
            response = g4f.ChatCompletion.create(
                model=reviewer_model,
                messages=[{"role": "user", "content": review_prompt}],
                provider=reviewer_provider,
                timeout=25
            )
            score, comment = parse_peer_review_json(detect_and_truncate(str(response)))
            review_result['score'] = score
            review_result['comment'] = comment
            return review_result
        except Exception as e:
            last_exc = e
            err_str = str(e).lower()
            # 仅 429 / queue-full 类错误值得重试；其他异常直接跳出
            if attempt == 0 and ('429' in err_str or 'queue' in err_str):
                wait = 2 + random.uniform(0, 1)
                logger.warning(
                    f"Peer review 429/queue from {reviewer_provider.__name__}, "
                    f"retrying in {wait:.1f}s"
                )
                time.sleep(wait)
                continue
            break
    err_str = str(last_exc).lower()
    if any(kw in err_str for kw in CONTENT_POLICY_ERROR_KEYWORDS):
        review_result['comment'] = "This provider's content filter blocked the review. Try rephrasing your prompt."
    elif any(kw in err_str for kw in PEER_REVIEW_NETWORK_ERROR_KEYWORDS):
        review_result['comment'] = 'The system is busy and trying to reconnect. Please try again shortly.'
    else:
        review_result['comment'] = f'Review failed: {str(last_exc)}'
    return review_result


# ==================================================
# 辅助函数：校验对话历史路由的登录状态
# 未登录（含游客）一律拒绝，返回 (None, 401 响应)；已登录返回 (user_id, None)
# ==================================================
def _get_authenticated_user_id():
    user_id = session.get('user_id')
    if not user_id:
        return None, (jsonify({'error': 'Authentication required'}), 401)
    return user_id, None


# ==================================================
# 首页（通过 Jinja2 传递严谨的联动数据映射）
# ==================================================
@app.route('/')
def index():
    if not session.get('user_id') and not session.get('is_guest'):
        return render_template('home.html')

    if not G4F_AVAILABLE:
        return render_template(
            'index.html',
            providers=[],
            provider_models_json={},
            image_providers=[],
            image_provider_models_json={}
        )

    provider_list = []
    provider_models_json = {}

    # 整理 Provider 数据，建立精确的名称到模型的映射字典
    for p in G4F_PROVIDERS:
        name = p.__name__
        models = PROVIDER_MODELS_MAP.get(name, [])

        if models:
            provider_list.append({
                'name': name,
                'default_model': models[0]
            })
            # 存入字典，方便前端将其转换为 JavaScript 对象进行动态单选过滤
            provider_models_json[name] = models

    # 同上，整理文生图 Provider 数据（独立的映射表，与文本 Provider 完全隔离）
    image_provider_list = []
    image_provider_models_json = {}
    for p in IMAGE_PROVIDERS:
        name = p.__name__
        models = IMAGE_PROVIDER_MODELS_MAP.get(name, [])

        if models:
            image_provider_list.append({
                'name': name,
                'default_model': models[0]
            })
            image_provider_models_json[name] = models

    # 将结构化数据注入前端
    return render_template(
        'index.html',
        providers=provider_list,
        provider_models_json=provider_models_json,
        image_providers=image_provider_list,
        image_provider_models_json=image_provider_models_json
    )


@app.route('/home')
def home():
    session.pop('is_guest', None)
    return redirect(url_for('index'))


# ==================================================
# 只读历史详情页：展示某条历史记录的原始 prompt + 完整结果快照
# GET /history/<history_id>
#
# 已登录用户：从 Firestore 按 id + 归属校验取出该记录，直接注入模板渲染。
# 游客：对话历史从不落库，这里没有任何东西可查——渲染一个空壳模板，
# 由客户端 JS 从 sessionStorage（history.html 里维护的 guestHistory 持久化副本）
# 里按 URL 中的 history_id 自行查找并渲染，服务端全程不接触游客数据。
# 匿名/未认证：与 index() 的身份路由保持一致，交回 index() 处理（渲染 home.html）。
# ==================================================
@app.route('/history/<history_id>')
def view_history(history_id):
    if not session.get('user_id') and not session.get('is_guest'):
        return redirect(url_for('index'))

    if session.get('user_id'):
        entry = get_chat_history_by_id(session['user_id'], history_id)
        if not entry:
            flash('History entry not found', 'error')
            return redirect(url_for('index'))
        return render_template(
            'history.html',
            history_id=history_id,
            history_entry=entry,
            is_guest_view=False
        )

    return render_template(
        'history.html',
        history_id=history_id,
        history_entry=None,
        is_guest_view=True
    )


# ==================================================
# 获取所有可用Provider和它们支持的模型列表
# GET /api/providers
# ==================================================
@app.route('/api/providers', methods=['GET'])
def get_providers():
    if not G4F_AVAILABLE:
        return jsonify([])

    provider_list = []

    for p in G4F_PROVIDERS:
        name = p.__name__
        models = PROVIDER_MODELS_MAP.get(name, ['unknown'])
        provider_list.append({
            'name': name,
            'models': models,            
            'default_model': models[0],   
            'type': 'g4f',
            'status': 'available'
        })

    return jsonify(provider_list)


# ==================================================
# 获取所有可用的文生图 Provider 及其支持的模型列表
# GET /api/image-providers
# ==================================================
@app.route('/api/image-providers', methods=['GET'])
def get_image_providers():
    if not G4F_AVAILABLE:
        return jsonify([])

    provider_list = []

    for p in IMAGE_PROVIDERS:
        name = p.__name__
        models = IMAGE_PROVIDER_MODELS_MAP.get(name, ['unknown'])
        provider_list.append({
            'name': name,
            'models': models,
            'default_model': models[0],
            'type': 'g4f_image',
            'status': 'available'
        })

    return jsonify(provider_list)


# ==================================================
# 核心接口：同时比较多个Provider
# POST /api/compare
# ==================================================
@app.route('/api/compare', methods=['POST'])
def compare_providers():
    try:
        data = request.get_json()

        if not data or 'prompt' not in data:
            return jsonify({
                'error': 'Prompt is required'
            }), 400

        if not G4F_AVAILABLE:
            return jsonify({
                'error': 'g4f is not available'
            }), 503

        prompt = data['prompt']

        # 用户选中的 Provider 名字列表
        selected_providers = data.get('providers', [])

        # 用户全局指定的单选模型名称（可选）
        requested_model = data.get('model', None)

        # 最大线程数
        max_workers = min(
            data.get('max_workers', 3),
            5
        )

        logger.info(
            f"Comparing providers for prompt: {prompt[:50]}..."
        )

        # 筛选需要测试的 Provider 实例
        if selected_providers:
            providers_to_test = [
                p for p in G4F_PROVIDERS
                if p.__name__ in selected_providers
            ]
        else:
            providers_to_test = G4F_PROVIDERS

        if not providers_to_test:
            return jsonify({
                'error': 'No valid providers found'
            }), 400

        results = []

        # 并发执行请求
        with ThreadPoolExecutor(
            max_workers=min(max_workers, len(providers_to_test))
        ) as executor:

            futures = {
                executor.submit(
                    test_g4f_provider,
                    p,
                    prompt,
                    requested_model
                ): p
                for p in providers_to_test
            }

            for future, provider in futures.items():
                try:
                    result = future.result(timeout=21)
                    results.append(result)
                    logger.info(
                        f"Completed: {result['provider']} "
                        f"success={result['success']}"
                    )
                except Exception as e:
                    name = provider.__name__
                    fallback_model = determine_actual_model(name, requested_model)
                    fallback_result = init_result_object(name, fallback_model)
                    if isinstance(e, TimeoutError):
                        fallback_result['error'] = 'The system is busy and trying to reconnect. Please try again shortly.'
                        logger.warning(f"Provider {name} timed out after 21s")
                    else:
                        fallback_result['error'] = f'Execution error: {str(e)}'
                        logger.error(f"Error testing {name}: {e}", exc_info=True)
                    results.append(fallback_result)

        # 为所有结果初始化空的互评列表（必须在互评 try 块之前，保证字段始终存在）
        for r in results:
            r['peer_reviews'] = []

        # 第二阶段：AI 互评（独立 try-except，任何崩溃不影响第一轮结果交付）
        try:
            successful_results = [r for r in results if r['success']]
            if len(providers_to_test) >= 2 and len(successful_results) >= 2:
                logger.info(
                    f"Peer review phase started: {len(successful_results)} successful providers"
                )
                provider_obj_map = {p.__name__: p for p in providers_to_test}

                # 构建互评任务：对每个成功结果 A，让其他成功模型 B 进行点评
                peer_review_tasks = []
                for result_a in successful_results:
                    for result_b in successful_results:
                        if result_b['provider'] == result_a['provider']:
                            continue
                        reviewer_provider = provider_obj_map[result_b['provider']]
                        reviewer_model = result_b['model']
                        judge_prefix = PEER_REVIEW_PROMPTS_MAP.get(
                            reviewer_model,
                            'Please evaluate the quality of the following answer, noting its strengths and weaknesses.'
                        )
                        review_prompt = (
                            f"{judge_prefix}\n\nHere is the anonymous text to review:\n{result_a['response']}"
                        )
                        peer_review_tasks.append(
                            (reviewer_provider, reviewer_model, review_prompt, result_a['provider'])
                        )

                logger.info(f"Peer review phase: {len(peer_review_tasks)} tasks scheduled")
                max_peer_workers = min(10, len(peer_review_tasks))
                with ThreadPoolExecutor(max_workers=max_peer_workers) as peer_executor:
                    peer_futures = {
                        peer_executor.submit(run_peer_review, rp, rm, rpr): target
                        for rp, rm, rpr, target in peer_review_tasks
                    }
                    for future, target_provider in peer_futures.items():
                        try:
                            review_item = future.result(timeout=32)
                            for r in results:
                                if r['provider'] == target_provider:
                                    r['peer_reviews'].append(review_item)
                                    break
                            logger.info(
                                f"Peer review: {review_item['reviewer_provider']} "
                                f"scored {target_provider} {review_item['score']}/100"
                            )
                        except TimeoutError:
                            logger.warning(
                                f"Peer review for {target_provider} timed out after 32s"
                            )
                        except Exception as e:
                            logger.error(
                                f"Peer review error for {target_provider}: {e}",
                                exc_info=True
                            )
                logger.info("Peer review phase completed")
        except Exception as e:
            logger.error(f"Peer review phase failed entirely: {e}", exc_info=True)
            # peer_reviews 字段已初始化为 []，第一轮回答数据完整，安全返回

        # 排序：成功优先，耗时短优先
        results.sort(
            key=lambda x: (
                not x['success'],
                x['response_time']
            )
        )

        successful_count = sum(1 for r in results if r['success'])

        # 已登录用户持久化对话历史；游客不落库。持久化失败不影响本次对比结果返回
        history_id = None
        if session.get('user_id'):
            try:
                saved = save_chat_history(session['user_id'], prompt, results)
                if saved:
                    history_id = saved['id']
            except Exception as e:
                logger.error(f"Failed to save chat history: {e}", exc_info=True)

        return jsonify({
            'prompt': prompt,
            'total_providers': len(results),
            'successful_providers': successful_count,
            'results': results,
            'history_id': history_id
        })

    except Exception as e:
        logger.error(f"Error in compare_providers: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# 测试单个Provider
# POST /api/test-single
# ==================================================
@app.route('/api/test-single', methods=['POST'])
def test_single_provider():
    try:
        data = request.get_json()

        if not data or 'prompt' not in data or 'provider' not in data:
            return jsonify({
                'error': 'Prompt and provider are required'
            }), 400

        if not G4F_AVAILABLE:
            return jsonify({
                'error': 'g4f is not available'
            }), 503

        prompt = data['prompt']
        provider_name = data['provider']
        requested_model = data.get('model', None)

        # 查找对应 Provider
        provider = next(
            (p for p in G4F_PROVIDERS if p.__name__ == provider_name),
            None
        )

        if not provider:
            return jsonify({
                'error': f'Provider "{provider_name}" not found'
            }), 404

        result = test_g4f_provider(provider, prompt, requested_model)
        return jsonify(result)

    except Exception as e:
        logger.error(f"Error in test_single_provider: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# 核心接口：并发调用多个文生图 Provider 生成图片
# POST /api/generate-images
#
# 与 compare_providers() 是同一种"并发调度 + 逐个兜底"骨架，但只有一个阶段
# （没有互评），且调用的是 test_g4f_image_provider()（images.generate()）而非
# test_g4f_provider()（ChatCompletion.create()）。图片结果不写入对话历史—— history
# 集合的 Firestore 文档结构（results 字段）是围绕文本 8-key result DTO 设计的，
# 混入图片 DTO 需要单独的 schema 演进，本次不在范围内。
# ==================================================
@app.route('/api/generate-images', methods=['POST'])
def generate_images():
    try:
        data = request.get_json()

        if not data or 'prompt' not in data:
            return jsonify({
                'error': 'Prompt is required'
            }), 400

        if not G4F_AVAILABLE:
            return jsonify({
                'error': 'g4f is not available'
            }), 503

        prompt = data['prompt']

        # 用户选中的图片 Provider 名字列表
        selected_providers = data.get('providers', [])

        # 用户全局指定的单选模型名称（可选）
        requested_model = data.get('model', None)

        # 最大线程数
        max_workers = min(
            data.get('max_workers', 3),
            5
        )

        logger.info(
            f"Generating images for prompt: {prompt[:50]}..."
        )

        # 筛选需要调用的图片 Provider 实例
        if selected_providers:
            providers_to_test = [
                p for p in IMAGE_PROVIDERS
                if p.__name__ in selected_providers
            ]
        else:
            providers_to_test = IMAGE_PROVIDERS

        if not providers_to_test:
            return jsonify({
                'error': 'No valid image providers found'
            }), 400

        results = []

        # 并发执行请求
        with ThreadPoolExecutor(
            max_workers=min(max_workers, len(providers_to_test))
        ) as executor:

            futures = {
                executor.submit(
                    test_g4f_image_provider,
                    p,
                    prompt,
                    requested_model
                ): p
                for p in providers_to_test
            }

            for future, provider in futures.items():
                name = provider.__name__
                # 每个 Provider 各自的 outer timeout 独立计算（见 get_image_timeouts()），
                # 慢速的聚合型 Provider（如 AnyProvider）不会拖慢同批次里其他 Provider
                # 的等待时间，也不会被默认预算过早判定超时。
                _, outer_timeout = get_image_timeouts(name)
                try:
                    result = future.result(timeout=outer_timeout)
                    results.append(result)
                    logger.info(
                        f"Image generation completed: {result['provider']} "
                        f"success={result['success']}"
                    )
                except Exception as e:
                    fallback_model = determine_actual_image_model(name, requested_model) or 'default'
                    fallback_result = init_image_result_object(name, fallback_model)
                    if isinstance(e, TimeoutError):
                        fallback_result['error'] = 'The system is busy and trying to reconnect. Please try again shortly.'
                        logger.warning(
                            f"Image provider {name} timed out after {outer_timeout}s"
                        )
                    else:
                        fallback_result['error'] = f'Execution error: {str(e)}'
                        logger.error(f"Error generating image with {name}: {e}", exc_info=True)
                    results.append(fallback_result)

        # 排序：成功优先，耗时短优先（与 compare_providers 的排序契约一致）
        results.sort(
            key=lambda x: (
                not x['success'],
                x['response_time']
            )
        )

        successful_count = sum(1 for r in results if r['success'])

        return jsonify({
            'prompt': prompt,
            'total_providers': len(results),
            'successful_providers': successful_count,
            'results': results
        })

    except Exception as e:
        logger.error(f"Error in generate_images: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# GET /media/<filename>
#
# g4f.client.Client().images.generate() 在返回前已经把生成的图片同步下载到本地
# get_media_dir() 目录（./generated_images 优先，否则 ./generated_media），并把
# Result DTO 的 url 字段设为形如 "/media/<filename>?url=<原始外部地址>" 的相对路径——
# 这是 g4f 自带 GUI/API 服务器注册的路由约定，本项目并未运行那套服务器，因此必须
# 自己补上这条静态文件路由，否则前端 <img> 与下载按钮请求 /media/<filename> 会 404
# （下载按钮会把 404 错误页当成图片字节存下来，导致"不支持的文件格式"）。
# 只读取本地已生成的文件，不按 url 查询参数发起任何服务端抓取——与"下载按钮不做
# 服务端代理"的 SSRF 规避原则一致。
# ==================================================
@app.route('/media/<path:filename>')
def serve_generated_media(filename):
    safe_filename = os.path.basename(filename)
    media_dir = os.path.abspath(get_media_dir())
    return send_from_directory(media_dir, safe_filename)


# ==================================================
# 对话历史：分页查询
# GET /api/history?page=1&limit=20
# ==================================================
@app.route('/api/history', methods=['GET'])
def get_history():
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        page = request.args.get('page', 1, type=int) or 1
        limit = request.args.get('limit', 20, type=int) or 20
        page = max(page, 1)
        limit = max(min(limit, 100), 1)
        offset = (page - 1) * limit

        history_list = get_chat_history_list(user_id, limit=limit, offset=offset)
        return jsonify({
            'history': history_list,
            'page': page,
            'limit': limit
        })

    except Exception as e:
        logger.error(f"Error in get_history: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# 对话历史：重命名
# PATCH /api/history/<history_id>/title
# ==================================================
@app.route('/api/history/<history_id>/title', methods=['PATCH'])
def update_history_title(history_id):
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        new_title = data.get('new_title', '').strip()
        if not new_title:
            return jsonify({'error': 'new_title is required'}), 400

        success = update_chat_history_title(user_id, history_id, new_title)
        if not success:
            return jsonify({'error': 'History entry not found'}), 404

        return jsonify({'status': 'ok'})

    except Exception as e:
        logger.error(f"Error in update_history_title: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# 对话历史：删除
# DELETE /api/history/<history_id>
# ==================================================
@app.route('/api/history/<history_id>', methods=['DELETE'])
def delete_history(history_id):
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        success = delete_chat_history(user_id, history_id)
        if not success:
            return jsonify({'error': 'History entry not found'}), 404

        return jsonify({'status': 'ok'})

    except Exception as e:
        logger.error(f"Error in delete_history: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# 对话历史：切换置顶状态
# POST /api/history/<history_id>/toggle-pin
# ==================================================
@app.route('/api/history/<history_id>/toggle-pin', methods=['POST'])
def toggle_history_pin(history_id):
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        new_pinned = toggle_pin_chat_history(user_id, history_id)
        if new_pinned is None:
            return jsonify({'error': 'History entry not found'}), 404

        return jsonify({'is_pinned': new_pinned})

    except Exception as e:
        logger.error(f"Error in toggle_history_pin: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# 健康检查
# ==================================================
@app.route('/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'g4f_available': G4F_AVAILABLE,
        'providers': [p.__name__ for p in G4F_PROVIDERS],
        'image_providers': [p.__name__ for p in IMAGE_PROVIDERS],
        'routing_rules_loaded': bool(ROUTE_PROMPTS_MAP),
        'peer_review_rules_loaded': bool(PEER_REVIEW_PROMPTS_MAP),
        'timestamp': time.time()
    })


@app.route('/api/auth/guest', methods=['POST'])
def guest_login():
    session['is_guest'] = True
    return jsonify({'status': 'ok'})


@app.errorhandler(404)
def not_found(_error):
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=False, host='0.0.0.0', port=port)