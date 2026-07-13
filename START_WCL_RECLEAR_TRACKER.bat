@echo off
title WCL Reclear Tracker v1.6.22

rem Find a working Python command. The standard Windows installer often
rem provides "py" without adding "python" to PATH.
set "PYTHON_CMD="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"

if not defined PYTHON_CMD (
    python --version >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    python3 --version >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python3"
)

if not defined PYTHON_CMD goto nopython

:menu
cls
echo WCL Reclear Tracker v1.6.22
echo.
echo 1. Run tracker using saved/configured guild
echo 2. Run tracker with guild entered now
echo 3. Save/change guild profile
echo 4. Check settings
echo 5. Reset saved Warcraft Logs API key
echo 6. Reset saved guild profile
echo 7. Run self-check
echo 8. Clear Warcraft Logs cache
echo 9. Clear output files
echo 10. Run EU comparison
echo 11. Set up/test WCL v2 OAuth
echo 12. Reset saved WCL v2 OAuth
echo 13. Test v2 guild reports
echo 14. Test guild discovery only
echo 15. Schedule scan only
echo 16. Show cached likely 2-day guilds
echo 17. Test WoWProgress 1-2 raids/week
echo 18. Exit
echo.
set /p choice=Choose an option: 

if "%choice%"=="1" goto run
if "%choice%"=="2" goto runguild
if "%choice%"=="3" goto saveguild
if "%choice%"=="4" goto check
if "%choice%"=="5" goto resetkey
if "%choice%"=="6" goto resetguild
if "%choice%"=="7" goto selfcheck
if "%choice%"=="8" goto clearcache
if "%choice%"=="9" goto clearoutput
if "%choice%"=="10" goto comparison
if "%choice%"=="11" goto setupv2
if "%choice%"=="12" goto resetv2
if "%choice%"=="13" goto testv2reports
if "%choice%"=="14" goto testdiscovery
if "%choice%"=="15" goto schedulescan
if "%choice%"=="16" goto querytwoday
if "%choice%"=="17" goto testwowprogress
if "%choice%"=="18" goto end

echo.
echo Invalid option.
pause
goto menu

:run
cls
%PYTHON_CMD% START_HERE.py
echo.
echo Finished. Press any key to return to menu.
pause >nul
goto menu

:runguild
cls
echo Enter guild details for this run only.
echo.
set /p guild=Guild name: 
set /p realm=Realm name: 
set /p region=Region [EU]: 
if "%region%"=="" set region=EU
%PYTHON_CMD% START_HERE.py --guild "%guild%" --realm "%realm%" --region "%region%"
echo.
echo Finished. Press any key to return to menu.
pause >nul
goto menu

:saveguild
cls
echo Enter guild details to save globally.
echo.
set /p guild=Guild name: 
set /p realm=Realm name: 
set /p region=Region [EU]: 
if "%region%"=="" set region=EU
%PYTHON_CMD% START_HERE.py --guild "%guild%" --realm "%realm%" --region "%region%" --save-guild
echo.
echo Finished. Press any key to return to menu.
pause >nul
goto menu

:check
cls
%PYTHON_CMD% START_HERE.py --check-settings
echo.
echo Finished. Press any key to return to menu.
pause >nul
goto menu

:resetkey
cls
%PYTHON_CMD% START_HERE.py --reset-key
echo.
echo Finished. Press any key to return to menu.
pause >nul
goto menu

:resetguild
cls
%PYTHON_CMD% START_HERE.py --reset-guild
echo.
echo Finished. Press any key to return to menu.
pause >nul
goto menu

:selfcheck
cls
%PYTHON_CMD% self_check.py
echo.
echo Finished. Press any key to return to menu.
pause >nul
goto menu

:clearcache
cls
%PYTHON_CMD% START_HERE.py --clear-cache
echo.
echo Finished. Press any key to return to menu.
pause >nul
goto menu

:clearoutput
cls
%PYTHON_CMD% START_HERE.py --clear-output
echo.
echo Finished. Press any key to return to menu.
pause >nul
goto menu

:comparison
cls
%PYTHON_CMD% START_HERE.py --comparison
echo.
echo Finished. Press any key to return to menu.
pause >nul
goto menu

:setupv2
cls
%PYTHON_CMD% START_HERE.py --setup-v2
echo.
echo Finished. Press any key to return to menu.
pause >nul
goto menu

:resetv2
cls
%PYTHON_CMD% START_HERE.py --reset-v2
echo.
echo Finished. Press any key to return to menu.
pause >nul
goto menu

:testv2reports
cls
%PYTHON_CMD% START_HERE.py --test-v2-reports
echo.
echo Finished. Press any key to return to menu.
pause >nul
goto menu

:testdiscovery
cls
%PYTHON_CMD% START_HERE.py --test-discovery
echo.
echo Finished. Press any key to return to menu.
pause >nul
goto menu

:schedulescan
cls
%PYTHON_CMD% START_HERE.py --schedule-scan
echo.
echo Finished. Press any key to return to menu.
pause >nul
goto menu

:querytwoday
cls
%PYTHON_CMD% query_schedule_cache.py --two-day --limit 100
echo.
echo Finished. Press any key to return to menu.
pause >nul
goto menu


:testwowprogress
cls
%PYTHON_CMD% START_HERE.py --test-wowprogress
echo.
echo Finished. Press any key to return to menu.
pause >nul
goto menu

:end
exit

:nopython
cls
echo Python could not be started using py -3, python, or python3.
echo.
echo If Python is installed, open Command Prompt and try:
echo     py -3 --version
echo     python --version
echo.
echo If one command works, restart Windows or reinstall Python with
echo "Add python.exe to PATH" enabled.
echo.
pause
exit /b 1
