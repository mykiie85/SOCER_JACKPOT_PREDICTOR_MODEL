@echo off
REM Windows runner — uses EdgeBot's virtualenv (has all model dependencies).
REM Schedule with Task Scheduler daily at 20:00; main.py gates the actual send.
cd /d "%~dp0"
"C:\Users\mykii\Downloads\Betting algorithim\edgebot_env\Scripts\python.exe" main.py %*
