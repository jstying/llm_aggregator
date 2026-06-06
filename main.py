from flask import Flask, request, jsonify, render_template  # 导入 Flask 框架所需的类和函数
import time  # 导入时间模块用于计算响应时间
import traceback  # 导入用于输出详细异常堆栈的模块
import json  # 导入 JSON 处理模块
import logging  # 导入日志记录模块
import os  # 导入操作系统接口模块
from concurrent.futures import ThreadPoolExecutor  # 导入线程池，用于并发执行任务
import requests  # 导入 HTTP 请求库
import threading  # 导入线程模块

# 配置日志记录，设置日志级别为 INFO
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)  # 创建 Flask 应用实例

# 定义模拟提供商列表，用于测试（实际环境中可替换为真实的 g4f 服务）
PROVIDERS = [
    {'name': 'OpenAI_GPT35', 'url': 'https://api.openai.com/v1/chat/completions'},
    {'name': 'Anthropic_Claude', 'url': 'https://api.anthropic.com/v1/messages'},
    {'name': 'Google_Bard', 'url': 'https://generativelanguage.googleapis.com/v1/models'},
    {'name': 'Cohere_Command', 'url': 'https://api.cohere.ai/v1/generate'},
    {'name': 'Hugging_Face', 'url': 'https://api-inference.huggingface.co/models'},
    {'name': 'Replicate_AI', 'url': 'https://api.replicate.com/v1/predictions'},
]

# 尝试导入 g4f 库，如果失败则在不影响程序运行的情况下捕获异常
try:
    import g4f
    G4F_AVAILABLE = True  # 设置标志位表示 g4f 可用
    logger.info("g4f imported successfully")
    
    # 定义 g4f 实际可用的提供商列表
    G4F_PROVIDERS = [
        g4f.Provider.Bing,
        g4f.Provider.You,
        g4f.Provider.Aichat,
        g4f.Provider.ChatBase,
        g4f.Provider.Vercel,
    ]
except ImportError as e:
    G4F_AVAILABLE = False
    G4F_PROVIDERS = []
    logger.warning(f"g4f not available: {e}")
except Exception as e:
    G4F_AVAILABLE = False
    G4F_PROVIDERS = []
    logger.warning(f"g4f initialization failed: {e}")

def test_g4f_provider(provider, prompt, model="gpt-3.5-turbo"):
    """使用给定的提示词测试 g4f 提供商"""
    start_time = time.time()  # 记录测试开始时间
    result = {
        'provider': provider.__name__,
        'success': False,
        'response': '',
        'error': '',
        'response_time': 0,
        'model': model,
        'type': 'g4f'
    }
    
    try:
        # 调用 g4f 创建聊天补全请求
        response = g4f.ChatCompletion.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            provider=provider,
            timeout=20
        )
        
        result['success'] = True
        result['response'] = str(response)
        result['response_time'] = round(time.time() - start_time, 2)  # 计算并保留两位小数的响应时间
        
    except Exception as e:
        result['error'] = str(e)
        result['response_time'] = round(time.time() - start_time, 2)
    
    return result

def test_mock_provider(provider, prompt, model="gpt-3.5-turbo"):
    """测试模拟提供商（仅供演示使用）"""
    start_time = time.time()
    result = {
        'provider': provider['name'],
        'success': False,
        'response': '',
        'error': '',
        'response_time': 0,
        'model': model,
        'type': 'mock'
    }
    
    try:
        # 导入随机库以模拟不同响应时间和成功率
        import random
        time.sleep(random.uniform(0.5, 3.0))  # 模拟处理延迟
        
        # 随机设置 70% 的成功率以供演示
        if random.random() > 0.3:
            mock_responses = [
                f"This is a response from {provider['name']} to your prompt: '{prompt[:50]}...' using model {model}.",
                f"According to {provider['name']}, here's what I found about your query: {prompt[:30]}...",
                f"{provider['name']} suggests that your question about '{prompt[:40]}...' requires careful consideration.",
                f"From {provider['name']}: Your prompt '{prompt[:35]}...' is interesting. Let me provide some insights.",
                f"Response from {provider['name']}: Based on your request '{prompt[:45]}...', here's my analysis."
            ]
            result['success'] = True
            result['response'] = random.choice(mock_responses)
        else:
            errors = ["Rate limit exceeded", "Provider temporarily unavailable", "Connection timeout", "Authentication failed", "Service overloaded"]
            result['error'] = random.choice(errors)
        
        result['response_time'] = round(time.time() - start_time, 2)
        
    except Exception as e:
        result['error'] = str(e)
        result['response_time'] = round(time.time() - start_time, 2)
    
    return result

@app.route('/')
def index():
    """提供主页面渲染"""
    return render_template('index.html')

