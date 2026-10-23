<#
  Télécharge et assemble toutes les chaînes de compilation dans le dossier -Out (tools/).
  Utilisé par GitHub Actions, mais exécutable à la main sous Windows (PowerShell 7) :

      pwsh scripts/fetch_toolchains.ps1 -Out .\tools [-SkipRust] [-SkipDotnet]
#>
param(
    [Parameter(Mandatory = $true)][string]$Out,
    [switch]$SkipRust,
    [switch]$SkipDotnet
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

New-Item -ItemType Directory -Force $Out | Out-Null
$Out = (Resolve-Path $Out).Path
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("ziptoexe_dl_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force $tmp | Out-Null

function Download($url, $file) {
    Write-Host "  ↓ $url"
    Invoke-WebRequest $url -OutFile $file -UseBasicParsing -Headers @{ "User-Agent" = "ZipToExe-build" }
}

# Télécharge un .zip et l'installe dans $target (-Flatten : retire le dossier racine du zip)
function Install-Zip($url, $target, [switch]$Flatten) {
    $zip = Join-Path $tmp ([IO.Path]::GetFileName(($url -split "\?")[0]))
    Download $url $zip
    $x = Join-Path $tmp ("x_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    & 7z x $zip "-o$x" -y | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Extraction échouée : $zip" }
    Remove-Item $zip -Force
    $kids = @(Get-ChildItem $x)
    if ($Flatten -and $kids.Count -eq 1 -and $kids[0].PSIsContainer) {
        Move-Item $kids[0].FullName $target
    } else {
        Move-Item $x $target
    }
}

function Get-LatestAsset($repo, $regex) {
    $h = @{ "User-Agent" = "ZipToExe-build" }
    if ($env:GITHUB_TOKEN) { $h["Authorization"] = "Bearer $env:GITHUB_TOKEN" }
    $rel = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" -Headers $h
    $a = $rel.assets | Where-Object { $_.name -match $regex } | Select-Object -First 1
    if (-not $a) { throw "Aucun fichier correspondant à $regex dans $repo" }
    return $a.browser_download_url
}

function Check($what) {
    if ($LASTEXITCODE -ne 0) { throw "Échec : $what (code $LASTEXITCODE)" }
}

# ---------------------------------------------------------------- Python
Write-Host "== Python =="
$base = (python -c "import sys; print(sys.base_prefix)").Trim()
$py = Join-Path $Out "python"
robocopy $base $py /E /NFL /NDL /NJH /NJS /NP `
    /XD "$base\Lib\test" "$base\Lib\idlelib" "$base\Lib\turtledemo" "$base\Doc" "__pycache__" | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy a échoué ($LASTEXITCODE)" }
$global:LASTEXITCODE = 0
& "$py\python.exe" -m pip install --no-warn-script-location --disable-pip-version-check pyinstaller
Check "pip install pyinstaller"

# ---------------------------------------------------------------- Node.js + pkg
Write-Host "== Node.js + pkg =="
$idx = Invoke-RestMethod "https://nodejs.org/dist/index.json"
$nv = ($idx | Where-Object { $_.version -like "v20.*" } | Select-Object -First 1).version
Install-Zip "https://nodejs.org/dist/$nv/node-$nv-win-x64.zip" "$Out\node" -Flatten
$env:PATH = "$Out\node;$env:PATH"
$env:npm_config_prefix = "$Out\node"
npm install -g "@yao-pkg/pkg" --no-audit --no-fund
Check "npm install pkg"
# Pré-chauffe le cache de pkg pour fonctionner hors-ligne
$env:PKG_CACHE_PATH = "$Out\pkg-cache"
$w = Join-Path $tmp "pkgwarm"
New-Item -ItemType Directory -Force $w | Out-Null
Set-Content "$w\hello.js" 'console.log("ok")'
& "$Out\node\pkg.cmd" "$w\hello.js" --targets node20-win-x64 --output "$w\hello.exe"
Check "pkg warmup"

# ---------------------------------------------------------------- Go
Write-Host "== Go =="
$gv = (Invoke-RestMethod "https://go.dev/dl/?mode=json")[0].version
Install-Zip "https://go.dev/dl/$gv.windows-amd64.zip" "$Out\go" -Flatten

# ---------------------------------------------------------------- MinGW-w64 (gcc/g++)
Write-Host "== MinGW-w64 =="
$u = Get-LatestAsset "brechtsanders/winlibs_mingw" '^winlibs-x86_64-posix-seh-gcc-[\d\.]+-mingw-w64ucrt-[\d\.]+-r\d+\.zip$'
Install-Zip $u "$Out\mingw" -Flatten      # le zip contient un dossier « mingw64 »

# ---------------------------------------------------------------- CMake + Ninja
Write-Host "== CMake + Ninja =="
$u = Get-LatestAsset "Kitware/CMake" '^cmake-[\d\.]+-windows-x86_64\.zip$'
Install-Zip $u "$Out\cmake" -Flatten
$u = Get-LatestAsset "ninja-build/ninja" '^ninja-win\.zip$'
Install-Zip $u "$Out\ninja"

# ---------------------------------------------------------------- Rust
if (-not $SkipRust) {
    Write-Host "== Rust =="
    $env:RUSTUP_HOME = "$Out\rust\rustup"
    $env:CARGO_HOME = "$Out\rust\cargo"
    $ri = Join-Path $tmp "rustup-init.exe"
    Download "https://win.rustup.rs/x86_64" $ri
    & $ri -y --no-modify-path --profile minimal --default-host x86_64-pc-windows-gnu --default-toolchain stable
    Check "rustup"
    Remove-Item "$Out\rust\rustup\downloads", "$Out\rust\rustup\tmp" -Recurse -Force -ErrorAction SilentlyContinue
} else { Write-Host "== Rust ignoré ==" }

# ---------------------------------------------------------------- .NET SDK
if (-not $SkipDotnet) {
    Write-Host "== .NET SDK =="
    $dis = Join-Path $tmp "dotnet-install.ps1"
    Download "https://dot.net/v1/dotnet-install.ps1" $dis
    & $dis -Channel 8.0 -InstallDir "$Out\dotnet" -Architecture x64 -NoPath
    $env:DOTNET_ROOT = "$Out\dotnet"
    $env:PATH = "$Out\dotnet;$env:PATH"
    $env:NUGET_PACKAGES = "$Out\nuget"
    $env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
    $env:DOTNET_NOLOGO = "1"
    # Pré-chauffe le cache NuGet (runtime pack win-x64) pour compiler hors-ligne
    $dn = Join-Path $tmp "dnwarm"
    dotnet new console -o $dn --force | Out-Null
    Check "dotnet new"
    dotnet publish $dn -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -o "$dn\out"
    Check "dotnet publish warmup"
} else { Write-Host "== .NET ignoré ==" }

# ---------------------------------------------------------------- Résumé
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "`n== Tailles =="
Get-ChildItem $Out -Directory | ForEach-Object {
    $n = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
    "{0,-12} {1,8:N0} Mo" -f $_.Name, ($n / 1MB)
}
