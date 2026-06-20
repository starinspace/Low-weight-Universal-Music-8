@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title LUM8 EXE Builder - Conda

set "RAW=https://raw.githubusercontent.com/starinspace/Low-weight-Universal-Music-8/main"

echo.
echo === LUM8: create encode.exe och decode.exe ===
echo Mapp: %CD%
echo.

if defined CONDA_PREFIX (
    if exist "%CONDA_PREFIX%\python.exe" (
        set "PY=%CONDA_PREFIX%\python.exe"
        echo Anvander conda Python:
        echo   %CONDA_PREFIX%\python.exe
        goto :python_ok
    )
)

where python >nul 2>nul
if %errorlevel%==0 (
    set "PY=python"
    echo Anvander Python fran PATH.
    goto :python_ok
)

where py >nul 2>nul
if %errorlevel%==0 (
    set "PY=py -3"
    echo Anvander Python Launcher.
    goto :python_ok
)

echo FEL: Python hittades inte.
echo Om du anvander conda, kor forst:
echo   conda activate neon
echo och kontrollera sedan:
echo   where python
pause
exit /b 1

:python_ok
echo.
%PY% --version
if errorlevel 1 goto :fail

if not exist "lum8_encoder.py" (
    echo Laddar ner lum8_encoder.py...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -UseBasicParsing -Uri '%RAW%/lum8_encoder.py' -OutFile 'lum8_encoder.py'"
    if errorlevel 1 goto :fail
)

if not exist "lum8_decoder.py" (
    echo Laddar ner lum8_decoder.py...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -UseBasicParsing -Uri '%RAW%/lum8_decoder.py' -OutFile 'lum8_decoder.py'"
    if errorlevel 1 goto :fail
)

echo.
echo Installerar beroenden i aktiv miljo...
%PY% -m pip install --upgrade pip
if errorlevel 1 goto :fail

%PY% -m pip install numpy soundfile numba zstandard pyinstaller
if errorlevel 1 goto :fail

echo.
echo Bygger encode.exe...
%PY% -m PyInstaller --clean --onefile --name encode ^
  --collect-all numba ^
  --collect-all llvmlite ^
  --collect-all soundfile ^
  --collect-all zstandard ^
  lum8_encoder.py
if errorlevel 1 goto :fail

echo.
echo Bygger decode.exe...
%PY% -m PyInstaller --clean --onefile --name decode ^
  --collect-all numba ^
  --collect-all llvmlite ^
  --collect-all soundfile ^
  --collect-all zstandard ^
  lum8_decoder.py
if errorlevel 1 goto :fail

if not exist "bin" mkdir "bin"
copy /Y "dist\encode.exe" "bin\encode.exe" >nul
copy /Y "dist\decode.exe" "bin\decode.exe" >nul

echo.
echo KLART.
echo Filer skapade:
echo   %CD%\bin\encode.exe
echo   %CD%\bin\decode.exe
echo.
echo Testa:
echo   bin\encode.exe input.wav output.lum8
echo   bin\decode.exe input.lum8 output.wav
echo.
pause
exit /b 0

:fail
echo.
echo FEL: Bygget misslyckades.
echo Kontrollera raden ovanfor for exakt fel.
pause
exit /b 1
