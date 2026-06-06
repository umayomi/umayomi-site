#!/usr/bin/env python3
"""ウマヨミ - Selenium版 v9 (フィルタ修正：特別レース全取得 + 平場2勝以上)"""

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
    "枠": "waku", "馬番": "umaban", "馬名": "horse_name",
    "性齢": "sex_age", "斤量": "weight_carried",
    "騎手": "jockey", "厩舎": "stable",
    "馬体重(増減)": "horse_weight", "馬体重": "horse_weight",
    "オッズ": "odds", "人気": "popularity",
}

# 特別レース系のキーワード（レース名で判定）
SPECIAL_RACE_KEYWORDS = [
    "特別", "ステークス", "Ｓ", "記念", "賞", "杯", "カップ",
    "ハンデ", "オープン", "オーフン",
]

# 平場の2勝以上系キーワード（venue_infoで判定）
HIGH_CLASS_KEYWORDS = [
    "2勝", "3勝", "２勝", "３勝",
    "オープン", "OP", "リステッド",
    "G1", "G2", "G3", "Ｇ１", "Ｇ２", "Ｇ３",
    "GⅠ", "GⅡ", "GⅢ", "(L)", "（Ｌ）",
]

# 除外キーワード（これらが含まれていたら絶対除外）
EXCLUDE_KEYWORDS = ["未勝利", "新馬", "1勝", "１勝"]


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
    for d in range(-7, 8):
        dd = today + timedelta(days=d)
        if dd.weekday() in [5, 6]:
            dates.append(dd.strftime("%Y%m%d"))
    return dates


def is_past_date(date_str):
    """過去の日付かどうか判定"""
    today = datetime.now().strftime("%Y%m%d")
    return date_str < today


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


def is_target_class(venue_info, race_name):
    """対象レースか判定
    
    ルール:
    1. 「未勝利・新馬・1勝クラス」は問答無用で除外
    2. レース名に「特別/ステークス/Ｓ/S/記念/賞/杯/カップ」→ 特別レース → 採用
    3. venue_info に「2勝/3勝/オープン/リステッド/G/L」→ 平場の2勝以上 → 採用
    """
    venue_info = venue_info or ""
    race_name = race_name or ""
    combined = venue_info + " " + race_name
    
    # ステップ1: 除外キーワードチェック
    for kw in EXCLUDE_KEYWORDS:
        if kw in combined:
            return False
    
    # ステップ2: 特別レース判定（レース名で）
    for kw in SPECIAL_RACE_KEYWORDS:
        if kw in race_name:
            return True
    
    # 「○○S」のような大文字Sで終わるパターン
    if re.search(r'[A-Za-z]?S\s*$|[A-Za-z]?S\s*\(', race_name):
        return True
    
    # ステップ3: 平場の高クラス判定（venue_infoで）
    for kw in HIGH_CLASS_KEYWORDS:
        if kw in venue_info:
            return True
    
    return False


def get_race_detail(driver, race_id):
    """出馬表ページから基本情報を取得"""
    url = f'https://race.netkeiba.com/race/shutuba.html?race_id={race_id}'
    print(f"    GET shutuba: {url}")
    
    driver.get(url)
    time.sleep(5)
    
    html = driver.page_source
    soup = BeautifulSoup(html, "html.parser")
    
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
    
    if not is_target_class(venue_info, race_name):
        print(f"    SKIP: not target class - {race_name} (venue: {venue_info[:50]})")
        return None
    
    print(f"    TARGET: {race_name}")
    
    table = soup.select_one("table.Shutuba_Table, table.RaceTable01")
    if not table:
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
        
        # horse_id を馬名リンクから抽出
        horse_link = row.select_one('a[href*="/horse/"]')
        if horse_link:
            m = re.search(r"/horse/(\d+)", horse_link.get("href", ""))
            if m:
                horse_dict["horse_id"] = m.group(1)
        
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


