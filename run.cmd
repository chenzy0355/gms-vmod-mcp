@echo off
REM gms-vmod-mcp 启动器（stdio）。用法: run.cmd [--check|--info]
setlocal
set "SRC=%~dp0src"
if exist "%~dp0.venv\Scripts\python.exe" (
  set "PY=%~dp0.venv\Scripts\python.exe"
) else (
  set "PY=python"
)
set "PYTHONPATH=%SRC%"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
"%PY%" -m mfmcp.server %*
