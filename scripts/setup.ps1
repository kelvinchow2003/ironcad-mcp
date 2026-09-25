# ironcad-mcp setup (PRODUCTION_READINESS_PLAN.md Phase 8.1)
#
# Automates the manual steps in README.md's "Setup" section: checks Python
# version/bitness, creates/reuses the venv, installs the package, detects the
# installed IronCAD version, and prints a ready-to-paste claude_desktop_config.json
# snippet with real absolute paths already filled in.
#
# Idempotent: safe to re-run. Never touches an existing venv beyond `pip
# install -e .` (won't delete/recreate it). Does NOT perform the one-time
# elevated COM registration step (README's "One-time elevated COM
# registration") — that needs an Administrator prompt and is intentionally
# left as an explicit, separate, user-initiated step (see README), not
# something a setup script should do silently.
#
# Usage (from the project root, in an ORDINARY — not elevated — PowerShell):
#   .\scripts\setup.ps1

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPath = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"

Write-Host "== ironcad-mcp setup ==" -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot"

# ---- 1. Python version/bitness check ------------------------------------
$pyCmd = Get-Command py -ErrorAction SilentlyContinue
if (-not $pyCmd) {
    Write-Warning "The 'py' launcher was not found. Install Python 3.11+ (64-bit) from python.org, then re-run this script."
    exit 1
}
$pyInfo = & py -3 -c "import sys; print(sys.version_info[0], sys.version_info[1], 8*'\u0000' if False else ('64bit' if sys.maxsize > 2**32 else '32bit'))" 2>$null
if (-not $pyInfo) {
    Write-Warning "Could not run 'py -3 -c ...' -- is a Python 3.x installed and on PATH via the py launcher?"
    exit 1
}
$parts = $pyInfo -split " "
$pyMajor = [int]$parts[0]
$pyMinor = [int]$parts[1]
$pyBits = $parts[2]
Write-Host "Detected Python: $pyMajor.$pyMinor ($pyBits)"
if ($pyBits -ne "64bit") {
    Write-Warning "A 32-bit Python was detected -- this causes COM load failures (see README). Install 64-bit Python 3.11+ and re-run."
    exit 1
}
if ($pyMajor -lt 3 -or ($pyMajor -eq 3 -and $pyMinor -lt 11)) {
    Write-Warning "Python $pyMajor.$pyMinor found; 3.11+ is required. Install a newer 64-bit Python and re-run."
    exit 1
}

# ---- 2. venv: create if missing, else reuse ------------------------------
if (Test-Path $VenvPython) {
    Write-Host "Reusing existing venv: $VenvPath"
} else {
    Write-Host "Creating venv: $VenvPath"
    & py -3 -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}

# ---- 3. install the package (editable) -----------------------------------
Write-Host "Installing ironcad-mcp (editable) + dependencies..."
& $VenvPython -m pip install --upgrade pip --quiet
& $VenvPython -m pip install -e $ProjectRoot
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# ---- 4. detect IronCAD install + version ---------------------------------
# NOTE: `C:\Program Files\IronCAD\` can contain non-version sibling folders
# (e.g. "PDFViewer") that sort alphabetically ahead of a year-named version
# folder like "2024" -- found live testing this script. Identify the real
# install by the presence of the DLL the elevated registration step targets
# (README's `3iICApiIronCADApp.dll`), not by folder-name sort order.
$IroncadRoot = "C:\Program Files\IronCAD"
$IroncadVersion = $null
$IroncadBinPath = $null
if (Test-Path $IroncadRoot) {
    $candidates = Get-ChildItem $IroncadRoot -Directory | Where-Object {
        Test-Path (Join-Path $_.FullName "bin\3iICApiIronCADApp.dll")
    } | Sort-Object Name -Descending
    if ($candidates.Count -gt 0) {
        $IroncadVersion = $candidates[0].Name
        $IroncadBinPath = Join-Path $candidates[0].FullName "bin"
    }
}
if ($IroncadVersion) {
    Write-Host "Detected IronCAD: $IroncadVersion at $IroncadRoot\$IroncadVersion"
} else {
    Write-Warning "Could not find an IronCAD install with ICAPI DLLs under '$IroncadRoot'. This server needs IronCAD 2024 (or newer) installed and its ICAPI COM components registered -- see README's 'One-time elevated COM registration' section."
}

# ---- 5. check COM registration status (best-effort, read-only) -----------
# NOTE: deliberately do NOT redirect this native call's stderr (e.g. `2>$null`)
# -- found live testing this script that doing so makes PowerShell wrap
# python_ironcad's informational stderr banner as a terminating
# NativeCommandError under $ErrorActionPreference="Stop", crashing this
# script even though the python process itself exits 0. Let stderr pass
# through to the console (it's just IronCAD's own import banner, harmless);
# only stdout (this script's own print() lines) is captured into $statusCheck.
Write-Host ""
Write-Host "Checking API registration status (this launches nothing; safe)..."
$statusCheck = & $VenvPython -c @"
import sys
sys.path.insert(0, r'$ProjectRoot\src')
try:
    from ironcad_mcp.connection import get_state
    from ironcad_mcp.com_worker import start_worker, stop_worker, run_on_com
    import asyncio
    start_worker()
    try:
        st = asyncio.run(run_on_com(lambda: get_state().attach()))
        print('connected=' + str(st.get('connected')))
        print('api_registered=' + str(st.get('api_registered')))
    finally:
        stop_worker()
except Exception as exc:
    print('check_failed=' + repr(exc))
"@
Write-Host $statusCheck
if ($statusCheck -match "api_registered=False") {
    Write-Warning "IronCAD's ICAPI COM components are NOT registered yet. Run this ONCE from an ELEVATED (Administrator) PowerShell, then re-run this script:"
    Write-Host "    & `"$VenvPython`" -m python_ironcad" -ForegroundColor Yellow
} elseif ($statusCheck -match "connected=False") {
    Write-Host "IronCAD does not appear to be running right now -- that's fine for setup; open it before actually using the server." -ForegroundColor Yellow
} elseif ($statusCheck -match "api_registered=True") {
    Write-Host "API registration looks good." -ForegroundColor Green
}

# ---- 6. print the claude_desktop_config.json snippet ---------------------
$BackupDir = Join-Path $env:USERPROFILE "ironcad-mcp-backups"
Write-Host ""
Write-Host "== claude_desktop_config.json snippet (paths filled in for this machine) ==" -ForegroundColor Cyan
$snippet = @"
{
  "mcpServers": {
    "ironcad": {
      "command": "$($VenvPython -replace '\\','\\')",
      "args": ["-m", "ironcad_mcp.server"],
      "env": {
        "IRONCAD_MCP_MODE": "read_only",
        "IRONCAD_MCP_BACKUP_DIR": "$($BackupDir -replace '\\','\\')"
      }
    }
  }
}
"@
Write-Host $snippet
Write-Host ""
Write-Host "Flip IRONCAD_MCP_MODE to read_write to enable building. Open IronCAD first." -ForegroundColor Cyan
Write-Host "Setup complete." -ForegroundColor Green