@app.route('/api/providers', methods=['GET'])
def get_providers():
    """获取所有可用提供商的列表"""
    provider_list = []
    
    # 如果 g4f 可用，将 g4f 提供商添加到列表
    if G4F_AVAILABLE:
        for p in G4F_PROVIDERS:
            provider_list.append({
                'name': p.__name__,
                'type': 'g4f',
                'status': 'available'
            })
    
    # 将模拟提供商添加到列表
    for p in PROVIDERS:
        provider_list.append({
            'name': p['name'],
            'type': 'mock',
            'status': 'demo'
        })
    
    return jsonify(provider_list)

@app.route('/api/compare', methods=['POST'])
def compare_providers():
    """对比多个提供商对同一个提示词的响应"""
    try:
        data = request.get_json()
        
        if not data or 'prompt' not in data:
            return jsonify({'error': 'Prompt is required'}), 400
        
        prompt = data['prompt']
        selected_providers = data.get('providers', [])
        model = data.get('model', 'gpt-3.5-turbo')
        max_workers = min(data.get('max_workers', 3), 5)  # 限制最大并发线程数
        
        logger.info(f"Comparing providers for prompt: {prompt[:50]}...")
        
        # 确定需要测试的提供商
        providers_to_test = []
        
        if selected_providers:
            # 筛选用户选中的提供商
            if G4F_AVAILABLE:
                for p in G4F_PROVIDERS:
                    if p.__name__ in selected_providers:
                        providers_to_test.append(('g4f', p))
            
            for p in PROVIDERS:
                if p['name'] in selected_providers:
                    providers_to_test.append(('mock', p))
        else:
            # 如果未指定，则测试所有提供商
            if G4F_AVAILABLE:
                providers_to_test.extend([('g4f', p) for p in G4F_PROVIDERS])
            providers_to_test.extend([('mock', p) for p in PROVIDERS])
        
        results = []
        
        # 使用线程池并发执行测试
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            
            for provider_type, provider in providers_to_test:
                if provider_type == 'g4f':
                    future = executor.submit(test_g4f_provider, provider, prompt, model)
                else:
                    future = executor.submit(test_mock_provider, provider, prompt, model)
                futures.append((future, provider_type, provider))
            
            # 获取并发任务的结果
            for future, provider_type, provider in futures:
                try:
                    result = future.result(timeout=25)
                    results.append(result)
                    logger.info(f"Completed test for {result['provider']}: success={result['success']}")
                except Exception as e:
                    provider_name = provider.__name__ if provider_type == 'g4f' else provider['name']
                    results.append({
                        'provider': provider_name,
                        'success': False,
                        'response': '',
                        'error': f'Execution error: {str(e)}',
                        'response_time': 0,
                        'model': model,
                        'type': provider_type
                    })
                    logger.error(f"Error testing {provider_name}: {e}")
        
        # 按成功状态和响应时间对结果排序
        results.sort(key=lambda x: (not x['success'], x['response_time']))
        
        successful_count = len([r for r in results if r['success']])
        
        logger.info(f"Comparison complete: {successful_count}/{len(results)} providers successful")
        
        return jsonify({
            'prompt': prompt,
            'model': model,
            'total_providers': len(results),
            'successful_providers': successful_count,
            'g4f_available': G4F_AVAILABLE,
            'results': results
        })
        
    except Exception as e:
        logger.error(f"Error in compare_providers: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@app.route('/api/test-single', methods=['POST'])
def test_single_provider():
    """测试单个提供商"""
    try:
        data = request.get_json()
        
        if not data or 'prompt' not in data or 'provider' not in data:
            return jsonify({'error': 'Prompt and provider are required'}), 400
        
        prompt = data['prompt']
        provider_name = data['provider']
        model = data.get('model', 'gpt-3.5-turbo')
        
        logger.info(f"Testing single provider: {provider_name}")
        
        result = None
        
        # 在 g4f 提供商中查找目标
        if G4F_AVAILABLE:
            for p in G4F_PROVIDERS:
                if p.__name__ == provider_name:
                    result = test_g4f_provider(p, prompt, model)
                    break
        
        # 如果未找到，则在模拟提供商中查找
        if not result:
            for p in PROVIDERS:
                if p['name'] == provider_name:
                    result = test_mock_provider(p, prompt, model)
                    break
        
        if not result:
            return jsonify({'error': 'Provider not found'}), 404
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error in test_single_provider: {str(e)}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@app.route('/health')
def health_check():
    """服务健康检查接口"""
    return jsonify({
        'status': 'healthy',
        'g4f_available': G4F_AVAILABLE,
        'timestamp': time.time()
    })

@app.errorhandler(404)
def not_found(error):
    """处理 404 未找到错误"""
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    """处理 500 服务器内部错误"""
    logger.error(f"Internal server error: {error}")
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    # 从环境变量读取端口，默认为 8080
    port = int(os.environ.get('PORT', 8080))
    app.run(debug=False, host='0.0.0.0', port=port)