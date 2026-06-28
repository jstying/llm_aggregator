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

except ImportError as e:
    G4F_AVAILABLE = False
    G4F_PROVIDERS = []
    PROVIDER_MODELS_MAP = {}
    logger.warning(f"g4f not available: {e}")

except Exception as e:
    G4F_AVAILABLE = False
    G4F_PROVIDERS = []
    PROVIDER_MODELS_MAP = {}
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
        # 调用大模型
        response = g4f.ChatCompletion.create(
            model=actual_model,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            provider=provider,
            timeout=20
        )

        result['success'] = True
        result['response'] = str(response)

    except Exception as e:
        result['error'] = str(e)

    finally:
        result['response_time'] = round(
            time.time() - start_time,
            2
        )

    return result


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
                    fallback_result['error'] = f'Execution error: {str(e)}'
                    results.append(fallback_result)
                    logger.error(f"Error testing {name}: {e}", exc_info=True)

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