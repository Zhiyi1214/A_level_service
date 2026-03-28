#!/usr/bin/env python3
import argparse
import os

import requests


def parse_args():
    parser = argparse.ArgumentParser(description="Test Dify API connection")
    parser.add_argument("--api-key", default=os.getenv("DIFY_API_KEY", ""))
    parser.add_argument("--base-url", default=os.getenv("DIFY_API_URL", "http://localhost/v1"))
    return parser.parse_args()


args = parse_args()
api_key = args.api_key
base_url = args.base_url.rstrip("/")

if not api_key:
    raise SystemExit("Missing API key. Pass --api-key or set DIFY_API_KEY.")

print("Testing Dify API connection...")
print(f"Base URL: {base_url}")
print(f"API Key: {api_key[:20]}...")
print()

try:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "inputs": {},
        "query": "Hello, are you working?",
        "response_mode": "blocking",
        "conversation_id": "",
        "user": "test_user",
    }

    endpoint = f"{base_url}/chat-messages"
    print(f"Endpoint: {endpoint}")

    response = requests.post(endpoint, json=payload, headers=headers, timeout=10)

    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text[:500]}")

except Exception as e:
    print(f"Error: {e}")


