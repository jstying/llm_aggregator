import g4f
import time

# 重新筛选的测试列表，修复了部分模型名称
test_providers = [
    # 1. 之前唯一成功的接口
    ("Yqcloud", g4f.Provider.Yqcloud, "gpt-4"),
    
    # 2. 修正参数后的聚合免密通道
    ("AIBadgr", g4f.Provider.AIBadgr, "default"),
    ("Antigravity", g4f.Provider.Antigravity, "default"),
    ("ApiAirforce", g4f.Provider.ApiAirforce, "default"),
    
    # 3. 补充你日志中存在的其他可能免 Key 接口
    ("PollinationsAI", g4f.Provider.PollinationsAI, "default"),
    ("PuterJS", g4f.Provider.PuterJS, "default"),
    ("OperaAria", g4f.Provider.OperaAria, "aria")
]

log_file = "provider_test_results_v2.txt"
print("=== 开始第二轮接口自动化测试 ===")

success_count = 0
fail_count = 0

with open(log_file, "w", encoding="utf-8") as f:
    f.write("=== g4f 免 Key 接口测试报告 V2 ===\n")
    
    for name, provider, model in test_providers:
        status_msg = f"正在测试 [{name}]，使用模型: {model} ..."
        print(status_msg)
        f.write(status_msg + "\n")
        
        try:
            messages = [{"role": "user", "content": "What is 2+2?"}]
            
            # 显式传递 messages 参数，修复位置参数缺失问题
            if model == "default":
                resp = g4f.ChatCompletion.create(
                    messages=messages,
                    provider=provider,
                    timeout=15
                )
            else:
                resp = g4f.ChatCompletion.create(
                    model=model,
                    messages=messages,
                    provider=provider,
                    timeout=15
                )
            
            if resp and len(str(resp).strip()) > 0:
                res_msg = f"✅ [{name}] 成功! 响应: {str(resp)[:50]}"
                print(res_msg)
                f.write(res_msg + "\n")
                success_count += 1
            else:
                res_msg = f"⚠️ [{name}] 返回内容为空"
                print(res_msg)
                f.write(res_msg + "\n")
                fail_count += 1
                
        except Exception as e:
            err_str = str(e)[:60].replace('\n', ' ')
            res_msg = f"❌ [{name}] 失败: {err_str}"
            print(res_msg)
            f.write(res_msg + "\n")
            fail_count += 1
        
        print("-" * 40)
        f.write("-" * 40 + "\n")
        f.flush()
        time.sleep(5)

    summary = f"\n测试结束 -> 成功: {success_count} 个 | 失败: {fail_count} 个\n"
    print(summary)
    f.write(summary)