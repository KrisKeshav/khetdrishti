"""Deploy to a Hugging Face Docker Space in one command.

  1. Create a free account at https://huggingface.co and a WRITE token at https://huggingface.co/settings/tokens
  2. Add to .env:   HF_TOKEN=hf_xxx      HF_SPACE=<your-username>/khetdrishti
  3. python scripts/deploy_hf.py

Uploads the code (never .env, the database or caches) and stores the keys from .env as encrypted Space
*secrets*, so they never appear in the repository. Re-run any time to redeploy.
"""
import sys
from pathlib import Path

from dotenv import dotenv_values
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ["GROQ_API_KEYS", "DATA_GOV_IN_API_KEY", "CDSE_CLIENT_ID", "CDSE_CLIENT_SECRET", "SARVAM_API_KEY",
           "TELEGRAM_BOT_TOKEN", "TELEGRAM_DEFAULT_CHAT_ID"]
VARIABLES = ["GROQ_MODELS", "GROQ_STT_MODEL", "CONTACT_UA", "MONITOR_HOUR_IST"]
IGNORE = [".env", ".env.*", ".venv/*", ".vscode/*", ".git/*", "**/__pycache__/*", ".pytest_cache/*",
          "data/*.db", "data/*.db-*", "ui_check_out/*", "data/knowledge/raw/*"]


def main() -> int:
    env = dotenv_values(ROOT / ".env")
    token, space = env.get("HF_TOKEN"), env.get("HF_SPACE")
    if not token or not space:
        print("Set HF_TOKEN and HF_SPACE (e.g. yourname/khetdrishti) in .env first.")
        return 1
    api = HfApi(token=token)
    print(f"Creating / reusing Space {space} …")
    api.create_repo(space, repo_type="space", space_sdk="docker", exist_ok=True)
    for k in SECRETS:
        if env.get(k):
            api.add_space_secret(space, k, env[k])
            print(f"  secret   {k} set")
    for k in VARIABLES:
        if env.get(k):
            api.add_space_variable(space, k, env[k])
            print(f"  variable {k} = {env[k]}")
    print("Uploading code …")
    api.upload_folder(repo_id=space, repo_type="space", folder_path=str(ROOT), ignore_patterns=IGNORE,
                      commit_message="Deploy KhetDrishti")
    user, name = space.split("/")
    print(f"\nDone. Build logs: https://huggingface.co/spaces/{space}?logs=build")
    print(f"Live app (after the build, ~5 min): https://{user}-{name}.hf.space".lower())
    return 0


if __name__ == "__main__":
    sys.exit(main())
