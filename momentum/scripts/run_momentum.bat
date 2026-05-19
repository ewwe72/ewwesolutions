@echo off
REM Task Scheduler wrapper for the Discord-alerting momentum bot.
REM Scheduling: weekdays 15:35 Poland time (= 9:35 AM ET = 5 min after US open).
REM This batch file exists to give schtasks a stable working-dir + command;
REM trying to inline the cd + python invocation in /tr fights cmd quoting.

cd /d "E:\CLAUDE CODE PLAYSPACE\momentum"
python -m scripts.run_live --state state\momentum_state.json
exit /b %ERRORLEVEL%
