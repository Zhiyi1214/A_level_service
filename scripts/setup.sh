#!/bin/bash

# 进入项目根目录（与 docker-compose.yml、app.py 同级）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.." || exit 1

# Setup script for AI Assistant

set -e

echo "🔧 Setting up AI Assistant..."

# 检查Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 not found"
    exit 1
fi

echo "✓ Python found: $(python3 --version)"

# 创建虚拟环境
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
    echo "✓ Virtual environment created"
fi

# 激活虚拟环境
source venv/bin/activate

# 安装依赖
echo "📥 Installing dependencies..."
pip install -q --upgrade pip setuptools wheel
pip install -q -r requirements.txt
echo "✓ Dependencies installed"

# 创建.env文件
if [ ! -f ".env" ]; then
    echo "⚙️  Creating .env file..."
    cp .env.example .env
    echo "✓ .env file created (please update with your API key)"
fi

# 创建uploads目录
mkdir -p uploads

echo ""
echo "✅ Setup complete!"
echo ""
echo "📝 Next steps:"
echo "1. Edit .env file and add your Dify API key"
echo "2. Run: source venv/bin/activate"
echo "3. Run: python app.py"
echo ""
echo "Or use: ./scripts/start.sh"

