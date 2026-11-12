@echo off
rem PDF Indexer launcher.  start.bat        -> start the app and open the browser
rem                        start.bat scan   -> only (re)index the PDFs, then exit
rem Optional environment variables: PORT (default 8000), NO_BROWSER=1,
rem   PDFINDEX_HOME (folder of this program; default: the parent of this script's folder),
rem   PDFINDEX_CONFIG (config.toml path; default: config.toml next to this script)
setlocal
set "REPO=%PDFINDEX_HOME%"
if "%REPO%"=="" set "REPO=%~dp0.."
set "CFG=%PDFINDEX_CONFIG%"
if "%CFG%"=="" set "CFG=%~dp0config.toml"
if "%PORT%"=="" set "PORT=8000"

if not exist "%REPO%\pdfindex\__main__.py" goto norepo
if not exist "%CFG%" goto nocfg
cd /d "%REPO%" || goto fail

if exist ".venv\Scripts\python.exe" goto havevenv
where python >nul 2>nul
if errorlevel 1 goto nopython
echo First run: creating virtual environment and installing requirements...
python -m venv .venv || goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto fail
:havevenv

if /i "%~1"=="scan" goto scan

netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul
if not errorlevel 1 goto portbusy

if "%NO_BROWSER%"=="1" goto run
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 120;$i++){try{Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:%PORT%/api/status' -TimeoutSec 2 | Out-Null; Start-Process 'http://127.0.0.1:%PORT%'; break}catch{Start-Sleep -Milliseconds 500}}"
:run
echo PDF Indexer: http://127.0.0.1:%PORT%   (close this window or press Ctrl+C to stop)
".venv\Scripts\python.exe" -m pdfindex --version
".venv\Scripts\python.exe" -m pdfindex serve --config "%CFG%" --port %PORT%
if errorlevel 1 goto fail
goto end

:scan
echo Indexing PDFs in "%~dp0" ...
".venv\Scripts\python.exe" -m pdfindex scan --config "%CFG%"
if errorlevel 1 goto fail
echo Scan finished.
goto end

:norepo
echo ERROR: Offline Doc Indexer not found at "%REPO%". Set PDFINDEX_HOME to the program folder.
goto fail
:nocfg
echo ERROR: config file not found: "%CFG%"
goto fail
:nopython
echo ERROR: Python was not found on PATH. Install Python 3.11+ from python.org
echo        (tick "Add python.exe to PATH") and run start.bat again.
goto fail
:portbusy
echo ERROR: port %PORT% is already in use. PDF Indexer may already be running at
echo        http://127.0.0.1:%PORT%  - or start with another port: set PORT=8001 ^& start.bat
goto fail
:fail
echo.
echo PDF Indexer did not start. See the message above.
pause
exit /b 1
:end
endlocal
