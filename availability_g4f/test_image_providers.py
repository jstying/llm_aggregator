import concurrent.futures
import time

import g4f.Provider
from g4f.client import Client

# Candidate list: all image generation Providers statically scanned by find_image_providers.py with
# working=True and needs_auth=False, plus two extra entries added just to separately verify the question
# "can Gemini image generation (corresponding to Google AI Studio, https://aistudio.google.com/prompts/new_chat)
# be used for free without a key":
#   - AnyProvider + nano-banana (gemini-2.5-flash-image-preview): AnyProvider is g4f's meta-routing Provider,
#     with needs_auth=False; if it can route to some available Gemini image backend key-free under the hood,
#     that would indicate a free path exists;
#   - GeminiPro: g4f's wrapper around the official Google AI Studio (generativelanguage.googleapis.com) API;
#     login_url points directly to https://aistudio.google.com/u/0/apikey, and in the source code
#     models_needs_auth=True, meaning that although this Provider itself is marked needs_auth=False, actually
#     calling the model requires the user to bring their own API Key, so this is expected to fail.
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

print("=== Starting automated text-to-image interface testing ===")

client = Client()
success_count = 0
fail_count = 0


def _generate(provider, model, prompt):
    kwargs = dict(prompt=prompt, provider=provider, response_format="url", timeout=40)
    if model:
        kwargs["model"] = model
    return client.images.generate(**kwargs)


with open(log_file, "w", encoding="utf-8") as f:
    f.write("=== g4f key-free text-to-image interface test report ===\n\n")

    for name, provider, model in test_providers:
        status_msg = f"Testing [{name}], using model: {model or '(default)'} ..."
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
                res_msg = f"SUCCESS [{name}] generated result: {str(image_url)[:100]}"
                success_count += 1
            else:
                res_msg = f"EMPTY   [{name}] returned empty content or no url/b64_json"
                fail_count += 1

        except concurrent.futures.TimeoutError:
            res_msg = f"TIMEOUT [{name}] did not return within {CALL_TIMEOUT_SECONDS} seconds"
            fail_count += 1
        except Exception as e:
            err_str = str(e)[:150].replace("\n", " ")
            res_msg = f"FAIL    [{name}] {err_str}"
            fail_count += 1

        print(res_msg)
        f.write(res_msg + "\n" + "-" * 60 + "\n")
        f.flush()
        time.sleep(3)

    summary = f"\nTest finished -> success: {success_count} | fail: {fail_count}\n"
    print(summary)
    f.write(summary)
