@echo off
REM Build the native flow_sim engine for Windows x64 and drop it in engines\.
REM Mirrors .github/workflows/build-engines.yml exactly.
REM
REM Run this from an "x64 Native Tools Command Prompt for VS 2022" (Start menu),
REM so cl.exe and the Windows SDK are on PATH. GCC/MinGW won't work -- use MSVC.
setlocal
pushd "%~dp0.."

where cl >nul 2>nul
if errorlevel 1 (
  echo cl.exe not found. Open the "x64 Native Tools Command Prompt for VS 2022" and re-run. 1>&2
  popd & exit /b 1
)

set "out=engines\flow_sim-windows-x86_64.exe"
echo ==^> Building Windows x64 with MSVC
cl /std:c++20 /O2 /EHsc /MT /nologo ^
  /I cpp\salabim++ /I cpp\simulation++ /I cpp\engine /I cpp\third_party ^
  cpp\engine\main.cpp /Fe:%out%
if errorlevel 1 ( echo BUILD FAILED 1>&2 & popd & exit /b 1 )
del main.obj 2>nul

echo ==^> Smoke test (must find a line starting with @@DONE)
%out% flow_designer\sample_flow_rate.json | findstr /b "@@DONE" >nul
if errorlevel 1 ( echo SMOKE TEST FAILED 1>&2 & popd & exit /b 1 )
echo ==^> OK: built and smoke-tested %out%
echo.
echo Now commit it:
echo     git add -f %out%
echo     git commit -m "engines: add flow_sim-windows-x86_64.exe (local build)"
echo     git push
popd
endlocal
