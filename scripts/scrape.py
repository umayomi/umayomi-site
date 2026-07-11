#!/usr/bin/env python3
"""
ウマヨミ scrape v10 — 全レース・軽量版
- 今日から7日先までの開催日について、レース一覧ページ1枚から
  全レースの race_id / レース名 / 発走時刻 / コースを取得
- 出馬表・オッズは取得しない（オッズは scrape_odds_live.py が当日取得）
- 実行時間: 開催日1日あたり数秒

races.json スキーマ:
{
  "updated_at": ISO, "race_count": N,
  "races": {
    "<race_id>": {
      "race_id", "date" (YYYYMMDD), "race_name",
      "venue", "race_no", "start_time" ("9:50"), "course" ("芝1200m"),
      "course_info" (互換用: "9:50発走 芝1200m"),
      "is_past": false
    }
  }
}
"""

import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

try:
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_WDM = True
except ImportError:
    HAS_WDM = False

OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(exist_ok=True)

VENUE_MAP = {"01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
             "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉"}


def create_driver():
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1280,2000')
    options.add_argument('--lang=ja-JP')
    options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    if HAS_WDM:
        return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    return webdriver.Chrome(options=options)


def target_dates():
    """今日〜7日先"""
    today = datetime.now()
    return [(today + timedelta(days=d)).strftime("%Y%m%d") for d in range(0, 8)]


def parse_race_item(li, race_id):
    """一覧の <li> からレース名・発走時刻・コースを抽出"""
    text = re.sub(r"\s+", " ", li.get_text(" ", strip=True))

    # レース名: ItemTitle スパン優先、無ければ a の title 属性
    name = ""
    el = li.select_one(".ItemTitle")
    if el:
        name = el.get_text(strip=True)
    if not name:
        a = li.select_one(f'a[href*="{race_id}"]')
        if a and a.get("title"):
            name = re.sub(r"\s*出馬表.*$", "", a["title"]).strip()
    if not name:
        # フォールバック: RRの直後の語
        m = re.search(r"\d{1,2}R\s+(\S+)", text)
        name = m.group(1) if m else ""

    # 発走時刻
    m = re.search(r"(\d{1,2}:\d{2})", text)
    start = m.group(1) if m else ""

    # コース
    m = re.search(r"((?:芝|ダ|障)\S*?\d{3,4}m?)", text)
    course = m.group(1) if m else ""
    if course and not course.endswith("m"):
        course += "m"

    return name, start, course


def scrape_date(driver, date_str):
    url = f'https://race.netkeiba.com/top/race_list.html?kaisai_date={date_str}'
    print(f"  GET: {url}")
    driver.get(url)
    time.sleep(5)
    soup = BeautifulSoup(driver.page_source, "html.parser")

    races = {}
    for a in soup.find_all("a", href=True):
        m = re.search(r"race_id=(\d{12})", a["href"])
        if not m:
            continue
        rid = m.group(1)
        if rid in races:
            continue
        li = a.find_parent("li") or a.find_parent("dd") or a.parent
        name, start, course = parse_race_item(li, rid) if li else ("", "", "")
        venue = VENUE_MAP.get(rid[4:6], "")
        race_no = int(rid[10:12]) if rid[10:12].isdigit() else 0
        races[rid] = {
            "race_id": rid,
            "date": date_str,
            "race_name": name or f"{venue}{race_no}R",
            "venue": venue,
            "race_no": race_no,
            "start_time": start,
            "course": course,
            "course_info": f"{start}発走 {course}".strip(),
            "is_past": False,
        }
    print(f"  → {len(races)}レース")
    return races


def main():
    print("=" * 60)
    print(f"umayomi scrape v10 (all races, light) - {datetime.now()}")
    print("=" * 60)

    driver = None
    all_races = {}
    try:
        driver = create_driver()
        print("driver ready")
        for date_str in target_dates():
            print(f"\n[{date_str}]")
            try:
                all_races.update(scrape_date(driver, date_str))
            except Exception as e:
                print(f"  error: {e}")
            time.sleep(2)

        output = OUTPUT_DIR / "races.json"
        with open(output, "w", encoding="utf-8") as f:
            json.dump({
                "updated_at": datetime.now().isoformat(),
                "race_count": len(all_races),
                "races": all_races,
            }, f, ensure_ascii=False, indent=1)
        print(f"\nDONE: {len(all_races)} races saved")

    except Exception as e:
        print(f"FATAL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if driver:
            driver.quit()


main()
