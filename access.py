"""Kullanıcı/parola, oturum ve paylaşılan klasör kayıt kilidi yardımcıları."""
from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Any

ROLES = {"admin": "Yönetici", "viewer": "Yalnız görüntüleme", "operator": "Özel yetkili"}
PBKDF2_ITERATIONS = 200_000
LOCK_STALE_SECONDS = 120
LOCK_HEARTBEAT_SECONDS = 30


def hash_password(password: str, salt: str | None = None, iterations: int = PBKDF2_ITERATIONS) -> dict[str, Any]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return {"salt": salt, "hash": digest.hex(), "iterations": iterations}


def verify_password(password: str, record: dict[str, Any]) -> bool:
    try:
        expected = hash_password(password, record["salt"], int(record.get("iterations", PBKDF2_ITERATIONS)))["hash"]
    except (KeyError, TypeError, ValueError):
        return False
    return hmac.compare_digest(expected, str(record.get("hash", "")))


def normalize_username(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Kullanıcı adı metin olmalıdır.")
    name = value.strip()
    if not 2 <= len(name) <= 40 or not re.fullmatch(r"[\wçğıöşüÇĞİÖŞÜ.\- ]+", name):
        raise ValueError("Kullanıcı adı 2-40 karakter olmalı; harf, rakam, boşluk, nokta veya tire içerebilir.")
    return name


def validate_password(value: Any) -> str:
    if not isinstance(value, str) or not 6 <= len(value) <= 128:
        raise ValueError("Parola 6-128 karakter olmalıdır.")
    return value


def instance_owner() -> dict[str, Any]:
    try:
        user = getpass.getuser()
    except Exception:
        user = os.environ.get("USERNAME", "kullanıcı")
    return {"host": socket.gethostname(), "user": user, "pid": os.getpid()}


def process_alive(pid: Any) -> bool:
    """Bu bilgisayarda verilen işlem hâlâ çalışıyor mu? Emin olunamazsa True döner."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True
    if pid <= 0:
        return True
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # erişim reddi: işlem var
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


class EditLock:
    """Veritabanı paylaşılan klasördeyse aynı anda tek bilgisayarın yazmasına izin verir."""

    def __init__(self, data_path: Path):
        self.path = Path(data_path).with_suffix(".lock")
        self.owner = instance_owner()
        self.last_beat = 0.0
        self.holder: dict[str, Any] | None = None
        self._guard = threading.Lock()

    def _same(self, other: dict[str, Any] | None) -> bool:
        return bool(other) and other.get("host") == self.owner["host"] and other.get("pid") == self.owner["pid"]

    def _read(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + f".{os.getpid()}-{threading.get_ident()}.tmp")
        temporary.write_text(json.dumps(self.owner | {"heartbeat": time.time()}, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)
        self.last_beat = time.monotonic()

    def status(self) -> dict[str, Any]:
        """Kilidi alır/tazeler; başkası tutuyorsa salt okunur durumunu döndürür."""
        with self._guard:
            return self._status()

    def _status(self) -> dict[str, Any]:
        current = self._read()
        fresh = bool(current) and time.time() - float(current.get("heartbeat", 0) or 0) < LOCK_STALE_SECONDS
        if fresh and current.get("host") == self.owner["host"] and not self._same(current):
            # Aynı bilgisayarda kapanmış/çökmüş bir kopyanın kilidi beklenmeden bırakılır.
            fresh = process_alive(current.get("pid"))
        if current and fresh and not self._same(current):
            self.holder = current
            return {"read_only": True, "holder": self._public(current)}
        if not self._same(current) or time.monotonic() - self.last_beat > LOCK_HEARTBEAT_SECONDS:
            try:
                self._write()
            except OSError:
                pass
        self.holder = None
        return {"read_only": False, "holder": self._public(self._read() or self.owner)}

    def take_over(self) -> dict[str, Any]:
        with self._guard:
            try:
                self._write()
            except OSError as exc:
                raise ValueError(f"Kilit dosyası yazılamadı: {exc}") from exc
            return self._status()

    def release(self) -> None:
        if self._same(self._read()):
            try:
                self.path.unlink()
            except OSError:
                pass

    @staticmethod
    def _public(value: dict[str, Any]) -> dict[str, Any]:
        beat = value.get("heartbeat")
        return {"host": str(value.get("host", "")), "user": str(value.get("user", "")),
                "since": time.strftime("%d.%m.%Y %H:%M", time.localtime(beat)) if beat else ""}
