@echo off
cd /d 
"C:\Users\Kin\secret-grounds-system\dashboard"
"C:\Program Files\nodejs\node.exe" "C:\Users\Kin\secret-grounds-system\dashboard\node_modules\next\dist\bin\next" "start" "-H" "0.0.0.0" "-p" "13002" >> "C:\Users\Kin\secret-grounds-system\logs\dashboard.log" 2>> "C:\Users\Kin\secret-grounds-system\logs\dashboard-error.log"
