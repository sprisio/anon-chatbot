import google.generativeai as genai
import os

# --- 💡 PASTE YOUR GEMINI API KEY HERE ---
GEMINI_API_KEY = "AIzaSyDgLGiOFdm_fSGnLI4MQgDewK7UtYMm7mo"

try:
    print("Connecting to Google AI...\n")
    genai.configure(api_key=GEMINI_API_KEY)

    print("--- Available Models for Your API Key ---")
    model_count = 0
    for model in genai.list_models():
        # We are looking for models that support 'generateContent' which is used for chatting
        if 'generateContent' in model.supported_generation_methods:
            print(f"Model Name: {model.name}")
            print(f"  - Description: {model.description}")
            print("-" * 20)
            model_count += 1
    
    if model_count == 0:
        print("\n!!! No chat models found for your API key.")
        print("This might be due to regional restrictions or the key not being fully activated.")
    else:
        print(f"\nFound {model_count} usable chat models.")
        print("Please use one of the 'Model Name' values listed above in your bot.py file.")

except Exception as e:
    print(f"\n--- An Error Occurred ---")
    print(f"Error details: {e}")
    print("\nPlease double-check that your API key is correct and has been activated.")


