#!/usr/bin/env python3
"""Build standalone executables for the TeamTalk tools with PyInstaller.

Each tool is frozen into a one-file executable that bundles the official
``TeamTalk5.py`` Python interface and the native library (``libTeamTalk5.so``
on Linux, ``libTeamTalk5.dylib`` on macOS, ``TeamTalk5.dll`` on Windows) so the
result runs without a separately installed SDK.

The SDK files are bundled into ``TeamTalkPy/`` and ``TeamTalk_DLL/`` subdirs of
the frozen archive, matching the layout the upstream ``TeamTalk5.py`` wrapper
expects: it adds ``../TeamTalk_DLL`` as a DLL directory and loads the native
library from there, and imports the Python interface from ``TeamTalkPy/``.
Keeping this layout means the wrapper's own discovery code finds the bundled
files at runtime with no monkeypatching required.

Requirements:
  * PyInstaller installed in the current interpreter (``pip install pyinstaller``).
  * The TeamTalk 5 SDK extracted somewhere the tools can find it.  Set
    ``TEAMTALK_SDK_PATH`` to the extracted SDK root, or rely on the same
    search logic the tools use (``TEAMTALK_SDK_PYTHON`` / ``TEAMTALK_SDK_LIBRARY``
    for explicit file paths).

Outputs go to ``dist/`` (one executable per tool).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import tt_teamtalk

# Display name -> source script.  The names match the historical hyphenated
# launchers so the executables drop in as direct replacements.
TOOLS = {
    "tt-suite": "tt_suite.py",
    "tt-spammer": "tt_spammer.py",
    "tt-message-spammer": "tt_message_spammer.py",
    "tt-leave-join-spammer": "tt_leave_join_spammer.py",
    "ttbot-the-offender": "ttbot_the_offender.py",
}


def find_sdk_files() -> tuple[Path, Path]:
    """Locate the SDK Python interface and native library to bundle."""

    sdk_python = tt_teamtalk._find_sdk_python()
    sdk_library = tt_teamtalk._find_sdk_library(sdk_python)
    if sdk_python is None or not Path(sdk_python).is_file():
        raise SystemExit(
            "TeamTalk5.py was not found. Extract the TeamTalk 5 SDK and set "
            "TEAMTALK_SDK_PATH to its root directory."
        )
    if sdk_library is None or not Path(sdk_library).is_file():
        raise SystemExit(
            "The TeamTalk native library (libTeamTalk5.so / .dylib, or "
            "TeamTalk5.dll on Windows) was not found. Set TEAMTALK_SDK_PATH "
            "(or TEAMTALK_SDK_LIBRARY) to the extracted SDK."
        )
    return Path(sdk_python), Path(sdk_library)


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit(
            "PyInstaller is not installed. Run: pip install pyinstaller"
        )

    sdk_python, sdk_library = find_sdk_files()
    print(f"SDK python interface: {sdk_python}")
    print(f"SDK native library:  {sdk_library}")

    # PyInstaller uses ';' as the --add-data separator on Windows and ':' on
    # POSIX; os.pathsep matches that on both.
    sep = os.pathsep
    here = Path(__file__).resolve().parent
    dist_dir = here / "dist"
    build_dir = here / "build"
    # Bundle into TeamTalkPy/ and TeamTalk_DLL/ subdirs so the upstream
    # TeamTalk5.py wrapper finds them at runtime: on Windows it calls
    # os.add_dll_directory(<wrapperdir>/../TeamTalk_DLL) and loads
    # TeamTalk5.dll from there, and on Linux/macOS the tools' own loader
    # resolves the native lib from <TeamTalkPy>/../TeamTalk_DLL/.
    common = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(build_dir),
        "--add-data",
        f"{sdk_python}{sep}TeamTalkPy",
        "--add-data",
        f"{sdk_library}{sep}TeamTalk_DLL",
    ]
    # Bundle the SDK License.txt into sdk/ so the first-run license gate in a
    # frozen build can print the full terms (it looks for <meipass>/sdk/License.txt).
    license_file = here / "sdk" / "License.txt"
    if not license_file.is_file():
        # Fall back to the License.txt shipped next to the discovered SDK.
        for candidate in (sdk_python.parent.parent.parent / "License.txt",):
            if candidate.is_file():
                license_file = candidate
                break
    if license_file.is_file():
        common += ["--add-data", f"{license_file}{sep}sdk"]

    for name, script in TOOLS.items():
        cmd = common + ["--name", name, str(here / script)]
        print("+ " + " ".join(cmd))
        subprocess.run(cmd, check=True)

    print(f"\nBuilt {len(TOOLS)} executable(s) in {dist_dir}/")
    for entry in sorted(dist_dir.iterdir()):
        print(f"  {entry.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())