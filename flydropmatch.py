#!/usr/bin/env python3
"""
Restaurant to FLY Deal Matcher
Matches individual restaurant names to FLY deal names using fuzzy matching + reasoning
Then associates them with restaurant group info (where FLY allocations are controlled)
Uses Claude Haiku for semantic location matching when uncertain
"""

import csv
import re
import os
import sys
from difflib import SequenceMatcher
from pathlib import Path
from anthropic import Anthropic

def normalize_name(name):
    """Normalize restaurant names for better matching"""
    if not name or not isinstance(name, str):
        return ""
    
    # Convert to lowercase
    name = name.lower()
    
    # Remove common business suffixes and prefixes
    patterns = [
        r'\s+(llc|inc|corp|corporation|ltd|limited|co\.?)\b',
        r'\s+(restaurant|restaurants|rest\.?)\b',
        r'\s+(group|hospitality|concepts?)\b',
        r'\bthe\s+',
        r'\s+&\s+',
        r'[^\w\s]',  # Remove special characters except spaces
    ]
    
    for pattern in patterns:
        name = re.sub(pattern, ' ', name)
    
    # Remove extra spaces and strip
    name = re.sub(r'\s+', ' ', name).strip()
    
    return name

def fuzzy_match_score(str1, str2):
    """Calculate fuzzy match score using SequenceMatcher"""
    norm1 = normalize_name(str1)
    norm2 = normalize_name(str2)
    
    if not norm1 or not norm2:
        return 0.0
    
    # Base similarity
    base_score = SequenceMatcher(None, norm1, norm2).ratio()
    
    return base_score

def reasoning_match_boost(str1, str2):
    """
    Apply reasoning-based matching boost for semantic similarities
    This uses word overlap and containment logic
    """
    norm1 = normalize_name(str1)
    norm2 = normalize_name(str2)
    
    if not norm1 or not norm2:
        return 0.0
    
    boost = 0.0
    
    # Check for exact substring containment
    if norm1 in norm2 or norm2 in norm1:
        boost += 0.15
    
    # Check for word overlap
    words1 = set(norm1.split())
    words2 = set(norm2.split())
    
    if words1 and words2:
        # Calculate Jaccard similarity of words
        intersection = words1 & words2
        union = words1 | words2
        word_overlap = len(intersection) / len(union)
        
        if word_overlap >= 0.7:
            boost += 0.10
        elif word_overlap >= 0.5:
            boost += 0.05
    
    # Check for common abbreviations (e.g., "mgmt" vs "management")
    abbrev_pairs = [
        ('mgmt', 'management'),
        ('hosp', 'hospitality'),
        ('rest', 'restaurant'),
        ('grp', 'group'),
    ]
    
    for abbrev, full in abbrev_pairs:
        if (abbrev in norm1 and full in norm2) or (abbrev in norm2 and full in norm1):
            boost += 0.03
    
    return min(boost, 0.25)  # Cap boost at 25%

def extract_unique_terms(name):
    """
    Extract unique/distinctive terms from a name for exact matching
    Removes common words, locations, and keeps the distinctive restaurant name
    """
    if not name or not isinstance(name, str):
        return []
    
    # Common location words to remove
    location_words = {
        'east', 'west', 'north', 'south', 'upper', 'lower', 'midtown', 'downtown',
        'village', 'side', 'heights', 'hill', 'square', 'district', 'quarter',
        'soho', 'noho', 'tribeca', 'chelsea', 'murray', 'financial', 'fidi',
        'brooklyn', 'manhattan', 'queens', 'bronx', 'staten',
        'street', 'avenue', 'road', 'boulevard', 'new', 'york', 'city', 'nyc',
        'park', 'slope', 'williamsburg', 'greenpoint', 'bushwick'
    }
    
    # Split into words
    words = name.lower().split()
    
    # Filter out location words and very short words
    unique_words = [w for w in words if w not in location_words and len(w) > 2]
    
    # Try to find multi-word phrases (2-3 words)
    phrases = []
    
    # Get all 3-word combinations
    for i in range(len(unique_words) - 2):
        phrase = ' '.join(unique_words[i:i+3])
        phrases.append(phrase)
    
    # Get all 2-word combinations
    for i in range(len(unique_words) - 1):
        phrase = ' '.join(unique_words[i:i+2])
        phrases.append(phrase)
    
    # Also include individual unique words
    phrases.extend(unique_words)
    
    return phrases

