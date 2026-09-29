"""Create a password hash for a team account in secrets.toml, so no plain-text password is stored.

Usage:  python hash_password.py
Then paste the printed line under [users] in .streamlit/secrets.toml (and in Streamlit Cloud > Settings > Secrets).
"""
from getpass import getpass

import store

email = input("Team account email: ").strip().lower()
pw = getpass("Password (not shown): ")
if pw != getpass("Repeat password: "):
    raise SystemExit("The passwords do not match.")
problem = store.password_problem(pw, email)
if problem:
    raise SystemExit(problem)
print(f'\n"{email}" = "{store.make_hash(pw)}"\n')
