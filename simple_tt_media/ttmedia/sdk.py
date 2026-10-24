"""Locate and load the official TeamTalk 5 Python SDK.

The SDK ships two files that must agree in version:

* ``TeamTalk5.py``   - a ctypes wrapper (importable module)
* the native library - ``libTeamTalk5.so`` (Linux), ``libTeamTalk5.dylib``
  (macOS) or ``TeamTalk5.dll`` (Windows)

The wrapper loads the native library *by bare name*, which only works if the
dynamic loader can already find it.  We handle that for the user, per platform:

* **Linux/macOS**: pre-load the ``.so`` / ``.dylib`` from its absolute path
  with ``RTLD_GLOBAL`` so the wrapper's bare-name load resolves to it (no need
  to set ``LD_LIBRARY_PATH``).
* **Windows**: register the folder holding ``TeamTalk5.dll`` (and its
  dependent DLLs) with ``os.add_dll_directory`` before import. The official
  SDK layout puts ``TeamTalk5.py`` under ``Library/TeamTalkPy`` and the DLLs in
  a sibling ``Library/TeamTalk_DLL``; both that layout and a flat folder work.

Download the SDK from https://bearware.dk/?page_id=419 and point
``sdk.path`` (or the ``TEAMTALK_SDK`` env var) at the folder that holds
``TeamTalk5.py``.
"""

from __future__ import annotations

import ctypes
import importlib
import os
import sys
from pathlib import Path
from typing import Optional

from .log import get_logger

log = get_logger(__name__)

SDK_DOWNLOAD = "https://bearware.dk/?page_id=419"

# Candidate locations to try when no explicit path is configured.
_DEFAULT_GUESSES = (
    "./sdk", "./TeamTalk5", "./TeamTalkPy", "./Library/TeamTalkPy",
    "../sdk", ".",
)


class SdkNotFoundError(RuntimeError):
    """Raised when the TeamTalk SDK cannot be located or loaded."""


def _native_lib_names() -> tuple[str, ...]:
    if sys.platform == "win32":
        return ("TeamTalk5.dll",)
    if sys.platform == "darwin":
        return ("libTeamTalk5.dylib",)
    return ("libTeamTalk5.so",)


def _looks_like_sdk_dir(path: Path) -> bool:
    return (path / "TeamTalk5.py").is_file()


def _windows_dll_dir(sdk_dir: Path) -> Optional[Path]:
    """Find the folder that holds TeamTalk5.dll for a given wrapper folder."""
    if (sdk_dir / "TeamTalk5.dll").is_file():
        return sdk_dir
    # Official SDK layout: TeamTalkPy/ next to TeamTalk_DLL/.
    sibling = sdk_dir.parent / "TeamTalk_DLL"
    if (sibling / "TeamTalk5.dll").is_file():
        return sibling
    return None


def _prepare_native(sdk_dir: Path) -> None:
    """Make the native library loadable before importing the wrapper.

    Raises :class:`SdkNotFoundError` only when we find the library but it
    cannot be loaded (a version/architecture mismatch).
    """
    if sys.platform == "win32":
        dll_dir = _windows_dll_dir(sdk_dir)
        if dll_dir is not None:
            os.add_dll_directory(str(dll_dir))
            log.debug("Added DLL directory %s", dll_dir)
        else:
            log.warning(
                "TeamTalk5.dll not found near %s; relying on PATH / cwd.",
                sdk_dir,
            )
        return

    # Linux / macOS: pre-load the shared library by absolute path.
    for lib_name in _native_lib_names():
        lib_path = sdk_dir / lib_name
        if lib_path.is_file():
            try:
                mode = getattr(ctypes, "RTLD_GLOBAL", 0)
                ctypes.CDLL(str(lib_path), mode=mode)
                log.debug("Pre-loaded native library %s", lib_path)
            except OSError as exc:
                raise SdkNotFoundError(
                    f"Found {lib_path} but could not load it: {exc}\n"
                    "This usually means a 32/64-bit or platform mismatch "
                    "between the wrapper and the native library."
                ) from exc
            return
    log.warning(
        "No native TeamTalk library found in %s; relying on the system "
        "loader to locate it.", sdk_dir,
    )


def find_sdk_dir(configured: str = "") -> Optional[Path]:
    """Return the directory containing ``TeamTalk5.py``, or ``None``."""
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(Path(g) for g in _DEFAULT_GUESSES)
    for cand in candidates:
        try:
            if cand.is_dir() and _looks_like_sdk_dir(cand):
                return cand.resolve()
        except OSError:
            continue
    return None


def load_sdk(configured_path: str = ""):
    """Import and return the ``TeamTalk5`` module.

    Raises :class:`SdkNotFoundError` with actionable guidance on failure.
    """
    # Already importable (e.g. installed on PYTHONPATH)?
    if not configured_path:
        try:
            return importlib.import_module("TeamTalk5")
        except Exception:  # noqa: BLE001 - fall through to directory search
            pass

    sdk_dir = find_sdk_dir(configured_path)
    if sdk_dir is None:
        raise SdkNotFoundError(
            "Could not find the TeamTalk 5 SDK (TeamTalk5.py).\n"
            f"Download it from {SDK_DOWNLOAD} and set `sdk.path` in your "
            "config (or the TEAMTALK_SDK env var) to the folder containing "
            "TeamTalk5.py and the native library."
        )

    _prepare_native(sdk_dir)

    if str(sdk_dir) not in sys.path:
        sys.path.insert(0, str(sdk_dir))
    try:
        return importlib.import_module("TeamTalk5")
    except Exception as exc:  # noqa: BLE001
        raise SdkNotFoundError(
            f"Found the SDK at {sdk_dir} but importing it failed: {exc}\n"
            f"Make sure the native library matches the wrapper version. "
            f"Re-download from {SDK_DOWNLOAD} if in doubt."
        ) from exc