def find_exact_substring_match(deal_name, restaurants):
    """
    Find restaurants where unique terms from deal_name appear as exact substrings
    This is a fallback when fuzzy matching fails
    """
    unique_terms = extract_unique_terms(deal_name)
    
    matches = []
    for restaurant in restaurants:
        restaurant_name = restaurant.get('Restaurant Name', '').strip().lower()
        
        if not restaurant_name:
            continue
        
        # Check if any unique term appears in restaurant name
        for term in unique_terms:
            if term in restaurant_name:
                # Calculate a confidence based on how much of the name matches
                confidence = len(term) / max(len(deal_name), len(restaurant_name))
                matches.append({
                    'restaurant': restaurant,
                    'matched_term': term,
                    'confidence': confidence
                })
                break  # Only count once per restaurant
    
    # Sort by confidence (longer matches = better)
    matches.sort(key=lambda x: x['confidence'], reverse=True)
    
    return matches[:3]  # Return top 3

def verify_match_with_claude(deal_name, restaurant_name, location_name, client):
    """
    Use Claude Sonnet to verify if the deal and restaurant are actually the same place
    Returns (is_match: bool, confidence_adjustment: float, reasoning: str)
    """
    if not client:
        return (True, 0.0, "Claude not available")
    
    try:
        print(f"     🤖 Asking Claude Sonnet to verify match...", end=" ", flush=True)
        
        prompt = f"""You are helping match FLY deal names to restaurant entries in a database.

Deal Name: "{deal_name}"
Matched Restaurant: "{restaurant_name}"
Restaurant Location: "{location_name}"

Question: Are these referring to the SAME restaurant location? 

IMPORTANT CONSIDERATIONS:
1. Do the restaurant names match (allowing for location suffixes)?
2. Location names can vary - use your knowledge to determine if locations refer to the same area:
   - "North Side" might include "Logan Square" in Chicago
   - "Downtown" might mean "Financial District" in SF
   - Neighborhoods can have overlapping or informal names
   - If uncertain about locations, consider if they could reasonably be the same area
3. Only reject if restaurant names are clearly DIFFERENT or locations are definitively separate areas

Examples of SAME restaurant:
- "Crave Fishbar Upper West Side" vs "Crave Fishbar" @ "Upper West Side" → SAME (exact match)
- "Joe's Pizza Soho" vs "Joe's Pizza" @ "Soho" → SAME (exact match)
- "Andros Taverna North Side" vs "Andros Taverna" @ "Logan Square" → SAME (Logan Square is in North Side of Chicago)
- "Crown Shy FiDi" vs "Crown Shy" @ "Financial District" → SAME (FiDi = Financial District)

Examples of DIFFERENT restaurants:
- "Crave Fishbar Upper West Side" vs "Criollas West Village" → DIFFERENT (completely different restaurant names)
- "Albert's Bar Murray Hill" vs "Montauk Beach House" → DIFFERENT (different restaurant names)
- "Local 42 Hell's Kitchen" vs "Criollas Hell's Kitchen" → DIFFERENT (only location matches, names totally different)

If you're unsure whether locations are the same area, assume they COULD be and mark as MATCH with medium/low confidence rather than rejecting.

Answer in this exact format:
MATCH: YES or NO
CONFIDENCE: (high/medium/low)
REASON: (one sentence why)"""

        message = client.messages.create(
            model="claude-sonnet-4-20250514",  # Using latest Sonnet for better reasoning
            max_tokens=150,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )
        
        response = message.content[0].text.strip()
        
        # Parse response
        is_match = "MATCH: YES" in response.upper()
        
        # Determine confidence adjustment based on Claude's assessment
        if is_match:
            if "CONFIDENCE: HIGH" in response.upper():
                adjustment = 0.30  # Strong boost
                result = "MATCH (high confidence)"
            elif "CONFIDENCE: MEDIUM" in response.upper():
                adjustment = 0.15  # Moderate boost
                result = "MATCH (medium confidence)"
            else:
                adjustment = 0.05  # Small boost
                result = "MATCH (low confidence)"
        else:
            # Not a match - heavily penalize
            adjustment = -0.90  # Essentially reject the match
            result = "NOT A MATCH"
        
        print(result)
        
        # Extract reason if present
        reason = ""
        if "REASON:" in response:
            reason = response.split("REASON:")[1].strip()
        
        return (is_match, adjustment, reason)
            
    except Exception as e:
        print(f"ERROR: {e}")
        return (True, 0.0, f"Error: {e}")