def get_odds_future(driver, race_id):
    """未来レース: 単勝オッズページから取得"""
    url = f'https://race.netkeiba.com/odds/index.html?race_id={race_id}&type=b1'
    print(f"    GET odds (future): {url}")
    
    try:
        driver.get(url)
        time.sleep(5)
        
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")
        
        table = soup.select_one("table.RaceOdds_HorseList_Table, table.Odds_Table")
        if not table:
            print(f"    no odds table")
            return {}
        
        odds_data = {}
        rows = table.find_all("tr")
        for row in rows[1:]:
            cols = row.find_all("td")
            if len(cols) < 4:
                continue
            
            umaban_text = ""
            for col in cols[:3]:
                text = col.get_text(strip=True)
                if text.isdigit():
                    umaban_text = text
                    break
            
            if not umaban_text:
                continue
            
            odds_values = []
            for col in cols:
                text = col.get_text(strip=True)
                if re.match(r"^\d+\.\d+$", text):
                    odds_values.append(text)
            
            if odds_values:
                odds_data[umaban_text] = {"tansho": odds_values[0]}
        
        print(f"    got odds for {len(odds_data)} horses")
        return odds_data
        
    except Exception as e:
        print(f"    odds error: {e}")
        return {}


def get_odds_past(driver, race_id):
    """過去レース: 結果ページから単勝オッズ取得"""
    url = f'https://db.netkeiba.com/race/{race_id}/'
    print(f"    GET result (past): {url}")
    
    try:
        driver.get(f'https://race.netkeiba.com/race/result.html?race_id={race_id}')
        time.sleep(3)
        
        driver.get(url)
        time.sleep(5)
        
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")
        
        table = soup.select_one("table.race_table_01, table.nk_tb_common")
        if not table:
            print(f"    no result table")
            return {}
        
        rows = table.find_all("tr")
        if len(rows) < 2:
            return {}
        
        header_cells = rows[0].find_all(["th", "td"])
        odds_col_idx = -1
        umaban_col_idx = -1
        
        for i, cell in enumerate(header_cells):
            text = cell.get_text(strip=True)
            if text == "単勝":
                odds_col_idx = i
            if text == "馬番":
                umaban_col_idx = i
        
        if odds_col_idx == -1 or umaban_col_idx == -1:
            print(f"    odds/umaban column not found (odds_idx={odds_col_idx}, umaban_idx={umaban_col_idx})")
            return {}
        
        odds_data = {}
        for row in rows[1:]:
            cols = row.find_all("td")
            if len(cols) <= max(odds_col_idx, umaban_col_idx):
                continue
            
            umaban_text = cols[umaban_col_idx].get_text(strip=True)
            odds_text = cols[odds_col_idx].get_text(strip=True)
            
            if not umaban_text.isdigit():
                continue
            
            if re.match(r"^\d+\.\d+$", odds_text):
                odds_data[umaban_text] = {"tansho": odds_text}
        
        print(f"    got past odds for {len(odds_data)} horses")
        return odds_data
        
    except Exception as e:
        print(f"    past odds error: {e}")
        return {}


def merge_odds(race_data, odds_data):
    if not odds_data:
        return race_data
    
    for horse in race_data["horses"]:
        umaban = horse.get("umaban", "")
        if umaban in odds_data:
            horse["odds_tansho"] = odds_data[umaban].get("tansho", "")
        else:
            horse["odds_tansho"] = ""
    
    return race_data


def main():
    print("=" * 60)
    print(f"umayomi Selenium v9 (filter fix) - {datetime.now()}")
    print("=" * 60)
    
    driver = None
    try:
        driver = create_driver()
        print("driver ready")
        
        dates = get_target_dates()
        print(f"target dates: {dates}")
        
        all_races = {}
        skipped_count = 0
        
        for date_str in dates:
            print(f"\n[{date_str}]")
            is_past = is_past_date(date_str)
            print(f"  is_past: {is_past}")
            
            try:
                race_ids = get_race_ids(driver, date_str)
            except Exception as e:
                print(f"  error: {e}")
                continue
            
            if not race_ids:
                continue
            
            print(f"  processing {len(race_ids)} race_ids")
            
            for rid in race_ids:
                print(f"  race_id: {rid}")
                try:
                    data = get_race_detail(driver, rid)
                    if not data:
                        skipped_count += 1
                        continue
                    
                    if is_past:
                        odds = get_odds_past(driver, rid)
                    else:
                        odds = get_odds_future(driver, rid)
                    
                    data = merge_odds(data, odds)
                    
                    data["date"] = date_str
                    data["is_past"] = is_past
                    all_races[rid] = data
                    race_name_display = data['race_name'] or '(no name)'
                    print(f"    OK: {race_name_display} ({len(data['horses'])} horses)")
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
        
        print(f"\nDONE: {len(all_races)} races saved, {skipped_count} skipped")
        
    except Exception as e:
        print(f"FATAL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    finally:
        if driver:
            driver.quit()


main()
