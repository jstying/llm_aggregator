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
    append_chat_history_result,
    save_image_history,
    get_image_history_list,
    get_image_history_by_id,
    delete_image_history,
    update_image_history_title,
    toggle_pin_image_history,
    append_image_history_result,
    get_claude_free_tier_usage,
    increment_claude_free_tier_usage,
    decrement_claude_free_tier_usage,
    get_gemini_free_tier_usage,
    increment_gemini_free_tier_usage,
    decrement_gemini_free_tier_usage,
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


# =========================
# 初始化官方 Anthropic (Claude) SDK
# 与上面 g4f 的初始化完全独立的第三条调用链路——既不是 g4f.ChatCompletion.create()
# 也不是 g4f 的 images.generate()，而是直接用官方 `anthropic` 库调用 Anthropic 自己的
# API 端点。CLAUDE_AVAILABLE 是独立于 G4F_AVAILABLE 的全局布尔标志，任一方缺失/初始化
# 失败都只降级各自的功能，互不影响；Claude 也有自己独立的 Provider/模型命名空间
# （CLAUDE_MODELS），不与 PROVIDER_MODELS_MAP/IMAGE_PROVIDER_MODELS_MAP 混用。
# =========================
try:
    import anthropic

    CLAUDE_AVAILABLE = True
    logger.info("anthropic SDK imported successfully")
except ImportError as e:
    CLAUDE_AVAILABLE = False
    anthropic = None
    logger.warning(f"anthropic SDK not available: {e}")

# 模型 key（前端 <select> 的 value，同时也是展示名/请求体里的 model 字段）→ 官方
# API model ID 的映射。key 与 ID 目前一一对应，但保留一层映射是为了不让前端/请求体
# 直接暴露官方精确的模型 ID 字符串（未来 ID 变化时只需改这一处）。
CLAUDE_MODELS = {
    'claude-sonnet-5': 'claude-sonnet-5',
    'claude-haiku-4-5': 'claude-haiku-4-5-20251001',
}

# 非流式请求，max_tokens 刻意保持较小（远低于官方 SDK 对非流式请求约 16000 token 的
# 超时保护阈值），足够支撑本项目"多 Provider 对比"场景下的一次简短回答，不需要流式。
CLAUDE_MAX_TOKENS = 2048

# 单个注册用户在未自带 Key 时，可免费消耗开发者账户额度调用 Claude 的次数上限。一次
# "Compare"点击无论前端后续还勾选了多少个免 Key g4f Provider 都只发起一次
# /api/claude-chat 请求，因此只消耗 1 次额度——这条"一次点击=一次额度"的语义与
# GEMINI_FREE_TIER_LIMIT 同构（见 CLAUDE.md 第 6 节）。
CLAUDE_FREE_TIER_LIMIT = 10


# =========================
# 初始化官方 Google Gemini（"Nano Banana"系列）图片生成 SDK
# 与 Claude 同构的第四条完全独立的调用链路，但作用于文生图场景而不是对话场景：不经过
# g4f 的 images.generate()（IMAGE_PROVIDERS 名字空间），而是直接用官方 google-genai
# SDK 调用 Gemini 的图片生成 API。这是本项目在"文生图"场景下第一个"付费/有配额"的
# Provider，配套与 Claude 完全同构的免费额度/自带 Key 防滥用机制（见 call_gemini_image_model()
# 与 /api/gemini-image 路由）。GEMINI_AVAILABLE 独立于 G4F_AVAILABLE/CLAUDE_AVAILABLE，
# 任一方缺失/初始化失败都只降级各自的功能。
#
# 包名是 google-genai（PyPI），导入路径是 `from google import genai`——不要与 Google
# 早期/其他项目里出现过的 `google-generativeai` 包（导入路径 `google.generativeai`）
# 混淆，那是已废弃的旧 SDK，本项目不使用。
# =========================
try:
    from google import genai as google_genai

    GEMINI_AVAILABLE = True
    logger.info("google-genai SDK imported successfully")
except ImportError as e:
    GEMINI_AVAILABLE = False
    google_genai = None
    logger.warning(f"google-genai SDK not available: {e}")

# 模型 key（前端 <select> 的 value）→ 官方 API model ID 的映射，与 CLAUDE_MODELS 同构。
# 三档 Nano Banana 系列模型（官方文档 https://ai.google.dev/gemini-api/docs/models，
# 2026-07-04 查证）全部已接入（2026-07-05 补齐 Nano Banana 2/Lite 两档，此前只有 Pro）：
# - nano-banana-pro（gemini-3-pro-image）：旗舰档位，"Professional design engine with
#   a reasoning core for studio-quality 4K visuals, complex layouts, and precise text
#   rendering"，与 Claude Sonnet 5（CLAUDE_MODELS 里的旗舰模型）同属"frontier"定位。
# - nano-banana-2（gemini-3.1-flash-image）：中档，更轻量/低延迟。
# - nano-banana-lite（gemini-3.1-flash-lite-image）：最轻量档位。
# 三个 model ID 均已用真实 GEMINI_API_KEY 直接调用验证过合法（见 call_gemini_image_model()
# 上方注释——一个零配额的真实账户对三者都得到同一种 429 配额耗尽错误，而不是模型不存在
# 才会出现的 404/400，说明三者都通过了模型名校验）。
GEMINI_IMAGE_MODELS = {
    'nano-banana-pro': 'gemini-3-pro-image',
    'nano-banana-2': 'gemini-3.1-flash-image',
    'nano-banana-lite': 'gemini-3.1-flash-lite-image',
}

