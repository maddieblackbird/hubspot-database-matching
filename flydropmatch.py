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

def check_location_match_with_claude(deal_name, restaurant_name, location_name, client):
    """
    Use Claude Haiku to check if the deal name and location are related
    Returns a boost score (0.0 to 0.3) if location seems relevant
    """
    if not location_name or not client:
        return 0.0
    
    try:
        print(f"     🤖 Asking Claude about location: '{location_name}'...", end=" ", flush=True)
        
        prompt = f"""Looking at this FLY deal name and restaurant location:

Deal Name: "{deal_name}"
Restaurant Name: "{restaurant_name}"
Location Name: "{location_name}"

Does the deal name contain any reference to the location name, or do they seem to refer to the same place? Consider neighborhood names, street names, or location descriptors.

Answer with just: YES, NO, or MAYBE"""

        message = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=10,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )
        
        response = message.content[0].text.strip().upper()
        
        if "YES" in response:
            print("YES (boosting +20%)")
            return 0.20  # Strong location match boost
        elif "MAYBE" in response:
            print("MAYBE (boosting +10%)")
            return 0.10  # Moderate location match boost
        else:
            print("NO")
            return 0.0
            
    except Exception as e:
        print(f"ERROR: {e}")
        # If API fails, return no boost
        return 0.0

def find_best_restaurant_match(deal_name, restaurants, claude_client=None):
    """
    Find the best matching restaurant for a FLY deal name
    Always returns the best match (no threshold - every deal gets matched)
    Uses Claude Haiku for location matching ONLY on the best candidate when uncertain
    """
    best_match = None
    best_confidence = 0.0
    
    # First pass: Find best match using only fuzzy matching (no API calls)
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
        
        # Track best match (no Claude yet - just fuzzy matching)
        if confidence > best_confidence:
            best_confidence = confidence
            best_match = {
                'restaurant_id': restaurant.get('Restaurant ID', '').strip(),
                'restaurant_name': restaurant_name,
                'location_name': location_name,
                'restaurant_group_id': restaurant.get('Restaurant Group ID', '').strip(),
                'restaurant_group_name': restaurant.get('Restaurant Group Name', '').strip(),
                'confidence': confidence,
                'used_location_boost': False
            }
    
    # Second pass: If best match has low confidence AND has location, try Claude boost
    if best_match and best_match['confidence'] < 0.90 and best_match['location_name'] and claude_client:
        location_boost = check_location_match_with_claude(
            deal_name, 
            best_match['restaurant_name'], 
            best_match['location_name'], 
            claude_client
        )
        
        if location_boost > 0:
            best_match['confidence'] = min(best_match['confidence'] + location_boost, 1.0)
            best_match['used_location_boost'] = True
    
    return best_match

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
    
    # Initialize Claude client
    claude_client = None
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if api_key:
        claude_client = Anthropic(api_key=api_key)
        print("✓ Claude Haiku API initialized for location matching")
    else:
        print("⚠ ANTHROPIC_API_KEY not found - location matching disabled")
    
    print("=" * 70)
    print("Restaurant to FLY Deal Matcher (with Claude Haiku)")
    print("=" * 70)
    
    # Load data
    print(f"\n📁 Loading restaurant groups from {rest_groups_file}...")
    rest_groups = load_restaurant_groups(rest_groups_file)
    print(f"   ✓ Loaded {len(rest_groups)} restaurant groups")
    
    if rest_groups:
        print(f"   Columns: {', '.join(rest_groups[0].keys())}")
    
    print(f"\n📁 Loading FLY allocations from {fly_alloc_file}...")
    fly_allocations = load_fly_allocations(fly_alloc_file)
    print(f"   ✓ Loaded {len(fly_allocations)} FLY deal allocations")
    
    if fly_allocations:
        sample_deals = [d['deal_name'] for d in fly_allocations[:3]]
        print(f"   Sample deals: {', '.join(sample_deals)}")
    
    # Perform matching - iterate through DEALS and find matching restaurants
    print(f"\n🔍 Matching FLY deals to restaurant names (no threshold - all deals matched)...")
    print(f"{'='*80}")
    print("\nLegend: ✓ = High confidence (≥92%)  |  ○ = Medium (80-92%)  |  ⚠ = Low (<80%)")
    print()
    
    # Define field names for CSV output
    fieldnames = [
        'deal_name',
        'restaurant_name',
        'location_name',
        'match_confidence',
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
                
                # Show match result with confidence indicator
                confidence_icon = "✓" if match['confidence'] >= 0.92 else "○" if match['confidence'] >= 0.80 else "⚠"
                location_info = f" @ {match['location_name']}" if match['location_name'] else ""
                claude_used = " [+Claude boost]" if match.get('used_location_boost', False) else ""
                
                print(f"  {confidence_icon} Matched to: \"{match['restaurant_name']}\"{location_info}")
                print(f"     Confidence: {match['confidence']:.1%}{claude_used}")
                print(f"     Group: {match['restaurant_group_name']}")
                print(f"     FLY Amount: {fly_allocation}")
                
                result = {
                    'deal_name': deal_name,
                    'restaurant_name': match['restaurant_name'],
                    'location_name': match['location_name'],
                    'match_confidence': f"{match['confidence']:.1%}",
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
        print(f"   ✓ Used Claude location matching: {location_boost_count} times")
    
    # Count high confidence results (already written)
    high_conf_count = high_confidence_count - review_count
    
    # Summary
    print(f"\n" + "=" * 70)
    print("📊 MATCHING SUMMARY")
    print("=" * 70)
    print(f"Total FLY deals:                {results_count}")
    print(f"All deals matched:              {results_count} (100%)")
    print(f"")
    print(f"High confidence (≥92%):         {high_conf_count}")
    print(f"Review needed (80-92%):         {review_count}")
    print(f"Low confidence (<80%):          {results_count - high_confidence_count}")
    print(f"Used location boost:            {location_boost_count}")
    print(f"")
    print("✅ Done! All results saved incrementally to CSV files.")
    print(f"")
    print(f"Output files:")
    print(f"  • restaurant_fly_matches_all.csv ({results_count} rows)")
    print(f"  • restaurant_fly_matches_high_confidence.csv ({high_conf_count} rows)")
    print(f"  • restaurant_fly_matches_review.csv ({review_count} rows)")
    print("=" * 70)

if __name__ == '__main__':
    main()