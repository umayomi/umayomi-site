#!/usr/bin/env python3
"""ウマヨミ - Selenium版 v5 (オッズ取得追加)"""

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

EXCLUDE_COLS = {
    "印", "お気に入り馬", "馬メモ切替",
    "マスターレース別馬メモ切替", "オッズ更新"
}

COL_MAP = {
    "枠": "waku",
    "馬番": "umaban",
    "馬名": "horse_name",
    "性齢": "sex_age",
    "斤量": "weight_carried",
    "騎手": "jockey",
    "厩舎": "stable",
    "馬体重(増減)": "horse_weight",
    "馬体重": "horse_weight",
    "オッズ": "odds",
    "人気": "popularity",
}


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
    url = f'https://race.netkeiba.com/top/race_list.html?kaisai_date={date_str}'
    print(f"  GET: {url}")
    
    driver.get(url)
    time.sleep(5)
    
    html = driver.page_source
    soup = BeautifulSoup(html, "html.parser")
    
    print(f"  html size: {len(html)}")
    
    race_ids = []
    a_all = soup.find_all("a", href=True)
    
    for a in a_all:
        match = re.search(r"race_id=(\d{12})", a["href"])
        if match:
            race_ids.append(match.group(1))
    
    unique_ids = sorted(set(race_ids))
    print(f"  unique race_ids: {len(unique_ids)}")
    return unique_ids


def get_race_detail(driver, race_id):
    """出馬表ページから基本情報を取得"""
    url = f'https://race.netkeiba.com/race/shutuba.html?race_id={race_id}'
    print(f"    GET shutuba: {url}")
    
    driver.get(url)
    time.sleep(5)
    
    html = driver.page_source
    soup = BeautifulSoup(html, "html.parser")
    
    # レース名取得
    race_name = ""
    for selector in [".RaceName", ".RaceList_Item02 .RaceName", "h1.RaceName"]:
        el = soup.select_one(selector)
        if el and el.text.strip():
            race_name = re.sub(r"\s+", " ", el.text.strip())
            break
    
    course_info = ""
    el = soup.select_one(".RaceData01")
    if el:
        course_info = re.sub(r"\s+", " ", el.text.strip())
    
    venue_info = ""
    el = soup.select_one(".RaceData02")
    if el:
        venue_info = re.sub(r"\s+", " ", el.text.strip())
    
    # 出走表テーブル取得
    table = soup.select_one("table.Shutuba_Table, table.RaceTable01")
    if not table:
        print(f"    no table found")
        return None
    
    rows = table.find_all("tr")
    if len(rows) < 2:
        return None
    
    raw_header = [th.get_text(strip=True) for th in rows[0].find_all("th")]
    
    keep_indices = []
    clean_header = []
    for i, col_name in enumerate(raw_header):
        if col_name not in EXCLUDE_COLS:
            keep_indices.append(i)
            clean_header.append(COL_MAP.get(col_name, col_name))
    
    horses = []
    for row in rows[1:]:
        cols = [td.get_text(strip=True) for td in row.find_all("td")]
        if not cols:
            continue
        
        clean_cols = []
        for i in keep_indices:
            if i < len(cols):
                clean_cols.append(cols[i])
            else:
                clean_cols.append("")
        
        horse_dict = dict(zip(clean_header, clean_cols))
        
        if not horse_dict.get("horse_name", "").strip():
            continue
        
        horses.append(horse_dict)
    
    if not horses:
        return None
    
    return {
        "race_id": race_id,
        "race_name": race_name,
        "course_info": course_info,
        "venue_info": venue_info,
        "horses": horses,
        "url": url,
    }


def get_odds(driver, race_id):
    """単勝・複勝オッズページから取得"""
    url = f'https://race.netkeiba.com/odds/index.html?race_id={race_id}&type=b1'
    print(f"    GET odds: {url}")
    
    try:
        driver.get(url)
        time.sleep(5)
        
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")
        
        # オッズテーブル取得
        table = soup.select_one("table.RaceOdds_HorseList_Table, table.Odds_Table")
        if not table:
            print(f"    no odds table")
            return {}
        
        # 馬番 → {tan: オッズ, fuku_min: 複勝下限, fuku_max: 複勝上限}
        odds_data = {}
        
        rows = table.find_all("tr")
        for row in rows[1:]:  # ヘッダースキップ
            cols = row.find_all("td")
            if len(cols) < 4:
                continue
            
            # 馬番取得（通常は2列目あたり）
            umaban_text = ""
            for col in cols[:3]:
                text = col.get_text(strip=True)
                if text.isdigit():
                    umaban_text = text
                    break
            
            if not umaban_text:
                continue
            
            # オッズらしき数値を探す
            odds_values = []
            for col in cols:
                text = col.get_text(strip=True)
                # 数値+小数点のパターン
                if re.match(r"^\d+\.\d+$", text):
                    odds_values.append(text)
                elif re.match(r"^\d+\.\d+\s*[-~]\s*\d+\.\d+$", text):
                    # 複勝の範囲表記
                    odds_values.append(text)
            
            if odds_values:
                odds_data[umaban_text] = {
                    "tansho": odds_values[0] if len(odds_values) > 0 else "",
                    "fukusho": odds_values[1] if len(odds_values) > 1 else "",
                }
        
        print(f"    got odds for {len(odds_data)} horses")
        return odds_data
        
    except Exception as e:
        print(f"    odds error: {e}")
        return {}


def merge_odds(race_data, odds_data):
    """馬データにオッズを合体"""
    if not odds_data:
        return race_data
    
    for horse in race_data["horses"]:
        umaban = horse.get("umaban", "")
        if umaban in odds_data:
            horse["odds_tansho"] = odds_data[umaban].get("tansho", "")
            horse["odds_fukusho"] = odds_data[umaban].get("fukusho", "")
        else:
            horse["odds_tansho"] = ""
            horse["odds_fukusho"] = ""
    
    return race_data


def main():
    print("=" * 60)
    print(f"umayomi Selenium v5 - {datetime.now()}")
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
            
            mains = [r for r in race_ids if r.endswith("11")]
            target = mains[:2] if mains else race_ids[:2]
            
            for rid in target:
                print(f"  race_id: {rid}")
                try:
                    # 出馬表取得
                    data = get_race_detail(driver, rid)
                    if not data:
                        continue
                    
                    # オッズ取得して合体
                    odds = get_odds(driver, rid)
                    data = merge_odds(data, odds)
                    
                    data["date"] = date_str
                    all_races[rid] = data
                    race_name_display = data['race_name'] or '(no name)'
                    print(f"    OK: {race_name_display} ({len(data['horses'])} horses, {len(odds)} odds)")
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
