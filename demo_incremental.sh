#!/bin/bash
# Demonstration of incremental writing

echo "Starting script in background..."
python3 flydropmatch.py > /dev/null 2>&1 &
PID=$!

echo "Script PID: $PID"
echo ""
echo "Watching file grow in real-time (Ctrl+C to stop watching):"
echo ""

# Watch the file grow
for i in {1..10}; do
    sleep 2
    LINES=$(wc -l < restaurant_fly_matches_all.csv 2>/dev/null || echo "0")
    echo "[After ${i}s] restaurant_fly_matches_all.csv has $LINES lines"
done

echo ""
echo "Script is still running (PID $PID)"
echo "You can:"
echo "  - Check progress: wc -l restaurant_fly_matches_all.csv"
echo "  - View results:   tail restaurant_fly_matches_all.csv"
echo "  - Stop script:    kill $PID"
