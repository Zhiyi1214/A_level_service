#!/bin/bash

# 进入项目根目录（与 docker-compose.yml、app.py 同级）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.." || exit 1

# Test the chat API endpoint

echo "🧪 Testing AI Assistant API..."
echo ""

# Test 1: Health Check
echo "1️⃣  Health Check:"
curl -s http://localhost:8000/api/health | jq .
echo ""

# Test 2: Simple message (FormData)
echo "2️⃣  Testing Chat (FormData):"
curl -s -X POST http://localhost:8000/api/chat \
  -F "message=Hello, how are you?" \
  -F "user_id=test_user" \
  -F "conversation_id=" | jq .
echo ""

# Test 3: Check conversations
echo "3️⃣  Getting Conversations:"
curl -s "http://localhost:8000/api/conversations?user_id=test_user" | jq .
echo ""

echo "✅ Test complete!"

