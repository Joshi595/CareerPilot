# CareerPilot installer for Windows. Paste this into PowerShell:
#   powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/Joshi595/CareerPilot/main/install.ps1 | iex"
# It installs uv (a small tool that brings its own Python), installs CareerPilot, adds a Start menu and desktop
# shortcut, and offers to install the AI you use. No admin rights needed.
$ErrorActionPreference = "Stop"
$source = if ($env:CAREERPILOT_SOURCE) { $env:CAREERPILOT_SOURCE } else {
    # a released version (a tag), never whatever is on main
    $tags = (Invoke-RestMethod "https://api.github.com/repos/Joshi595/CareerPilot/tags?per_page=100").name | Where-Object { $_ -match '^v\d+\.\d+(\.\d+)*$' }
    $tag = $tags | Sort-Object { [version]$_.Substring(1) } -Descending | Select-Object -First 1
    if (-not $tag) { throw "Couldn't find a released version of CareerPilot. Nothing was installed." }
    "careerpilot @ https://github.com/Joshi595/CareerPilot/archive/refs/tags/$tag.zip"
}

Write-Host "`n== CareerPilot setup ==`n" -ForegroundColor Cyan

# 1. uv (official installer from astral.sh)
$uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
if (-not (Test-Path $uv)) {
    Write-Host "Installing uv..."
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
}

# 2. CareerPilot itself
Write-Host "Installing CareerPilot..."
& $uv tool install --force $source
$app = Join-Path $env:USERPROFILE ".local\bin\careerpilot-app.exe"

# 3. Shortcuts (no console window: careerpilot-app is the windowless launcher)
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Desktop"), (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"))) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "Start CareerPilot.lnk"))
    $lnk.TargetPath = $app
    $lnk.Description = "Find jobs and tailor your CV"
    $lnk.Save()
}

# 4. The AI the person pays for (each vendor's own installer, only with their OK)
Write-Host "`nWhich AI plan do you have?"
Write-Host "  1) Claude (Pro or Max)"
Write-Host "  2) ChatGPT (Plus or Pro) - Codex"
Write-Host "  3) Google AI Pro / Ultra - Antigravity"
Write-Host "  4) Already installed / skip"
$choice = Read-Host "Type 1, 2, 3 or 4"
switch ($choice) {
    "1" { if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { irm https://claude.ai/install.ps1 | iex } }
    "2" {
        if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
            Write-Host "Codex needs Node.js. Installing it with winget..."
            winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
        }
        npm install -g @openai/codex
    }
    "3" { Start-Process "https://antigravity.google" ; Write-Host "Follow the Antigravity download page that just opened." }
}

Write-Host "`nDone. Starting CareerPilot... (next time: the 'Start CareerPilot' icon on your desktop)`n" -ForegroundColor Green
Start-Process $app
