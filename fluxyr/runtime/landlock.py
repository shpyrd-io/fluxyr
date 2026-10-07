"""Linux filesystem write confinement. Also runs as a standalone stdlib launcher.

Never call restrict_writes() in the engine: it irreversibly restricts the calling
thread. Launch this file in a fresh process instead (no threaded preexec_fn).
Reads, execution, environment variables and network access remain unrestricted.
"""

import argparse
import ctypes
import os
import platform
import subprocess
import sys
from pathlib import Path

# Linux UAPI: include all mutation rights through ABI 3, including TRUNCATE.
# READ_FILE, READ_DIR and EXECUTE are deliberately not handled by this policy.
WRITE_FILE = 1 << 1
MAKE_CHAR = 1 << 6
MAKE_BLOCK = 1 << 11
TRUNCATE = 1 << 14
IOCTL_DEV = 1 << 15
MUTATIONS = WRITE_FILE | sum(1 << bit for bit in range(4, 15))
WRITABLE_DIRECTORY = MUTATIONS & ~(MAKE_CHAR | MAKE_BLOCK)


class LandlockUnavailable(RuntimeError):
    pass


class Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class PathRule(ctypes.Structure):
    _pack_ = 1  # UAPI landlock_path_beneath_attr is packed (12 bytes).
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def _kernel():
    # These architectures use the same Linux syscall numbers. Fail explicitly
    # for others rather than guessing a syscall table.
    if sys.platform != "linux" or platform.machine().lower() not in (
        "x86_64",
        "amd64",
        "aarch64",
        "arm64",
        "riscv64",
    ):
        raise LandlockUnavailable(
            "Landlock requires Linux on x86_64, aarch64 or riscv64; "
            "use a supported Linux host or explicitly select FLUXYR_EXECUTION_MODE=local"
        )
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    libc.prctl.restype = ctypes.c_int
    return libc


def _checked(result, operation):
    if result < 0:
        error = ctypes.get_errno()
        raise LandlockUnavailable(
            f"Landlock {operation}: {os.strerror(error)} (errno {error}). "
            "Check kernel Landlock support and the container seccomp profile; "
            "execution was not started without protection."
        )
    return result


def restrict_writes(directories):
    libc = _kernel()
    abi = _checked(libc.syscall(444, 0, 0, 1), "ABI probe")
    if abi < 3:
        raise LandlockUnavailable(
            f"Landlock ABI {abi} is insufficient: ABI >= 3 is required to block file truncation"
        )
    handled = MUTATIONS | (IOCTL_DEV if abi >= 5 else 0)
    ruleset = Ruleset(handled)
    ruleset_fd = _checked(
        libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0),
        "create ruleset",
    )
    try:

        def allow(path, rights):
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathRule(rights, fd)
                _checked(
                    libc.syscall(445, ruleset_fd, 1, ctypes.byref(rule), 0),
                    "add path rule",
                )
            finally:
                os.close(fd)

        for directory in directories:
            path = Path(directory).resolve(strict=True)
            if not path.is_dir() or path == Path("/"):
                raise ValueError(
                    "Landlock writable roots must be directories other than /"
                )
            allow(str(path), WRITABLE_DIRECTORY)
        # Subprocess DEVNULL redirection is common; no other device is writable.
        allow("/dev/null", WRITE_FILE | TRUNCATE)
        _checked(libc.prctl(38, 1, 0, 0, 0), "set no_new_privs")
        _checked(libc.syscall(446, ruleset_fd, 0), "restrict process")
    finally:
        os.close(ruleset_fd)
    return abi


def launch_command(command, writable):
    # -I -S prevents user site hooks/PYTHONPATH from running before restriction.
    # After exec, the actual action retains the configured environment/venv.
    args = [sys.executable, "-I", "-S", str(Path(__file__).resolve())]
    for path in writable:
        args.extend(["--allow-write", str(path)])
    return [*args, "--", *map(str, command)]


def validate_support(writable):
    """Probe ABI, rule creation and enforcement in a disposable child at startup."""
    args = launch_command([], writable)
    args[-1:] = ["--probe"]
    try:
        result = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            close_fds=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LandlockUnavailable(f"Landlock startup check failed: {exc}") from exc
    if result.returncode:
        raise LandlockUnavailable(
            result.stderr.strip() or "Landlock startup check failed; execution disabled"
        )
    return int(result.stdout.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-write", action="append", default=[])
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        abi = restrict_writes(args.allow_write)
        if args.probe:
            print(abi)
            return 0
        command = args.command
        if command[:1] == ["--"]:
            command = command[1:]
        if not command:
            raise ValueError("A command is required")
        os.execvpe(command[0], command, os.environ)
    except (OSError, ValueError, LandlockUnavailable) as exc:
        print(f"Fluxyr execution protection failed: {exc}", file=sys.stderr)
        return 126


if __name__ == "__main__":
    sys.exit(main())
