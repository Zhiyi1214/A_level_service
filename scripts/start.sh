#!/bin/bash

# 进入项目根目录（与 docker-compose.yml、app.py 同级）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.." || exit 1

# AI Assistant Startup Script
# 用于快速启动应用

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     AI Assistant - Powered by Dify         ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════╝${NC}"
echo ""

# 检查Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python3 not found. Please install Python 3.8+${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo -e "${GREEN}✓ Python ${PYTHON_VERSION}${NC}"

# 检查并创建虚拟环境
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv venv
    echo -e "${GREEN}✓ Virtual environment created${NC}"
fi

# 激活虚拟环境
echo -e "${YELLOW}Activating virtual environment...${NC}"
source venv/bin/activate
echo -e "${GREEN}✓ Virtual environment activated${NC}"

# 安装依赖
echo -e "${YELLOW}Installing dependencies...${NC}"
pip install -q -r requirements.txt
echo -e "${GREEN}✓ Dependencies installed${NC}"

# 检查.env文件
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}Creating .env file...${NC}"
    cp .env.example .env
    echo -e "${YELLOW}⚠️  Please edit .env file and set your Dify API key${NC}"
    echo -e "${YELLOW}Then run this script again${NC}"
    echo ""
    echo -e "${BLUE}Example:${NC}"
    echo "DIFY_API_URL=http://localhost/v1"
    echo "DIFY_API_KEY=your_actual_api_key_here"
    exit 0
fi

# 检查API Key
if ! grep -q "DIFY_API_KEY=your_api_key_here" .env; then
    echo -e "${YELLOW}Checking .env configuration...${NC}"
    echo -e "${GREEN}✓ Configuration found${NC}"
else
    echo -e "${RED}❌ Please set DIFY_API_KEY in .env file${NC}"
    exit 1
fi

echo ""
echo -e "${BLUE}╔════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║          Starting Application              ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════╝${NC}"
echo ""

# 显示启动信息
echo -e "${GREEN}🚀 Server starting...${NC}"
echo -e "${BLUE}📍 Web UI: http://localhost:5000${NC}"
echo -e "${BLUE}🔗 Dify API: $(grep DIFY_API_URL .env | cut -d= -f2-)${NC}"
echo -e "${BLUE}📚 Docs: http://localhost:5000${NC}"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop the server${NC}"
echo ""

# 启动应用
python dev.py

