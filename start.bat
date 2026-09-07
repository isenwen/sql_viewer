@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   SQL 血缘查看器启动中...
echo   http://127.0.0.1:8866/
echo ============================================
python run.py
pause
