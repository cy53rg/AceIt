"""
atlas_fs_paths.py — Shared filesystem path rules (deny list, write scopes).

Used by atlas_fs_v2, atlas_policy, and atlas_shell.  The model never overrides
these rules via free-text framing.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# Hardcoded deny fragments — READ and WRITE both DENY, no ASK override.
_DENY_DIR_FRAGMENTS: tuple[str, ...] = (
    "/.ssh/",
    "\\.ssh\\",
    "/.ssh\\",
    "\\.ssh/",
    "/.aws/",
    "\\.aws\\",
    "/.aws\\",
    "\\.aws/",
    "/google/chrome/user data",
    "\\google\\chrome\\user data",
    "/microsoft/edge/user data",
    "\\microsoft\\edge\\user data",
    "/mozilla/firefox/profiles",
    "\\mozilla\\firefox\\profiles",
    "/bravesoftware/brave-browser/user data",
    "\\bravesoftware\\brave-browser\\user data",
    "/1password/",
    "\\1password\\",
    "/bitwarden",
    "\\bitwarden",
    "/lastpass",
    "\\lastpass",
    "/keepass",
    "\\keepass",
    "/dashlane",
    "\\dashlane",
)

_PRIVATE_KEY_NAME_RE = re.compile(
    r"(^|[\\/])id_rsa[^\\/]*$|(^|[\\/])id_dsa[^\\/]*$|(^|[\\/])id_ecdsa[^\\/]*$|"
    r"(^|[\\/])id_ed25519[^\\/]*$",
    re.IGNORECASE,
)
_PRIVATE_KEY_SUFFIXES = (".pem", ".key", ".ppk", ".pfx", ".p12")


def normalize_path(path: str | Path) -> str:
    expanded = str(path or "").strip().replace("~", str(Path.home()))
    try:
        resolved = str(Path(expanded).expanduser().resolve())
    except (OSError, ValueError):
        resolved = expanded
    return resolved.replace("/", "\\").lower()


def is_private_key_path(path: str | Path) -> bool:
    norm = normalize_path(path)
    name = Path(norm).name
    if _PRIVATE_KEY_NAME_RE.search(norm):
        return True
    return any(name.endswith(s) for s in _PRIVATE_KEY_SUFFIXES)


def is_env_file(path: str | Path) -> bool:
    norm = normalize_path(path)
    name = Path(norm).name
    if name == ".env" or name.startswith(".env."):
        return True
    return "/.env" in norm or "\\.env" in norm


def is_path_denied(path: str | Path) -> bool:
    """True when path touches credentials, keys, browser profiles, or .env."""
    norm = normalize_path(path)
    if not norm:
        return False
    for fragment in _DENY_DIR_FRAGMENTS:
        if fragment.lower() in norm:
            return True
    if is_env_file(path):
        return True
    if is_private_key_path(path):
        return True
    return False


def deny_message(path: str | Path) -> str:
    return (
        f"Access denied: '{path}' is on Atlas's hardcoded security deny list "
        f"(SSH keys, cloud credentials, browser profiles, password managers, "
        f".env files, or private-key patterns). This cannot be overridden."
    )


def is_under_user_home(path: str | Path) -> bool:
    norm = normalize_path(path)
    home = normalize_path(Path.home())
    return norm.startswith(home)


def path_in_write_scope(path: str | Path, scopes: tuple[str, ...] | list[str]) -> bool:
    """True when resolved path is inside any granted write scope directory."""
    if not scopes:
        return False
    norm = normalize_path(path)
    for scope in scopes:
        scope_norm = normalize_path(scope)
        if not scope_norm:
            continue
        if norm == scope_norm or norm.startswith(scope_norm.rstrip("\\") + "\\"):
            return True
    return False
