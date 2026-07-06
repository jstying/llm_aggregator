import g4f.Provider

output_file = "available_providers_models.txt"

print(f"Scanning for available interfaces and writing results to {output_file} ...")

# Open the file in 'w' mode with encoding set to utf-8 to avoid garbled Chinese or special characters
with open(output_file, "w", encoding="utf-8") as f:
    f.write("=== Currently available Providers and their supported models ===\n")

    # Iterate over all Providers marked as working
    for provider in g4f.Provider.__providers__:
        if provider.working:
            provider_name = provider.__name__
            try:
                # Get the list of models supported by this Provider
                models = provider.models
                if models:
                    f.write(f"🔹 {provider_name}:\n")
                    for model in models:
                        f.write(f"  - {model}\n")
                else:
                    f.write(f"🔹 {provider_name}: no specific models listed (may use a default model)\n")
            except AttributeError:
                f.write(f"🔹 {provider_name}: unable to read model attribute\n")

            f.write("-" * 30 + "\n")

print("Done writing.")