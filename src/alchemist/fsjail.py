"""A run's kernel sees only its own files: Linux Landlock, applied by the kernel process to itself.

`python -m alchemist.fsjail <ipykernel arguments>` restricts the process to the paths in ALC_JAIL_READ (read and
execute) and ALC_JAIL_WRITE (anything), both separated by os.pathsep, then starts the Jupyter kernel. The rules hold
for the kernel and everything it starts, and cannot be lifted: code in the kernel cannot open another run's folder or
a dataset its run was not given, and cannot ptrace or read the memory of processes outside its domain. Landlock
needs no privileges, only Linux 5.13 or later with Landlock enabled (Ubuntu 22.04+, Debian 12+, RHEL 9+), and a
container runtime that allows its system calls (Docker 23+).

`available()` says whether this machine can do it; `sandbox serve` reports it on /healthz and in each run's
manifest.
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys

SYS_CREATE_RULESET, SYS_ADD_RULE, SYS_RESTRICT_SELF = 444, 445, 446  # the same on every architecture
CREATE_RULESET_VERSION = 1
RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38

EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
READ = EXECUTE | READ_FILE | READ_DIR
FILE_RIGHTS = EXECUTE | WRITE_FILE | READ_FILE | (1 << 14) | (1 << 15)  # rights that apply to a file itself
# Every file-system right Landlock knows, by ABI version: 13 rights in v1, REFER in v2, TRUNCATE in v3, IOCTL_DEV in v5.
ALL_BY_ABI = {1: (1 << 13) - 1, 2: (1 << 14) - 1, 3: (1 << 15) - 1, 4: (1 << 15) - 1}
ALL_LATEST = (1 << 16) - 1

# What any Python process needs to read: the interpreter, its libraries, configuration and the pseudo file systems.
SYSTEM_READ = ("/usr", "/lib", "/lib64", "/lib32", "/bin", "/sbin", "/etc", "/opt", "/proc", "/sys", "/dev", "/nix")
SYSTEM_WRITE = ("/dev/null",)


def _libc():
    return ctypes.CDLL(None, use_errno=True)


def abi() -> int:
    """The Landlock ABI version this machine supports, or 0."""
    if not sys.platform.startswith("linux"):
        return 0
    try:
        v = _libc().syscall(SYS_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(CREATE_RULESET_VERSION))
    except (OSError, AttributeError):
        return 0
    return max(int(v), 0)


def available() -> bool:
    return abi() > 0


def python_paths() -> list[str]:
    """Where this interpreter and its packages live (a venv, uv's managed Pythons, the system's)."""
    paths = {sys.prefix, sys.base_prefix, sys.exec_prefix, os.path.dirname(os.path.realpath(sys.executable))}
    paths.update(p for p in sys.path if p and os.path.isdir(p))
    return sorted({os.path.realpath(p) for p in paths} | paths)


def _check(r: int, what: str) -> int:
    if r < 0:
        err = ctypes.get_errno()
        raise OSError(err, f"landlock {what}: {os.strerror(err)}")
    return r


def restrict(read: list[str], write: list[str]) -> int:
    """Restrict this process (and what it starts) to `read` and `write`; returns the ABI version used.

    Paths that do not exist are skipped. Raises OSError when Landlock is unavailable.
    """
    version = abi()
    if version <= 0:
        raise OSError("Landlock is not available on this machine")
    handled = ALL_BY_ABI.get(version, ALL_LATEST)
    libc = _libc()
    attr = struct.pack("Q", handled)
    fd = _check(libc.syscall(SYS_CREATE_RULESET, ctypes.c_char_p(attr), ctypes.c_size_t(len(attr)),
                             ctypes.c_uint32(0)), "create_ruleset")
    try:
        for paths, rights in ((read, READ), (write, handled)):
            for p in paths:
                try:
                    pfd = os.open(p, os.O_PATH | os.O_CLOEXEC)
                except OSError:
                    continue
                try:
                    allowed = rights & handled
                    if not os.path.isdir(p):
                        allowed &= FILE_RIGHTS
                    rule = struct.pack("=Qi", allowed, pfd)
                    _check(libc.syscall(SYS_ADD_RULE, ctypes.c_int(fd), ctypes.c_int(RULE_PATH_BENEATH),
                                        ctypes.c_char_p(rule), ctypes.c_uint32(0)), f"add_rule {p}")
                finally:
                    os.close(pfd)
        _check(libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), "no_new_privs")
        _check(libc.syscall(SYS_RESTRICT_SELF, ctypes.c_int(fd), ctypes.c_uint32(0)), "restrict_self")
    finally:
        os.close(fd)
    return version


def split(value: str | None) -> list[str]:
    return [p for p in (value or "").split(os.pathsep) if p]


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    # DuckDB's extensions ship in the image under the home folder (see the Dockerfile).
    read = [*SYSTEM_READ, *python_paths(), os.path.expanduser("~/.duckdb"), *split(os.environ.get("ALC_JAIL_READ"))]
    restrict(read, [*SYSTEM_WRITE, *split(os.environ.get("ALC_JAIL_WRITE"))])
    os.execv(sys.executable, [sys.executable, "-m", "ipykernel_launcher", *argv])


if __name__ == "__main__":
    main()
