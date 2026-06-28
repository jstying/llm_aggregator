# 先加载环境变量
from dotenv import load_dotenv
load_dotenv()

# Flask Web框架相关模块
from flask import Flask, request, jsonify, render_template, redirect, session, url_for

# 计时模块（统计模型响应时间）
import time

# 打印详细异常堆栈
import traceback

# 日志模块
import logging

# 读取环境变量
import os
import secrets
import re
import json

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


# =========================
# 初始化 g4f Provider
# =========================
try:
    import g4f

    G4F_AVAILABLE = True
    logger.info("g4f imported successfully")

    # 当前支持的 Provider 列表
    G4F_PROVIDERS = [
        g4f.Provider.Yqcloud,
        g4f.Provider.OperaAria,
    ]

    # 配置映射表：一个 Provider 对应一个模型列表
    # 列表中的第一个模型会被当作该 Provider 的默认模型
    PROVIDER_MODELS_MAP = {
        'Yqcloud': ['gpt-3.5-turbo', 'gpt-4'],
        'OperaAria': ['aria'],
    }

    # 隐形 Prompt 路由表：(provider_name, model) → 追加到用户 prompt 尾部的 Style Prompt
    ROUTE_PROMPTS_MAP = {
        ('Yqcloud', 'gpt-4'): '\n\n【系统提示：请从底层逻辑深入剖析，注重结构化表达，给出最严谨的步骤或推导。】',
        ('Yqcloud', 'gpt-3.5-turbo'): '\n\n【系统提示：请用最精炼、通俗的语言快速回答，突出核心要点，字数控制在 200 字内。】',
        ('OperaAria', 'aria'): '\n\n【系统提示：请结合最新行业现状与实用应用场景，给出最具实操性的建议。】',
    }

    # 互评裁判提示词配置表：model → 裁判专属提示词前缀（要求输出 JSON 格式）
    PEER_REVIEW_PROMPTS_MAP = {
        'gpt-4': '你现在是盲评裁判。请严谨审视以下匿名回答，挑出逻辑漏洞、事实错误或论证不充分之处。请严格按照以下 JSON 格式输出，不要包含任何其他文字：{"score": 分数(1-100的整数), "comment": "一句话犀利点评"}',
        'gpt-3.5-turbo': '请迅速评估以下匿名回答的条理性和易读性。请严格按照以下 JSON 格式输出，不要包含任何其他文字：{"score": 分数(1-100的整数), "comment": "一句话效率至上的改稿意见"}',
        'aria': '请从实用角度点评以下匿名回答，指出其接地气程度与可操作性。请严格按照以下 JSON 格式输出，不要包含任何其他文字：{"score": 分数(1-100的整数), "comment": "一句话大白话吐槽"}',
    }

except ImportError as e:
    G4F_AVAILABLE = False
    G4F_PROVIDERS = []
    PROVIDER_MODELS_MAP = {}
    ROUTE_PROMPTS_MAP = {}
    PEER_REVIEW_PROMPTS_MAP = {}
    logger.warning(f"g4f not available: {e}")

except Exception as e:
    G4F_AVAILABLE = False
    G4F_PROVIDERS = []
    PROVIDER_MODELS_MAP = {}
    ROUTE_PROMPTS_MAP = {}
    PEER_REVIEW_PROMPTS_MAP = {}
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


