@echo off
setlocal
cd /d "%~dp0"
where cmake >nul 2>nul
if errorlevel 1 (
 echo Install CMake, Git and Visual Studio 2022 Build Tools with Desktop development with C++.
 pause
 exit /b 1
)
cmake -S . -B build -A x64 -DCMAKE_INSTALL_PREFIX="%CD%/dist"
if errorlevel 1 goto fail
cmake --build build --config Release --target lum8 lum8codec
if errorlevel 1 goto fail
cmake --install build --config Release --prefix "%CD%/dist" --component LUM8
if errorlevel 1 goto fail
echo Ready: dist\bin\lum8.exe
pause
exit /b 0
:fail
echo Build failed. See error above.
pause
exit /b 1
