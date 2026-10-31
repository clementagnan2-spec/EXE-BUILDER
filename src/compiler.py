"""Moteur de compilation de ZipToExe : projet .zip -> .exe

Langages pris en charge : Python, Node.js, Go, Rust, .NET (C#/F#), CMake, C/C++ (gcc).
Les outils sont pris en priorité dans le dossier `tools/` intégré (voir toolchain.py),
sinon dans le PATH de la machine.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from toolchain import bundled_python, tools_dir

IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW
SKIP_DIRS = {"node_modules", ".git", "venv", ".venv", "env", "__pycache__",
             "target", "bin", "obj", "dist", "build", "__MACOSX"}


class BuildError(Exception):
    """Erreur lisible affichée à l'utilisateur."""


@dataclass
class Options:
    name: str = "app"       # nom du .exe final
    entry: str = ""         # fichier d'entrée (optionnel, Python/Node)
    icon: str = ""          # fichier .ico (optionnel, Python)
    windowed: Optional[bool] = None  # sans console : True / False / None = détection auto


@dataclass
class Ctx:
    root: Path
    marker: Optional[Path]
    work: Path
    stage: Path
    opts: Options
    log: Callable[[str], None]


# --------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------
def safe_name(stem: str) -> str:
    return re.sub(r"[^\w\-]+", "_", stem).strip("_") or "app"


def safe_extract(zip_path: Path, dest: Path) -> None:
    """Extraction protégée contre les chemins malveillants (zip slip)."""
    dest = dest.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for m in z.infolist():
            target = (dest / m.filename).resolve()
            if target != dest and dest not in target.parents:
                raise BuildError(f"ZIP invalide (chemin dangereux) : {m.filename}")
        z.extractall(dest)


def unwrap(root: Path) -> Path:
    """Si le zip contient un seul dossier racine, on descend dedans."""
    while True:
        items = [p for p in root.iterdir() if p.name not in ("__MACOSX", ".DS_Store")]
        if len(items) == 1 and items[0].is_dir():
            root = items[0]
        else:
            return root


def walk(root: Path, maxdepth: int = 3):
    base = len(root.parts)
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS)
        if len(Path(dp).parts) - base >= maxdepth:
            dns[:] = []
        yield Path(dp), sorted(fns)


def find_first(root: Path, test) -> Optional[Path]:
    for dp, fns in walk(root):
        for f in fns:
            if test(f):
                return dp / f
    return None


def need(tool: str, hint: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise BuildError(f"Outil '{tool}' introuvable. {hint}")
    return path


STRIP_ENV = {"TCL_LIBRARY", "TK_LIBRARY", "TCL_DATA", "PYTHONHOME", "PYTHONPATH",
             "_MEIPASS2", "PYINSTALLER_RESET_ENVIRONMENT"}


def clean_env(extra: Optional[dict] = None) -> dict:
    """Environnement propre pour les sous-processus (sans les variables de PyInstaller)."""
    env = {k: v for k, v in os.environ.items()
           if k not in STRIP_ENV and not k.startswith("_PYI")}
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    if extra:
        env.update(extra)
    return env


def run(cmd, log, cwd=None, env=None) -> None:
    cmd = [str(c) for c in cmd]
    log("$ " + " ".join(cmd))
    try:
        p = subprocess.Popen(
            cmd, cwd=cwd, env=clean_env(env), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW,
        )
    except OSError as e:
        raise BuildError(f"Impossible de lancer {cmd[0]} : {e}")
    assert p.stdout is not None
    for line in p.stdout:
        log(line.rstrip())
    code = p.wait()
    if code != 0:
        raise BuildError(f"La commande a échoué (code {code}) : {' '.join(cmd[:3])} …")


def has_module(py, module: str) -> bool:
    r = subprocess.run([str(py), "-c", f"import {module}"], capture_output=True,
                       env=clean_env(), creationflags=NO_WINDOW, stdin=subprocess.DEVNULL)
    return r.returncode == 0


def find_python() -> List[str]:
    b = bundled_python()
    if b:
        return [str(b)]
    for cmd in (["py", "-3"], ["python"], ["python3"]):
        if not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd + ["--version"], capture_output=True, text=True,
                               timeout=15, creationflags=NO_WINDOW, env=clean_env(),
                               stdin=subprocess.DEVNULL)
            if r.returncode == 0 and (r.stdout + r.stderr).startswith("Python 3"):
                return cmd
        except Exception:
            continue
    raise BuildError("Python 3 introuvable. Installez-le depuis https://www.python.org "
                     "(cochez « Add to PATH »).")


def stage_files(files: List[Path], stage: Path) -> None:
    stage.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(f, stage / f.name)


# --------------------------------------------------------------------------
# Détection du type de projet
# --------------------------------------------------------------------------
CHECKS = [
    ("node",   lambda f: f == "package.json"),
    ("go",     lambda f: f == "go.mod"),
    ("rust",   lambda f: f == "Cargo.toml"),
    ("dotnet", lambda f: f.endswith((".csproj", ".fsproj", ".sln"))),
    ("cmake",  lambda f: f == "CMakeLists.txt"),
    ("python", lambda f: f.endswith(".py")),
    ("c",      lambda f: f.endswith((".c", ".cpp", ".cc", ".cxx"))),
]


