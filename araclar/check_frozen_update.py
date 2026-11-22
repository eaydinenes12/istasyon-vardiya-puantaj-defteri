"""Exercise the Windows updater on disposable copies of two smoke-tested packages."""
from contextlib import closing
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from smoke_package import window_for

ROOT = Path(__file__).resolve().parent
NAME = "VardiyaPuantaj.exe"
PROBE = "Kabul 116"
NET = build_opener(ProxyHandler({}))


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package(name):
    if not re.fullmatch(r"package-\d+", name):
        raise ValueError("Expected an isolated package")
    folder = (ROOT / name).resolve(strict=True)
    assert folder.parent == ROOT
    smoke = json.loads((folder / "smoke.json").read_bytes())
    exe = folder / NAME
    assert smoke["runtime"] == "exe" and digest(exe) == smoke["sha256"]
    receipt = json.loads(Path(f"{exe}.schema.json").read_bytes())
    assert (receipt["executable_sha256"] == smoke["sha256"] and
            receipt["minimum_schema"] <= smoke["database_schema"] <= receipt["maximum_schema"])
    return exe, smoke["sha256"]


def request(origin, path, method="GET", data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = Request(origin + path, body, {"Content-Type": "application/json", "Origin": origin}, method=method)
    with NET.open(req, timeout=20) as response:
        return json.load(response)


def instance(exe, data, excluded=None, timeout=80):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        pid, hwnd = window_for(exe)
        if pid and pid != excluded:
            lines = subprocess.check_output(["netstat", "-ano", "-p", "tcp"]).decode("mbcs").splitlines()
            for fields in map(str.split, lines):
                if (len(fields) == 5 and fields[0] == "TCP" and fields[-1] == str(pid)
                        and fields[1].startswith("127.0.0.1:") and fields[2] == "0.0.0.0:0"):
                    origin = "http://" + fields[1]
                    info = request(origin, "/api/app-info")
                    assert (info["runtime"] == "exe" and Path(info["executable_path"]).samefile(exe)
                            and Path(info["data_path"]).samefile(data))
                    return pid, hwnd, origin
        time.sleep(.25)
    raise TimeoutError(f"Expected a new application window and listener for {exe.name}, excluding PID {excluded}")


def snapshot(path, person=True):
    with closing(sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro", uri=True)) as con:
        assert con.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        if person:
            assert con.execute("SELECT name FROM employees").fetchall() == [(PROBE,)]
        return con.execute("PRAGMA user_version").fetchone()[0]


def manifest(exe, schema):
    Path(str(exe) + ".schema.json").write_text(json.dumps({
        "format": "vardiya-schema-compatibility", "version": 1,
        "minimum_schema": schema, "maximum_schema": schema, "executable_sha256": digest(exe)
    }), encoding="utf-8")


def await_ready(path, timeout=65):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if path.is_file():
            state = json.loads(path.read_text(encoding="utf-8-sig"))
            if state.get("status") == "ready":
                return
            raise AssertionError(f"Updater reported {state!r}")
        time.sleep(.2)
    raise TimeoutError("The replacement EXE did not report its loaded WebView")


def run(old, new, work):
    exe, candidate = work / NAME, work / "incoming" / NAME
    data = work / "data" / "vardiya_puantaj.sqlite3"
    for path in (candidate.parent, data.parent, work / "local", work / "tmp"):
        path.mkdir()
    for src, dst in ((old[0], exe), (new[0], candidate)):
        shutil.copy2(src, dst)
        shutil.copy2(f"{src}.schema.json", f"{dst}.schema.json")
    assert old[1] != new[1]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    backups = []
    try:
        env = {**os.environ, "LOCALAPPDATA": str(work / "local"), "TEMP": str(work / "tmp"),
               "TMP": str(work / "tmp"), "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS":
               "--disable-background-networking --disable-component-update --disable-sync "
               "--proxy-server=http://127.0.0.1:0 --proxy-bypass-list=localhost;127.0.0.1"}
        proc = subprocess.Popen([str(exe)], cwd=work, env=env)
        if not kernel.AssignProcessToJobObject(job, int(proc._handle)):
            error = ctypes.WinError(ctypes.get_last_error())
            proc.terminate()
            proc.wait(10)
            raise error
        pid, _, origin = instance(exe, data)
        request(origin, "/api/people", "POST", {"name": PROBE})
        schema = snapshot(data)
        info = request(origin, "/api/update", "PUT", {"folder": str(candidate.parent)})
        assert info["available"] and info["schema_compatibility"]["compatible"]
        for action, source, before, after in [
            ("apply", candidate, old[1], new[1]), ("rollback", Path(f"{exe}.onceki"), new[1], old[1])
        ]:
            receipt = Path(f"{source}.schema.json")
            original_receipt = receipt.read_bytes()
            manifest(source, schema + 1)
            try:
                request(origin, "/api/update/" + action, "POST", {})
            except HTTPError as exc:
                with exc:
                    assert exc.code == 400 and "uyumluluk" in json.load(exc)["error"].lower()
            else:
                raise AssertionError("Incompatible package was accepted")
            assert digest(exe) == before and snapshot(data) == schema
            receipt.write_bytes(original_receipt)
            result = request(origin, "/api/update/" + action, "POST", {})
            assert result["started"]
            status = Path(result["status_path"]).resolve()
            assert status.parent == (work / "tmp").resolve()
            assert status.name == f"vardiya-update-{result['operation_id']}.json"
            backup = data.parent / "backups" / result["backup"]
            assert backup.resolve().parent == (data.parent / "backups").resolve() and snapshot(backup) == schema
            backups.append(result["backup"])
            try:
                pid, hwnd, origin = instance(exe, data, excluded=pid)
                await_ready(status)
            except TimeoutError:
                previous = Path(f"{exe}.onceki")
                print(json.dumps({"stage": action, "initial_process_exit": proc.poll(),
                                  "current_sha256": digest(exe), "expected_sha256": after,
                                  "rollback_copy": digest(previous) if previous.is_file() else None,
                                  "startup_status": status.read_text(encoding="utf-8-sig") if status.is_file() else None}),
                      flush=True)
                raise
            assert digest(exe) == after and snapshot(data) == schema
            assert request(origin, "/api/people")["people"] == [{"name": PROBE, "id": 1, "active": True}]
            assert json.loads(Path(f"{exe}.schema.json").read_bytes())["executable_sha256"] == after
            assert digest(Path(f"{exe}.onceki")) == before
            assert json.loads(Path(f"{exe}.onceki.schema.json").read_bytes())["executable_sha256"] == before
        assert len(set(backups)) == 2
        ctypes.windll.user32.PostMessageW(hwnd, 0x10, 0, 0)
        proc.wait(15)
        return {"from_sha256": old[1], "to_sha256": new[1], "schema": schema,
                "update_and_rollback_restarted": True, "incompatible_actions_refused": 2,
                "loaded_handshakes": 2, "previous_pairs_verified": 2,
                "reopened_verified_backups": len(backups), "person_preserved": True}
    finally:
        kernel.TerminateJobObject(job, 0)
        kernel.CloseHandle(job)


if __name__ == "__main__":
    initial, target = map(package, sys.argv[1:])
    temporary = Path(tempfile.mkdtemp(prefix="update-116-"))
    try:
        result = run(initial, target, temporary)
    finally:
        # WebView2 can release its profile cache just after the job has terminated.
        deadline = time.monotonic() + 30
        while True:
            try:
                shutil.rmtree(temporary)
                break
            except PermissionError as exc:
                if exc.winerror not in (5, 32) or time.monotonic() >= deadline:
                    raise
                time.sleep(.25)
    (target[0].parent / "update-roundtrip.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
