#!/usr/bin/env python3
"""Interactive setup script for Orbit model routing.

This script lets the user choose a model provider (Ollama, OpenRouter, Groq,
OpenAI, Anthropic), configure the default model, ensure the model is
available, store any required API keys in a local ``.env`` file (ignored by
git), and write a ``config/routing.yaml`` entry for the chosen model.

After configuration it sends a trivial test prompt to the selected model to
validate the setup.
"""

import os
import sys
import subprocess
import json
import getpass
from pathlib import Path
import yaml
import requests

# Load existing .env if present (for safety) – we don't depend on python-dotenv
# because we only write the file here.

DEFAULT_ROUTING_PATH = Path("config/routing.yaml")
ENV_PATH = Path(".env")
ENV_EXAMPLE_PATH = Path(".env.example")

PROVIDERS = {
    "1": "ollama",
    "2": "openrouter",
    "3": "groq",
    "4": "openai",
    "5": "anthropic",
}

def prompt_provider():
    print("Choose a model provider:")
    for key, name in PROVIDERS.items():
        print(f"  {key}) {name.title()}")
    choice = input("Enter number (default 1): ").strip() or "1"
    if choice not in PROVIDERS:
        print("Invalid choice – falling back to Ollama.")
        choice = "1"
    return PROVIDERS[choice]

def prompt_model_name(provider):
    if provider == "ollama":
        default = "deepseek-coder-v2:latest"
        suggestion = input(f"Enter Ollama model name [{default}]: ").strip()
        return suggestion or default
    else:
        return input("Enter model name (e.g. " "meta-llama/Meta-Llama-3.1-8B-Instruct" "): ").strip()

def ensure_ollama_model(model_name):
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        available = [m["name"] for m in data.get("models", [])]
        if model_name in available:
            print(f"Model '{model_name}' is already pulled.")
            return True
        else:
            ans = input(f"Model '{model_name}' not found locally. Pull it now? (y/n): ").strip().lower()
            if ans != "y":
                print("Skipping model pull – you will need to pull manually later.")
                return False
            # Pull using subprocess; show progress
            print(f"Pulling model '{model_name}' via 'ollama pull' (this may take a while)...")
            subprocess.run(["ollama", "pull", model_name], check=True)
            return True
    except Exception as e:
        print(f"Error communicating with Ollama API: {e}")
        return False

def write_env_key(provider, api_key):
    var_name = {
        "openrouter": "OPENROUTER_API_KEY",
        "groq": "GROQ_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }.get(provider)
    if not var_name:
        return
    # Append or replace line in .env
    lines = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text().splitlines()
    # Remove any existing line for this var
    lines = [ln for ln in lines if not ln.startswith(f"{var_name}=")]
    lines.append(f"{var_name}={api_key}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    print(f"Wrote {var_name} to .env (will be ignored by git).")

def update_routing_yaml(provider, model_name):
    config = {}
    if DEFAULT_ROUTING_PATH.exists():
        config = yaml.safe_load(DEFAULT_ROUTING_PATH.read_text()) or {}
    # Ensure stage_a exists
    stage_a = config.setdefault("stage_a", {})
    stage_a["default_model"] = model_name
    stage_a["provider"] = provider
    # Base URLs per provider
    base_urls = {
        "ollama": "http://localhost:11434",
        "openrouter": "https://openrouter.ai/api/v1",
        "groq": "https://api.groq.com/openai/v1",
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com/v1",
    }
    stage_a["base_url"] = base_urls.get(provider, "")
    # Disable stage B overrides – user can enable later.
    stage_b = config.setdefault("stage_b", {})
    stage_b["enabled"] = False
    # Write back
    DEFAULT_ROUTING_PATH.write_text(yaml.safe_dump(config, sort_keys=False))
    print(f"Updated {DEFAULT_ROUTING_PATH} with provider '{provider}'.")

def test_model(provider, model_name):
    print("Testing model with a trivial prompt...")
    prompt = "reply with the single word OK"
    system = "You are a helpful assistant. Respond with only the word OK."
    try:
        if provider == "ollama":
            # Use the Ollama chat endpoint directly
            resp = requests.post(
                "http://localhost:11434/api/chat",
                json={"model": model_name, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}], "temperature": 0},
                timeout=30,
            )
            resp.raise_for_status()
            answer = resp.json()["message"]["content"].strip()
        else:
            # Use generic OpenAI-compatible endpoint (OpenRouter, Groq, OpenAI)
            api_key_var = {
                "openrouter": "OPENROUTER_API_KEY",
                "groq": "GROQ_API_KEY",
                "openai": "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
            }[provider]
            api_key = os.getenv(api_key_var) or ""
            if not api_key:
                print(f"API key for {provider} not found in environment – cannot test.")
                return False
            base_url = {
                "openrouter": "https://openrouter.ai/api/v1",
                "groq": "https://api.groq.com/openai/v1",
                "openai": "https://api.openai.com/v1",
                "anthropic": "https://api.anthropic.com/v1",
            }[provider]
            endpoint = f"{base_url.rstrip('/')}/chat/completions"
            payload = {
                "model": model_name,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "temperature": 0,
            }
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            resp = requests.post(endpoint, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            answer = resp.json()["choices"][0]["message"]["content"].strip()
        if answer.upper() == "OK":
            print("✅ Model responded correctly.")
            return True
        else:
            print(f"Unexpected response: {answer}")
            return False
    except Exception as e:
        print(f"Model test failed: {e}")
        return False

def main():
    provider = prompt_provider()
    model_name = prompt_model_name(provider)
    if provider == "ollama":
        if not ensure_ollama_model(model_name):
            print("Proceeding despite Ollama model check failure.")
    else:
        # For automated testing we allow the key to be supplied via an env var.
        # If ORBIT_SETUP_API_KEY is set, use it; otherwise fall back to hidden prompt.
        api_key = os.getenv("ORBIT_SETUP_API_KEY")
        if not api_key:
            api_key = getpass.getpass(f"Enter API key for {provider} (input hidden): ").strip()
        if not api_key:
            print("No API key provided – setup cannot continue for cloud provider.")
            sys.exit(1)
        write_env_key(provider, api_key)
    update_routing_yaml(provider, model_name)
    # Load env for the test step
    from dotenv import load_dotenv
    load_dotenv()
    if not test_model(provider, model_name):
        print("\nSetup completed, but the test prompt failed. Check the provider, model name, and API key.")
        sys.exit(1)
    print("\nSetup successful! You can now run the Orbit harness.")

if __name__ == "__main__":
    main()
