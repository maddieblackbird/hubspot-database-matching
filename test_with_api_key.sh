#!/bin/bash
# Test script with API key
# Usage: Set your API key and run this script

if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "❌ Please set ANTHROPIC_API_KEY first:"
    echo ""
    echo "   export ANTHROPIC_API_KEY='your-api-key-here'"
    echo ""
    echo "Get your API key from: https://console.anthropic.com/"
    exit 1
fi

echo "✓ API key found, running matcher with Claude Haiku..."
python3 flydropmatch.py
