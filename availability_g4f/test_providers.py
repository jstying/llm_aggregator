import g4f
import time

# 候选列表：全部为 needs_auth=False 且支持文本生成的 Provider
test_providers = [
    # 已在项目中的 Provider（基线对照）
    ("Yqcloud",             g4f.Provider.Yqcloud,             "gpt-4"),
    ("OperaAria",           g4f.Provider.OperaAria,           "aria"),

    # 新候选
    ("PollinationsAI",      g4f.Provider.PollinationsAI,      "openai"),
    ("Qwen_Qwen_3",         g4f.Provider.Qwen_Qwen_3,         "qwen-3-14b"),
    ("Pi",                  g4f.Provider.Pi,                  "pi"),
    ("Chatai",              g4f.Provider.Chatai,              "gpt-4o-mini"),
    ("ItalyGPT",            g4f.Provider.ItalyGPT,            "gpt-4o"),
    ("ApiAirforce",         g4f.Provider.ApiAirforce,         ""),
]

log_file = "../provider_test_results_v2.txt"
print("=== 开始接口自动化测试（第三轮，新候选筛查）===")

success_count = 0
fail_count = 0

with open(log_file, "w", encoding="utf-8") as f:
    f.write("=== g4f 免 Key 接口测试报告 V3 ===\n\n")

    for name, provider, model in test_providers:
        status_msg = f"正在测试 [{name}]，使用模型: {model or '(默认)'} ..."
        print(status_msg)
        f.write(status_msg + "\n")

        try:
            messages = [{"role": "user", "content": "What is 2+2? Answer in one sentence."}]

            kwargs = dict(messages=messages, provider=provider, timeout=20)
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
            err_str = str(e)[:80].replace('\n', ' ')
            res_msg = f"FAIL    [{name}] {err_str}"
            fail_count += 1

        print(res_msg)
        f.write(res_msg + "\n" + "-" * 60 + "\n")
        f.flush()
        time.sleep(3)

    summary = f"\n测试结束 -> 成功: {success_count} 个 | 失败: {fail_count} 个\n"
    print(summary)
    f.write(summary)
