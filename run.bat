@echo off
setlocal
chcp 65001 >nul
rem Prefer the verified conda environment, fall back to "python" on PATH.
set "PYEXE="
if exist "D:\miniconda3\envs\chem_env\python.exe" set "PYEXE=D:\miniconda3\envs\chem_env\python.exe"
if not defined PYEXE (
  where python >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
  echo [!] 未找到 Python。请安装 Python 3.10+ 并安装 requirements.txt。
  pause
  exit /b 1
)
"%PYEXE%" "%~dp0app.py" %*
if errorlevel 1 (
  echo.
  echo [!] 程序异常退出（代码 %errorlevel%）。
  pause
)
