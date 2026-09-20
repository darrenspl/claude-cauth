# Install Cauth from a checkout or a remotely evaluated copy of this script.
#
# Local execution delegates to install.cmd, which owns the Windows venv and launcher.
# Remote execution clones or safely fast-forwards a managed checkout first. The source,
# venv, and command directories can be overridden with CAUTH_APP_DIR, CAUTH_VENV, and
# CAUTH_BIN. See docs/adr/0006 for the cross-platform decision.

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

function Write-Step {
    param([string]$Message)
    Write-Host "  $Message"
}

function Normalize-RepoUrl {
    param([string]$Url)
    return (($Url.TrimEnd("/")) -replace "\.git$", "")
}

function Invoke-Git {
    param([string[]]$GitArgs)
    & git @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw "git $($GitArgs -join ' ') exited with status $LASTEXITCODE"
    }
}

function Install-LocalCheckout {
    param([string]$RepoDir)
    $Installer = Join-Path $RepoDir "install.cmd"
    if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
        throw "install.cmd was not found in $RepoDir"
    }
    & $Installer
    if ($LASTEXITCODE -ne 0) {
        throw "install.cmd exited with status $LASTEXITCODE"
    }
}

function Install-Cauth {
    $LocalRepo = $null
    if ($PSScriptRoot) {
        $Candidate = [System.IO.Path]::GetFullPath($PSScriptRoot)
        if ((Test-Path -LiteralPath (Join-Path $Candidate "cauth.py") -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $Candidate "install.cmd") -PathType Leaf)) {
            $LocalRepo = $Candidate
        }
    }

    if ($LocalRepo) {
        Install-LocalCheckout -RepoDir $LocalRepo
        return
    }

    if ([string]::IsNullOrWhiteSpace($env:CAUTH_REPO_URL)) {
        throw "Set CAUTH_REPO_URL to the Git repository URL for a remote install, or run install.ps1 from a checkout"
    }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "git is required for a remote install"
    }

    $RepoUrl = $env:CAUTH_REPO_URL
    $AppDir = if ($env:CAUTH_APP_DIR) {
        $env:CAUTH_APP_DIR
    } else {
        Join-Path $HOME ".local\share\claude-cauth\app"
    }
    $AppDir = [System.IO.Path]::GetFullPath($AppDir)
    $HomeDir = [System.IO.Path]::GetFullPath($HOME).TrimEnd("\", "/")
    $AppRoot = [System.IO.Path]::GetPathRoot($AppDir).TrimEnd("\", "/")
    if (($AppDir.TrimEnd("\", "/") -eq $HomeDir) -or
        ($AppDir.TrimEnd("\", "/") -eq $AppRoot)) {
        throw "CAUTH_APP_DIR must name a dedicated application directory"
    }

    Write-Step "remote  $RepoUrl"
    Write-Step "app     $AppDir"

    if ((Test-Path -LiteralPath $AppDir) -and
        -not (Test-Path -LiteralPath (Join-Path $AppDir ".git") -PathType Container)) {
        throw "$AppDir already exists and is not a Git checkout"
    }

    if (Test-Path -LiteralPath (Join-Path $AppDir ".git") -PathType Container) {
        $CurrentOrigin = (& git -C $AppDir remote get-url origin 2>$null)
        if ($LASTEXITCODE -ne 0) {
            throw "$AppDir has no readable origin remote"
        }
        $CurrentOrigin = ($CurrentOrigin | Select-Object -First 1).Trim()
        if ((Normalize-RepoUrl $CurrentOrigin) -ne (Normalize-RepoUrl $RepoUrl)) {
            throw "$AppDir belongs to a different origin: $CurrentOrigin"
        }

        $CurrentBranch = (& git -C $AppDir symbolic-ref --quiet --short HEAD 2>$null)
        if (($LASTEXITCODE -ne 0) -or ($CurrentBranch.Trim() -ne "main")) {
            throw "$AppDir must be on main, not a different branch or detached HEAD"
        }
        $Dirty = (& git -C $AppDir status --porcelain)
        if ($LASTEXITCODE -ne 0) {
            throw "could not inspect $AppDir"
        }
        if ($Dirty) {
            throw "$AppDir has local changes; refusing to overwrite them"
        }

        Write-Step "updating managed checkout"
        Invoke-Git @("-C", $AppDir, "fetch", "--quiet", "origin", "main:refs/remotes/origin/main")
        Invoke-Git @("-C", $AppDir, "merge", "--ff-only", "--quiet", "origin/main")
    } else {
        $ParentDir = Split-Path -Parent $AppDir
        New-Item -ItemType Directory -Force -Path $ParentDir | Out-Null
        Write-Step "cloning managed checkout"
        Invoke-Git @("clone", "--quiet", "--depth", "1", "--branch", "main", "--single-branch", $RepoUrl, $AppDir)
    }

    Install-LocalCheckout -RepoDir $AppDir
}

Install-Cauth