# 单个注册用户在未自带 Key 时，可免费消耗开发者账户额度调用 Gemini 图片生成的次数上限。
# 与 CLAUDE_FREE_TIER_LIMIT 同构，各自独立计数（互不共享额度）。一次生成点击无论
# geminiModelSelect 选中哪一档模型都只发起一次 /api/gemini-image 请求，因此只消耗 1 次
# 额度——这条"一次点击=一次额度"的语义不随模型档位数量变化（见 CLAUDE.md 第 6 节）。
GEMINI_FREE_TIER_LIMIT = 10


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
#
# outer 的缓冲公式是 2 * advisory + IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER，不是
# "advisory + 固定小缓冲"（2026-07-04 前的旧公式，固定缓冲 10s）。原因：
# test_g4f_image_provider 对 429/queue 类瞬时错误会重试一次，而"第一次尝试要多久才
# 抛出 429"和"重试后的第二次尝试要多久才成功"都可能各自跑到接近 advisory_timeout
# 才结束（不是"429 立刻快速失败"这种理想情况）——PollinationsImage 曾在 429 重试后，
# 图片其实已经生成并写入 get_media_dir()，却因为 outer 只比 advisory 宽 10s 而被
# future.result() 提前判超时丢弃。两次尝试各自最坏都要吃满 advisory，所以 outer 必须
# 覆盖 2 倍 advisory，而不是 1 倍 advisory 加一点缓冲。这是重试机制本身的通用时序问题
# （对任何会 429 重试的 Provider 都成立），因此在公式层面修正，不是给 PollinationsImage
# 单独加一条 override。
IMAGE_GENERATION_ADVISORY_TIMEOUT = 40
IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER = 5


def _compute_outer_timeout(advisory_timeout):
    return advisory_timeout * 2 + IMAGE_GENERATION_RETRY_SCHEDULING_BUFFER


IMAGE_GENERATION_OUTER_TIMEOUT = _compute_outer_timeout(IMAGE_GENERATION_ADVISORY_TIMEOUT)

# 单个 Provider 的 advisory 超时覆盖表：多数图片 Provider 用上面的默认值即可，但
# AnyProvider 是 g4f 的"聚合再路由"型 Provider——内部会依次尝试多个真实图片后端直到
# 成功或全部耗尽，耗时明显更长、方差也更大，因此单独给它更宽松的 advisory 预算。
# outer 不在这里单独配置——统一由 _compute_outer_timeout() 从 advisory 推导，
# 确保"两次尝试都跑满 advisory"的重试缓冲对所有 Provider（含被覆盖 advisory 的
# AnyProvider）一致生效。outer timeout 是每个 future 独立计算的，不会拖慢同批次里
# 其他 Provider 的等待时间。
IMAGE_PROVIDER_TIMEOUT_OVERRIDES = {
    'AnyProvider': {'advisory': 70},
}


def get_image_timeouts(provider_name):
    override = IMAGE_PROVIDER_TIMEOUT_OVERRIDES.get(provider_name)
    advisory = override['advisory'] if override else IMAGE_GENERATION_ADVISORY_TIMEOUT
    return advisory, _compute_outer_timeout(advisory)


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
# 调用官方 Anthropic API 获取 Claude 的回答
# 与 test_g4f_provider()/test_g4f_image_provider() 是第三条完全独立的调用链路：不经过
# g4f，不参与 ROUTE_PROMPTS_MAP 隐形路由，也不参与互评（run_peer_review 只在
# providers_to_test/G4F_PROVIDERS 名字空间内调度，Claude 从不出现在那里）。
#
# Key 路由规则（防薅羊毛核心）：user_api_key 非空时优先使用它实例化客户端，完全不
# 消耗开发者账户额度，调用方（claude_chat 路由）据此决定是否需要检查/递增免费额度
# 计数器——本函数自身不知道、也不关心计数器，只负责"用哪个 Key 发起这次请求"。
#
# 错误分类：账户余额不足在真实环境下实测返回的是 400 + error.type ==
# "invalid_request_error" + message 含 "credit balance is too low"（而不是最初
# 设想的 429 + "insufficient_funds"——Anthropic 的错误体系里没有这个组合；也不是
# 通用文档字面暗示的 403 + "billing_error"，实测该账号返回的就是 400。真正稳定的
# 判断依据是 message 里的 "credit balance" 关键词，而不是某个具体 status_code 或
# error.type 值——429 仍然专指限流 rate_limit_error，是可重试的瞬时错误，与余额
# 耗尽是两回事，不应该混淆。同时兼容性地保留 error.type == 'billing_error' 分支，
# 以防某些账户/未来 API 版本确实走那个更"文档化"的错误形状。
# ==================================================
def call_claude_model(prompt, model_key, user_api_key=None):
    model_id = CLAUDE_MODELS.get(model_key)
    start_time = time.time()
    result = {
        'provider': 'Claude',
        'success': False,
        'response': '',
        'error': '',
        'response_time': 0,
        'model': model_key,
        'type': 'anthropic',
    }

    if not model_id:
        result['error'] = f'Unknown Claude model "{model_key}".'
        result['response_time'] = round(time.time() - start_time, 2)
        return result

    try:
        client = anthropic.Anthropic(api_key=user_api_key) if user_api_key else anthropic.Anthropic()
        response = client.messages.create(
            model=model_id,
            max_tokens=CLAUDE_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )

        text = next((block.text for block in response.content if block.type == 'text'), '')
        result['response'] = detect_and_truncate(text)
        result['success'] = True

    except anthropic.APIStatusError as e:
        error_message = getattr(e, 'message', str(e))
        is_credits_exhausted = (
            getattr(e, 'type', None) == 'billing_error'
            or 'credit balance' in error_message.lower()
        )
        if is_credits_exhausted:
            result['error'] = 'SERVER_CREDITS_EXHAUSTED'
            result['error_code'] = 'SERVER_CREDITS_EXHAUSTED'
        elif e.status_code == 401:
            result['error'] = 'Invalid or missing Claude API key.'
        else:
            result['error'] = f'Error {e.status_code}: {error_message}'

    except anthropic.APIConnectionError:
        result['error'] = 'The system is busy and trying to reconnect. Please try again shortly.'

    except Exception as e:
        result['error'] = str(e)

    finally:
        result['response_time'] = round(time.time() - start_time, 2)

    return result


