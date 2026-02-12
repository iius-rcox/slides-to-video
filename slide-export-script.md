# Slide Export Script Templates

Two methods for exporting PPTX slides as 1920x1080 PNG images.

## Method A: PowerPoint COM (Windows, preferred)

Generate this PowerShell script. Adapt paths for the user's file locations.

```powershell
# export_slides.ps1 - Export PPTX slides as PNGs using PowerPoint COM
param(
    [Parameter(Mandatory=$true)][string]$PptxPath,
    [Parameter(Mandatory=$true)][string]$OutputDir,
    [int]$Width = 1920,
    [int]$Height = 1080
)

$ErrorActionPreference = 'Stop'

function Export-SlidesToPNG {
    param(
        [string]$PptxPath,
        [string]$OutputDir,
        [object]$PowerPoint,
        [int]$Width,
        [int]$Height
    )

    if (-not (Test-Path $PptxPath)) {
        Write-Error "PPTX not found: $PptxPath"
        return $false
    }

    if (-not (Test-Path $OutputDir)) {
        New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
    }

    Write-Host "  Opening: $(Split-Path $PptxPath -Leaf)"
    # Open(FileName, ReadOnly=msoTrue(-1), Untitled=msoFalse(0), WithWindow=msoFalse(0))
    # IMPORTANT: Use integer constants, NOT .NET interop types
    $pres = $PowerPoint.Presentations.Open($PptxPath, -1, 0, 0)

    try {
        $slideCount = $pres.Slides.Count
        Write-Host "  Exporting $slideCount slides to: $OutputDir"

        for ($i = 1; $i -le $slideCount; $i++) {
            $slide = $pres.Slides.Item($i)
            $filename = "slide_{0:D2}.png" -f $i
            $fullPath = Join-Path $OutputDir $filename
            $slide.Export($fullPath, "PNG", $Width, $Height)
        }

        Write-Host "  Exported $slideCount slides"
        return $true
    }
    finally {
        $pres.Close()
    }
}

# Start PowerPoint COM
Write-Host "Starting PowerPoint..."
$pp = New-Object -ComObject PowerPoint.Application

try {
    $result = Export-SlidesToPNG -PptxPath $PptxPath -OutputDir $OutputDir -PowerPoint $pp -Width $Width -Height $Height
    if ($result) {
        Write-Host "Export complete."
    } else {
        Write-Error "Export failed."
    }
}
finally {
    Write-Host "Closing PowerPoint..."
    $pp.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($pp) | Out-Null
    [System.GC]::Collect()
    [System.GC]::WaitForPendingFinalizers()
}
```

### Key PowerPoint COM Notes

- **Integer constants are mandatory:** `-1` for msoTrue, `0` for msoFalse. Using `[Microsoft.Office.Interop.PowerPoint.MsoTriState]::msoTrue` will fail if the interop assembly isn't loaded.
- **Always close + release COM:** Use `try/finally` with `$pres.Close()`, `$pp.Quit()`, and `ReleaseComObject`.
- **Hidden window:** The `WithWindow=0` parameter keeps the presentation hidden.
- **If PowerPoint hangs:** `Stop-Process -Name POWERPNT -Force` then retry.

## Method B: LibreOffice (cross-platform fallback)

```python
import subprocess
from pathlib import Path

def export_slides_libreoffice(pptx_path: Path, output_dir: Path):
    """Export slides using LibreOffice (headless mode)."""
    output_dir.mkdir(parents=True, exist_ok=True)

    subprocess.run([
        "soffice", "--headless", "--convert-to", "png",
        "--outdir", str(output_dir),
        str(pptx_path),
    ], check=True, capture_output=True, timeout=120)

    # LibreOffice names files differently - rename to standard format
    pngs = sorted(output_dir.glob("*.png"))
    for i, png in enumerate(pngs, 1):
        target = output_dir / f"slide_{i:02d}.png"
        if png != target:
            png.rename(target)

    return len(pngs)
```

### LibreOffice Notes

- Install: `winget install TheDocumentFoundation.LibreOffice` (Windows) or `apt install libreoffice` (Linux)
- Font rendering may differ from PowerPoint — verify exported slides visually
- Some animations/smart-art may not render correctly
- LibreOffice exports slides at default resolution; may need post-processing to resize to 1920x1080

## Choosing the Method

```python
import shutil

def export_slides(pptx_path, output_dir, width=1920, height=1080):
    """Export slides using best available method."""
    if sys.platform == "win32":
        # Try PowerPoint COM first
        try:
            # Write and run the PowerShell script
            ...
            return
        except Exception as e:
            print(f"PowerPoint COM failed: {e}, trying LibreOffice...")

    # Fallback to LibreOffice
    if shutil.which("soffice"):
        export_slides_libreoffice(pptx_path, output_dir)
    else:
        raise RuntimeError("Neither PowerPoint nor LibreOffice found. Install one to export slides.")
```
