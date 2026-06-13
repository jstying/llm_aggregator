from flask import Flask, request, jsonify, render_template
import time
import traceback
import logging
import os
from concurrent.futures import ThreadPoolExecutor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

try:
    import g4f
    G4F_AVAILABLE = True
    logger.info("g4f imported successfully")

    G4F_PROVIDERS = [
        g4f.Provider.Yqcloud,
        g4f.Provider.OpenRouterFree,
    ]

    PROVIDER_MODELS = {
        'Yqcloud': 'gpt-3.5-turbo',
        'OpenRouterFree': 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free',
    }

except ImportError as e:
    G4F_AVAILABLE = False
    G4F_PROVIDERS = []
    PROVIDER_MODELS = {}
    logger.warning(f"g4f not available: {e}")
except Exception as e:
    G4F_AVAILABLE = False
    G4F_PROVIDERS = []
    PROVIDER_MODELS = {}
    logger.warning(f"g4f initialization failed: {e}")


def test_g4f_provider(provider, prompt, model="gpt-3.5-turbo"):
    actual_model = PROVIDER_MODELS.get(provider.__name__, model)

    start_time = time.time()
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
        response = g4f.ChatCompletion.create(
            model=actual_model,
            messages=[{"role": "user", "content": prompt}],
            provider=provider,
            timeout=20
        )
        result['success'] = True
        result['response'] = str(response)
    except Exception as e:
        result['error'] = str(e)
    finally:
        result['response_time'] = round(time.time() - start_time, 2)

    return result


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/providers', methods=['GET'])
def get_providers():
    if not G4F_AVAILABLE:
        return jsonify([])

    provider_list = []
    for p in G4F_PROVIDERS:
        provider_list.append({
            'name': p.__name__,
            'model': PROVIDER_MODELS.get(p.__name__, 'unknown'),
            'type': 'g4f',
            'status': 'available'
        })
    return jsonify(provider_list)


@app.route('/api/compare', methods=['POST'])
def compare_providers():
    try:
        data = request.get_json()

        if not data or 'prompt' not in data:
            return jsonify({'error': 'Prompt is required'}), 400

        if not G4F_AVAILABLE:
            return jsonify({'error': 'g4f is not available'}), 503

        prompt = data['prompt']
        selected_providers = data.get('providers', [])
        model = data.get('model', 'gpt-3.5-turbo')
        max_workers = min(data.get('max_workers', 3), 5)

        logger.info(f"Comparing providers for prompt: {prompt[:50]}...")

        if selected_providers:
            providers_to_test = [
                p for p in G4F_PROVIDERS
                if p.__name__ in selected_providers
            ]
        else:
            providers_to_test = G4F_PROVIDERS

        if not providers_to_test:
            return jsonify({'error': 'No valid providers found'}), 400

        results = []

        with ThreadPoolExecutor(max_workers=min(max_workers, len(providers_to_test))) as executor:
            futures = {
                executor.submit(test_g4f_provider, p, prompt, model): p
                for p in providers_to_test
            }

            for future, provider in futures.items():
                try:
                    result = future.result(timeout=25)
                    results.append(result)
                    logger.info(f"Completed: {result['provider']} success={result['success']}")
                except Exception as e:
                    results.append({
                        'provider': provider.__name__,
                        'success': False,
                        'response': '',
                        'error': f'Execution error: {str(e)}',
                        'response_time': 0,
                        'model': PROVIDER_MODELS.get(provider.__name__, model),
                        'type': 'g4f'
                    })
                    logger.error(f"Error testing {provider.__name__}: {e}")

        results.sort(key=lambda x: (not x['success'], x['response_time']))
        successful_count = sum(1 for r in results if r['success'])

        logger.info(f"Comparison complete: {successful_count}/{len(results)} successful")

        return jsonify({
            'prompt': prompt,
            'total_providers': len(results),
            'successful_providers': successful_count,
            'results': results
        })

    except Exception as e:
        logger.error(f"Error in compare_providers: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500


@app.route('/api/test-single', methods=['POST'])
def test_single_provider():
    try:
        data = request.get_json()

        if not data or 'prompt' not in data or 'provider' not in data:
            return jsonify({'error': 'Prompt and provider are required'}), 400

        if not G4F_AVAILABLE:
            return jsonify({'error': 'g4f is not available'}), 503

        prompt = data['prompt']
        provider_name = data['provider']
        model = data.get('model', 'gpt-3.5-turbo')

        provider = next((p for p in G4F_PROVIDERS if p.__name__ == provider_name), None)

        if not provider:
            return jsonify({'error': f'Provider "{provider_name}" not found'}), 404

        result = test_g4f_provider(provider, prompt, model)
        return jsonify(result)

    except Exception as e:
        logger.error(f"Error in test_single_provider: {str(e)}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500


@app.route('/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'g4f_available': G4F_AVAILABLE,
        'providers': [p.__name__ for p in G4F_PROVIDERS],
        'timestamp': time.time()
    })


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