# Example Output with Claude Haiku Enabled

When you run the script with `ANTHROPIC_API_KEY` set, here's what you'll see:

## High Confidence Match (No Claude needed)
```
[8/1378] Matching: "Please Don't Tell East Village"
  ✓ Matched to: "Please Don't Tell" @ East Village
     Confidence: 92.3%
     Group: PDT/Crif Dogs
     FLY Amount: 250,000
```
**No Claude call** - confidence already high!

---

## Low Confidence Match WITH Location Boost
```
[2/1378] Matching: "gertrude's Prospect Heights"
     🤖 Asking Claude about location: 'Prospect Heights'... YES (boosting +20%)
  ✓ Matched to: "gertrude's" @ Prospect Heights
     Confidence: 94.1% [+Claude boost]
     Group: Gertrude's
     FLY Amount: 250,000
```
**One Claude call** - improved from 74.1% to 94.1%!

---

## Low Confidence Match WITHOUT Location Boost
```
[6/1378] Matching: "The Fragile Flour"
     🤖 Asking Claude about location: 'Downtown'... NO
  ⚠ Matched to: "Frannie & The Fox" @ Downtown
     Confidence: 66.7%
     Group: Hotel Emeline
     FLY Amount: 250,000
```
**One Claude call** - no boost (location doesn't help)

---

## Efficiency Summary
- **Total deals**: 1,378
- **Maximum Claude calls**: ~900 (only for low-confidence matches)
- **Claude calls per deal**: 0 or 1 (never more!)
- **Processing time**: ~2-3 minutes total

Compare to the buggy version:
- ❌ ~1.3 million Claude calls
- ❌ Would take hours
- ❌ Would cost hundreds of dollars