# ==================================================
# 调用官方 Google Gemini（"Nano Banana"）API 生成图片
# 与 test_g4f_image_provider()（g4f 的 IMAGE_PROVIDERS 名字空间）和 call_claude_model()
# 都是完全独立的调用链路：不经过 g4f，不参与 GPU_QUOTA_ERROR_KEYWORDS/
# PEER_REVIEW_NETWORK_ERROR_KEYWORDS 判定与 429/queue 重试逻辑（那套是 g4f 图片 Provider
# 专属的），也不参与互评（图片生成本身就没有互评这个概念）。返回值形状是"图片 8-key
# 契约"的第二种独立实现——与 g4f 的 init_image_result_object() 字段名/含义相同
# （provider/success/url/b64_json/error/response_time/model/type），但 type 用
# 'google_genai' 而不是 'g4f_image'，与 Claude Result 的 type='anthropic' 之于
# LLM Result 的 type='g4f' 是同一种"字段结构相似但类型标记独立"的关系（见 CLAUDE.md
# 第 7 节 Data Models）。官方 API 直接返回图片字节的 base64 编码（Interaction.output_image.data），
# 因此这里恒定走 b64_json 分支，从不设置 url——不像 g4f 图片 Provider 那样需要落地到
# get_media_dir() 本地文件再通过 /media/<filename> 提供，省去一整套本地存储/清理的复杂度。
#
# Key 路由规则（防薅羊毛核心，与 call_claude_model 完全同构）：user_api_key 非空时优先
# 用它构造客户端，完全不消耗开发者账户额度，调用方（/api/gemini-image 路由）据此决定
# 是否需要检查/递增免费额度计数器——本函数自身不知道、也不关心计数器。
#
# 错误分类：判断依据的 429/403 分支**已用真实账户实测验证过**（2026-07-05，一个真实
# 但零配额的 GEMINI_API_KEY，直接跑通 call_gemini_image_model() → 三个 Nano Banana
# 模型全部）。实测确认：(1) 三个 model ID（gemini-3.1-flash-image/gemini-3-pro-image/
# gemini-3.1-flash-lite-image）都是官方承认的合法模型名——如果 ID 拼写有误，API 会返回
# 404/400 之类的"模型不存在"错误，而不是配额错误，三次请求全部命中同一种 429 错误说明
# 三个 ID 都先通过了模型校验，本项目 GEMINI_IMAGE_MODELS 里的 ID 映射因此得到交叉验证；
# (2) 真实的"零配额"错误形状是 429 + 异常类型名 RateLimitError（google-genai 内部私有
# 兼容错误层的类，见下）+ .status_code == 429（.status 属性不存在/为 None，与官方
# troubleshooting 文档字面暗示的 "429/RESOURCE_EXHAUSTED 状态字符串" 组合不完全一致——
# 实测只有 .status_code 是可靠信号，.status 检查是防御性兜底，可能对应另一条尚未实测
# 到的错误路径）+ message 形如 "Error code: 429 - {'error': {'message': 'You do not
# have enough quota to make this request.', 'code': 'too_many_requests'}}"。403
# PERMISSION_DENIED（"API Key 权限不足"）来自 Gemini API 官方 troubleshooting 文档
# （https://ai.google.dev/gemini-api/docs/troubleshooting，2026-07-04 查证）发布的
# HTTP 状态码表，尚未用真实的、权限不足的 Key 实测验证（用户提供的这个 Key 本身是合法
# Key，只是零配额，触发的是 429 而非 403）。直接读取本项目锁定版本（google-genai==2.10.0）
# 的 SDK 源码确认：client.interactions.create() 抛出的异常实例带有 .status_code/.code/
# .status/.message 属性（无论是走公开的 google.genai.errors.APIError 分支还是
# interactions 资源专属的内部兼容错误类），因此用 getattr() 鸭子类型读取这些属性做
# 分类，而不是 import 任何具体异常类——这些具体异常类（如实测命中的 RateLimitError）
# 目前只存在于 google-genai 包内部一个带下划线前缀的私有子模块里，没有稳定的公开导入
# 路径，直接 import 会是比 CLAUDE_MODELS 硬编码映射更脆弱的耦合，鸭子类型不依赖这条
# 私有路径也能拿到同样的判断依据。测试见 tests/test_gemini_integration.py 的
# test_real_world_quota_exhausted_error_maps_to_server_quota_exhausted（默认参数
# 即实测捕获的真实错误形状）。
#
# 另一处与 Claude 的行为差异（已实测确认，见本函数下方 google_genai.Client() 调用）：
# anthropic.Anthropic() 零参构造不检查 Key，缺 Key 只在真正调用 messages.create() 时
# 才报错；而 google_genai.Client() 零参构造会**立即**检查 GOOGLE_API_KEY/GEMINI_API_KEY
# 环境变量，缺失时直接在构造阶段抛 ValueError（不是调用阶段）。这里的 except Exception
# 兜底分支同样能捕获这个 ValueError 并把其消息透传给前端、不会导致 500，用户体感上与
# Claude 一致（"没配置就业务失败，不影响进程启动"），只是失败发生的具体调用点不同。
# ==================================================
def call_gemini_image_model(prompt, model_key, user_api_key=None):
    model_id = GEMINI_IMAGE_MODELS.get(model_key)
    start_time = time.time()
    result = {
        'provider': 'Gemini',
        'success': False,
        'url': None,
        'b64_json': None,
        'error': '',
        'response_time': 0,
        'model': model_key,
        'type': 'google_genai',
    }

    if not model_id:
        result['error'] = f'Unknown Gemini model "{model_key}".'
        result['response_time'] = round(time.time() - start_time, 2)
        return result

    try:
        client = google_genai.Client(api_key=user_api_key) if user_api_key else google_genai.Client()
        interaction = client.interactions.create(model=model_id, input=prompt)

        image_content = getattr(interaction, 'output_image', None)
        image_b64 = getattr(image_content, 'data', None) if image_content else None

        if image_b64:
            result['b64_json'] = image_b64
            result['success'] = True
        else:
            result['error'] = 'Gemini did not return an image for this prompt.'

    except Exception as e:
        status_code = getattr(e, 'status_code', None) or getattr(e, 'code', None)
        status_str = (getattr(e, 'status', None) or '').upper()
        message = getattr(e, 'message', None) or str(e)

        is_quota_exhausted = status_code == 429 or status_str == 'RESOURCE_EXHAUSTED'
        is_permission_denied = status_code == 403

        if is_quota_exhausted:
            result['error'] = 'SERVER_QUOTA_EXHAUSTED'
            result['error_code'] = 'SERVER_QUOTA_EXHAUSTED'
        elif is_permission_denied:
            result['error'] = 'Invalid or missing Gemini API key.'
        elif status_code is not None:
            result['error'] = f'Error {status_code}: {message}'
        else:
            result['error'] = message

    finally:
        result['response_time'] = round(time.time() - start_time, 2)

    return result


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
# "Stop Generating"按钮的额度退款账本（2026-07-05 新增）
#
# 本项目的 Flask 部署是同步的：客户端 abort 一个 fetch 只断开它自己这端的连接，
# 不会中断服务器里正在阻塞执行的 anthropic.messages.create()/genai
# interactions.create() 调用——免费额度计数器可能在客户端已经放弃等待之后才递增。
# 因此"点击 Stop 立刻退还额度"不能靠前端本地猜测，而是靠这本账：claude_chat()/
# gemini_image_chat() 每次真的成功递增免费额度时，把这次调用的 request_id（前端生成
# 的一次性 UUID）连同 user_id/provider 记进账本；前端 abort 之后带着同一个
# request_id 调用 /api/claude-chat/refund 或 /api/gemini-image/refund，账本命中
# 才退 1 次，退完立刻从账本摘掉——不存在"认证用户反复调用退款接口就能无限刷回额度"
# 这个滥用面，因为退款只能核销一次真实发生过的递增，不能主动创造。
#
# 只保存在单进程内存里，不落库、不跨实例共享——与本项目已接受的"GAE 多实例下本地
# 磁盘各自独立"简化同一个精神：最坏情况是请求恰好分配到另一个实例、账本没命中，
# 退款失败，用户损失这一次额度，是已知的边界情况，不是这次要解决的问题。
# ==================================================
_PENDING_FRONTIER_REFUNDS = {}
_PENDING_FRONTIER_REFUND_TTL_SECONDS = 600


