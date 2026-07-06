import inspect

import g4f.Provider

output_file = "available_image_providers_models.txt"

print(f"Scanning for available text-to-image interfaces and writing results to {output_file} ...")

# Open the file in 'w' mode with encoding set to utf-8 to avoid garbled Chinese or special characters
with open(output_file, "w", encoding="utf-8") as f:
    f.write("=== Providers in the current g4f library with image generation capability ===\n")
    f.write("Criterion: the class attribute image_models or default_image_model is non-empty/non-None.\n")
    f.write("Note: this file is only a static scan result, it does not represent real network availability; verify with test_image_providers.py.\n\n")

    seen_names = set()
    for name in dir(g4f.Provider):
        # dir() also enumerates plain, non-Provider attributes in the module (e.g. the internal alias `provider`),
        # so filter with inspect.isclass + the working attribute to exclude non-Provider classes
        obj = getattr(g4f.Provider, name)
        if not inspect.isclass(obj) or not hasattr(obj, "working"):
            continue
        if name in seen_names or name != obj.__name__:
            # Skip module-level aliases (the attribute name doesn't match the class's __name__, meaning it's an import alias, not the real definition)
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
                f.write(f" ... and {len(image_models) - 20} more")
            f.write("\n")
        elif image_models:
            f.write(f"  - image_models: {image_models}\n")
        f.write("-" * 30 + "\n")

print("Done writing.")
