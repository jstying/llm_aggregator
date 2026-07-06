import time
import g4f

# 候选列表：find_providers_models.py 静态扫描出的全部 working=True 且 needs_auth=False
# 且具备文本对话能力（models 或 default_model 非空）的 Provider，已在项目中的
# Yqcloud/OperaAria/PollinationsAI/CohereForAI_C4AI_Command 除外。
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
print("=== 开始接口自动化测试（第四轮，全量新候选筛查）===")

success_count = 0
fail_count = 0

with open(log_file, "w", encoding="utf-8") as f:
    f.write("=== g4f 免 Key 接口测试报告 V4 ===\n\n")

    for name, model in test_providers:
        provider = getattr(g4f.Provider, name)
        status_msg = f"正在测试 [{name}]，使用模型: {model or '(默认)'} ..."
        print(status_msg)
        f.write(status_msg + "\n")

        try:
            messages = [{"role": "user", "content": PROMPT}]
            kwargs = dict(messages=messages, provider=provider, timeout=25)
            if model:
                kwargs["model"] = model

            resp = g4f.ChatCompletion.create(**kwargs)

            if resp and len(str(resp).strip()) > 0:
                res_msg = f"SUCCESS [{name}] 响应: {str(resp)[:80]}"
                success_count += 1
            else:
                res_msg = f"EMPTY   [{name}] 返回内容为空"
                fail_count += 1

        except Exception as e:
            err_str = str(e)[:150].replace('\n', ' ')
            res_msg = f"FAIL    [{name}] {err_str}"
            fail_count += 1

        print(res_msg)
        f.write(res_msg + "\n" + "-" * 60 + "\n")
        f.flush()
        time.sleep(2)

    summary = f"\n测试结束 -> 成功: {success_count} 个 | 失败: {fail_count} 个\n"
    print(summary)
    f.write(summary)
