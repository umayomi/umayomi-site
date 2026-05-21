#!/usr/bin/env python3
"""ウマヨミ - Selenium版 (改良版)"""

import json
import time
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup

try:
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_WDM = True
except ImportError:
    HAS_WDM = False


OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(exist_ok=True)


def create_driver():
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--lang=ja-JP')
    options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    
    if HAS_WDM:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    else:
        driver = webdriver.Chrome(options=options)
    
    return driver


def get_target_dates():
    today = datetime.now()
    dates = []
    for d in range(-14, 15):
        dd = today + timedelta(days=d)
        if dd.weekday() in [5, 6]:
            dates.append(dd.strftime("%Y%m%d"))
    return dates


def get_race_ids(driver, date_str):
    """記事スタイル: シンプルにaタグから抽出"""
    url = f'https://race.netkeiba.com/top/race_list.html?kaisai_date={date_str}'
    print(f"  GET: {url}")
    
    driver.get(url)
    time.sleep(5)  # JavaScript実行を待つ（記事は2秒だが余裕を持って5秒）
    
    html = driver.page_source
    soup = BeautifulSoup(html, "html.parser")
    
    print(f"  html size: {len(html)}")
    
    race_ids = []
    a_all = soup.find_all("a", href=True)
    print(f"  found {len(a_all)} <a> tags")
    
    for a in a_all:
        match = re.search(r"race_id=(\d{12})", a["href"])
        if match:
            race_ids.append(match.group(1))
    
    unique_ids = sorted(set(race_ids))
    print(f"  unique race_ids: {len(unique_ids)}")
    return unique_ids


def get_race_detail(driver, race_id):
    """個別レース詳細を取得"""
    url = f'https://race.netkeiba.com/race/shutuba.html?race_id={race_id}'
    print(f"    GET: {url}")
    
    driver.get(url)
    time.sleep(5)
    
    html = driver.page_source
    soup = BeautifulSoup(html, "html.parser")
    
    # レース名
    race_name_el = soup.select_one(".RaceName, h1")
    race_name = race_name_el.text.strip() if race_name_el else f"race_{race_id}"
    
    # 出走表テーブル
    table = soup.select_one("table.Shutuba_Table, table.RaceTable01")
    if not table:
        print(f"    no table found")
        return None
    
    rows = table.find_all("tr")
    if len(rows) < 2:
        return None
    
    header = [th.get_text(strip=True) for th in rows[0].find_all("th")]
    horses = []
    for row in rows[1:]:
        cols = [td.get_text(strip=True) for td in row.find_all("td")]
        if cols:
            horses.append(cols)
    
    if not horses:
        return None
    
    return {
        "race_id": race_id,
        "race_name": race_name,
        "header": header,
        "horses": horses,
        "url": url,
    }


def main():
    print("=" * 60)
    print(f"umayomi Selenium v3 - {datetime.now()}")
    print("=" * 60)
    
    driver = None
    try:
        driver = create_driver()
        print("driver ready")
        
        dates = get_target_dates()
        print(f"target dates: {dates}")
        
        all_races = {}
        
        for date_str in dates:
            print(f"\n[{date_str}]")
            try:
                race_ids = get_race_ids(driver, date_str)
            except Exception as e:
                print(f"  error: {e}")
                continue
            
            if not race_ids:
                continue
            
            # 11Rを最大2つだけ
            mains = [r for r in race_ids if r.endswith("11")]
            target = mains[:2] if mains else race_ids[:2]
            
            for rid in target:
                print(f"  race_id: {rid}")
                try:
                    data = get_race_detail(driver, rid)
                    if data:
                        data["date"] = date_str
                        all_races[rid] = data
                        print(f"    OK: {data['race_name']} ({len(data['horses'])} horses)")
                except Exception as e:
                    print(f"    error: {e}")
                
                time.sleep(3)
        
        output = OUTPUT_DIR / "races.json"
        with open(output, "w", encoding="utf-8") as f:
            json.dump({
                "updated_at": datetime.now().isoformat(),
                "race_count": len(all_races),
                "races": all_races,
            }, f, ensure_ascii=False, indent=2)
        
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