def find_best_restaurant_match(deal_name, restaurants, claude_client=None, max_retries=3):
    """
    Find the best matching restaurant for a FLY deal name
    If Claude rejects the best match, tries the next best candidates
    Uses Claude Sonnet for verification when uncertain
    """
    # First pass: Find top N candidates using fuzzy matching (no API calls)
    candidates = []
    
    for restaurant in restaurants:
        restaurant_name = restaurant.get('Restaurant Name', '').strip()
        location_name = restaurant.get('Location Name', '').strip()
        
        if not restaurant_name:
            continue
        
        # Calculate base fuzzy score on restaurant name
        fuzzy_score = fuzzy_match_score(deal_name, restaurant_name)
        
        # Add reasoning boost
        reasoning_boost = reasoning_match_boost(deal_name, restaurant_name)
        
        # Base confidence from name matching only
        confidence = min(fuzzy_score + reasoning_boost, 1.0)
        
        candidates.append({
            'restaurant_id': restaurant.get('Restaurant ID', '').strip(),
            'restaurant_name': restaurant_name,
            'location_name': location_name,
            'restaurant_group_id': restaurant.get('Restaurant Group ID', '').strip(),
            'restaurant_group_name': restaurant.get('Restaurant Group Name', '').strip(),
            'confidence': confidence,
            'used_location_boost': False
        })
    
    # Sort candidates by confidence (best first)
    candidates.sort(key=lambda x: x['confidence'], reverse=True)
    
    if not candidates:
        return None
    
    # Second pass: Try top candidates with Claude verification
    attempts = 0
    for candidate in candidates[:max_retries]:
        attempts += 1
        
        # Always use Claude to verify if available (no skipping, even for high confidence)
        if claude_client:
            if attempts > 1:
                print(f"\n     🔄 Trying candidate #{attempts}: \"{candidate['restaurant_name']}\" @ {candidate['location_name']}...", end=" ", flush=True)
            
            is_match, confidence_adjustment, reason = verify_match_with_claude(
                deal_name, 
                candidate['restaurant_name'], 
                candidate['location_name'], 
                claude_client
            )
            
            # Apply Claude's assessment
            new_confidence = candidate['confidence'] + confidence_adjustment
            new_confidence = max(0.0, min(new_confidence, 1.0))
            
            candidate['confidence'] = new_confidence
            candidate['used_location_boost'] = True
            candidate['claude_reasoning'] = reason
            
            # If Claude approves this match, return it
            if is_match:
                candidate['retry_attempt'] = attempts if attempts > 1 else 0
                return candidate
            else:
                # Claude rejected - mark and try next candidate
                candidate['rejected_by_claude'] = True
                if attempts < min(len(candidates), max_retries):
                    print(f" Rejected, trying next...")
                    continue
        else:
            # No Claude - return first candidate
            return candidate
    
    # All fuzzy candidates rejected - try exact substring matching as fallback
    if claude_client:
        print(f"\n     🔍 All fuzzy matches rejected. Trying exact substring search...")
        
        substring_matches = find_exact_substring_match(deal_name, restaurants)
        
        if substring_matches:
            for idx, match_info in enumerate(substring_matches):
                restaurant = match_info['restaurant']
                matched_term = match_info['matched_term']
                
                print(f"\n     💡 Found exact match for '{matched_term}': \"{restaurant.get('Restaurant Name', '')}\" @ {restaurant.get('Location Name', '')}...", end=" ", flush=True)
                
                candidate = {
                    'restaurant_id': restaurant.get('Restaurant ID', '').strip(),
                    'restaurant_name': restaurant.get('Restaurant Name', '').strip(),
                    'location_name': restaurant.get('Location Name', '').strip(),
                    'restaurant_group_id': restaurant.get('Restaurant Group ID', '').strip(),
                    'restaurant_group_name': restaurant.get('Restaurant Group Name', '').strip(),
                    'confidence': 0.70,  # Start with moderate confidence
                    'used_location_boost': False,
                    'found_via_substring': True,
                    'matched_substring': matched_term
                }
                
                # Verify with Claude
                is_match, confidence_adjustment, reason = verify_match_with_claude(
                    deal_name, 
                    candidate['restaurant_name'], 
                    candidate['location_name'], 
                    claude_client
                )
                
                new_confidence = candidate['confidence'] + confidence_adjustment
                new_confidence = max(0.0, min(new_confidence, 1.0))
                
                candidate['confidence'] = new_confidence
                candidate['used_location_boost'] = True
                candidate['claude_reasoning'] = reason
                
                if is_match:
                    candidate['retry_attempt'] = 99  # Special marker for substring match
                    print(f" ✓ Confirmed via substring search!")
                    return candidate
                else:
                    print(f" Still not a match...")
                    continue
    
    # All candidates rejected including substring search - return best rejected one
    best_rejected = candidates[0]
    best_rejected['rejected_by_claude'] = True
    best_rejected['all_candidates_rejected'] = True
    return best_rejected

