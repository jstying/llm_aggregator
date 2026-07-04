import concurrent.futures
import time

import g4f.Provider
from g4f.client import Client

# 候选列表：均为 find_image_providers.py 静态扫描出的 working=True 且 needs_auth=False 的图片生成 Provider，
# 额外追加两条用于单独验证"Gemini 生图（对应 Google AI Studio, https://aistudio.google.com/prompts/new_chat）
# 是否可免 Key 免费使用"这一问题：
#   - AnyProvider + nano-banana（gemini-2.5-flash-image-preview）：AnyProvider 是 g4f 的元路由 Provider，
#     needs_auth=False，若它能在底层免 Key 路由到某个可用的 Gemini 图片后端，则说明存在免费路径；
#   - GeminiPro：g4f 对 Google AI Studio（generativelanguage.googleapis.com）官方 API 的封装，
#     login_url 直接指向 https://aistudio.google.com/u/0/apikey，源码里 models_needs_auth=True，
#     即该 Provider 本身虽标记 needs_auth=False，但实际调用模型需要用户自备 API Key，预期会失败。
test_providers = [
    ("AnyProvider (flux baseline)",     g4f.Provider.AnyProvider,              "flux"),
    ("AnyProvider (gemini nano-banana)", g4f.Provider.AnyProvider,             "gemini-2.5-flash-image-preview"),
    ("BlackForestLabs_Flux1Dev",        g4f.Provider.BlackForestLabs_Flux1Dev, "flux-dev"),
    ("BlackForestLabs_Flux1KontextDev", g4f.Provider.BlackForestLabs_Flux1KontextDev, "flux-kontext-dev"),
    ("DeepseekAI_JanusPro7b",           g4f.Provider.DeepseekAI_JanusPro7b,    "janus-pro-7b-image"),
    ("HuggingSpace",                    g4f.Provider.HuggingSpace,             ""),
    ("OpenaiChat",                      g4f.Provider.OpenaiChat,               "gpt-image"),
    ("OperaAria",                       g4f.Provider.OperaAria,                "aria"),
    ("PollinationsImage",               g4f.Provider.PollinationsImage,        ""),
    ("Qwen",                            g4f.Provider.Qwen,                     ""),
    ("StabilityAI_SD35Large",           g4f.Provider.StabilityAI_SD35Large,    "sd-3.5-large"),
    ("GeminiPro (Google AI Studio)",    g4f.Provider.GeminiPro,                ""),
]

log_file = "../image_provider_test_results.txt"
PROMPT = "A small red apple on a white background, digital art"
CALL_TIMEOUT_SECONDS = 45

print("=== 开始文生图接口自动化测试 ===")

client = Client()
success_count = 0
fail_count = 0


def _generate(provider, model, prompt):
    kwargs = dict(prompt=prompt, provider=provider, response_format="url", timeout=40)
    if model:
        kwargs["model"] = model
    return client.images.generate(**kwargs)


with open(log_file, "w", encoding="utf-8") as f:
    f.write("=== g4f 免 Key 文生图接口测试报告 ===\n\n")

    for name, provider, model in test_providers:
        status_msg = f"正在测试 [{name}]，使用模型: {model or '(默认)'} ..."
        print(status_msg)
        f.write(status_msg + "\n")

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_generate, provider, model, PROMPT)
                result = future.result(timeout=CALL_TIMEOUT_SECONDS)

            image_url = None
            if result and getattr(result, "data", None):
                image_url = getattr(result.data[0], "url", None) or getattr(result.data[0], "b64_json", None)

            if image_url:
                res_msg = f"SUCCESS [{name}] 生成结果: {str(image_url)[:100]}"
                success_count += 1
            else:
                res_msg = f"EMPTY   [{name}] 返回内容为空或无 url/b64_json"
                fail_count += 1

        except concurrent.futures.TimeoutError:
            res_msg = f"TIMEOUT [{name}] 超过 {CALL_TIMEOUT_SECONDS} 秒未返回"
            fail_count += 1
        except Exception as e:
            err_str = str(e)[:150].replace("\n", " ")
            res_msg = f"FAIL    [{name}] {err_str}"
            fail_count += 1

        print(res_msg)
        f.write(res_msg + "\n" + "-" * 60 + "\n")
        f.flush()
        time.sleep(3)

    summary = f"\n测试结束 -> 成功: {success_count} 个 | 失败: {fail_count} 个\n"
    print(summary)
    f.write(summary)
