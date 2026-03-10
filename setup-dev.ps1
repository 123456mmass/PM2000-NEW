[CmdletBinding()]
param(
    [switch]$SkipBackend,
    [switch]$SkipFrontend,
    [switch]$RecreateVenv
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = $PSScriptRoot
$BackendDir = Join-Path $RepoRoot "backend"
$FrontendDir = Join-Path $RepoRoot "frontend"
$BackendVenvDir = Join-Path $BackendDir ".venv"
$BackendPython = Join-Path $BackendVenvDir "Scripts\\python.exe"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Ensure-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found in PATH."
    }
}

function Copy-IfMissing {
    param(
        [string]$Source,
        [string]$Destination
    )

    if ((Test-Path $Source) -and -not (Test-Path $Destination)) {
        Copy-Item $Source $Destination
        Write-Host "Created $Destination from template."
    }
}

function Get-Python312Command {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            $version = (& py -3.12 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
            if ($LASTEXITCODE -eq 0 -and $version -eq "3.12") {
                return @{
                    Exe = "py"
                    Args = @("-3.12")
                    Label = "py -3.12"
                }
            }
        } catch {
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        try {
            $version = (& python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
            if ($LASTEXITCODE -eq 0 -and $version -eq "3.12") {
                return @{
                    Exe = "python"
                    Args = @()
                    Label = "python"
                }
            }
        } catch {
        }
    }

    throw "Python 3.12 is required for this project. Install Python 3.12, then run this script again."
}

function Invoke-CommandChecked {
    param(
        [string]$Exe,
        [string[]]$ArgumentList,
        [string]$Description
    )

    Write-Host $Description
    & $Exe @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed: $Exe $($ArgumentList -join ' ')"
    }
}

function Assert-NodeVersion {
    $versionText = (& node --version).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to determine Node.js version."
    }

    $majorText = $versionText.TrimStart("v").Split(".")[0]
    $major = [int]$majorText
    if ($major -lt 18) {
        throw "Node.js 18+ is required. Current version: $versionText"
    }

    Write-Host "Using Node.js $versionText"
}

Write-Host "PM2000 developer setup"
Write-Host "Repository: $RepoRoot"

Ensure-Command "node"
Ensure-Command "npm"
Assert-NodeVersion

$pythonCommand = Get-Python312Command
Write-Host "Using Python via $($pythonCommand.Label)"

Write-Step "Preparing environment files"
Copy-IfMissing (Join-Path $BackendDir ".env.example") (Join-Path $BackendDir ".env")
Copy-IfMissing (Join-Path $FrontendDir ".env.example") (Join-Path $FrontendDir ".env.local")

if (-not $SkipBackend) {
    Write-Step "Setting up backend virtual environment"

    if ($RecreateVenv -and (Test-Path $BackendVenvDir)) {
        Write-Host "Removing existing backend virtual environment..."
        Remove-Item $BackendVenvDir -Recurse -Force
    }

    if (-not (Test-Path $BackendPython)) {
        Invoke-CommandChecked -Exe $pythonCommand.Exe -ArgumentList ($pythonCommand.Args + @("-m", "venv", $BackendVenvDir)) -Description "Creating backend\\.venv"
    } else {
        Write-Host "backend\\.venv already exists. Reusing it."
    }

    Invoke-CommandChecked -Exe $BackendPython -ArgumentList @("-m", "pip", "install", "--upgrade", "pip") -Description "Upgrading pip"
    Invoke-CommandChecked -Exe $BackendPython -ArgumentList @("-m", "pip", "install", "-r", (Join-Path $BackendDir "requirements.txt")) -Description "Installing backend dependencies"
} else {
    Write-Host "Skipping backend setup."
}

if (-not $SkipFrontend) {
    Write-Step "Installing frontend dependencies"
    Push-Location $FrontendDir
    try {
        Invoke-CommandChecked -Exe "npm" -ArgumentList @("install", "--legacy-peer-deps") -Description "Running npm install --legacy-peer-deps"
    } finally {
        Pop-Location
    }
} else {
    Write-Host "Skipping frontend setup."
}

Write-Step "Setup completed"
Write-Host "Backend run command : cd backend; .\\.venv\\Scripts\\python.exe main.py"
Write-Host "Frontend run command: cd frontend; npm run dev"
