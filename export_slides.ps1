# export_slides.ps1 — Export PPTX slides as 1920x1080 PNGs using PowerPoint COM
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File export_slides.ps1 -PptxPath "file.pptx" -OutputDir "slides/"
#
# Notes:
#   - Uses integer constants: -1 = msoTrue, 0 = msoFalse (interop enums won't load)
#   - Always wrap COM in try/finally for cleanup
#   - If PowerPoint hangs: Stop-Process -Name POWERPNT -Force

param(
    [Parameter(Mandatory=$true)][string]$PptxPath,
    [Parameter(Mandatory=$true)][string]$OutputDir,
    [int]$Width = 1920,
    [int]$Height = 1080
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $PptxPath)) {
    Write-Error "PPTX not found: $PptxPath"
    exit 1
}

if (-not (Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

# Check if already exported
$existingPngs = @(Get-ChildItem -Path $OutputDir -Filter 'slide_*.png' -ErrorAction SilentlyContinue).Count
# We can't know expected count without opening, so only skip if user passes -SkipIfExists
# For now, always re-export (idempotent — overwrites existing PNGs)

Write-Host "Starting PowerPoint..."
$pp = New-Object -ComObject PowerPoint.Application

try {
    $leaf = Split-Path $PptxPath -Leaf
    Write-Host "  Opening: $leaf"
    # Open(FileName, ReadOnly=msoTrue(-1), Untitled=msoFalse(0), WithWindow=msoFalse(0))
    $pres = $pp.Presentations.Open($PptxPath, -1, 0, 0)

    try {
        $slideCount = $pres.Slides.Count
        Write-Host "  Exporting $slideCount slides to: $OutputDir"

        for ($i = 1; $i -le $slideCount; $i++) {
            $slide = $pres.Slides.Item($i)
            $filename = "slide_{0:D2}.png" -f $i
            $fullPath = Join-Path $OutputDir $filename
            $slide.Export($fullPath, "PNG", $Width, $Height)
            Write-Host "  Exported slide $i/$slideCount"
        }

        Write-Host "  Exported $slideCount slides successfully"
    }
    finally {
        $pres.Close()
    }
}
finally {
    Write-Host "Closing PowerPoint..."
    $pp.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($pp) | Out-Null
    [System.GC]::Collect()
    [System.GC]::WaitForPendingFinalizers()
}
Write-Host "Export complete."
