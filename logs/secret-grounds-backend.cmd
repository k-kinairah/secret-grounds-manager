@echo off
cd /d 
"C:\Users\Kin\secret-grounds-system"
"C:\Users\Kin\secret-grounds-system\.venv\Scripts\python.exe" "-m" "uvicorn" "server.main:app" "--host" "0.0.0.0" "--port" "8000" "--no-access-log" >> "C:\Users\Kin\secret-grounds-system\logs\server-console.log" 2>> "C:\Users\Kin\secret-grounds-system\logs\server-error.log"
