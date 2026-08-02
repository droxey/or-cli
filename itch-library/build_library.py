#!/usr/bin/env python3
"""Scrape itch.io purchase pages for tags, then assemble purchase-library.json with categories."""
import asyncio, json, os, re, sys
from urllib.parse import urlparse
import httpx
from bs4 import BeautifulSoup

API_KEY = os.environ["ITCH_API_KEY"]
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "purchase-library.json")
RAW_FILE = os.path.join(os.path.dirname(__file__), "api-keys-raw.json")
CONCURRENCY = 20
TIMEOUT = 30

def safe_filename(slug: str) -> str:
    """Derive a filesystem-safe filename from the URL slug."""
    return re.sub(r'[^a-zA-Z0-9_-]', '_', slug).strip('_') or "untitled"

def extract_slug(url: str) -> str:
    path = urlparse(url).path.strip('/')
    return path if path else url

def fetch_all_keys():
    """Fetch all owned keys from itch API."""
    import requests
    all_keys = []
    page = 1
    while True:
        r = requests.get(f"https://itch.io/api/1/{API_KEY}/my-owned-keys", params={"page": page}, timeout=30)
        data = r.json()
        keys = data.get("owned_keys", [])
        all_keys.extend(keys)
        print(f"  API page {page}: {len(keys)} keys (total: {len(all_keys)})")
        if len(keys) < data.get("per_page", 50):
            break
        page += 1
    return all_keys

async def scrape_tags(client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore) -> list:
    """Scrape tags from a single itch.io page."""
    async with sem:
        try:
            r = await client.get(url, follow_redirects=True, timeout=TIMEOUT)
            if r.status_code != 200:
                return []
            soup = BeautifulSoup(r.text, 'html.parser')
            tags = []
            # itch.io tags are <a> links with /tag- in href
            for tag_el in soup.select('a[href*="/tag-"]'):
                tag_text = tag_el.get_text(strip=True)
                if tag_text:
                    tags.append(tag_text)
            return tags
        except Exception as e:
            return []

async def scrape_all_tags(urls: list) -> dict:
    """Scrape tags from all pages concurrently."""
    sem = asyncio.Semaphore(CONCURRENCY)
    headers = {"User-Agent": "itch-library-export/1.0"}
    async with httpx.AsyncClient(headers=headers) as client:
        task_map = {url: asyncio.create_task(scrape_tags(client, url, sem)) for url in urls}
        results = {}
        done = 0
        for task in asyncio.as_completed(task_map.values()):
            result = await task
            done += 1
            if done % 50 == 0 or done == len(urls):
                print(f"  Scraped {done}/{len(urls)} pages")
        # Collect all results after completion
        for url, t in task_map.items():
            results[url] = t.result()
        return results

def categorize(title: str, tags: list, classification: str) -> str:
    """Derive a category from title keywords, tags, and classification."""
    text = (title + " " + " ".join(tags)).lower()
    
    categories = [
        ("Icons & UI", ["icon", "ui", "button", "cursor", "interface", "hud", "menu"]),
        ("Sprites & Characters", ["sprite", "character", "hero", "enemy", "monster", "creature", "npc", "battler", "boss", "warrior", "person", "people", "animal"]),
        ("Tilesets & Maps", ["tile", "tileset", "map", "level", "dungeon", "terrain", "ground"]),
        ("Backgrounds & Parallax", ["background", "parallax", "sky", "landscape", "scenery", "cityscape"]),
        ("Music & Audio", ["music", "sound", "audio", "sfx", "ambient", "song", "track", "ost", "instrument"]),
        ("Fonts & Text", ["font", "text", "typography", "letter"]),
        ("2D Art & Illustrations", ["illustration", "art", "drawing", "painting", "concept", "portrait", "painting"]),
        ("3D Models & Assets", ["3d", "model", "low-poly", "lowpoly", "mesh", "blender", "obj", "fbx"]),
        ("Game Templates & Kits", ["template", "kit", "starter", "boilerplate", "demo", "prototype", "engine"]),
        ("Tilemap & Level Design", ["tilemap", "level design", "world building"]),
        ("Items & Loot", ["item", "loot", "treasure", "potion", "weapon", "armor", "shield", "sword", "axe", "glove", "ring", "ammo"]),
        ("Card Game Assets", ["card", "tcg", "ccg", "trading card"]),
        ("Animations & Effects", ["animation", "effect", "particle", "vfx", "fx", "explosion", "magic effect", "spell effect"]),
        ("Tools & Software", ["tool", "editor", "generator", "plugin", "script", "software"]),
    ]
    
    for category, keywords in categories:
        if any(kw in text for kw in keywords):
            return category
    
    # Fall back to itch classification
    if classification == "tool":
        return "Tools & Software"
    if classification == "assets":
        return "Other Game Assets"
    
    return "Other"

