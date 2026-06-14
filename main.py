# Flask Web框架相关模块
from flask import Flask, request, jsonify, render_template

# 计时模块（统计模型响应时间）
import time

# 打印详细异常堆栈
import traceback

# 日志模块
import logging

# 读取环境变量
import os

# 用于并发执行多个Provider请求
from concurrent.futures import ThreadPoolExecutor


# 配置日志级别
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 创建Flask应用
app = Flask(__name__)


# =========================
# 初始化 g4f Provider
# =========================
try:
    import g4f

    G4F_AVAILABLE = True
    logger.info("g4f imported successfully")

    # 当前支持测试的Provider
    G4F_PROVIDERS = [
        g4f.Provider.Yqcloud,
        g4f.Provider.OpenRouterFree,
    ]

    # Provider对应模型映射
    PROVIDER_MODELS = {
        'Yqcloud': 'gpt-3.5-turbo',
        'OpenRouterFree': 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free',
    }

except ImportError as e:
    # g4f未安装
    G4F_AVAILABLE = False
    G4F_PROVIDERS = []
    PROVIDER_MODELS = {}
    logger.warning(f"g4f not available: {e}")

except Exception as e:
    # 其它初始化错误
    G4F_AVAILABLE = False
    G4F_PROVIDERS = []
    PROVIDER_MODELS = {}
    logger.warning(f"g4f initialization failed: {e}")


# ==================================================
# 测试单个Provider
# 功能：
# 1. 调用指定Provider
# 2. 记录是否成功
# 3. 记录回复内容
# 4. 统计响应时间
# ==================================================
def test_g4f_provider(provider, prompt, model="gpt-3.5-turbo"):

    # 获取当前Provider对应模型
    actual_model = PROVIDER_MODELS.get(provider.__name__, model)

    start_time = time.time()

    # 结果对象
    result = {
        'provider': provider.__name__,
        'success': False,
        'response': '',
        'error': '',
        'response_time': 0,
        'model': actual_model,
        'type': 'g4f'
    }

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
        # 记录错误信息
        result['error'] = str(e)

    finally:
        # 记录耗时
        result['response_time'] = round(
            time.time() - start_time,
            2
        )

    return result


# ==================================================
# 首页
# 返回前端页面
# ==================================================
@app.route('/')
def index():
    return render_template('index.html')


# ==================================================
# 获取所有可用Provider
# GET /api/providers
# ==================================================
@app.route('/api/providers', methods=['GET'])
def get_providers():

    if not G4F_AVAILABLE:
        return jsonify([])

    provider_list = []

    for p in G4F_PROVIDERS:
        provider_list.append({
            'name': p.__name__,
            'model': PROVIDER_MODELS.get(
                p.__name__,
                'unknown'
            ),
            'type': 'g4f',
            'status': 'available'
        })

    return jsonify(provider_list)


