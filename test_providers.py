import g4f
import time

print("=== OpenRouterFree - 挑 :free 模型 ===")
free_models = [
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",  # ✅
    "poolside/laguna-xs.2:free", # ✅
    "google/gemma-4-26b-a4b-it:free",
    "arcee-ai/trinity-large-thinking:free",
    "minimax/minimax-m2.5:free",
]

for model in free_models:
    try:
        resp = g4f.ChatCompletion.create(
            model=model,
            messages=[{"role": "user", "content": "What is 2+2?"}],
            provider=g4f.Provider.OpenRouterFree,
            timeout=20
        )
        print(f"✅ {model}: {str(resp)[:80]}")
        time.sleep(13)  # 避免 5次/分钟 限制
    except Exception as e:
        print(f"❌ {model}: {str(e)[:80]}")
        time.sleep(13)