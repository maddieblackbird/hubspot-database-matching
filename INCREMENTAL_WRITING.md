# Incremental Writing Feature

## How It Works

The script writes each match to the CSV files **immediately** after processing, instead of waiting until the end. This means:

### ✅ Benefits

1. **No Lost Progress**: If the script crashes, gets interrupted (Ctrl+C), or has an API error, all already-processed deals are saved
2. **Real-time Progress Tracking**: You can open the CSV files while the script is running to see results
3. **Safe Error Handling**: Files are always properly closed, even if an error occurs

### 📝 Example Scenario

**Without Incremental Writing (old approach):**
```
Processing 1378 deals...
[1/1378] ✓
[2/1378] ✓
[3/1378] ✓
...
[845/1378] ✓
[846/1378] ❌ CRASH! API error
Result: Lost all 846 matches, have to start over
```

**With Incremental Writing (new approach):**
```
Processing 1378 deals...
[1/1378] ✓ → Saved to CSV
[2/1378] ✓ → Saved to CSV
[3/1378] ✓ → Saved to CSV
...
[845/1378] ✓ → Saved to CSV
[846/1378] ❌ CRASH! API error
Result: 845 matches saved! Only need to process the remaining 533 deals
```

### 🔍 What Gets Written

Each deal is immediately written to:

1. **restaurant_fly_matches_all.csv** - Every single match
2. **restaurant_fly_matches_high_confidence.csv** - Only if confidence ≥92%
3. **restaurant_fly_matches_review.csv** - Only if confidence 80-92%

### 💾 File Flushing

After each write, the script calls `flush()` to ensure data is written to disk immediately, not just buffered in memory. This guarantees:
- Data persists even if the script terminates unexpectedly
- You can check progress by opening the files in another program while running

### 🛡️ Error Safety

The script uses a `try/finally` block to ensure files are always closed properly:

```python
try:
    # Process all deals and write results
    for deal in deals:
        result = match_deal(deal)
        write_to_csv(result)
finally:
    # Always close files, even if error occurs
    close_all_files()
```

This means the CSV files will always be valid and complete for whatever was processed, even if interrupted.