def detect(root: Path) -> Tuple[str, Path]:
    for kind, test in CHECKS:
        m = find_first(root, test)
        if m:
            return kind, m
    raise BuildError("Type de projet non reconnu (Python, Node, Go, Rust, .NET, CMake, C/C++).")


# --------------------------------------------------------------------------
# Compilateurs par langage
# --------------------------------------------------------------------------
def build_python(c: Ctx) -> None:
    py = find_python()
    entry = find_python_entry(c.root, c.opts.entry)
    c.log(f"Point d'entrée : {entry.relative_to(c.root)}")
    venv = c.work / "venv"
    # Python intégré : on réutilise PyInstaller déjà installé (fonctionne hors-ligne)
    sysp = ["--system-site-packages"] if bundled_python() else []
    run(py + ["-m", "venv"] + sysp + [venv], c.log)
    vpy = venv / ("Scripts/python.exe" if IS_WIN else "bin/python")
    if not has_module(vpy, "PyInstaller"):
        run([vpy, "-m", "pip", "install", "pyinstaller"], c.log)
    req = find_first(c.root, lambda f: f == "requirements.txt")
    if req:
        run([vpy, "-m", "pip", "install", "-r", req], c.log, cwd=req.parent)
    dist = c.work / "pydist"
    cmd = [vpy, "-m", "PyInstaller", "--onefile", "--noconfirm", "--clean",
           "--name", c.opts.name, "--distpath", dist,
           "--workpath", c.work / "pybuild", "--specpath", c.work,
           "--paths", c.root]
    windowed = c.opts.windowed
    if windowed is None:
        windowed = detect_gui(c.root)
        c.log("Interface graphique détectée → sans console." if windowed
              else "Aucune interface graphique détectée → avec console.")
    if windowed:
        cmd.append("--windowed")
    if c.opts.icon:
        cmd += ["--icon", c.opts.icon]
    cmd.append(entry)
    run(cmd, c.log, cwd=c.root)
    stage_files([p for p in dist.iterdir() if p.is_file()], c.stage)


GUI_MODULES = {"tkinter", "Tkinter", "customtkinter", "ttkbootstrap", "PyQt5", "PyQt6",
               "PySide2", "PySide6", "wx", "kivy", "pygame", "flet", "dearpygui",
               "pyglet", "toga", "webview", "pystray"}
_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", re.M)


def detect_gui(root: Path) -> bool:
    """True si le projet Python utilise une bibliothèque d'interface graphique."""
    n = 0
    for dp, fns in walk(root, 6):
        for f in fns:
            if not f.endswith((".py", ".pyw")):
                continue
            n += 1
            if n > 300:
                return False
            try:
                text = (dp / f).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if GUI_MODULES & set(_IMPORT_RE.findall(text)):
                return True
    return False


def find_python_entry(root: Path, hint: str) -> Path:
    if hint:
        p = root / hint
        if not p.is_file():
            raise BuildError(f"Fichier d'entrée introuvable dans le zip : {hint}")
        return p
    for name in ("main.py", "app.py", "__main__.py", "run.py", "start.py", "cli.py"):
        p = find_first(root, lambda f, n=name: f == n)
        if p:
            return p
    pys = [dp / f for dp, fns in walk(root) for f in fns
           if f.endswith(".py") and f != "setup.py" and not f.startswith("test_")
           and f != "conftest.py"]
    for p in pys:
        try:
            if "__main__" in p.read_text(encoding="utf-8", errors="ignore"):
                return p
        except OSError:
            pass
    if pys:
        return pys[0]
    raise BuildError("Aucun fichier .py trouvé.")


def build_node(c: Ctx) -> None:
    node_msg = "Installez Node.js depuis https://nodejs.org"
    need("node", node_msg)
    assert c.marker
    d = c.marker.parent
    data = json.loads(c.marker.read_text(encoding="utf-8", errors="ignore") or "{}")
    if data.get("dependencies"):
        npm = need("npm", node_msg)
        run([npm, "install", "--no-audit", "--no-fund"], c.log, cwd=d)
    entry = c.opts.entry or data.get("main") or "index.js"
    if not (d / entry).is_file():
        raise BuildError(f"Point d'entrée Node introuvable : {entry}")
    out = c.stage / f"{c.opts.name}.exe"
    c.stage.mkdir(parents=True, exist_ok=True)
    bundled_pkg = tools_dir() / "node" / "pkg.cmd"
    if bundled_pkg.is_file():
        runner = [bundled_pkg]
    else:
        runner = [need("npx", node_msg), "--yes", "@yao-pkg/pkg"]
    run(runner + [entry, "--targets", "node20-win-x64", "--output", out], c.log, cwd=d)


