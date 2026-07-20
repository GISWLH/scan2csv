# Build portable Windows folder: dist\scan2csv\scan2csv.exe
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m pip install -U pip
python -m pip install -r requirements.txt pyinstaller

if (Test-Path dist) { Remove-Item -Recurse -Force dist }
if (Test-Path build) { Remove-Item -Recurse -Force build }

@"
from scan2csv.cli import main
if __name__ == "__main__":
    raise SystemExit(main())
"@ | Set-Content -Encoding UTF8 .\scan2csv_entry.py

python -m PyInstaller `
  --noconfirm `
  --clean `
  --name scan2csv `
  --onedir `
  --console `
  --add-data "scan2csv/web;scan2csv/web" `
  --add-data "scan2csv/prompts;scan2csv/prompts" `
  --collect-all uvicorn `
  --collect-all fastapi `
  --hidden-import scan2csv.server `
  --hidden-import scan2csv.pipeline.run `
  .\scan2csv_entry.py

Write-Host "Done. Run: dist\scan2csv\scan2csv.exe serve --host 127.0.0.1 --port 8787"
