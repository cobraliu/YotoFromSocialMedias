"""V2M user accounts + sessions (file-backed, stdlib crypto).

State lives under the Yoto state dir (honors YOTO_STATE_DIR):
    users.json     -> {"users": [{uid, username, salt, pw, created}]}
    sessions.json  -> {token: {uid, created}}
Per-user Yoto state is separate, under users/<uid>/ (see yoto_client).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

import app.yoto_client as yconfig

_ITERS = 200_000

_LEGACY_FILES = (
    ".env",
    ".yoto_token.json",
    ".yoto_pkce.json",
    "yoto.icons.json",
    "me.icons.json",
    "yotoicons.cache.json",
)


def _users_path() -> Path:
    return yconfig.state_dir() / "users.json"


def _sessions_path() -> Path:
    return yconfig.state_dir() / "sessions.json"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    os.chmod(path, 0o600)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(pw: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), _ITERS)
    return salt, dk.hex()


def verify_password(pw: str, salt: str, expected: str) -> bool:
    _, h = hash_password(pw, salt)
    return hmac.compare_digest(h, expected)


def _load_users() -> list[dict]:
    return _read(_users_path(), {"users": []}).get("users", [])


def _save_users(users: list[dict]) -> None:
    _write(_users_path(), {"users": users})


def create_user(username: str, password: str, is_admin: bool = False) -> str:
    username = (username or "").strip()
    if not username or not password:
        raise ValueError("用户名和密码不能为空")
    users = _load_users()
    if any(u["username"].lower() == username.lower() for u in users):
        raise ValueError("用户名已存在")
    salt, pw = hash_password(password)
    uid = secrets.token_hex(8)
    first = not users
    users.append({
        "uid": uid,
        "username": username,
        "salt": salt,
        "pw": pw,
        "admin": bool(is_admin),
        "created": _now(),
    })
    _save_users(users)
    if first:
        migrate_legacy_into(uid)
    return uid


def is_admin(uid: str) -> bool:
    return any(u["uid"] == uid and u.get("admin") for u in _load_users())


def set_password(uid: str, password: str) -> None:
    if not password:
        raise ValueError("密码不能为空")
    users = _load_users()
    for u in users:
        if u["uid"] == uid:
            u["salt"], u["pw"] = hash_password(password)
            _save_users(users)
            return
    raise ValueError("用户不存在")


def delete_user(uid: str) -> None:
    import shutil

    users = _load_users()
    target = next((u for u in users if u["uid"] == uid), None)
    if target is None:
        raise ValueError("用户不存在")
    if target.get("admin") and sum(1 for u in users if u.get("admin")) <= 1:
        raise ValueError("不能删除最后一个管理员")
    _save_users([u for u in users if u["uid"] != uid])
    # drop that user's sessions
    sessions = _load_sessions()
    for tok in [t for t, r in sessions.items() if r.get("uid") == uid]:
        del sessions[tok]
    _save_sessions(sessions)
    # clean up their per-user Yoto state
    shutil.rmtree(yconfig.user_dir(uid), ignore_errors=True)


def authenticate(username: str, password: str) -> str | None:
    target = (username or "").strip().lower()
    for u in _load_users():
        if u["username"].lower() == target:
            if verify_password(password, u["salt"], u["pw"]):
                return u["uid"]
            return None
    return None


def get_user(uid: str) -> dict | None:
    for u in _load_users():
        if u["uid"] == uid:
            return {"uid": u["uid"], "username": u["username"]}
    return None


def list_users() -> list[dict]:
    return [
        {"uid": u["uid"], "username": u["username"], "is_admin": bool(u.get("admin"))}
        for u in _load_users()
    ]


def _load_sessions() -> dict:
    return _read(_sessions_path(), {})


def _save_sessions(s: dict) -> None:
    _write(_sessions_path(), s)


def new_session(uid: str) -> str:
    token = secrets.token_urlsafe(32)
    s = _load_sessions()
    s[token] = {"uid": uid, "created": _now()}
    _save_sessions(s)
    return token


def session_user(token: str) -> str | None:
    if not token:
        return None
    rec = _load_sessions().get(token)
    return rec.get("uid") if rec else None


def end_session(token: str) -> None:
    s = _load_sessions()
    if token in s:
        del s[token]
        _save_sessions(s)


def migrate_legacy_into(uid: str) -> None:
    """Move any legacy flat .yoto/ binding into users/<uid>/ (first user only,
    and only when the destination has none of these files). Best-effort."""
    import shutil

    base = yconfig.state_dir()
    dest = yconfig.user_dir(uid)
    if any((dest / f).exists() for f in _LEGACY_FILES):
        return
    for f in _LEGACY_FILES:
        src = base / f
        if src.exists():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest / f))


# ─── CLI (account creation is admin/CLI-only) ─────────────────────────
def _main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="accounts", description="V2M 账号管理")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in ("create-admin", "create-user"):
        sp = sub.add_parser(cmd)
        sp.add_argument("username")
        sp.add_argument("password")
    sp = sub.add_parser("passwd")
    sp.add_argument("username")
    sp.add_argument("password")
    sub.add_parser("list")
    sp = sub.add_parser("delete")
    sp.add_argument("username")

    args = parser.parse_args(argv)

    def _uid_by_name(name):
        for u in _load_users():
            if u["username"].lower() == name.lower():
                return u["uid"]
        return None

    if args.cmd in ("create-admin", "create-user"):
        uid = create_user(args.username, args.password,
                          is_admin=(args.cmd == "create-admin"))
        role = "管理员" if args.cmd == "create-admin" else "用户"
        print(f"已创建{role}：{args.username} ({uid})")
    elif args.cmd == "passwd":
        uid = _uid_by_name(args.username)
        if not uid:
            print("用户不存在"); return 1
        set_password(uid, args.password)
        print(f"已重置密码：{args.username}")
    elif args.cmd == "delete":
        uid = _uid_by_name(args.username)
        if not uid:
            print("用户不存在"); return 1
        delete_user(uid)
        print(f"已删除：{args.username}")
    elif args.cmd == "list":
        for u in list_users():
            tag = " [admin]" if u["is_admin"] else ""
            print(f"{u['uid']}  {u['username']}{tag}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
