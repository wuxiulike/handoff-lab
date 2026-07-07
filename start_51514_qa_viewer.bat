@echo off
setlocal

set "ROOT=%~dp0"
if "%HANDOFF_LAB_HOST%"=="" set "HANDOFF_LAB_HOST=127.0.0.1"
if "%HANDOFF_LAB_PORT%"=="" set "HANDOFF_LAB_PORT=51514"
set "PORT=%HANDOFF_LAB_PORT%"
set "URL=http://127.0.0.1:%PORT%/qa-viewer"

echo [Handoff Lab] Starting service on port %PORT%...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue';" ^
  "$conns=Get-NetTCPConnection -LocalPort %PORT% -State Listen;" ^
  "foreach($conn in $conns){ Stop-Process -Id $conn.OwningProcess -Force }"

timeout /t 1 /nobreak >nul

pushd "%ROOT%"
start "Handoff Lab %PORT%" /min python server.py
popd

echo Waiting for service...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ok=$false;" ^
  "for($i=0;$i -lt 20;$i++){" ^
  "  if(Test-NetConnection -ComputerName 127.0.0.1 -Port %PORT% -InformationLevel Quiet){$ok=$true;break}" ^
  "  Start-Sleep -Milliseconds 500" ^
  "}" ^
  "if(-not $ok){exit 1}"

if errorlevel 1 (
  echo Failed to start service on port %PORT%.
  echo Please check whether Python is installed and server.py can run.
  pause
  exit /b 1
)

echo Service is ready: %URL%
start "" "%URL%"

endlocal
