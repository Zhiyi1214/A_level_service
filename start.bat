@echo off
REM AI Assistant Startup Script for Windows

setlocal enabledelayedexpansion

echo.
echo ╔════════════════════════════════════════════╗
echo ║     AI Assistant - Powered by Dify         ║
echo ╚════════════════════════════════════════════╝
echo.

REM 检查Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python not found. Please install Python 3.8+
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo ✓ Python %PYTHON_VERSION%

REM 检查虚拟环境
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
    echo ✓ Virtual environment created
)

REM 激活虚拟环境
echo Activating virtual environment...
call venv\Scripts\activate.bat
echo ✓ Virtual environment activated

REM 安装依赖
echo Installing dependencies...
pip install -q -r requirements.txt
echo ✓ Dependencies installed

REM 检查.env文件
if not exist ".env" (
    echo Creating .env file...
    copy .env.example .env
    echo ⚠️  Please edit .env file and set your Dify API key
    echo Then run this script again
    echo.
    echo Example:
    echo DIFY_API_URL=http://localhost/v1
    echo DIFY_API_KEY=your_actual_api_key_here
    pause
    exit /b 0
)

REM 检查API Key
findstr /c:"DIFY_API_KEY=your_api_key_here" .env >nul 2>&1
if not errorlevel 1 (
    echo ❌ Please set DIFY_API_KEY in .env file
    pause
    exit /b 1
)

echo.
echo ╔════════════════════════════════════════════╗
echo ║          Starting Application              ║
echo ╚════════════════════════════════════════════╝
echo.

echo 🚀 Server starting...
echo 📍 Web UI: http://localhost:5000
echo 📚 Docs: http://localhost:5000
echo.
echo Press Ctrl+C to stop the server
echo.

REM 启动应用
python dev.py

pause

