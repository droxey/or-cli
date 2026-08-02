#!/usr/bin/env python3
"""Sequentially re-scrape remaining empty-tag pages with agent-browser and wait."""
import json
import os
import re
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).parent
TAGS_CACHE = HERE / "tags-cache.json"
JS_EXTRACT = 'Array.from(document.querySelectorAll(\'a[href*="/tag-"]\')).map(a => a.textContent.trim()).filter(Boolean)'

def main():
    with open(TAGS_CACHE) as f:
        cache = json.load(f)

    empty = [url for url, tags in cache.items() if not tags]
    print(f"Sequential re-scrape: {len(empty)} pages remaining\n")

    for i, url in enumerate(empty):
        try:
            subprocess.run(
                ["agent-browser", "--session", "seq", "open", url],
                capture_output=True, timeout=25, text=True
            )
            time.sleep(1.5)
            r = subprocess.run(
                ["agent-browser", "--session", "seq", "eval", JS_EXTRACT],
                capture_output=True, timeout=15, text=True
            )
            output = r.stdout.strip()
            if output:
                clean = re.sub(r'\x1b\[[0-9;]*m', '', output)
                start = clean.find('[')
                end = clean.rfind(']') + 1
                if start >= 0 and end > start:
                    tags = json.loads(clean[start:end])
                    cache[url] = tags
        except Exception:
            pass

        done = i + 1
        if done % 25 == 0:
            with_tags = sum(1 for v in cache.values() if v)
            print(f"  {done}/{len(empty)} | tags found: {with_tags}/{len(cache)}", flush=True)
            with open(TAGS_CACHE, "w") as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)

    with_tags = sum(1 for v in cache.values() if v)
    print(f"\nDone. Tags found: {with_tags}/{len(cache)}")
    with open(TAGS_CACHE, "w") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    main()
