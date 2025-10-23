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
            return 0.20  # Strong location match boost
        elif "MAYBE" in response:
            return 0.10  # Moderate location match boost
        else:
            return 0.0
            
    except Exception as e:
        # If API fails, return no boost
        return 0.0

def find_best_restaurant_match(deal_name, restaurants, claude_client=None):
    """
    Find the best matching restaurant for a FLY deal name
    Always returns the best match (no threshold - every deal gets matched)
    Uses Claude Haiku for location matching when uncertain
    """
    best_match = None
    best_confidence = 0.0
    
    for restaurant in restaurants:
        restaurant_name = restaurant.get('Restaurant Name', '').strip()
        location_name = restaurant.get('Location Name', '').strip()
        
        if not restaurant_name:
            continue
        
        # Calculate base fuzzy score on restaurant name
        fuzzy_score = fuzzy_match_score(deal_name, restaurant_name)
        
        # Add reasoning boost
        reasoning_boost = reasoning_match_boost(deal_name, restaurant_name)
        
        # Base confidence from name matching
        confidence = min(fuzzy_score + reasoning_boost, 1.0)
        
        # If confidence is uncertain (below 90%), check location with Claude
        location_boost = 0.0
        if confidence < 0.90 and location_name and claude_client:
            location_boost = check_location_match_with_claude(
                deal_name, restaurant_name, location_name, claude_client
            )
            confidence = min(confidence + location_boost, 1.0)
        
        # Track best match (no threshold - always find the best one)
        if confidence > best_confidence:
            best_confidence = confidence
            best_match = {
                'restaurant_id': restaurant.get('Restaurant ID', '').strip(),
                'restaurant_name': restaurant_name,
                'location_name': location_name,
                'restaurant_group_id': restaurant.get('Restaurant Group ID', '').strip(),
                'restaurant_group_name': restaurant.get('Restaurant Group Name', '').strip(),
                'confidence': confidence,
                'used_location_boost': location_boost > 0
            }
    
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
    print()
    
    results = []
    high_confidence_count = 0
    location_boost_count = 0
    
    for idx, deal in enumerate(fly_allocations):
        deal_name = deal['deal_name']
        fly_allocation = deal['fly_allocation']
        
        # Show progress every 100 deals
        if (idx + 1) % 100 == 0:
            print(f"   Processing... {idx + 1}/{len(fly_allocations)} deals")
        
        # Find best matching restaurant for this deal (always returns best match)
        match = find_best_restaurant_match(deal_name, rest_groups, claude_client)
        
        if match:
            if match['confidence'] >= 0.80:
                high_confidence_count += 1
            if match.get('used_location_boost', False):
                location_boost_count += 1
            
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
        
        results.append(result)
    
    print(f"\n   ✓ Processed {len(results)} FLY deals")
    print(f"   ✓ High confidence (≥80%): {high_confidence_count}")
    if claude_client:
        print(f"   ✓ Used Claude location matching: {location_boost_count} times")
    
    # Write outputs
    print(f"\n💾 Writing output files...")
    
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
    
    # 1. All results
    output_file = 'restaurant_fly_matches_all.csv'
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"   ✓ {output_file} ({len(results)} rows)")
    
    # 2. Review needed (80-92% confidence)
    review_needed = []
    for r in results:
        if r['match_confidence'] and r['match_confidence'] != '0.0%':
            conf_str = r['match_confidence'].strip('%')
            conf_val = float(conf_str) / 100
            if 0.80 <= conf_val < 0.92:
                review_needed.append(r)
    
    output_file = 'restaurant_fly_matches_review.csv'
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(review_needed)
    print(f"   ✓ {output_file} ({len(review_needed)} rows)")
    
    # 3. High confidence (≥92%)
    high_confidence = []
    for r in results:
        if r['match_confidence'] and r['match_confidence'] != '0.0%':
            conf_str = r['match_confidence'].strip('%')
            conf_val = float(conf_str) / 100
            if conf_val >= 0.92:
                high_confidence.append(r)
    
    output_file = 'restaurant_fly_matches_high_confidence.csv'
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(high_confidence)
    print(f"   ✓ {output_file} ({len(high_confidence)} rows)")
    
    # Summary
    print(f"\n" + "=" * 70)
    print("📊 MATCHING SUMMARY")
    print("=" * 70)
    print(f"Total FLY deals:                {len(results)}")
    print(f"All deals matched:              {len(results)} (100%)")
    print(f"")
    print(f"High confidence (≥92%):         {len(high_confidence)}")
    print(f"Review needed (80-92%):         {len(review_needed)}")
    print(f"Low confidence (<80%):          {len(results) - len(high_confidence) - len(review_needed)}")
    print(f"Used location boost:            {location_boost_count}")
    print(f"")
    print("✅ Done! Check the output CSV files.")
    print("=" * 70)

if __name__ == '__main__':
    main()