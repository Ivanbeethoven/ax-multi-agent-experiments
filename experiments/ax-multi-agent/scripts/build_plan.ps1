param()
$ErrorActionPreference = 'Stop'
$planRoot = Split-Path -Parent $PSScriptRoot
$reportRoot = Join-Path $planRoot 'report'
Push-Location -LiteralPath $reportRoot
try {
    & latexmk -xelatex '-outdir=.' '-auxdir=.' -interaction=nonstopmode -halt-on-error -file-line-error main.tex
    if ($LASTEXITCODE -ne 0) { throw 'Windows XeLaTeX build failed.' }
} finally {
    Pop-Location
}