# ==================================================
# 辅助函数：检测重复文本并截断，同时过滤敏感内容
# ==================================================
def detect_and_truncate(text):
    for kw in SENSITIVE_KEYWORDS:
        if kw in text:
            return "内容涉及敏感信息，已拦截。"

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
                return text[:third_pos] + '...（因文本重复已被系统自动截断）'

    # --- 滑动窗口短串重复检测（窗口 8~50 字符，覆盖短句/词组抽风）---
    for win in range(8, min(51, n // 3 + 1)):
        for i in range(n - win * 3 + 1):
            chunk = text[i:i + win]
            if (text[i + win:i + win * 2] == chunk and
                    text[i + win * 2:i + win * 3] == chunk):
                return text[:i + win * 2] + '...（因文本重复已被系统自动截断）'

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
        network_keywords = [
            'timeout', 'timed out', 'connection', 'network', 'remote',
            '502', '504', 'rate limit', 'too many requests', 'unavailable',
            'ssl', 'broken pipe', 'connection reset',
        ]
        if any(kw in err_str for kw in network_keywords):
            result['error'] = '系统正忙，正在努力重新连接中，请稍后再试。'
        else:
            result['error'] = str(e)

    finally:
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
    try:
        response = g4f.ChatCompletion.create(
            model=reviewer_model,
            messages=[{"role": "user", "content": review_prompt}],
            provider=reviewer_provider,
            timeout=15
        )
        score, comment = parse_peer_review_json(detect_and_truncate(str(response)))
        review_result['score'] = score
        review_result['comment'] = comment
    except Exception as e:
        err_str = str(e).lower()
        network_keywords = [
            'timeout', 'timed out', 'connection', 'network', 'remote',
            '502', '504', 'rate limit', 'too many requests', 'unavailable',
            'ssl', 'broken pipe', 'connection reset',
        ]
        if any(kw in err_str for kw in network_keywords):
            review_result['comment'] = '系统正忙，正在努力重新连接中，请稍后再试。'
        else:
            review_result['comment'] = f'点评失败：{str(e)}'
    return review_result


# ==================================================
# 首页（通过 Jinja2 传递严谨的联动数据映射）
# ==================================================
@app.route('/')
def index():
    if not session.get('user_id') and not session.get('is_guest'):
        return render_template('home.html')

    if not G4F_AVAILABLE:
        return render_template('index.html', providers=[], provider_models_json={})

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

    # 将结构化数据注入前端
    return render_template(
        'index.html',
        providers=provider_list,
        provider_models_json=provider_models_json
    )


@app.route('/home')
def home():
    session.pop('is_guest', None)
    return redirect(url_for('index'))


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
                        fallback_result['error'] = '系统正忙，正在努力重新连接中，请稍后再试。'
                        logger.warning(f"Provider {name} timed out after 21s")
                    else:
                        fallback_result['error'] = f'Execution error: {str(e)}'
                        logger.error(f"Error testing {name}: {e}", exc_info=True)
                    results.append(fallback_result)

        # 为所有结果初始化空的互评列表
        for r in results:
            r['peer_reviews'] = []

        # 互评触发条件：providers_to_test >= 2 且成功结果 >= 2
        successful_results = [r for r in results if r['success']]
        if len(providers_to_test) >= 2 and len(successful_results) >= 2:
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
                        '请评估以下回答的质量，指出优点与不足。'
                    )
                    review_prompt = (
                        f"{judge_prefix}\n\n需要点评的匿名文本如下：\n{result_a['response']}"
                    )
                    peer_review_tasks.append(
                        (reviewer_provider, reviewer_model, review_prompt, result_a['provider'])
                    )

            max_peer_workers = min(10, len(peer_review_tasks))
            with ThreadPoolExecutor(max_workers=max_peer_workers) as peer_executor:
                peer_futures = {
                    peer_executor.submit(run_peer_review, rp, rm, rpr): target
                    for rp, rm, rpr, target in peer_review_tasks
                }
                for future, target_provider in peer_futures.items():
                    try:
                        review_item = future.result(timeout=25)
                        for r in results:
                            if r['provider'] == target_provider:
                                r['peer_reviews'].append(review_item)
                                break
                    except Exception as e:
                        logger.error(
                            f"Peer review error targeting {target_provider}: {e}",
                            exc_info=True
                        )

        # 排序：成功优先，耗时短优先
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
        logger.error(f"Error in compare_providers: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            'error': f'Internal server error: {str(e)}'
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
        logger.error(f"Error in test_single_provider: {str(e)}")
        return jsonify({
            'error': f'Internal server error: {str(e)}'
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
        'timestamp': time.time()
    })


@app.route('/api/auth/guest', methods=['POST'])
def guest_login():
    session['is_guest'] = True
    return jsonify({'status': 'ok'})


@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error(f"Internal server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=False, host='0.0.0.0', port=port)