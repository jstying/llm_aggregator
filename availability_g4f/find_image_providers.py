import inspect

import g4f.Provider

output_file = "available_image_providers_models.txt"

print(f"正在扫描可用的文生图接口并将结果写入 {output_file} ...")

# 使用 'w' 模式打开文件，编码指定为 utf-8 以免中文或特殊符号乱码
with open(output_file, "w", encoding="utf-8") as f:
    f.write("=== 当前 g4f 库中具备图片生成能力的 Provider ===\n")
    f.write("判定依据：类属性 image_models 或 default_image_model 非空/非 None。\n")
    f.write("注意：本文件仅为静态扫描结果，不代表真实网络可用性，需配合 test_image_providers.py 验证。\n\n")

    seen_names = set()
    for name in dir(g4f.Provider):
        # dir() 会枚举模块内非 Provider 的普通属性（如内部别名 `provider`），
        # 用 inspect.isclass + working 属性过滤，排除非 Provider 类
        obj = getattr(g4f.Provider, name)
        if not inspect.isclass(obj) or not hasattr(obj, "working"):
            continue
        if name in seen_names or name != obj.__name__:
            # 跳过模块级别名（属性名与类的 __name__ 不一致，说明是 import 别名而非真正定义）
            continue
        seen_names.add(name)

        image_models = getattr(obj, "image_models", None)
        default_image_model = getattr(obj, "default_image_model", None)
        if not image_models and not default_image_model:
            continue

        working = getattr(obj, "working", False)
        needs_auth = getattr(obj, "needs_auth", True)

        f.write(f"🔹 {name}:\n")
        f.write(f"  - working: {working}\n")
        f.write(f"  - needs_auth: {needs_auth}\n")
        f.write(f"  - default_image_model: {default_image_model}\n")
        if isinstance(image_models, list) and image_models:
            f.write(f"  - image_models ({len(image_models)}): {image_models[:20]}")
            if len(image_models) > 20:
                f.write(f" ... 以及另外 {len(image_models) - 20} 个")
            f.write("\n")
        elif image_models:
            f.write(f"  - image_models: {image_models}\n")
        f.write("-" * 30 + "\n")

print("写入完成。")
