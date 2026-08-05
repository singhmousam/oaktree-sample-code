#!/usr/bin/env python3
"""
Hash demo user passwords and emit the JSON blob deploy.sh stores in Key
Vault as the 'app-users' secret.

Reads users from stdin, one per line, formatted as:
    username:password:role

Outputs a JSON array of {"username":, "password_hash":, "role":} to stdout.
Plaintext passwords are never written anywhere by this script — only the
PBKDF2 hash goes into the output.

Uses only the Python standard library (hashlib.pbkdf2_hmac), so no extra
pip installs are needed at deploy time. The exact same scheme (algorithm,
iteration count, format string) is re-implemented in fastapi_app/auth.py to
verify logins at runtime — if you change PBKDF2_ITERATIONS here, change it
there too, or existing hashes won't verify.
"""

import binascii
import hashlib
import json
import os
import sys

PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return (
        f"pbkdf2_sha256${PBKDF2_ITERATIONS}"
        f"${binascii.hexlify(salt).decode()}"
        f"${binascii.hexlify(derived).decode()}"
    )


def main():
    users = []
    for line in sys.stdin:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            username, password, role = line.split(":", 2)
        except ValueError:
            sys.exit(f"Malformed user line (expected username:password:role): {line!r}")
        users.append(
            {
                "username": username,
                "password_hash": hash_password(password),
                "role": role,
            }
        )
    if not users:
        sys.exit("No users provided on stdin.")
    print(json.dumps(users))


if __name__ == "__main__":
    main()
