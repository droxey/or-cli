#!/usr/bin/env python3
"""Re-scrape itch.io pages that returned empty tags, with a wait for JS rendering."""
import json, subprocess, re, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).parent
TAGS_CACHE = HERE / "tags-cache.json"
JS_EXTRACT = 'Array.from(document.querySelectorAll(\'a[href*="/tag-"]\')).map(a => a.textContent.trim()).filter(Boolean)'
NUM_WORKERS = 10

def scrape_batch_retry(urls, session_name, batch_idx):
    results = {}
    for i, url in enumerate(urls):
        try:
            subprocess.run(
                ["agent-browser", "--session", session_name, "open", url],
                capture_output=True, timeout=20, text=True
            )
            time.sleep(2)  # Wait for JS to render tags
            r = subprocess.run(
                ["agent-browser", "--session", session_name, "eval", JS_EXTRACT],
                capture_output=True, timeout=15, text=True
            )
            output = r.stdout.strip()
            if output:
                clean = re.sub(r'\x1b\[[0-9;]*m', '', output)
                start = clean.find('[')
                end = clean.rfind(']') + 1
                if start >= 0 and end > start:
                    tags = json.loads(clean[start:end])
                    results[url] = tags
                else:
                    results[url] = []
            else:
                results[url] = []
        except:
            results[url] = []
        done = i + 1
        if done % 10 == 0 or done == len(urls):
            print(f"  Worker {batch_idx}: {done}/{len(urls)}", flush=True)
    return results

def main():
    with open(TAGS_CACHE) as f:
        cache = json.load(f)

    empty = [url for url, tags in cache.items() if not tags]
    print(f"Re-scraping {len(empty)} URLs with 2s wait delay...")

    batch_size = (len(empty) + NUM_WORKERS - 1) // NUM_WORKERS
    batches = []
    for i in range(NUM_WORKERS):
        batch = empty[i * batch_size : (i + 1) * batch_size]
        if batch:
            batches.append((batch, f"itch-retry-{i}", i))

    print(f"Launching {len(batches)} workers...")
    start = time.time()

    with ProcessPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {
            executor.submit(scrape_batch_retry, batch, session, idx): idx
            for batch, session, idx in batches
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                batch_results = future.result()
                cache.update(batch_results)
                with open(TAGS_CACHE, "w") as f:
                    json.dump(cache, f, indent=2, ensure_ascii=False)
                got_tags = sum(1 for v in batch_results.values() if v)
                print(f"  Worker {idx} done: {len(batch_results)} pages, {got_tags} with tags")
            except Exception as e:
                print(f"  Worker {idx} error: {e}")

    elapsed = time.time() - start
    with_tags = sum(1 for url, tags in cache.items() if tags)
    print(f"\nRetry complete in {elapsed:.0f}s")
    print(f"Tags found for {with_tags}/{len(cache)} pages")
    with open(TAGS_CACHE, "w") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    main()
