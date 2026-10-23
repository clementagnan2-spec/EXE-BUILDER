# ZipToExe

Application Windows (`.exe`) qui **compile un projet ZIP en `.exe`**.
Elle est elle-même compilée automatiquement par **GitHub Actions**.

## 1. Obtenir ZipToExe via GitHub

1. Créez un dépôt GitHub et poussez-y tout ce dossier (y compris `.github/` et `scripts/`).
2. Onglet **Actions** → le workflow *Build ZipToExe* se lance seul (15 à 30 min).
3. Téléchargez l'artefact **ZipToExe-AllInOne** (ou **ZipToExe-Lite**, sans outils).

Pour publier dans **Releases** : `git tag v1.0.0 && git push origin v1.0.0`.
Pour alléger le paquet : *Actions → Build ZipToExe → Run workflow*, puis décochez Rust et/ou .NET.

## 2. Version « tout-en-un »

Le paquet contient `ZipToExe.exe` + un dossier `tools/` avec **tous les outils** :

| Dossier | Contenu | Langage compilé |
|---|---|---|
| `tools/python` | Python 3.12 + PyInstaller | Python |
| `tools/node`, `tools/pkg-cache` | Node.js 20 + pkg (hors-ligne) | Node.js |
| `tools/go` | Go | Go |
| `tools/mingw` | MinGW-w64 (gcc, g++, make) | C / C++ |
| `tools/cmake`, `tools/ninja` | CMake + Ninja | CMake |
| `tools/rust` | Rust (cargo, cible windows-gnu) | Rust |
| `tools/dotnet`, `tools/nuget` | SDK .NET 8 + runtime win-x64 | C# / F# |

**Installation** : décompressez l'archive dans un dossier **où vous avez le droit d'écrire**
(ex. `C:\ZipToExe` ou `Documents`, pas `Program Files`), gardez `ZipToExe.exe` à côté de `tools/`,
puis lancez `ZipToExe.exe`. Aucune installation, aucun PATH à configurer.
Taille : environ 1,3 Go compressé, 3 Go extrait.

> Si l'archive dépasse 1,9 Go, elle est découpée en volumes `.7z.001`, `.7z.002`… (ouvrir avec 7-Zip).

**Hors-ligne** : Python (compilateur), Go, C/C++, CMake, Node (sans dépendances) et .NET (sans paquets
supplémentaires) fonctionnent sans Internet. Les **dépendances du projet** (`requirements.txt`,
`npm install`, crates Rust, paquets NuGet, modules Go) sont téléchargées si le projet en a besoin.

## 3. Utiliser ZipToExe

1. Lancez `ZipToExe.exe`, choisissez le `.zip` du projet et le dossier de sortie.
2. Cliquez sur **Compiler en .exe**.

Options : nom du `.exe`, fichier d'entrée (si la détection ne convient pas), icône `.ico` (Python), mode sans console.

| Détecté via | Méthode |
|---|---|
| fichiers `.py` | PyInstaller (venv isolé + `requirements.txt`) |
| `package.json` | `@yao-pkg/pkg` |
| `go.mod` | `go build` (GOOS=windows) |
| `Cargo.toml` | `cargo build --release` |
| `.csproj` / `.sln` | `dotnet publish` (single-file, self-contained) |
| `CMakeLists.txt` | CMake + Ninja + gcc |
| fichiers `.c` / `.cpp` | `gcc` / `g++` (statique) |

Sans dossier `tools/` (version Lite), ZipToExe utilise les outils installés sur le PC.

## Sécurité

L'archive est extraite avec protection contre les chemins malveillants (*zip slip*),
mais compiler un projet **exécute ses scripts de build** (`npm install`, `pip install`, etc.).
Ne compilez que des ZIP dont vous avez confiance.

## Développement local

```bash
python src/app.py
pip install pyinstaller
pyinstaller --onefile --windowed --name ZipToExe --paths src src/app.py

# (optionnel, Windows, PowerShell 7 + 7-Zip) télécharger les outils dans ./tools
pwsh scripts/fetch_toolchains.ps1 -Out .\tools
```
