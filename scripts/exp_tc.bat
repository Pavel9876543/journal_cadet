@echo off
setlocal
cd /d %~dp0\..

set OUTPUT=export.csv

if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe manage.py export_temporary_credentials --output "%OUTPUT%"
) else (
  python manage.py export_temporary_credentials --output "%OUTPUT%"
)

if %errorlevel% neq 0 exit /b %errorlevel%

echo Экспорт выполнен: %OUTPUT%
nano "%OUTPUT%"
