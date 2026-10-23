"""Détection des outils de compilation intégrés (dossier `tools/` à côté de ZipToExe.exe).

Arborescence attendue (créée par scripts/fetch_toolchains.ps1) :

    tools/python/python.exe     Python 3 complet + PyInstaller
    tools/node/                 Node.js + npm + @yao-pkg/pkg
    tools/pkg-cache/            binaires de base de pkg (hors-ligne)
    tools/go/                   Go
    tools/mingw/bin/            gcc / g++ / make (MinGW-w64)
    tools/cmake/bin/            CMake
    tools/ninja/                Ninja
    tools/rust/{rustup,cargo}/  Rust (cible windows-gnu)
    tools/dotnet/               SDK .NET
    tools/nuget/                cache NuGet (runtime pack win-x64)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional

_done = False
_found: List[str] = []


def tools_dir() -> Path:
    env = os.environ.get("ZIPTOEXE_TOOLS")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "tools"
    return Path(__file__).resolve().parent.parent / "tools"


def bundled_python() -> Optional[Path]:
    p = tools_dir() / "python" / "python.exe"
    return p if p.is_file() else None


def setup_env() -> List[str]:
    """Ajoute les outils intégrés au PATH du processus. Retourne la liste trouvée."""
    global _done, _found
    if _done:
        return _found
    _done = True
    t = tools_dir()
    if not t.is_dir():
        return _found

    paths: List[Path] = []
    env = {}

    if (t / "python" / "python.exe").is_file():
        _found.append("Python + PyInstaller")

    if (t / "node").is_dir():
        paths.append(t / "node")
        _found.append("Node.js + pkg")
        if (t / "pkg-cache").is_dir():
            env["PKG_CACHE_PATH"] = str(t / "pkg-cache")

    if (t / "go" / "bin").is_dir():
        paths.append(t / "go" / "bin")
        env["GOROOT"] = str(t / "go")
        env["GOTOOLCHAIN"] = "local"
        _found.append("Go")

    if (t / "mingw" / "bin").is_dir():
        paths.append(t / "mingw" / "bin")
        _found.append("MinGW-w64 (gcc/g++)")

    if (t / "cmake" / "bin").is_dir():
        paths.append(t / "cmake" / "bin")
        _found.append("CMake")
    if (t / "ninja").is_dir():
        paths.append(t / "ninja")

    if (t / "rust" / "cargo" / "bin").is_dir():
        paths.append(t / "rust" / "cargo" / "bin")
        env["RUSTUP_HOME"] = str(t / "rust" / "rustup")
        env["CARGO_HOME"] = str(t / "rust" / "cargo")
        _found.append("Rust")

    if (t / "dotnet").is_dir():
        paths.append(t / "dotnet")
        env["DOTNET_ROOT"] = str(t / "dotnet")
        env["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
        env["DOTNET_NOLOGO"] = "1"
        env["DOTNET_SKIP_FIRST_TIME_EXPERIENCE"] = "1"
        if (t / "nuget").is_dir():
            env["NUGET_PACKAGES"] = str(t / "nuget")
        _found.append(".NET SDK")

    os.environ["PATH"] = os.pathsep.join([str(p) for p in paths] + [os.environ.get("PATH", "")])
    os.environ.update(env)
    return _found