def _record_pending_frontier_refund(request_id, user_id, provider):
    if not request_id:
        return
    now = time.time()
    stale_ids = [
        rid for rid, entry in _PENDING_FRONTIER_REFUNDS.items()
        if now - entry['recorded_at'] > _PENDING_FRONTIER_REFUND_TTL_SECONDS
    ]
    for rid in stale_ids:
        _PENDING_FRONTIER_REFUNDS.pop(rid, None)
    _PENDING_FRONTIER_REFUNDS[request_id] = {
        'user_id': user_id,
        'provider': provider,
        'recorded_at': now,
    }


def _consume_pending_frontier_refund(request_id, user_id, provider):
    if not request_id:
        return False
    entry = _PENDING_FRONTIER_REFUNDS.get(request_id)
    if not entry or entry['user_id'] != user_id or entry['provider'] != provider:
        return False
    _PENDING_FRONTIER_REFUNDS.pop(request_id, None)
    return True


# ==================================================
# 辅助函数：把 Claude/Gemini 的结果追加进一条已存在的历史记录（2026-07-05 新增）
#
# 背景（修复的 bug）：/api/compare、/api/generate-images 各自在拿到 g4f 结果后立即
# 调用 save_chat_history()/save_image_history() 落库并返回 history_id；此时前端才
# 刚开始额外发起 POST /api/claude-chat / POST /api/gemini-image。因此 Claude/Gemini
# 的结果一定是在那条历史记录已经落库之后才计算出来的——旧实现只把它追加进浏览器
# 内存里的 data.results 数组用于当次页面渲染，从未回写 Firestore，导致用户重新打开
# /history/<id> 或 /image-history/<id> 时，刚才明明看到、还能下载的 Claude/Gemini
# 结果卡片凭空消失。修复方式：claude_chat()/gemini_image_chat() 现在都接受一个可选
# 的请求体字段 history_id（前端把 /api/compare 或 /api/generate-images 返回的
# history_id 原样转发过来），调用成功/失败后都把这次的 result 追加进该历史记录。
#
# 关键设计取舍：
# 1. 追加的是**后端自己刚计算出的** result 字典，不是客户端提交的任意 JSON——history_id
#    只是"写到哪条记录"的定位符，结果内容本身完全由服务器决定，避免了信任客户端
#    自行拼造的 result 数据这一攻击面。
# 2. 这不是"把 Claude/Gemini 结果混入 save_chat_history()/save_image_history()"
#    （历史上明确禁止的做法——那意味着让 Claude/Gemini 参与创建新的历史记录）：
#    这两个 append_* 函数只能追加进一条**已经存在**的记录，创建新记录的入口依然
#    只有 save_chat_history()/save_image_history()，且依然只由 g4f 调用链路触发。
# 3. history_id 缺失（如游客——Claude/Gemini 对游客本就完全锁定，不会走到这里；
#    或 g4f 侧持久化本身失败导致没有 history_id）时，两个函数直接跳过、不报错——
#    与"持久化失败不影响主结果返回"这一既有原则一致，缺失 history_id 不应该让
#    Claude/Gemini 本次请求本身失败。
# 4. append_chat_history_result()/append_image_history_result() 内部已经做了归属
#    校验；这里只需处理"未找到/不属于该用户/Firebase 不可用"（返回 False）与
#    抛异常两种失败情况，两者都只记录日志，不向调用方传播。
# ==================================================
def _append_claude_result_to_history(user_id, history_id, result):
    if not history_id:
        return
    try:
        appended = append_chat_history_result(user_id, history_id, result)
        if not appended:
            logger.warning(
                f"Could not append Claude result to chat history {history_id} for user {user_id} "
                "(entry not found, not owned by this user, or Firebase unavailable)"
            )
    except Exception as e:
        logger.error(
            f"Failed to append Claude result to chat history {history_id}: {e}",
            exc_info=True
        )


