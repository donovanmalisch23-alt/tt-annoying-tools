"""Locate and load the TeamTalk 5 SDK that ships with this project.

Windows layout (what we bundle and support as a product):

    sdk/TeamTalkPy/TeamTalk5.py        the ctypes wrapper
    sdk/TeamTalk_DLL/TeamTalk5.dll     the native library (x64)

The wrapper's Windows branch calls
``os.add_dll_directory(<wrapper>\\..\\TeamTalk_DLL)`` and then loads
``TeamTalk5`` by bare name, so the native library *must* live in that sibling
``TeamTalk_DLL`` folder. We also pre-load the DLL by absolute path so an
architecture mismatch gives a clear message instead of a cryptic loader error.

The loader also honours an explicit path (``sdk.path`` / ``TTMEDIA_SDK``) that
points at any ``TeamTalkPy`` wrapper folder, which is what the test harness
uses to load a matching SDK build on a non-Windows CI machine. Only Windows
binaries are bundled with the product.
"""

from __future__ import annotations

import ctypes
import importlib
import os
import sys
from pathlib import Path
from typing import Optional

from .errors import SdkError
from .log import get_logger

log = get_logger(__name__)

SDK_DOWNLOAD = "https://bearware.dk/?page_id=419"


def project_root() -> Path:
    # src/ttmedia/sdk.py -> ttmedia -> src -> project root
    return Path(__file__).resolve().parents[2]


def bundled_wrapper_dir() -> Path:
    return project_root() / "sdk" / "TeamTalkPy"


def _native_lib_names() -> tuple[str, ...]:
    if sys.platform == "win32":
        return ("TeamTalk5.dll",)
    if sys.platform == "darwin":
        return ("libTeamTalk5.dylib",)
    return ("libTeamTalk5.so",)


def _is_wrapper_dir(path: Path) -> bool:
    return (path / "TeamTalk5.py").is_file()


def _native_dirs(wrapper_dir: Path) -> tuple[Path, ...]:
    # Native lib may sit beside the wrapper or in a sibling TeamTalk_DLL/.
    return (wrapper_dir, wrapper_dir.parent / "TeamTalk_DLL")


def _find_native(wrapper_dir: Path) -> Optional[Path]:
    for folder in _native_dirs(wrapper_dir):
        for name in _native_lib_names():
            candidate = folder / name
            if candidate.is_file():
                return candidate
    return None


def resolve_wrapper_dir(configured: str = "") -> Optional[Path]:
    """Return the folder that holds TeamTalk5.py, or None."""
    candidates = []
    if configured:
        c = Path(configured).expanduser()
        candidates += [c, c / "TeamTalkPy", c / "Library" / "TeamTalkPy"]
    candidates.append(bundled_wrapper_dir())
    for cand in candidates:
        try:
            if cand.is_dir() and _is_wrapper_dir(cand):
                return cand.resolve()
        except OSError:
            continue
    return None


def _prepare_native(wrapper_dir: Path) -> None:
    native = _find_native(wrapper_dir)
    if sys.platform == "win32":
        dll_dir = None
        for folder in _native_dirs(wrapper_dir):
            if (folder / "TeamTalk5.dll").is_file():
                dll_dir = folder
                break
        if dll_dir is not None:
            os.add_dll_directory(str(dll_dir))
            log.debug("Added DLL directory %s", dll_dir)
        if native is not None:
            try:
                ctypes.CDLL(str(native))
            except OSError as exc:
                raise SdkError(
                    f"Found {native.name} but Windows could not load it: {exc}\n"
                    "This is almost always an architecture mismatch: the bundled "
                    "TeamTalk5.dll is 64-bit, so you must use 64-bit Python. On "
                    "Windows 'N' editions, install the Media Feature Pack."
                ) from exc
        else:
            log.warning("TeamTalk5.dll not found near %s.", wrapper_dir)
        return

    # Non-Windows (test harness only): pre-load the shared library absolutely.
    if native is None:
        log.warning("No native TeamTalk library found near %s.", wrapper_dir)
        return
    try:
        ctypes.CDLL(str(native), mode=getattr(ctypes, "RTLD_GLOBAL", 0))
    except OSError as exc:
        raise SdkError(f"Found {native} but could not load it: {exc}") from exc


def load_sdk(configured_path: str = ""):
    """Import and return the ``TeamTalk5`` module, or raise :class:`SdkError`."""
    wrapper_dir = resolve_wrapper_dir(configured_path)
    if wrapper_dir is None:
        raise SdkError(
            "Could not find the TeamTalk 5 SDK wrapper (TeamTalk5.py).\n"
            f"The bundled SDK should be at {bundled_wrapper_dir()}. If you moved "
            f"it, set sdk.path or TTMEDIA_SDK. (Re-download from {SDK_DOWNLOAD}.)"
        )
    _prepare_native(wrapper_dir)
    if str(wrapper_dir) not in sys.path:
        sys.path.insert(0, str(wrapper_dir))
    try:
        return importlib.import_module("TeamTalk5")
    except SdkError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SdkError(
            f"Found the SDK at {wrapper_dir} but importing it failed: {exc}"
        ) from exc
