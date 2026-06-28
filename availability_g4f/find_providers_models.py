import g4f.Provider

output_file = "available_providers_models.txt"

print(f"正在扫描可用接口并将结果写入 {output_file} ...")

# 使用 'w' 模式打开文件，编码指定为 utf-8 以免中文或特殊符号乱码
with open(output_file, "w", encoding="utf-8") as f:
    f.write("=== 当前可用 Provider 及其支持的模型 ===\n")
    
    # 遍历所有被标记为可工作的 Provider
    for provider in g4f.Provider.__providers__:
        if provider.working:
            provider_name = provider.__name__
            try:
                # 获取该 Provider 支持的模型列表
                models = provider.models
                if models:
                    f.write(f"🔹 {provider_name}:\n")
                    for model in models:
                        f.write(f"  - {model}\n")
                else:
                    f.write(f"🔹 {provider_name}: 未列出具体模型（可能使用默认模型）\n")
            except AttributeError:
                f.write(f"🔹 {provider_name}: 无法读取模型属性\n")
            
            f.write("-" * 30 + "\n")

print("写入完成。")