# FLY Drop Matching Script

This script matches FLY deal names to restaurant locations using fuzzy matching and Claude Haiku for intelligent location matching.

## Setup

### 1. Install Dependencies
```bash
pip3 install anthropic
```

### 2. Set Up Claude API Key

To enable intelligent location matching, you need to set your Anthropic API key:

#### Option A: Temporary (current terminal session only)
```bash
export ANTHROPIC_API_KEY="your-api-key-here"
```

#### Option B: Permanent (recommended)
Add to your `~/.zshrc` file:
```bash
echo 'export ANTHROPIC_API_KEY="your-api-key-here"' >> ~/.zshrc
source ~/.zshrc
```

#### Get Your API Key
1. Go to https://console.anthropic.com/
2. Sign in or create an account
3. Go to API Keys section
4. Create a new API key
5. Copy it and use it in the export command above

## Usage

```bash
python3 flydropmatch.py
```

## How It Works

1. **Fuzzy Matching**: Compares deal names to restaurant names using sequence matching
2. **Location Intelligence**: When confidence is below 90%, Claude Haiku checks if the deal name mentions the location
3. **Always Matches**: Every deal gets matched to the best available restaurant (no threshold)

## Output Files

- `restaurant_fly_matches_all.csv` - All matches (1378 deals)
- `restaurant_fly_matches_high_confidence.csv` - Matches with ≥92% confidence
- `restaurant_fly_matches_review.csv` - Matches between 80-92% confidence (need review)

## Output Columns

- `deal_name` - The FLY deal name
- `restaurant_name` - Matched restaurant name
- `location_name` - Restaurant location (used for matching)
- `match_confidence` - Confidence score
- `restaurant_id` - Unique restaurant ID
- `restaurant_group_id` - Restaurant group ID (FLY allocation level)
- `restaurant_group_name` - Restaurant group name
- `fly_allocation` - FLY drop amount