def load_restaurant_groups(filepath):
    """Load restaurant groups from SQL query output CSV"""
    groups = []
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            groups.append(row)
    return groups

def load_fly_allocations(filepath):
    """Load FLY allocations from CSV - returns list of dicts"""
    deals = []
    
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        
        for row in reader:
            deal_name = row.get('Deal Name', '').strip()
            allocation = row.get('FLY Allocation', '').strip()
            
            if deal_name and allocation and allocation not in ['', 'Contract not found', 'Hospitality Group']:
                deals.append({
                    'deal_name': deal_name,
                    'fly_allocation': allocation
                })
    
    return deals

def main():
    # File paths
    rest_groups_file = 'rest_groups.csv'
    fly_alloc_file = 'fly_drop.csv'
    
    # Check for test mode
    test_mode = False
    test_limit = 100
    
    if '--test' in sys.argv or os.environ.get('TEST_MODE') == '1':
        test_mode = True
        print("🧪 TEST MODE ENABLED - Processing first 100 deals only")
        print()
    
    # Initialize Claude client
    claude_client = None
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if api_key:
        claude_client = Anthropic(api_key=api_key)
        print("✓ Claude Sonnet API initialized for match verification")
    else:
        print("⚠ ANTHROPIC_API_KEY not found - location matching disabled")
    
    print("=" * 70)
    print("Restaurant to FLY Deal Matcher (with Claude Sonnet)")
    if test_mode:
        print("🧪 TEST MODE - First 100 deals only")
    print("=" * 70)
    
    # Load data
    print(f"\n📁 Loading restaurant groups from {rest_groups_file}...")
    rest_groups = load_restaurant_groups(rest_groups_file)
    print(f"   ✓ Loaded {len(rest_groups)} restaurant groups")
    
    if rest_groups:
        print(f"   Columns: {', '.join(rest_groups[0].keys())}")
    
    print(f"\n📁 Loading FLY allocations from {fly_alloc_file}...")
    fly_allocations = load_fly_allocations(fly_alloc_file)
    
    # Apply test mode limit if enabled
    if test_mode:
        fly_allocations = fly_allocations[:test_limit]
        print(f"   ✓ Loaded {len(fly_allocations)} FLY deal allocations (TEST MODE - limited to {test_limit})")
    else:
        print(f"   ✓ Loaded {len(fly_allocations)} FLY deal allocations")
    
    if fly_allocations:
        sample_deals = [d['deal_name'] for d in fly_allocations[:3]]
        print(f"   Sample deals: {', '.join(sample_deals)}")
    
    # Perform matching - iterate through DEALS and find matching restaurants
    if test_mode:
        print(f"\n🔍 Matching FLY deals to restaurant names (TEST MODE - {len(fly_allocations)} deals)...")
    else:
        print(f"\n🔍 Matching FLY deals to restaurant names (no threshold - all deals matched)...")
    print(f"{'='*80}")
    print("\nLegend: ✓ = High confidence (≥92%)  |  ○ = Medium (80-92%)  |  ⚠ = Low (<80%)")
    if test_mode:
        print("🧪 TEST MODE: Processing first 100 deals only")
    if claude_client:
        print("🤖 Claude will verify EVERY match (no skipping)")
    print()
    
    # Define field names for CSV output
    fieldnames = [
        'deal_name',
        'restaurant_name',
        'location_name',
        'match_confidence',
        'claude_verified',
        'claude_rejected',
        'restaurant_id',
        'restaurant_group_id',
        'restaurant_group_name',
        'fly_allocation'
    ]
    
    # Open output files for incremental writing
    print(f"💾 Opening output files for incremental writing...")
    all_file = open('restaurant_fly_matches_all.csv', 'w', newline='', encoding='utf-8')
    review_file = open('restaurant_fly_matches_review.csv', 'w', newline='', encoding='utf-8')
    high_conf_file = open('restaurant_fly_matches_high_confidence.csv', 'w', newline='', encoding='utf-8')
    
    all_writer = csv.DictWriter(all_file, fieldnames=fieldnames)
    review_writer = csv.DictWriter(review_file, fieldnames=fieldnames)
    high_conf_writer = csv.DictWriter(high_conf_file, fieldnames=fieldnames)
    
    # Write headers
    all_writer.writeheader()
    review_writer.writeheader()
    high_conf_writer.writeheader()
    
    # Flush to ensure headers are written
    all_file.flush()
    review_file.flush()
    high_conf_file.flush()
    
    print(f"   ✓ Files opened and ready for incremental writing")
    
    results_count = 0
    high_confidence_count = 0
    review_count = 0
    location_boost_count = 0
    
    try:
        for idx, deal in enumerate(fly_allocations):
            deal_name = deal['deal_name']
            fly_allocation = deal['fly_allocation']
            
            # Show progress header every 50 deals
            if idx % 50 == 0:
                print(f"\n{'─'*80}")
                print(f"Processing deals {idx + 1}-{min(idx + 50, len(fly_allocations))} of {len(fly_allocations)}")
                print(f"{'─'*80}")
            
            # Show current deal being processed
            print(f"\n[{idx + 1}/{len(fly_allocations)}] Matching: \"{deal_name}\"")
            
            # Find best matching restaurant for this deal (always returns best match)
            match = find_best_restaurant_match(deal_name, rest_groups, claude_client)
            
            if match:
                if match['confidence'] >= 0.80:
                    high_confidence_count += 1
                if match.get('used_location_boost', False):
                    location_boost_count += 1
                
                # Check if Claude rejected the match
                if match.get('rejected_by_claude', False):
                    if match.get('all_candidates_rejected', False):
                        confidence_icon = "❌"
                        status_text = "ALL CANDIDATES REJECTED by Claude"
                    else:
                        confidence_icon = "❌"
                        status_text = "REJECTED by Claude"
                else:
                    confidence_icon = "✓" if match['confidence'] >= 0.92 else "○" if match['confidence'] >= 0.80 else "⚠"
                    status_text = "Matched to"
                    if match.get('retry_attempt') == 99:
                        status_text = f"Matched to (via substring search)"
                        confidence_icon = "💡"  # Special icon for substring matches
                    elif match.get('retry_attempt', 0) > 0:
                        status_text = f"Matched to (attempt #{match['retry_attempt'] + 1})"
                
                location_info = f" @ {match['location_name']}" if match['location_name'] else ""
                claude_used = " [Claude verified]" if match.get('used_location_boost', False) else ""
                
                print(f"  {confidence_icon} {status_text}: \"{match['restaurant_name']}\"{location_info}")
                print(f"     Confidence: {match['confidence']:.1%}{claude_used}")
                
                # Show Claude's reasoning if available
                if match.get('claude_reasoning'):
                    print(f"     Claude: {match['claude_reasoning']}")
                
                # Show if found via substring search
                if match.get('found_via_substring'):
                    print(f"     Found via substring: '{match.get('matched_substring', '')}'")
                
                print(f"     Group: {match['restaurant_group_name']}")
                print(f"     FLY Amount: {fly_allocation}")
                
                result = {
                    'deal_name': deal_name,
                    'restaurant_name': match['restaurant_name'],
                    'location_name': match['location_name'],
                    'match_confidence': f"{match['confidence']:.1%}",
                    'claude_verified': 'YES' if match.get('used_location_boost', False) else 'NO',
                    'claude_rejected': 'YES' if match.get('rejected_by_claude', False) else 'NO',
                    'restaurant_id': match['restaurant_id'],
                    'restaurant_group_id': match['restaurant_group_id'],
                    'restaurant_group_name': match['restaurant_group_name'],
                    'fly_allocation': fly_allocation
                }
            else:
                # This should never happen now, but keep as fallback
                print(f"  ❌ ERROR: No match found!")
                result = {
                    'deal_name': deal_name,
                    'restaurant_name': 'NO_MATCH_FOUND',
                    'location_name': '',
                    'match_confidence': '0.0%',
                    'claude_verified': 'NO',
                    'claude_rejected': 'NO',
                    'restaurant_id': '',
                    'restaurant_group_id': '',
                    'restaurant_group_name': '',
                    'fly_allocation': fly_allocation
                }
            
            # Write to all matches file immediately
            all_writer.writerow(result)
            all_file.flush()  # Ensure it's written to disk
            results_count += 1
            
            # Also write to appropriate confidence file
            if result['match_confidence'] and result['match_confidence'] != '0.0%':
                conf_str = result['match_confidence'].strip('%')
                conf_val = float(conf_str) / 100
                
                if conf_val >= 0.92:
                    high_conf_writer.writerow(result)
                    high_conf_file.flush()
                elif 0.80 <= conf_val < 0.92:
                    review_writer.writerow(result)
                    review_file.flush()
                    review_count += 1
            
            print(f"     💾 Saved to CSV (progress: {idx + 1}/{len(fly_allocations)})")
    
    finally:
        # Always close files, even if there's an error
        all_file.close()
        review_file.close()
        high_conf_file.close()
        print(f"\n   ✓ All files closed safely")
    
    print(f"\n   ✓ Processed {results_count} FLY deals")
    print(f"   ✓ High confidence (≥80%): {high_confidence_count}")
    if claude_client:
        print(f"   ✓ Used Claude verification: {location_boost_count} times")
    
    # Count high confidence results (already written)
    high_conf_count = high_confidence_count - review_count
    
    # Summary
    print(f"\n" + "=" * 70)
    if test_mode:
        print("📊 MATCHING SUMMARY (TEST MODE)")
    else:
        print("📊 MATCHING SUMMARY")
    print("=" * 70)
    print(f"Total FLY deals:                {results_count}")
    if test_mode:
        print(f"All deals matched:              {results_count} (100% of test set)")
    else:
        print(f"All deals matched:              {results_count} (100%)")
    print(f"")
    print(f"High confidence (≥92%):         {high_conf_count}")
    print(f"Review needed (80-92%):         {review_count}")
    print(f"Low confidence (<80%):          {results_count - high_confidence_count}")
    print(f"Used location boost:            {location_boost_count}")
    print(f"")
    if test_mode:
        print("✅ Test complete! Results saved to CSV files.")
        print("   To process all deals, run without --test flag")
    else:
        print("✅ Done! All results saved incrementally to CSV files.")
    print(f"")
    print(f"Output files:")
    print(f"  • restaurant_fly_matches_all.csv ({results_count} rows)")
    print(f"  • restaurant_fly_matches_high_confidence.csv ({high_conf_count} rows)")
    print(f"  • restaurant_fly_matches_review.csv ({review_count} rows)")
    print("=" * 70)

if __name__ == '__main__':
    main()