# ==================================================
# 核心接口：
# 同时比较多个Provider
#
# POST /api/compare
#
# 输入Prompt
# 并发调用多个Provider
# 返回：
#   回复内容
#   是否成功
#   响应时间
# ==================================================
@app.route('/api/compare', methods=['POST'])
def compare_providers():

    try:
        data = request.get_json()

        # 检查Prompt
        if not data or 'prompt' not in data:
            return jsonify({
                'error': 'Prompt is required'
            }), 400

        # 检查g4f状态
        if not G4F_AVAILABLE:
            return jsonify({
                'error': 'g4f is not available'
            }), 503

        prompt = data['prompt']

        # 用户选中的Provider
        selected_providers = data.get(
            'providers',
            []
        )

        # 默认模型
        model = data.get(
            'model',
            'gpt-3.5-turbo'
        )

        # 最大线程数（限制最多5个）
        max_workers = min(
            data.get('max_workers', 3),
            5
        )

        logger.info(
            f"Comparing providers for prompt: "
            f"{prompt[:50]}..."
        )

        # 如果用户指定Provider
        if selected_providers:

            providers_to_test = [
                p
                for p in G4F_PROVIDERS
                if p.__name__ in selected_providers
            ]

        else:
            providers_to_test = G4F_PROVIDERS

        # 没找到合法Provider
        if not providers_to_test:
            return jsonify({
                'error': 'No valid providers found'
            }), 400

        results = []

        # ==================================================
        # 并发执行Provider请求
        # ==================================================
        with ThreadPoolExecutor(
            max_workers=min(
                max_workers,
                len(providers_to_test)
            )
        ) as executor:

            futures = {
                executor.submit(
                    test_g4f_provider,
                    p,
                    prompt,
                    model
                ): p
                for p in providers_to_test
            }

            # 收集结果
            for future, provider in futures.items():

                try:
                    result = future.result(timeout=25)

                    results.append(result)

                    logger.info(
                        f"Completed: "
                        f"{result['provider']} "
                        f"success={result['success']}"
                    )

                except Exception as e:

                    results.append({
                        'provider': provider.__name__,
                        'success': False,
                        'response': '',
                        'error': f'Execution error: {str(e)}',
                        'response_time': 0,
                        'model': PROVIDER_MODELS.get(
                            provider.__name__,
                            model
                        ),
                        'type': 'g4f'
                    })

                    logger.error(
                        f"Error testing "
                        f"{provider.__name__}: {e}"
                    )

        # ==================================================
        # 排序：
        # 成功优先
        # 速度快优先
        # ==================================================
        results.sort(
            key=lambda x: (
                not x['success'],
                x['response_time']
            )
        )

        successful_count = sum(
            1
            for r in results
            if r['success']
        )

        logger.info(
            f"Comparison complete: "
            f"{successful_count}/{len(results)} successful"
        )

        return jsonify({
            'prompt': prompt,
            'total_providers': len(results),
            'successful_providers': successful_count,
            'results': results
        })

    except Exception as e:

        logger.error(
            f"Error in compare_providers: {str(e)}"
        )

        logger.error(
            traceback.format_exc()
        )

        return jsonify({
            'error':
            f'Internal server error: {str(e)}'
        }), 500


# ==================================================
# 测试单个Provider
#
# POST /api/test-single
# ==================================================
@app.route('/api/test-single', methods=['POST'])
def test_single_provider():

    try:
        data = request.get_json()

        if (
            not data
            or 'prompt' not in data
            or 'provider' not in data
        ):
            return jsonify({
                'error':
                'Prompt and provider are required'
            }), 400

        if not G4F_AVAILABLE:
            return jsonify({
                'error':
                'g4f is not available'
            }), 503

        prompt = data['prompt']
        provider_name = data['provider']

        model = data.get(
            'model',
            'gpt-3.5-turbo'
        )

        # 查找对应Provider
        provider = next(
            (
                p
                for p in G4F_PROVIDERS
                if p.__name__ == provider_name
            ),
            None
        )

        if not provider:
            return jsonify({
                'error':
                f'Provider "{provider_name}" not found'
            }), 404

        result = test_g4f_provider(
            provider,
            prompt,
            model
        )

        return jsonify(result)

    except Exception as e:

        logger.error(
            f"Error in test_single_provider: {str(e)}"
        )

        return jsonify({
            'error':
            f'Internal server error: {str(e)}'
        }), 500


# ==================================================
# 健康检查接口
#
# GET /health
# ==================================================
@app.route('/health')
def health_check():

    return jsonify({
        'status': 'healthy',
        'g4f_available': G4F_AVAILABLE,
        'providers': [
            p.__name__
            for p in G4F_PROVIDERS
        ],
        'timestamp': time.time()
    })


# ==================================================
# 404错误处理
# ==================================================
@app.errorhandler(404)
def not_found(error):
    return jsonify({
        'error': 'Not found'
    }), 404


# ==================================================
# 500错误处理
# ==================================================
@app.errorhandler(500)
def internal_error(error):

    logger.error(
        f"Internal server error: {error}"
    )

    return jsonify({
        'error': 'Internal server error'
    }), 500


# ==================================================
# 程序入口
# ==================================================
if __name__ == '__main__':

    # Render/Railway等平台会注入PORT环境变量
    port = int(
        os.environ.get(
            'PORT',
            8080
        )
    )

    # 启动Flask服务
    app.run(
        debug=False,
        host='0.0.0.0',
        port=port
    )