def _append_gemini_result_to_image_history(user_id, history_id, result):
    if not history_id:
        return
    try:
        appended = append_image_history_result(user_id, history_id, result)
        if not appended:
            logger.warning(
                f"Could not append Gemini result to image history {history_id} for user {user_id} "
                "(entry not found, not owned by this user, or Firebase unavailable)"
            )
    except Exception as e:
        logger.error(
            f"Failed to append Gemini result to image history {history_id}: {e}",
            exc_info=True
        )


# ==================================================
# 辅助函数：为 index() 的模板渲染准备 Trial Quota 徽章所需的上下文（2026-07-05 新增）
#
# 只在已登录时才查询 Firestore 实际计数——游客/匿名对 Claude/Gemini 完全锁定（见
# CLAUDE.md 第 6 节），没有额度可展示，两个 quota 值就是 None，模板据此不渲染徽章
# （与 Claude/Gemini Provider 卡片本身的登录态锁定判断保持一致的"游客/匿名=完全不可用"
# 语义，不是"降级展示 0/10"）。CLAUDE_FREE_TIER_LIMIT/GEMINI_FREE_TIER_LIMIT 两个常量
# 无论是否登录都注入，供前端"额度已用完"弹窗动态拼出正确的次数文案，避免把这个数字
# 硬编码在 JS 里、未来改动限额时忘记同步。
# ==================================================
def _get_frontier_quota_context():
    claude_quota = None
    gemini_quota = None
    if session.get('user_id'):
        claude_quota = {
            'used': get_claude_free_tier_usage(session['user_id']),
            'limit': CLAUDE_FREE_TIER_LIMIT,
        }
        gemini_quota = {
            'used': get_gemini_free_tier_usage(session['user_id']),
            'limit': GEMINI_FREE_TIER_LIMIT,
        }
    return {
        'claude_quota': claude_quota,
        'gemini_quota': gemini_quota,
        'claude_free_tier_limit': CLAUDE_FREE_TIER_LIMIT,
        'gemini_free_tier_limit': GEMINI_FREE_TIER_LIMIT,
    }


