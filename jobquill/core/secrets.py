"""Secrets live in the computer's own password store (Windows Credential Manager, macOS Keychain, Linux Secret
Service) via keyring: never in a file, never in a log, never sent to the AI. Every entry is under one service name,
so "delete all my data" can remove them all."""
import json

import keyring

from jobquill.core import data

INDEX = "__index__"  # names of every stored secret, so they can all be listed and deleted


def _service() -> str:
    """"JobQuill", or "JobQuill-<id>" for a JOBQUILL_HOME (tests, scratch copies)."""
    i = data.instance()
    return f"JobQuill-{i}" if i else "JobQuill"


def _read(name: str) -> str:
    """No password store on this computer (Linux without a Secret Service) reads as nothing saved; saving still says why."""
    try:
        return keyring.get_password(_service(), name) or ""
    except keyring.errors.KeyringError:
        return ""


def _index() -> list[str]:
    return json.loads(_read(INDEX) or "[]")


def get(name: str) -> str:
    return _read(name)


def put(name: str, value: str):
    keyring.set_password(_service(), name, value)
    names = _index()
    if name not in names:
        keyring.set_password(_service(), INDEX, json.dumps(names + [name]))


def delete(name: str):
    try:
        keyring.delete_password(_service(), name)
    except keyring.errors.PasswordDeleteError:
        pass
    keyring.set_password(_service(), INDEX, json.dumps([n for n in _index() if n != name]))


def names() -> list[str]:
    return _index()


def delete_all():
    for name in _index():
        try:
            keyring.delete_password(_service(), name)
        except keyring.errors.PasswordDeleteError:
            pass
    try:
        keyring.delete_password(_service(), INDEX)
    except keyring.errors.PasswordDeleteError:
        pass


def redact(text: str) -> str:
    """Blank out every stored secret in a text (logs, problem reports)."""
    for name in _index():
        value = get(name)
        if value and len(value) >= 4:
            text = text.replace(value, "•••")
    return text
