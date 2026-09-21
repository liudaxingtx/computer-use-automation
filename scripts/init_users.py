"""Initialize the demo user directory with two mock users.

Each user gets a private folder under users/<id>/ holding:
  - profile.json      login identity for the /user runner (password stored as a
                      salted sha256 hash, never plaintext)
  - credentials.json  the legacy-app credentials this user replays with; the
                      password is AES-256-GCM encrypted with a per-user key
                      derived from the master key (SHA-256(master ‖ user id)),
                      so one user's folder cannot be read with another's key.

This mirrors the production isolation model (per-user credential injection +
per-user key derivation) at demo scale. Re-run to regenerate.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.crypto import derive_key, encrypt, master_key_from_env  # noqa: E402

USERS = {
    "alice": {
        "name": "Alice Smith",
        "password": "alice123",              # /user login password
        "legacy_username": "admin",          # legacy-app account
        "legacy_password": "secret123",      # legacy-app password (encrypted)
        "userBO": {                          # per-user business object — the
            "eeID": "1001",                  #   fields a task input can bind to
            "department": "Member Services",
        },
    },
    "bob": {
        "name": "Bob Chen",
        "password": "bob123",
        "legacy_username": "bob_op",
        "legacy_password": "bobpass123",
        "userBO": {
            "eeID": "1002",
            "department": "Risk",
        },
    },
}


def hash_password(pw: str) -> str:
    salt = "demo-salt"
    return "sha256:" + hashlib.sha256((salt + pw).encode()).hexdigest()


def main() -> None:
    master = master_key_from_env()
    for uid, u in USERS.items():
        folder = ROOT / "users" / uid
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "profile.json").write_text(json.dumps({
            "id": uid,
            "name": u["name"],
            "password_hash": hash_password(u["password"]),
            "legacy_username": u["legacy_username"],
            "userBO": u.get("userBO", {}),
        }, indent=2))
        user_key = derive_key(f"user:{uid}", master)
        (folder / "credentials.json").write_text(json.dumps({
            "legacy_username": u["legacy_username"],
            "legacy_password": encrypt(u["legacy_password"], user_key),
        }, indent=2))
        print(f"created users/{uid}/ (legacy login: {u['legacy_username']})")


if __name__ == "__main__":
    main()