# ==================================================
# 首页（通过 Jinja2 传递严谨的联动数据映射）
# ==================================================
@app.route('/')
def index():
    if not session.get('user_id') and not session.get('is_guest'):
        return render_template('home.html')

    quota_context = _get_frontier_quota_context()

    if not G4F_AVAILABLE:
        return render_template(
            'index.html',
            providers=[],
            provider_models_json={},
            image_providers=[],
            image_provider_models_json={},
            **quota_context
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
        image_provider_models_json=image_provider_models_json,
        **quota_context
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
# 只读文生图历史详情页：展示某条图片生成记录的原始 prompt + 完整结果快照
# GET /image-history/<history_id>
#
# 与 view_history() 的关键区别：图片版 Recents 侧边栏专门限定只对已登录用户开放
# （见 CLAUDE.md 第 6 节"文生图 Recents 访问限制"），游客与匿名一律重定向回 index()，
# 不像聊天历史那样为游客渲染一个由 sessionStorage 客户端自行填充的空壳——图片生成
# 结果对游客从来不落库，也不提供任何客户端临时记录，所以游客访问这个 URL 没有任何
# 东西可展示，直接重定向比渲染一个注定空的壳更清楚。
# ==================================================
@app.route('/image-history/<history_id>')
def view_image_history(history_id):
    if not session.get('user_id'):
        return redirect(url_for('index'))

    entry = get_image_history_by_id(session['user_id'], history_id)
    if not entry:
        flash('Image history entry not found', 'error')
        return redirect(url_for('index'))

    return render_template(
        'image_history.html',
        history_id=history_id,
        history_entry=entry
    )


# ==================================================
# 个人 API Key 配置页（第 3 节新增）
# GET /apikey-config
#
# 纯静态展示 + 客户端 localStorage 绑定，不需要登录态守卫——它本身不发起任何需要
# 权限的请求，只是把 Claude API Key 存到浏览器本地；真正的权限/额度校验发生在
# /api/claude-chat 路由里。ChatGPT/Gemini 两个输入框目前只是占位符，不落任何存储。
# ==================================================
@app.route('/apikey-config')
def apikey_config():
    return render_template('apikey-config.html')


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
# 官方 Claude (Anthropic) 对话接口
# POST /api/claude-chat
#
# 与 /api/compare 完全独立的第三条路由——不经过 ThreadPoolExecutor 并发调度、不参与
# 互评，单次请求只调用一个 Claude 模型。权限与成本控制（防薅羊毛）核心：
# 1. 游客/匿名一律 401（复用 _get_authenticated_user_id，与对话/图片历史路由同一套
#    守卫）——前端会把 Claude 勾选框置灰，这里是后端侧的第二层防御。
# 2. Header 里携带非空 X-User-Claude-Key 时，优先使用该 Key 实例化客户端，完全不
#    检查/消耗免费额度计数器。
# 3. 未携带自带 Key 时，检查该用户的 claude_free_tier_usage 是否已达到
#    CLAUDE_FREE_TIER_LIMIT（当前为 10）；达到则直接拦截、不调用开发者 API，返回
#    {"error": "FREE_TIER_EXHAUSTED"}；调用成功后才递增计数器（失败的调用不消耗额度）。
# 4. call_claude_model() 内部把余额耗尽（实测 400 + invalid_request_error + message
#    含 "credit balance"，billing_error 仅作兼容兜底）翻译成
#    error_code == 'SERVER_CREDITS_EXHAUSTED'，这里据此转换为统一的 JSON 错误体。
# 5. 可选的请求体字段 history_id（2026-07-05 新增，见 _append_claude_result_to_history()
#    上方注释）：非空时，本次调用实际发生（即越过了 FREE_TIER_EXHAUSTED 拦截）后，
#    无论成功/失败都把这次的 Claude Result 追加进该 history_id 对应的对话历史记录，
#    修复此前"Claude 结果在页面上看得见，重新打开 /history/<id> 却不见了"的问题。
# ==================================================
@app.route('/api/claude-chat', methods=['POST'])
def claude_chat():
    try:
        if not CLAUDE_AVAILABLE:
            return jsonify({
                'error': 'CLAUDE_UNAVAILABLE',
                'message': 'Claude integration is not available on this server.'
            }), 503

        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        prompt = data.get('prompt', '').strip()
        model_key = data.get('model')
        history_id = data.get('history_id')
        request_id = data.get('request_id')

        if not prompt or model_key not in CLAUDE_MODELS:
            return jsonify({
                'error': 'INVALID_REQUEST',
                'message': 'A non-empty prompt and a valid Claude model are required.'
            }), 400

        user_api_key = request.headers.get('X-User-Claude-Key', '').strip()
        using_own_key = bool(user_api_key)

        if not using_own_key:
            usage = get_claude_free_tier_usage(user_id)
            if usage >= CLAUDE_FREE_TIER_LIMIT:
                return jsonify({'error': 'FREE_TIER_EXHAUSTED'}), 403

        result = call_claude_model(prompt, model_key, user_api_key if using_own_key else None)

        if result.get('error_code') == 'SERVER_CREDITS_EXHAUSTED':
            # using_own_key 分支下"余额耗尽"指的是用户自己的 Key，跟开发者账户无关；
            # 走开发者账户这条路径时，能到这里说明前面的免费额度检查已经放行
            # （usage < CLAUDE_FREE_TIER_LIMIT），即用户的试用额度还有剩余——问题出在
            # 供给侧（开发者账户没钱了），不是用户的额度用完了，所以文案指向联系
            # 开发者，而不是重复"配置个人 Key"这条对试用额度耗尽场景才对症的建议。
            if using_own_key:
                friendly_message = 'Your personal Claude API key has run out of credits. Please check your Anthropic account balance.'
            else:
                friendly_message = (
                    "Your free trial quota still has uses left, but the developer's Claude "
                    "API account has run out of credits. Please contact the developer to restore access."
                )
            # 追加进历史的版本要和用户在页面上实际看到的卡片一致——result['error'] 此时
            # 仍是内部的原始标记字符串 'SERVER_CREDITS_EXHAUSTED'（不是这条友好文案），
            # error_code 字段本身也不属于 Claude Result 的 6-key 契约，两者都不应该
            # 原样存进 Firestore。
            history_result = {k: v for k, v in result.items() if k != 'error_code'}
            history_result['error'] = friendly_message
            _append_claude_result_to_history(user_id, history_id, history_result)
            return jsonify({
                'error': 'SERVER_CREDITS_EXHAUSTED',
                'message': friendly_message
            }), 503

        if result['success'] and not using_own_key:
            try:
                increment_claude_free_tier_usage(user_id)
                # 只在真正递增成功之后才记账，供"Stop Generating"退款接口核销
                # （见 _record_pending_frontier_refund() 上方注释）。
                _record_pending_frontier_refund(request_id, user_id, 'claude')
            except Exception as e:
                logger.error(
                    f"Failed to increment claude_free_tier_usage for {user_id}: {e}",
                    exc_info=True
                )

        _append_claude_result_to_history(user_id, history_id, result)

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error in claude_chat: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# POST /api/claude-chat/refund（2026-07-05 新增，"Stop Generating"按钮配套接口）
#
# 前端在用户点击 Stop Generating 时，如果本次 Claude 调用已经发起（走到了
# fetchClaudeResult()），会在 abort 之后带着同一个 request_id 调用这个接口。只有
# claude_chat() 真的成功递增过免费额度、且账本里 request_id 还没被核销过时才会真的
# 退 1 次——账本没有命中（比如额度检查阶段就被 abort、自带 Key 调用、或者已经退过一次）
# 时是无副作用的空操作，不会返回错误，前端不需要特殊处理。见
# _consume_pending_frontier_refund() 上方注释：这个设计不给"反复调用本接口刷回额度"
# 留任何空间。
# ==================================================
@app.route('/api/claude-chat/refund', methods=['POST'])
def claude_chat_refund():
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        request_id = data.get('request_id')

        refunded = False
        if _consume_pending_frontier_refund(request_id, user_id, 'claude'):
            try:
                refunded = bool(decrement_claude_free_tier_usage(user_id))
            except Exception as e:
                logger.error(
                    f"Failed to decrement claude_free_tier_usage for {user_id}: {e}",
                    exc_info=True
                )

        return jsonify({'refunded': refunded})

    except Exception as e:
        logger.error(f"Error in claude_chat_refund: {str(e)}", exc_info=True)
        return jsonify({'refunded': False}), 500


# ==================================================
# 核心接口：并发调用多个文生图 Provider 生成图片
# POST /api/generate-images
#
# 与 compare_providers() 是同一种"并发调度 + 逐个兜底"骨架，但只有一个阶段
# （没有互评），且调用的是 test_g4f_image_provider()（images.generate()）而非
# test_g4f_provider()（ChatCompletion.create()）。已登录用户的图片结果持久化到独立的
# 'image_history' Firestore 集合（save_image_history()，2026-07-04 新增）——不复用
# 'history' 集合，因为其文档结构是围绕文本 7-key result DTO（+ peer_reviews）设计的，
# 混入 8-key 图片 DTO 需要引入判别字段；游客与匿名用户的图片生成结果依然不落库，
# 且刻意不提供 sessionStorage 级别的临时记录（与文本对话历史对游客的处理不同）——
# 图片版 Recents 侧边栏专门限定只对已登录用户开放。
#
# 本路由**不再**做任何 get_media_dir() 本地文件清理（2026-07-05 移除，此前是
# cleanup_old_generated_media()/GENERATED_MEDIA_MAX_AGE_SECONDS 这套按年龄惰性清理
# 机制，见 CLAUDE.md 第 9 节该事故的完整记录）——图片版 Recents 历史需要"永久可查看"，
# 而历史详情页 <img> 标签引用的正是这些本地文件，按年龄自动删除会让超过 1 小时的
# 历史记录里的图片变成 404。刻意不做任何清理，接受"本地磁盘随请求量持续增长"这一
# 已知代价（见 CLAUDE.md 第 9 节风险，需要真正的长期解决方案时应迁移到 Cloud Storage
# 等持久化存储，而不是重新引入按年龄删除）。
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

        # 已登录用户持久化图片生成历史（独立的 'image_history' 集合，见 auth/db.py
        # 顶部注释）；游客与匿名不落库——图片版 Recents 侧边栏专门限定只有已登录用户
        # 才能使用，游客侧连"仅浏览器内存/sessionStorage"级别的临时记录都不提供
        # （与文本对话历史对游客的处理方式刻意不同，见 CLAUDE.md 第 6 节）。
        # 持久化失败不影响本次生成结果返回，独立 try/except。
        history_id = None
        if session.get('user_id'):
            try:
                saved = save_image_history(session['user_id'], prompt, results)
                if saved:
                    history_id = saved['id']
            except Exception as e:
                logger.error(f"Failed to save image history: {e}", exc_info=True)

        return jsonify({
            'prompt': prompt,
            'total_providers': len(results),
            'successful_providers': successful_count,
            'results': results,
            'history_id': history_id
        })

    except Exception as e:
        logger.error(f"Error in generate_images: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# 官方 Google Gemini（"Nano Banana"）文生图接口
# POST /api/gemini-image
#
# 与 /api/generate-images 完全独立的第四条路由——不经过 ThreadPoolExecutor 并发调度、
# 不参与 g4f 图片 Provider 的重试/超时预算逻辑，单次请求只调用一个 Gemini 模型。权限
# 与成本控制（防薅羊毛）核心与 /api/claude-chat 逐一同构：
# 1. 游客/匿名一律 401（复用 _get_authenticated_user_id，与 Claude/对话/图片历史路由
#    同一套守卫）——前端会把 Gemini 勾选框置灰，这里是后端侧的第二层防御。
# 2. Header 里携带非空 X-User-Gemini-Key 时，优先使用该 Key 实例化客户端，完全不
#    检查/消耗免费额度计数器。
# 3. 未携带自带 Key 时，检查该用户的 gemini_free_tier_usage 是否已达到
#    GEMINI_FREE_TIER_LIMIT（当前为 10）；达到则直接拦截、不调用开发者 API，返回
#    {"error": "FREE_TIER_EXHAUSTED"}；调用成功后才递增计数器（失败的调用不消耗额度）。
# 4. call_gemini_image_model() 内部把配额耗尽（429/RESOURCE_EXHAUSTED）翻译成
#    error_code == 'SERVER_QUOTA_EXHAUSTED'，这里据此转换为统一的 JSON 错误体。
# 5. 可选的请求体字段 history_id（2026-07-05 新增，与 claude_chat() 的 history_id
#    同构，见 _append_gemini_result_to_image_history() 上方注释）：非空时，本次调用
#    实际发生（即越过了 FREE_TIER_EXHAUSTED 拦截）后，无论成功/失败都把这次的
#    Gemini Image Result 追加进该 history_id 对应的图片生成历史记录，修复此前
#    "Gemini 结果在页面上看得见、能下载，重新打开 /image-history/<id> 却不见了"
#    的问题。
#
# 与 generate_images() 的关键不同：这里**不会**调用 save_image_history() 创建新的
# image_history 文档——Gemini 依旧不能像 g4f 图片 Provider 那样自己发起一条新的历史
# 记录，只能通过上面第 5 点追加进一条已经由 /api/generate-images 创建好的记录。
# ==================================================
@app.route('/api/gemini-image', methods=['POST'])
def gemini_image_chat():
    try:
        if not GEMINI_AVAILABLE:
            return jsonify({
                'error': 'GEMINI_UNAVAILABLE',
                'message': 'Gemini integration is not available on this server.'
            }), 503

        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        prompt = data.get('prompt', '').strip()
        model_key = data.get('model')
        history_id = data.get('history_id')
        request_id = data.get('request_id')

        if not prompt or model_key not in GEMINI_IMAGE_MODELS:
            return jsonify({
                'error': 'INVALID_REQUEST',
                'message': 'A non-empty prompt and a valid Gemini model are required.'
            }), 400

        user_api_key = request.headers.get('X-User-Gemini-Key', '').strip()
        using_own_key = bool(user_api_key)

        if not using_own_key:
            usage = get_gemini_free_tier_usage(user_id)
            if usage >= GEMINI_FREE_TIER_LIMIT:
                return jsonify({'error': 'FREE_TIER_EXHAUSTED'}), 403

        result = call_gemini_image_model(prompt, model_key, user_api_key if using_own_key else None)

        if result.get('error_code') == 'SERVER_QUOTA_EXHAUSTED':
            # 与 claude_chat() 的 SERVER_CREDITS_EXHAUSTED 分支同理：using_own_key 时
            # 耗尽的是用户自己的 Key，跟开发者账户无关；走开发者账户这条路径时，能到
            # 这里说明免费额度检查已经放行（试用额度还有剩余），问题出在供给侧，文案
            # 指向联系开发者。
            if using_own_key:
                friendly_message = 'Your personal Gemini API key has run out of quota. Please check your Google AI account.'
            else:
                friendly_message = (
                    "Your free trial quota still has uses left, but the developer's Gemini "
                    "API account has run out of quota. Please contact the developer to restore access."
                )
            # 追加进历史的版本要和用户实际看到的卡片一致，剥掉 error_code（不属于
            # Gemini Image Result 契约）并把 error 换成友好文案，而不是内部标记字符串。
            history_result = {k: v for k, v in result.items() if k != 'error_code'}
            history_result['error'] = friendly_message
            _append_gemini_result_to_image_history(user_id, history_id, history_result)
            return jsonify({
                'error': 'SERVER_QUOTA_EXHAUSTED',
                'message': friendly_message
            }), 503

        if result['success'] and not using_own_key:
            try:
                increment_gemini_free_tier_usage(user_id)
                # 只在真正递增成功之后才记账，供"Stop Generating"退款接口核销
                # （见 _record_pending_frontier_refund() 上方注释）。
                _record_pending_frontier_refund(request_id, user_id, 'gemini')
            except Exception as e:
                logger.error(
                    f"Failed to increment gemini_free_tier_usage for {user_id}: {e}",
                    exc_info=True
                )

        _append_gemini_result_to_image_history(user_id, history_id, result)

        return jsonify(result)

    except Exception as e:
        logger.error(f"Error in gemini_image_chat: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# POST /api/gemini-image/refund（2026-07-05 新增），与 /api/claude-chat/refund 同构，
# 见其上方注释。
# ==================================================
@app.route('/api/gemini-image/refund', methods=['POST'])
def gemini_image_refund():
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        request_id = data.get('request_id')

        refunded = False
        if _consume_pending_frontier_refund(request_id, user_id, 'gemini'):
            try:
                refunded = bool(decrement_gemini_free_tier_usage(user_id))
            except Exception as e:
                logger.error(
                    f"Failed to decrement gemini_free_tier_usage for {user_id}: {e}",
                    exc_info=True
                )

        return jsonify({'refunded': refunded})

    except Exception as e:
        logger.error(f"Error in gemini_image_refund: {str(e)}", exc_info=True)
        return jsonify({'refunded': False}), 500


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
# 文生图历史：分页查询
# GET /api/image-history?page=1&limit=20
#
# 与对话历史的 4 个 /api/history* 路由逐一同构，同样通过 _get_authenticated_user_id()
# 守卫——游客与匿名一律 401（该守卫本就不区分"聊天"还是"图片"，无需改动即可复用）。
# ==================================================
@app.route('/api/image-history', methods=['GET'])
def get_image_history():
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        page = request.args.get('page', 1, type=int) or 1
        limit = request.args.get('limit', 20, type=int) or 20
        page = max(page, 1)
        limit = max(min(limit, 100), 1)
        offset = (page - 1) * limit

        history_list = get_image_history_list(user_id, limit=limit, offset=offset)
        return jsonify({
            'history': history_list,
            'page': page,
            'limit': limit
        })

    except Exception as e:
        logger.error(f"Error in get_image_history: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# 文生图历史：重命名
# PATCH /api/image-history/<history_id>/title
# ==================================================
@app.route('/api/image-history/<history_id>/title', methods=['PATCH'])
def update_image_history_title_route(history_id):
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        data = request.get_json() or {}
        new_title = data.get('new_title', '').strip()
        if not new_title:
            return jsonify({'error': 'new_title is required'}), 400

        success = update_image_history_title(user_id, history_id, new_title)
        if not success:
            return jsonify({'error': 'History entry not found'}), 404

        return jsonify({'status': 'ok'})

    except Exception as e:
        logger.error(f"Error in update_image_history_title_route: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# 文生图历史：删除
# DELETE /api/image-history/<history_id>
# ==================================================
@app.route('/api/image-history/<history_id>', methods=['DELETE'])
def delete_image_history_route(history_id):
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        success = delete_image_history(user_id, history_id)
        if not success:
            return jsonify({'error': 'History entry not found'}), 404

        return jsonify({'status': 'ok'})

    except Exception as e:
        logger.error(f"Error in delete_image_history_route: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# 文生图历史：切换置顶状态
# POST /api/image-history/<history_id>/toggle-pin
# ==================================================
@app.route('/api/image-history/<history_id>/toggle-pin', methods=['POST'])
def toggle_image_history_pin_route(history_id):
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        new_pinned = toggle_pin_image_history(user_id, history_id)
        if new_pinned is None:
            return jsonify({'error': 'History entry not found'}), 404

        return jsonify({'is_pinned': new_pinned})

    except Exception as e:
        logger.error(f"Error in toggle_image_history_pin_route: {str(e)}", exc_info=True)
        return jsonify({
            'error': 'Service temporarily unavailable. Please try again later.'
        }), 500


# ==================================================
# 前沿模型（Claude/Gemini）试用额度查询
# GET /api/quota-status
#
# 供前端导航栏 "Trial Quota" 徽章使用（2026-07-05 新增）：页面加载后已经通过
# index() 注入了初始值（见该路由），这个接口用于每次调用 /api/claude-chat 或
# /api/gemini-image 之后刷新徽章数字，避免前端自行猜测"这次调用是否消耗了额度"
# （自带 Key/失败调用都不消耗额度，直接问后端真实计数比在前端复刻这套判断逻辑更可靠）。
# 复用 _get_authenticated_user_id() 守卫——游客/匿名一律 401，因为 Claude/Gemini
# 本来就不对游客开放，没有额度可查（与 Claude/Gemini/对话/图片历史路由同一套守卫）。
# 两个计数器完全独立返回，互不共享额度，与 CLAUDE.md 第 6/7 节记录的语义一致。
# ==================================================
@app.route('/api/quota-status', methods=['GET'])
def quota_status():
    try:
        user_id, err_response = _get_authenticated_user_id()
        if err_response:
            return err_response

        return jsonify({
            'claude': {
                'used': get_claude_free_tier_usage(user_id),
                'limit': CLAUDE_FREE_TIER_LIMIT,
            },
            'gemini': {
                'used': get_gemini_free_tier_usage(user_id),
                'limit': GEMINI_FREE_TIER_LIMIT,
            },
        })

    except Exception as e:
        logger.error(f"Error in quota_status: {str(e)}", exc_info=True)
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
        'claude_available': CLAUDE_AVAILABLE,
        'claude_models': list(CLAUDE_MODELS.keys()),
        'gemini_available': GEMINI_AVAILABLE,
        'gemini_models': list(GEMINI_IMAGE_MODELS.keys()),
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