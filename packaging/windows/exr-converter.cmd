@echo off
rem Runs EXR Converter and waits for it, so scripts and agents get its output and exit code.
start "" /wait /b "%~dp0exr_converter.exe" %*
exit /b %ERRORLEVEL%