def build_library(keys_data: list, tags_map: dict) -> dict:
    """Assemble the final library JSON."""
    # Deduplicate by game_id
    seen = set()
    purchases = []
    
    for entry in keys_data:
        game = entry.get("game", {})
        gid = game.get("id")
        if gid in seen:
            continue
        seen.add(gid)
        
        url = game.get("url", "")
        tags = tags_map.get(url, [])
        title = game.get("title", "")
        classification = game.get("classification", "")
        
        purchase = {
            "title": title,
            "url": url,
            "creator": game.get("user", {}).get("username", ""),
            "creator_display_name": game.get("user", {}).get("display_name", ""),
            "creator_url": game.get("user", {}).get("url", ""),
            "cover_url": game.get("cover_url", ""),
            "purchase_date": entry.get("created_at", ""),
            "game_id": gid,
            "classification": classification,
            "min_price_cents": game.get("min_price", 0),
            "price_tier": "free" if (game.get("min_price", 0) or 0) == 0 else "paid",
            "platforms": {
                "windows": game.get("p_windows", False),
                "mac": game.get("p_osx", False),
                "linux": game.get("p_linux", False),
                "android": game.get("p_android", False),
            },
            "tags": tags,
            "category": categorize(title, tags, classification),
            "slug": extract_slug(url),
            "safe_filename": safe_filename(extract_slug(url)),
            "downloads": entry.get("downloads", 0),
        }
        purchases.append(purchase)
    
    return {
        "source": "https://itch.io/my-purchases",
        "fetched_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "total": len(purchases),
        "purchases": purchases,
    }

def main():
    print("Step 1: Fetching purchase data from itch API...")
    if os.path.exists(RAW_FILE):
        with open(RAW_FILE) as f:
            keys_data = json.load(f)
        print(f"  Loaded {len(keys_data)} entries from cached API data")
    else:
        keys_data = fetch_all_keys()
        with open(RAW_FILE, "w") as f:
            json.dump(keys_data, f, indent=2, default=str)
        print(f"  Fetched and cached {len(keys_data)} entries")
    
    print(f"\nStep 2: Scraping tags from {len(keys_data)} itch.io pages...")
    urls = list({entry["game"]["url"] for entry in keys_data if entry.get("game", {}).get("url")})
    print(f"  {len(urls)} unique URLs to scrape")
    tags_map = asyncio.run(scrape_all_tags(urls))
    print(f"  Got tags for {sum(1 for v in tags_map.values() if v)}/{len(urls)} pages")
    
    print("\nStep 3: Deriving categories and assembling JSON...")
    library = build_library(keys_data, tags_map)
    
    print("\nStep 4: Category distribution:")
    from collections import Counter
    cats = Counter(p["category"] for p in library["purchases"])
    for cat, count in cats.most_common():
        print(f"  {cat}: {count}")
    
    print(f"\nStep 5: Saving to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w") as f:
        json.dump(library, f, indent=2, ensure_ascii=False)
    print(f"  Saved {library['total']} purchases")
    
    # Show a sample entry
    print("\nSample entry:")
    print(json.dumps(library["purchases"][0], indent=2, ensure_ascii=False)[:800])

if __name__ == "__main__":
    main()
