@echo off
if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe -m s3_browser.main %*
) else (
    python -m s3_browser.main %*
)
