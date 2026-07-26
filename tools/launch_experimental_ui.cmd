@echo off
setlocal
set "PROJECT_ROOT=%~dp0.."
set "PYTHON_EXE=D:\miniconda3_repair\envs\waveform-storage\python.exe"

if not exist "%PYTHON_EXE%" (
  echo [ERROR] Python environment was not found:
  echo %PYTHON_EXE%
  pause
  exit /b 1
)

set "PYTHONPATH=%PROJECT_ROOT%\src"
rem The editable-install .pth contains this Chinese workspace path in CP936.
rem Override an inherited UTF-8 mode so Python 3.10 can decode that .pth.
set "PYTHONUTF8=0"
cd /d "%PROJECT_ROOT%"
"%PYTHON_EXE%" "apps\operator_gui\experimental_main.py" %*

if errorlevel 1 (
  echo.
  echo The experimental UI exited with an error.
  pause
)
endlocal
