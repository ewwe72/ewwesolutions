@echo off
REM Task Scheduler wrapper for the Discord-alerting crypto momentum bot.
REM Sibling of momentum/scripts/run_momentum.bat; different working dir,
REM different state file, different Alpaca account, different webhook.

cd /d "C:\path\to\crypto_momentum"
python -m scripts.run_live --state state\momentum_state.json
exit /b %ERRORLEVEL%
