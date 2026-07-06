import time
import g4f

# Candidate list: all Providers statically scanned by find_providers_models.py with working=True and needs_auth=False,
# and with text chat capability (models or default_model non-empty), excluding those already in the
# project (Yqcloud/OperaAria/PollinationsAI/CohereForAI_C4AI_Command).
test_providers = [
    ("AnyProvider",                "gpt-4o-mini"),
    ("ApiAirforce",                "roleplay:free"),
    ("Chatai",                     "gpt-4o-mini"),
    ("Copilot",                    "Copilot"),
    ("DeepInfra",                  "MiniMaxAI/MiniMax-M2.5"),
    ("DeepseekAI_JanusPro7b",      "janus-pro-7b"),
    ("GeminiPro",                  "models/gemini-2.5-flash"),
    ("Groq",                       "openai/gpt-oss-120b"),
    ("HuggingFace",                "openai/gpt-oss-120b"),
    ("HuggingSpace",               "qwen-qwen2-72b-instruct"),
    ("ItalyGPT",                   "gpt-4o"),
    ("MetaAI",                     "meta-ai"),
    ("Microsoft_Phi_4_Multimodal", "phi-4-multimodal"),
    ("Nvidia",                     "openai/gpt-oss-120b"),
    ("Ollama",                     "nemotron-3-super"),
    ("OllamaSwarm",                "qwen3:14b"),
    ("OpenAIFM",                   "coral"),
    ("OpenRouterFree",             "openrouter/free"),
    ("OpenaiChat",                 "gpt-5-1"),
    ("Perplexity",                 "auto"),
    ("Pi",                         "pi"),
    ("Qwen",                       "qwen3.5-flash"),
    ("Qwen_Qwen_2_5",              "qwen-2.5"),
    ("Qwen_Qwen_2_5M",             "qwen-2.5-1m"),
    ("Qwen_Qwen_2_5_Max",          "qwen-2.5-max"),
    ("Qwen_Qwen_2_72B",            "qwen-2-72b"),
    ("Qwen_Qwen_3",                "qwen-3-14b"),
    ("TeachAnything",              "gemma"),
    ("WeWordle",                   "gpt-4"),
]

log_file = "../provider_test_results_v3.txt"
PROMPT = "What is 2+2? Answer in one short sentence."
print("=== Starting automated interface testing (round 4, full screening of new candidates) ===")

success_count = 0
fail_count = 0

with open(log_file, "w", encoding="utf-8") as f:
    f.write("=== g4f key-free interface test report V4 ===\n\n")

    for name, model in test_providers:
        provider = getattr(g4f.Provider, name)
        status_msg = f"Testing [{name}], using model: {model or '(default)'} ..."
        print(status_msg)
        f.write(status_msg + "\n")

        try:
            messages = [{"role": "user", "content": PROMPT}]
            kwargs = dict(messages=messages, provider=provider, timeout=25)
            if model:
                kwargs["model"] = model

            resp = g4f.ChatCompletion.create(**kwargs)

            if resp and len(str(resp).strip()) > 0:
                res_msg = f"SUCCESS [{name}] response: {str(resp)[:80]}"
                success_count += 1
            else:
                res_msg = f"EMPTY   [{name}] returned empty content"
                fail_count += 1

        except Exception as e:
            err_str = str(e)[:150].replace('\n', ' ')
            res_msg = f"FAIL    [{name}] {err_str}"
            fail_count += 1

        print(res_msg)
        f.write(res_msg + "\n" + "-" * 60 + "\n")
        f.flush()
        time.sleep(2)

    summary = f"\nTest finished -> success: {success_count} | fail: {fail_count}\n"
    print(summary)
    f.write(summary)
