@echo off
REM Simple helper to invoke standalone RFID scanner
REM Usage:  scan [--port COMx] [--baud 9600] [--list]

SETLOCAL ENABLEDELAYEDEXPANSION

REM Find python (assumes environment already activated)
where python >NUL 2>&1
IF ERRORLEVEL 1 (
  echo Python not found in PATH. Activate your environment first.
  EXIT /B 1
)

python "%~dp0scan.py" %*
EXIT /B %ERRORLEVEL%
