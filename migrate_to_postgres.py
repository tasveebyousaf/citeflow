"""Copy existing accounts, channels and projects from the data/ folder into PostgreSQL.

Usage (in the project folder, with DATABASE_URL in .streamlit/secrets.toml or the environment):
    python migrate_to_postgres.py

Safe to run more than once: existing rows are updated, nothing is deleted. The data/ folder is not changed.
"""
import os
import sys

from psycopg.types.json import Jsonb

import store


def main():
    url = store.database_url()
    if not url:
        try:
            import tomllib
            with open(os.path.join(".streamlit", "secrets.toml"), "rb") as fh:
                url = str(tomllib.load(fh).get("DATABASE_URL", "")).strip()
        except (OSError, ValueError):
            url = ""
    if not url:
        sys.exit("DATABASE_URL is not set (add it to .streamlit/secrets.toml or the environment).")
    import store_pg
    db = store_pg.PgStore(url)
    users = store._read(store.USERS, {})
    n_users = n_projects = n_files = 0
    with db.pool.connection() as c:
        for username, u in users.items():
            c.execute("INSERT INTO users (username, salt, hash, iter, name, created) VALUES (%s,%s,%s,%s,%s,%s) "
                      "ON CONFLICT (username) DO UPDATE SET salt=EXCLUDED.salt, hash=EXCLUDED.hash, iter=EXCLUDED.iter, "
                      "name=EXCLUDED.name",
                      (username, u["salt"], u["hash"], u.get("iter", store.LEGACY_ITERATIONS), u.get("name", username),
                       u.get("created", 0)))
            n_users += 1
    users_root = os.path.join(store.DATA, "users")
    for folder in (os.listdir(users_root) if os.path.isdir(users_root) else []):
        # folder names are sanitised usernames; map back to the real username where possible
        username = next((u for u in users if store._udir(u).endswith(os.sep + folder)), folder)
        udir = os.path.join(users_root, folder)
        ch = store._read(os.path.join(udir, "channels.json"), None)
        if ch is not None:
            db.save_channels(username, ch)
        proot = os.path.join(udir, "projects")
        for pid in (os.listdir(proot) if os.path.isdir(proot) else []):
            pdir = os.path.join(proot, pid)
            meta = store._read(os.path.join(pdir, "meta.json"), None)
            state = store._read(os.path.join(pdir, "state.json"), None)
            if meta is None or state is None:
                continue
            with db.pool.connection() as c:
                c.execute("INSERT INTO projects (username, pid, meta, state, updated) VALUES (%s,%s,%s,%s,%s) "
                          "ON CONFLICT (username, pid) DO UPDATE SET meta=EXCLUDED.meta, state=EXCLUDED.state, "
                          "updated=EXCLUDED.updated",
                          (username, pid, Jsonb(meta), Jsonb(state), meta.get("updated", 0)))
                for name in os.listdir(pdir):
                    p = os.path.join(pdir, name)
                    if name.endswith(".json") or name.endswith(".tmp") or not os.path.isfile(p):
                        continue
                    with open(p, "rb") as fh:
                        data = fh.read()
                    c.execute("INSERT INTO project_files (username, pid, name, size, data) VALUES (%s,%s,%s,%s,%s) "
                              "ON CONFLICT (username, pid, name) DO UPDATE SET size=EXCLUDED.size, data=EXCLUDED.data",
                              (username, pid, name, len(data), data))
                    n_files += 1
            n_projects += 1
    print(f"Copied {n_users} account(s), {n_projects} project(s) and {n_files} file(s) to PostgreSQL.")
    print("Database now contains:", db.stats())
    db.close()


if __name__ == "__main__":
    main()
