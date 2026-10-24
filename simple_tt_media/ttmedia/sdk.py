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


def _native_dirs(sdk_dir: Path) -> tuple[Path, ...]:
    """Folders that may hold the native library, for a given wrapper folder.

    The official SDK keeps the wrapper in ``TeamTalkPy/`` and the native
    library in a sibling ``TeamTalk_DLL/``; a flat folder (everything in one
    directory) is also supported.
    """
    return (sdk_dir, sdk_dir.parent / "TeamTalk_DLL")


def _find_native_lib(sdk_dir: Path) -> Optional[Path]:
    """Locate the platform's native library near the wrapper folder."""
    for folder in _native_dirs(sdk_dir):
        for name in _native_lib_names():
            candidate = folder / name
            if candidate.is_file():
                return candidate
    return None


def _windows_dll_dir(sdk_dir: Path) -> Optional[Path]:
    """Find the folder that holds TeamTalk5.dll for a given wrapper folder."""
    for folder in _native_dirs(sdk_dir):
        if (folder / "TeamTalk5.dll").is_file():
            return folder
    return None


def _prepare_native(sdk_dir: Path) -> None:
    """Make the native library loadable before importing the wrapper.

    Raises :class:`SdkNotFoundError` only when we find the library but it
    cannot be loaded (a version/architecture mismatch).
    """
    lib_path = _find_native_lib(sdk_dir)

    if sys.platform == "win32":
        # Register the DLL's folder so the wrapper's bare-name load resolves,
        # and pre-load it by absolute path for a clear error on a bad arch.
        dll_dir = _windows_dll_dir(sdk_dir)
        if dll_dir is not None:
            os.add_dll_directory(str(dll_dir))
            log.debug("Added DLL directory %s", dll_dir)
        if lib_path is not None:
            try:
                ctypes.CDLL(str(lib_path))
                log.debug("Pre-loaded %s", lib_path)
            except OSError as exc:
                raise SdkNotFoundError(
                    f"Found {lib_path} but Windows could not load it: {exc}\n"
                    "The most common cause is an architecture mismatch: the "
                    "bundled TeamTalk5.dll is 64-bit, so you must run 64-bit "
                    "Python. On Windows 'N' editions, install the Media "
                    "Feature Pack (the DLL uses Windows Media Foundation)."
                ) from exc
        else:
            log.warning(
                "TeamTalk5.dll not found near %s; relying on PATH / cwd.",
                sdk_dir,
            )
        return

    # Linux / macOS: pre-load the shared library by absolute path with
    # RTLD_GLOBAL so the wrapper's bare-name load resolves to it.
    if lib_path is None:
        log.warning(
            "No native TeamTalk library found near %s; relying on the system "
            "loader to locate it.", sdk_dir,
        )
        return
    try:
        ctypes.CDLL(str(lib_path), mode=getattr(ctypes, "RTLD_GLOBAL", 0))
        log.debug("Pre-loaded native library %s", lib_path)
    except OSError as exc:
        raise SdkNotFoundError(
            f"Found {lib_path} but could not load it: {exc}\n"
            "This usually means a 32/64-bit or platform mismatch between the "
            "wrapper and the native library."
        ) from exc


def bundled_sdk_dir() -> Path:
    """The wrapper folder of the SDK bundled with this project.

    Layout: simple_tt_media/sdk/TeamTalkPy/TeamTalk5.py with the native
    libraries in the sibling simple_tt_media/sdk/TeamTalk_DLL/.
    """
    # ttmedia/sdk.py -> parent is ttmedia/, its parent is the project root.
    return Path(__file__).resolve().parent.parent / "sdk" / "TeamTalkPy"


def find_sdk_dir(configured: str = "") -> Optional[Path]:
    """Return the directory containing ``TeamTalk5.py``, or ``None``.

    Order: an explicit configured path, then the SDK bundled with this
    project, then a few conventional locations relative to the working dir.
    """
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.append(bundled_sdk_dir())
    candidates.extend(Path(g) for g in _DEFAULT_GUESSES)
    # For each candidate, accept it if it holds TeamTalk5.py, or if a known
    # sub-path does (so pointing at an unpacked SDK root also works).
    subdirs = ("", "TeamTalkPy", "Library/TeamTalkPy")
    for cand in candidates:
        for sub in subdirs:
            path = cand / sub if sub else cand
            try:
                if path.is_dir() and _looks_like_sdk_dir(path):
                    return path.resolve()
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
