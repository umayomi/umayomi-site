#!/usr/bin/env python3
import json, time, random, re, sys
from datetime import datetime, timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Accept-Language": "ja,en-US;q=0.9"}
OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(exist_ok=True)

session = requests.Session()
session.headers.update(HEADERS)
session.headers.update({"Referer": "https://db.netkeiba.com/"})

def fetch(url, encoding="EUC-JP"):
    print("GET", url)
    try:
        r = session.get(url, timeout=20)
        r.encoding = encoding
        r.raise_for_status()
        print("chars:", len(r.text), "status:", r.status_code)
        time.sleep(random.uniform(3, 6))
        return BeautifulSoup(r.text, "lxml")
    except Exception as e:
        print("failed:", e)
        return None

def get_target_dates():
    today = datetime.now()
    dates = []
    for d in range(-21, 8):
        dd = today + timedelta(days=d)
        if dd.weekday() in [5, 6]:
            dates.append(dd.strftime("%Y%m%d"))
    return dates

def find_race_ids(date_str):
    url = "https://race.netkeiba.com/top/race_list.html?kaisai_date=" + date_str
    soup = fetch(url, encoding="EUC-JP")
    if not soup:
        return []
    text = str(soup)
    return sorted(set(re.findall(r"race_id=(\d{12})", text)))

def fetch_race(race_id):
    url = "https://db.netkeiba.com/race/" + race_id + "/"
    soup = fetch(url)
    if not soup:
        return None
    table = soup.select_one("table.race_table_01")
    if not table:
        tables = soup.select("table")
        print("no race_table_01, page has", len(tables), "tables")
        return None
    rows = table.find_all("tr")
    if len(rows) < 2:
        return None
    header = [th.text.strip() for th in rows[0].find_all("th")]
    horses = []
    for row in rows[1:]:
        cols = [td.text.strip() for td in row.find_all("td")]
        if len(cols) == len(header):
            horses.append(dict(zip(header, cols)))
    if not horses:
        return None
    return {"race_id": race_id, "horses": horses, "url": url}

def main():
    print("umayomi v2 start", datetime.now())
    dates = get_target_dates()
    print("target dates:", dates)
    all_races = {}
    for date_str in dates:
        print("date:", date_str)
        race_ids = find_race_ids(date_str)
        if not race_ids:
            print("  no race ids")
            continue
        print("  found", len(race_ids), "race ids")
        mains = [r for r in race_ids if r.endswith("11")]
        target = mains[:2] if mains else race_ids[:2]
        for rid in target:
            print("  rid:", rid)
            data = fetch_race(rid)
            if data:
                data["date"] = date_str
                all_races[rid] = data
                print("    OK", len(data["horses"]), "horses")
            else:
                print("    NG")
    output = OUTPUT_DIR / "races.json"
    with open(output, "w", encoding="utf-8") as f:
        json.dump({"updated_at": datetime.now().isoformat(), "race_count": len(all_races), "races": all_races}, f, ensure_ascii=False, indent=2)
    print("done:", len(all_races), "races saved")

main()
