#!/usr/bin/env python3

import os
import json
from pathlib import Path
from openai import OpenAI

# Setup
script_dir = Path(__file__).parent
prompt_file = script_dir / "last_prompt.txt"
stats_file = script_dir / "cache_stats_simple.json"

# Check API key
api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    print("ERROR: Set OPENAI_API_KEY environment variable")
    exit(1)

# Read the last prompt
if not prompt_file.exists():
    print(f"ERROR: {prompt_file} not found")
    exit(1)

with open(prompt_file, "r") as f:
    system_prompt = f.read()

print("=" * 70)
print("Simple Cache Test - Using last_prompt.txt")
print("=" * 70)
print(f"Prompt size: {len(system_prompt):,} characters")
print()

client = OpenAI(api_key=api_key)

# Test message
test_msg = "Turn on the living room light"

# Make 3 calls
results = []
for i in range(3):
    print(f"Call {i+1}/3...", end=" ", flush=True)
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": test_msg}
        ],
        response_format={"type": "json_object"},
    )
    
    # Extract usage
    usage = response.usage
    cached = 0
    cache_rate = 0.0
    
    if hasattr(usage, 'prompt_tokens_details') and usage.prompt_tokens_details:
        if hasattr(usage.prompt_tokens_details, 'cached_tokens'):
            cached = usage.prompt_tokens_details.cached_tokens
            cache_rate = (cached / usage.prompt_tokens * 100) if usage.prompt_tokens > 0 else 0
    
    result = {
        "call": i + 1,
        "prompt_tokens": usage.prompt_tokens,
        "cached_tokens": cached,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cache_hit_rate": round(cache_rate, 2)
    }
    results.append(result)
    
    print(f"✓ Cached: {cached}/{usage.prompt_tokens} ({cache_rate:.1f}%)")

# Summary
print()
print("=" * 70)
print("Results:")
print("=" * 70)
for r in results:
    print(f"Call {r['call']}: {r['cached_tokens']:,} / {r['prompt_tokens']:,} tokens cached ({r['cache_hit_rate']}%)")

print()
total_prompt = sum(r['prompt_tokens'] for r in results)
total_cached = sum(r['cached_tokens'] for r in results)
overall_rate = (total_cached / total_prompt * 100) if total_prompt > 0 else 0

print(f"Overall: {total_cached:,} / {total_prompt:,} tokens cached ({overall_rate:.2f}%)")
print(f"Savings: {total_cached:,} tokens not computed")

# Save to file
with open(stats_file, "w") as f:
    json.dump({
        "summary": {
            "total_calls": len(results),
            "total_prompt_tokens": total_prompt,
            "total_cached_tokens": total_cached,
            "overall_cache_hit_rate": round(overall_rate, 2)
        },
        "calls": results
    }, f, indent=2)

print(f"\nStats saved to: {stats_file}")
