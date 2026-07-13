@echo off
title WCL Reclear Tracker v1.7.6

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
echo WCL Reclear Tracker v1.7.6
echo.
echo 1. Run main tracker
echo 2. Find likely 2-day guilds
echo 3. Show saved likely 2-day guilds
echo 4. Settings and maintenance
echo 5. Check for and install updates
echo 6. Exit
echo.
echo The main tracker automatically asks for and saves your guild and
echo Warcraft Logs details if they have not been set up yet.
echo.
set /p choice=Choose an option: 

if "%choice%"=="1" goto run
if "%choice%"=="2" goto schedulescan
if "%choice%"=="3" goto querytwoday
if "%choice%"=="4" goto settings
if "%choice%"=="5" goto update
if "%choice%"=="6" goto end

echo.
echo Invalid option.
pause
goto menu

:run
cls
%PYTHON_CMD% START_HERE.py
goto finished

:schedulescan
cls
%PYTHON_CMD% START_HERE.py --schedule-scan
goto finished

:querytwoday
cls
%PYTHON_CMD% query_schedule_cache.py --two-day --limit 100
goto finished

:settings
cls
echo Settings and maintenance
echo.
echo 1. Change saved guild
echo 2. Set up or test WCL v2 Client ID and Secret
echo 3. Test one guild's actual raid days and WCL point cost
echo 4. Forget saved WCL v1 API key
echo 5. Forget saved WCL v2 credentials
echo 6. Check current settings
echo 7. Clear cached WCL and comparison data
echo 8. Clear output files
echo 9. Run app self-check
echo 10. Back to main menu
echo.
set /p settings_choice=Choose an option: 

if "%settings_choice%"=="1" goto changeguild
if "%settings_choice%"=="2" goto setupv2
if "%settings_choice%"=="3" goto testschedule
if "%settings_choice%"=="4" goto resetkey
if "%settings_choice%"=="5" goto resetv2
if "%settings_choice%"=="6" goto check
if "%settings_choice%"=="7" goto clearcache
if "%settings_choice%"=="8" goto clearoutput
if "%settings_choice%"=="9" goto selfcheck
if "%settings_choice%"=="10" goto menu

echo.
echo Invalid option.
pause
goto settings

:changeguild
cls
echo Enter the guild details to save for future runs.
echo.
set /p guild=Guild name: 
set /p realm=Realm name: 
set /p region=Region [EU]: 
if "%region%"=="" set region=EU
%PYTHON_CMD% START_HERE.py --configure-guild --guild "%guild%" --realm "%realm%" --region "%region%"
goto settingsfinished

:setupv2
cls
%PYTHON_CMD% START_HERE.py --setup-v2
goto settingsfinished

:testschedule
cls
%PYTHON_CMD% START_HERE.py --test-schedule-guild
goto settingsfinished

:resetkey
cls
%PYTHON_CMD% START_HERE.py --reset-key
goto settingsfinished

:resetv2
cls
%PYTHON_CMD% START_HERE.py --reset-v2
goto settingsfinished

:check
cls
%PYTHON_CMD% START_HERE.py --check-settings
goto settingsfinished

:clearcache
cls
%PYTHON_CMD% START_HERE.py --clear-cache
goto settingsfinished

:clearoutput
cls
%PYTHON_CMD% START_HERE.py --clear-output
goto settingsfinished

:selfcheck
cls
%PYTHON_CMD% self_check.py
goto settingsfinished

:update
cls
%PYTHON_CMD% updater.py
if errorlevel 10 goto updated
goto finished

:updated
echo.
echo Update installed. Press any key to close the tracker.
echo Then reopen START_WCL_RECLEAR_TRACKER.bat to use the new version.
pause >nul
exit

:finished
echo.
echo Finished. Press any key to return to the main menu.
pause >nul
goto menu

:settingsfinished
echo.
echo Finished. Press any key to return to settings.
pause >nul
goto settings

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
