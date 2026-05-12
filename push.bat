@echo off
cd /d "%~dp0"

echo.
echo ========================================
echo  investment-advisor push to GitHub
echo ========================================
echo.

git init
git add .

set MSG=update
set /p MSG="Commit message (Enter for 'update'): "
if "%MSG%"=="" set MSG=update

git commit -m "%MSG%"
git remote remove origin 2>nul
git remote add origin https://github.com/phantasm1220/investment-advisor.git
git branch -M main
git push -u origin main --force

echo.
if %ERRORLEVEL% == 0 (
    echo Done! Upload successful.
) else (
    echo Error occurred. Check messages above.
)
echo.
pause
