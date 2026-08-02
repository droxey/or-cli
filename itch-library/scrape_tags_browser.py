#!/usr/bin/env python3
"""Batch-scrape itch.io tags using parallel agent-browser sessions."""
import json, os, subprocess, sys, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).parent
RAW_FILE = HERE / "api-keys-raw.json"
TAGS_CACHE = HERE / "tags-cache.json"
OUTPUT_FILE = HERE / "purchase-library.json"

NUM_WORKERS = 10
JS_EXTRACT = "Array.from(document.querySelectorAll('a[href*=\"/tag-\"]')).map(a => a.textContent.trim()).filter(Boolean)"

def get_urls():
    """Get all unique URLs from the raw API data."""
    with open(RAW_FILE) as f:
        data = json.load(f)
    urls = []
    seen = set()
    for entry in data:
        url = entry.get("game", {}).get("url", "")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls

def load_existing_cache():
    """Load existing tags cache if present."""
    if TAGS_CACHE.exists():
        with open(TAGS_CACHE) as f:
            return json.load(f)
    return {}

def scrape_batch(urls, session_name, batch_idx):
    """Scrape tags for a batch of URLs using a single agent-browser session."""
    results = {}
    for i, url in enumerate(urls):
        try:
            # Open the page
            subprocess.run(
                ["agent-browser", "--session", session_name, "open", url],
                capture_output=True, timeout=20, text=True
            )
            # Extract tags via JS eval
            r = subprocess.run(
                ["agent-browser", "--session", session_name, "eval", JS_EXTRACT],
                capture_output=True, timeout=15, text=True
            )
            output = r.stdout.strip()
            # Parse the JSON array from output
            # agent-browser outputs the JS result as JSON
            if output:
                # Remove ANSI codes
                import re
                clean = re.sub(r'\x1b\[[0-9;]*m', '', output)
                # Find the JSON array
                start = clean.find('[')
                end = clean.rfind(']') + 1
                if start >= 0 and end > start:
                    tags = json.loads(clean[start:end])
                    results[url] = tags
                else:
                    results[url] = []
            else:
                results[url] = []
        except Exception as e:
            results[url] = []
        # Progress
        done = i + 1
        if done % 10 == 0 or done == len(urls):
            print(f"  Worker {batch_idx}: {done}/{len(urls)}", flush=True)
    return results

def main():
    urls = get_urls()
    print(f"Total unique URLs: {len(urls)}")

    # Load existing cache and skip already-scraped URLs
    cache = load_existing_cache()
    to_scrape = [u for u in urls if u not in cache or not cache[u]]
    print(f"Already cached: {len(urls) - len(to_scrape)}")
    print(f"To scrape: {len(to_scrape)}")

    if not to_scrape:
        print("All URLs already have tags cached!")
    else:
        # Split into batches
        batch_size = (len(to_scrape) + NUM_WORKERS - 1) // NUM_WORKERS
        batches = []
        for i in range(NUM_WORKERS):
            batch = to_scrape[i * batch_size : (i + 1) * batch_size]
            if batch:
                batches.append((batch, f"itch-{i}", i))

        print(f"\nLaunching {len(batches)} parallel browser workers...")
        start_time = time.time()

        with ProcessPoolExecutor(max_workers=NUM_WORKERS) as executor:
            futures = {
                executor.submit(scrape_batch, batch, session, idx): idx
                for batch, session, idx in batches
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    batch_results = future.result()
                    cache.update(batch_results)
                    # Save cache incrementally
                    with open(TAGS_CACHE, "w") as f:
                        json.dump(cache, f, indent=2, ensure_ascii=False)
                    got_tags = sum(1 for v in batch_results.values() if v)
                    print(f"  Worker {idx} done: {len(batch_results)} pages, {got_tags} with tags")
                except Exception as e:
                    print(f"  Worker {idx} error: {e}")

        elapsed = time.time() - start_time
        print(f"\nScraping complete in {elapsed:.0f}s")

    # Final stats
    with_tags = sum(1 for u in urls if cache.get(u))
    print(f"Tags found for {with_tags}/{len(urls)} pages")

    # Save final cache
    with open(TAGS_CACHE, "w") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)
    print(f"Saved tags cache to {TAGS_CACHE}")

if __name__ == "__main__":
    main()
