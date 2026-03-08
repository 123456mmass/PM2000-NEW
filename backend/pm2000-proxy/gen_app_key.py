#!/usr/bin/env python3
"""
Generate secure APP_KEY for PM2000 Proxy Server.
Usage:
    python gen_app_key.py              # Generate 1 key
    python gen_app_key.py 3            # Generate 3 keys
    python gen_app_key.py 2 --add      # Generate 2 keys and auto-append to .env
"""
import secrets
import string
import sys
import os

PREFIX = "pm2k"  # Easy to identify as PM2000 keys

def gen_key(length: int = 32) -> str:
    """Generate a cryptographically secure APP_KEY."""
    alphabet = string.ascii_letters + string.digits
    random_part = ''.join(secrets.choice(alphabet) for _ in range(length))
    return f"{PREFIX}_{random_part}"

def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 1
    auto_add = "--add" in sys.argv

    keys = [gen_key() for _ in range(count)]

    print("\n🔑 Generated APP_KEY(s):\n")
    for i, key in enumerate(keys, 1):
        print(f"  {i}. {key}")

    if auto_add:
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        if not os.path.exists(env_path):
            print(f"\n❌ .env not found at: {env_path}")
            print("   Please create .env from .env.example first.")
            return

        # Read current ALLOWED_APP_KEYS
        lines = open(env_path, "r", encoding="utf-8").readlines()
        updated = False
        for i, line in enumerate(lines):
            if line.startswith("ALLOWED_APP_KEYS="):
                current = line.strip().split("=", 1)[1]
                existing = [k.strip() for k in current.split(",") if k.strip()]
                existing.extend(keys)
                lines[i] = f"ALLOWED_APP_KEYS={','.join(existing)}\n"
                updated = True
                break

        if updated:
            open(env_path, "w", encoding="utf-8").writelines(lines)
            print(f"\n✅ Auto-added to {env_path}")
        else:
            print(f"\n⚠️ ALLOWED_APP_KEYS not found in .env. Add manually:")
            print(f"   ALLOWED_APP_KEYS={','.join(keys)}")
    else:
        print(f"\n📋 Copy to .env → ALLOWED_APP_KEYS={','.join(keys)}")
        print("   Or use: python gen_app_key.py --add  (to auto-append)")

if __name__ == "__main__":
    main()
