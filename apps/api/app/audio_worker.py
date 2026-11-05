"""Disposable decoder process. Apply OS resource limits before importing native codecs."""

import json
import os
import sys
from pathlib import Path

_job_handle = None


def resource_limits(memory_mib: int, cpu_seconds: int, max_processes: int = 1, file_mib: int = 16):
    global _job_handle
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", w.DWORD),
                ("min_working", ctypes.c_size_t),
                ("max_working", ctypes.c_size_t),
                ("active", w.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", w.DWORD),
                ("scheduling", w.DWORD),
            ]

        class Counters(ctypes.Structure):
            _fields_ = [(f"counter_{i}", ctypes.c_uint64) for i in range(6)]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", Counters),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        kernel.SetInformationJobObject.restype = w.BOOL
        kernel.GetCurrentProcess.restype = w.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        kernel.AssignProcessToJobObject.restype = w.BOOL
        handle = kernel.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x100 | 0x8 | 0x2000
        limits.basic.active = max_processes
        limits.process_memory = memory_mib * 1024 * 1024
        if (
            not handle
            or not kernel.SetInformationJobObject(
                handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            )
            or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess())
        ):
            raise RuntimeError("resource limit installation failed")
        _job_handle = handle
        return "windows-job-object"
    if sys.platform == "linux":
        import resource

        memory = memory_mib * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_mib * 1024 * 1024, file_mib * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        return "linux-rlimit"
    raise RuntimeError("unsupported resource limit platform")


def main():
    target = Path(sys.argv[1])
    try:
        envelope = json.loads((target / "request.json").read_text(encoding="utf-8"))
        platform = resource_limits(envelope["memory_mib"], envelope["cpu_seconds"])
        from app.audio import AssessmentInput, assess_bytes

        result = assess_bytes(
            (target / "input.audio").read_bytes(),
            AssessmentInput.model_validate(envelope["assessment"]),
        )
        result["execution"] = {
            "mode": "isolated-process-v1",
            "resource_limits": platform,
            "pid": os.getpid(),
        }
        payload = {"ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001 -- never expose decoder/OS paths and private input
        import re

        error = str(exc)
        code = (
            error
            if re.fullmatch(r"AUDIO_[A-Z_]+|NONFINITE_AUDIO", error)
            else "AUDIO_PROCESS_FAILED"
        )
        payload = {"ok": False, "error": code}
    (target / "response.json").write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