def build_go(c: Ctx) -> None:
    go = need("go", "Installez Go depuis https://go.dev/dl")
    assert c.marker
    c.stage.mkdir(parents=True, exist_ok=True)
    ldflags = "-s -w" + (" -H=windowsgui" if c.opts.windowed else "")
    run([go, "build", "-ldflags", ldflags, "-o", c.stage / f"{c.opts.name}.exe", "."],
        c.log, cwd=c.marker.parent,
        env={"GOOS": "windows", "GOARCH": "amd64", "CGO_ENABLED": "0"})


def build_rust(c: Ctx) -> None:
    cargo = need("cargo", "Installez Rust depuis https://rustup.rs")
    assert c.marker
    d = c.marker.parent
    env = {}
    if (tools_dir() / "rust").is_dir() and "RUSTFLAGS" not in os.environ:
        env["RUSTFLAGS"] = "-C target-feature=+crt-static"   # exe sans DLL MinGW
    run([cargo, "build", "--release"], c.log, cwd=d, env=env)
    exes = list((d / "target" / "release").glob("*.exe"))
    stage_files(exes, c.stage)


def build_dotnet(c: Ctx) -> None:
    dotnet = need("dotnet", "Installez le SDK .NET depuis https://dotnet.microsoft.com/download")
    assert c.marker
    c.stage.mkdir(parents=True, exist_ok=True)
    run([dotnet, "publish", c.marker, "-c", "Release", "-r", "win-x64",
         "--self-contained", "true", "-p:PublishSingleFile=true", "-o", c.stage],
        c.log, cwd=c.marker.parent)
    for f in list(c.stage.iterdir()):          # on ne garde que l'exe
        if f.suffix.lower() != ".exe":
            shutil.rmtree(f, ignore_errors=True) if f.is_dir() else f.unlink()


def build_cmake(c: Ctx) -> None:
    cmake = need("cmake", "Installez CMake depuis https://cmake.org/download")
    assert c.marker
    d = c.marker.parent
    bdir = c.work / "cmake-build"
    cfg = [cmake, "-S", d, "-B", bdir, "-DCMAKE_BUILD_TYPE=Release"]
    if shutil.which("ninja") and shutil.which("gcc"):
        cfg += ["-G", "Ninja", "-DCMAKE_C_COMPILER=gcc", "-DCMAKE_CXX_COMPILER=g++",
                "-DCMAKE_EXE_LINKER_FLAGS=-static"]
    run(cfg, c.log)
    run([cmake, "--build", bdir, "--config", "Release"], c.log)
    exes = [p for p in bdir.rglob("*.exe") if "CMakeFiles" not in p.parts]
    stage_files(exes, c.stage)


def build_c(c: Ctx) -> None:
    srcs = [dp / f for dp, fns in walk(c.root, 6) for f in fns
            if f.endswith((".c", ".cpp", ".cc", ".cxx"))]
    is_cpp = any(s.suffix != ".c" for s in srcs)
    compiler = need("g++" if is_cpp else "gcc",
                    "Installez MinGW-w64 (https://www.mingw-w64.org) ou MSYS2 et ajoutez-le au PATH.")
    c.stage.mkdir(parents=True, exist_ok=True)
    cmd = [compiler, "-O2", "-static", "-o", c.stage / f"{c.opts.name}.exe"]
    for inc in (c.root, c.root / "include"):
        if inc.is_dir():
            cmd += ["-I", inc]
    if c.opts.windowed:
        cmd.append("-mwindows")
    run(cmd + srcs, c.log, cwd=c.root)


BUILDERS = {
    "python": ("Python (PyInstaller)", build_python),
    "node":   ("Node.js (pkg)", build_node),
    "go":     ("Go", build_go),
    "rust":   ("Rust (cargo)", build_rust),
    "dotnet": (".NET (publish single-file)", build_dotnet),
    "cmake":  ("C/C++ (CMake)", build_cmake),
    "c":      ("C/C++ (gcc)", build_c),
}


# --------------------------------------------------------------------------
# Point d'entrée principal
# --------------------------------------------------------------------------
def compile_zip(zip_path: str, out_dir: str, opts: Options, work: Path,
                log: Callable[[str], None]) -> List[Path]:
    zp = Path(zip_path)
    if not zp.is_file() or not zipfile.is_zipfile(zp):
        raise BuildError("Le fichier sélectionné n'est pas un ZIP valide.")
    if not opts.name or opts.name == "app":
        opts.name = safe_name(zp.stem)

    log(f"Extraction de {zp.name} …")
    src = work / "src"
    src.mkdir(parents=True)
    safe_extract(zp, src)
    root = unwrap(src)

    kind, marker = detect(root)
    label, builder = BUILDERS[kind]
    log(f"Projet détecté : {label}  ({marker.relative_to(root)})")

    stage = work / "stage"
    builder(Ctx(root, marker, work, stage, opts, log))

    produced = [p for p in stage.glob("*") if p.is_file()] if stage.exists() else []
    if not produced:
        raise BuildError("La compilation s'est terminée mais aucun exécutable n'a été produit.")

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    final = []
    for p in produced:
        dst = out / p.name
        shutil.copy2(p, dst)
        final.append(dst)
        log(f"✔ Créé : {dst}")
    return final
