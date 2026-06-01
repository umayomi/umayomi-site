#!/usr/bin/env python3
"""
umarengod.com スクレイピング v2

改善点:
- races.json から未来レースを取得して連動
- 日付タブ・競馬場タブ・レースリンクを順番にクリック
- 取得データを構造化して保存
- エラー耐性とログ強化
- 馬番マーカー方式（強化版）で過去実績を正確に各馬に割り当て
"""

import json
import time
import re
import sys
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

try:
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_WDM = True
except ImportError:
    HAS_WDM = False


OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(exist_ok=True)

SCREENSHOT_DIR = Path("data/test_screenshots_v2")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


VENUE_CODE_MAP = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}

WEEKDAY_MAP = {0: "月", 1: "火", 2: "水", 3: "木", 4: "金", 5: "土", 6: "日"}


def create_driver():
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,3000')
    options.add_argument('--lang=ja-JP')
    options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    
    if HAS_WDM:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    else:
        driver = webdriver.Chrome(options=options)
    
    return driver


def save_screenshot(driver, name):
    try:
        path = SCREENSHOT_DIR / f"{name}.png"
        driver.save_screenshot(str(path))
        print(f"    [スクショ] {name}.png")
    except Exception as e:
        print(f"    [スクショ失敗] {e}")


def load_races():
    races_file = OUTPUT_DIR / "races.json"
    if not races_file.exists():
        print(f"❌ races.json が見つかりません")
        return []
    
    with open(races_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    future_races = []
    for race_id, race in data.get("races", {}).items():
        if not race.get("is_past", False):
            future_races.append(race)
    
    return future_races


def race_id_to_venue(race_id):
    if len(race_id) < 4:
        return None
    venue_code = race_id[4:6]
    return VENUE_CODE_MAP.get(venue_code)


def date_to_tab_text(date_str):
    try:
        d = datetime.strptime(date_str, "%Y%m%d")
        weekday = WEEKDAY_MAP[d.weekday()]
        return f"{d.month}月{d.day}日({weekday})"
    except Exception:
        return None


def click_link_by_text(driver, text, what="リンク"):
    try:
        link = driver.find_element(By.LINK_TEXT, text)
        link.click()
        time.sleep(3)
        print(f"    ✅ {what}クリック成功: {text} (method: LINK_TEXT)")
        return True
    except Exception:
        pass
    
    text_clean = text.replace(" ", "").replace("\u3000", "").replace("\xa0", "")
    if text_clean and text_clean != text:
        try:
            link = driver.find_element(By.PARTIAL_LINK_TEXT, text_clean)
            link.click()
            time.sleep(3)
            print(f"    ✅ {what}クリック成功: {text} (method: PARTIAL_LINK_TEXT)")
            return True
        except Exception:
            pass
    
    try:
        xpath = f"//*[contains(normalize-space(.), '{text}') and (@onclick or @href or self::a or self::td)]"
        elements = driver.find_elements(By.XPATH, xpath)
        for el in elements:
            try:
                el.click()
                time.sleep(3)
                print(f"    ✅ {what}クリック成功: {text} (method: XPath any)")
                return True
            except Exception:
                continue
    except Exception:
        pass
    
    if text_clean and len(text_clean) >= 2:
        try:
            xpath = f"//*[contains(., '{text_clean[0]}') and contains(., '{text_clean[1]}') and (@onclick or @href or self::a or self::td)]"
            elements = driver.find_elements(By.XPATH, xpath)
            for el in elements:
                el_text = el.text.replace(" ", "").replace("\u3000", "").replace("\xa0", "")
                if text_clean in el_text:
                    try:
                        el.click()
                        time.sleep(3)
                        print(f"    ✅ {what}クリック成功: {text} (method: XPath no-space)")
                        return True
                    except Exception:
                        continue
        except Exception:
            pass
    
    print(f"    ❌ {what}クリック失敗 ({text}): 全戦略失敗")
    return False


def find_available_links(driver, link_type="all"):
    soup = BeautifulSoup(driver.page_source, "html.parser")
    links = []
    
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        
        if link_type == "date" and "月" in text and "日" in text and "(" in text:
            links.append(text)
        elif link_type == "venue":
            text_normalized = text.replace(" ", "").replace("\u3000", "").replace("\xa0", "")
            if text_normalized in ["札幌", "函館", "福島", "新潟", "東京", "中山", "中京", "京都", "阪神", "小倉"]:
                links.append(text)
        elif link_type == "race" and "srch6_post_sel" in href:
            links.append(text)
        elif link_type == "all":
            links.append({"text": text, "href": href})
    
    return links


def extract_race_data(driver, race_id, race_name):
    """
    出馬表テーブルからデータを抽出（馬番マーカー方式・強化版）
    """
    soup = BeautifulSoup(driver.page_source, "html.parser")
    tables = soup.find_all("table")
    
    best_table = None
    best_score = 0
    
    for table in tables:
        text = table.get_text()
        score = 0
        for kw in ["馬名", "騎手", "斤量", "性齢", "調教師"]:
            if kw in text:
                score += 1
        rows = table.find_all("tr")
        if score >= 3 and len(rows) >= 5:
            if score > best_score:
                best_table = table
                best_score = score
    
    if not best_table:
        return None
    
    rows = best_table.find_all("tr")
    
    # 全セルを順序通り取得
    all_cells = []
    for row in rows:
        for cell in row.find_all("td"):
            text = cell.get_text(strip=True)
            all_cells.append(text)
    
    print(f"    [DEBUG] 全セル数: {len(all_cells)}")
    
    # 基本情報セクションの馬データ抽出
    basic_info_horses = []
    
    i = 0
    expected_umaban = 1
    while i < len(all_cells):
        cell = all_cells[i]
        if cell == str(expected_umaban):
            non_empty_after = []
            j = i + 1
            while j < len(all_cells) and len(non_empty_after) < 10:
                if all_cells[j]:
                    non_empty_after.append((j, all_cells[j]))
                j += 1
            
            if len(non_empty_after) >= 6:
                name_idx, name = non_empty_after[0]
                sex_age = non_empty_after[1][1] if len(non_empty_after) > 1 else ""
                weight = non_empty_after[2][1] if len(non_empty_after) > 2 else ""
                interval = non_empty_after[3][1] if len(non_empty_after) > 3 else ""
                jockey = non_empty_after[4][1] if len(non_empty_after) > 4 else ""
                trainer = non_empty_after[5][1] if len(non_empty_after) > 5 else ""
                
                if re.search(r'[\u30a0-\u30ff\u4e00-\u9faf]', name) and re.match(r'^[牡牝せ騙セ]\d+$', sex_age):
                    basic_info_horses.append({
                        "umaban": cell,
                        "horse_name": name,
                        "sex_age": sex_age,
                        "weight_carried": weight,
                        "interval": interval,
                        "jockey": jockey,
                        "trainer": trainer,
                        "_basic_end_idx": non_empty_after[5][0],
                    })
                    expected_umaban += 1
                    i = non_empty_after[5][0] + 1
                    continue
        i += 1
    
    num_horses = len(basic_info_horses)
    print(f"    [DEBUG] 基本情報から検出した馬数: {num_horses}")
    
    if num_horses == 0:
        return {
            "race_id": race_id,
            "race_name": race_name,
            "horses": [],
            "horse_count": 0,
            "extracted_at": datetime.now().isoformat(),
            "note": "馬データ検出失敗",
        }
    
    # 統計セクションは基本情報の終了後
    basic_section_end = basic_info_horses[-1]["_basic_end_idx"] + 1
    stats_section_full = all_cells[basic_section_end:]
    
    print(f"    [DEBUG] 統計セクション(全): {len(stats_section_full)}個")
    
    # 統計セクション内で「基本情報の重複出現位置」を検出して、そこで切り捨てる
    # 重複は「馬番1 → 馬名 → 性齢」のパターンで始まる
    stats_section = stats_section_full
    for idx in range(len(stats_section_full)):
        cell = stats_section_full[idx]
        if cell == "1":  # 馬番1の可能性
            # 後続セルに「馬名+性齢」のパターン
            non_empty_after = []
            j = idx + 1
            while j < len(stats_section_full) and len(non_empty_after) < 5:
                if stats_section_full[j]:
                    non_empty_after.append(stats_section_full[j])
                j += 1
            
            if len(non_empty_after) >= 3:
                name = non_empty_after[0]
                sex_age = non_empty_after[1]
                # 馬名（漢字・カナ）+ 性齢パターン
                if (re.search(r'[\u30a0-\u30ff\u4e00-\u9faf]', name) and 
                    re.match(r'^[牡牝せ騙セ]\d+$', sex_age)):
                    # この位置で重複が始まる → ここで切り捨て
                    stats_section = stats_section_full[:idx]
                    print(f"    [DEBUG] 末尾重複検出: idx={idx} で切り捨て（{len(stats_section_full)}→{len(stats_section)}）")
                    break
    
    # 馬番マーカー位置を見つける（各馬データの末尾の馬番）
    # 統計セクション内で「N」(N=1〜num_horses)が出現する位置を、Nの増加順に探す
    def is_stats_data(text):
        """統計データらしいセル内容か判定（緩めに）"""
        if not text:
            return False
        if text == "初":
            return True
        if re.match(r'^\d+-\s*\d+-\s*\d+-\s*\d+$', text.strip()):
            return True
        if re.match(r'^\d+\.\d+$', text):
            return True
        if re.match(r'^\d+位$', text):
            return True
        if re.match(r'^\d+着$', text):
            return True
        if re.match(r'^\d+人$', text):
            return True
        # 「X.X(X)」形式（上り3F）
        if re.match(r'^\d+\.\d+\(\d+\)$', text):
            return True
        # 「N-N-N」3要素もOK（産駒成績で出現する）
        if re.match(r'^\d+-\s*\d+-\s*\d+$', text.strip()):
            return True
        # コース文言
        if re.search(r'(ダ|芝)\d{3,4}', text):
            return True
        # ステークス・賞・クラス
        if "ステークス" in text or "賞" in text or "クラス" in text or "Ｓ" in text:
            return True
        # 「無」「取」も統計記号
        if text in ("無", "取"):
            return True
        # 漢字・カナ馬名（血統名）
        if re.search(r'[\u30a0-\u30ff\u4e00-\u9faf]', text) and len(text) >= 2:
            return True
        # 英字血統名（半角スペース・コンマ含む）
        if re.match(r'^[A-Za-z\' .\-]+$', text) and len(text) >= 3:
            return True
        return False
    
    # 馬番マーカーを順番に検索（前回より緩い判定で）
    horse_boundaries = [0]  # 馬1のデータ開始位置
    
    for target_umaban in range(1, num_horses):
        target_str = str(target_umaban)
        search_start = horse_boundaries[-1]
        found = False
        
        # 最低 10セル以降から探す（馬データには十分なセル数があるはず）
        min_offset = 10 if target_umaban == 1 else 5
        
        for idx in range(search_start + min_offset, len(stats_section)):
            if stats_section[idx] == target_str:
                # 直前に統計データがあるか（過去5セル以内）
                prev_ok = False
                for back in range(1, 8):
                    if idx - back < search_start:
                        break
                    prev_cell = stats_section[idx - back]
                    if is_stats_data(prev_cell):
                        prev_ok = True
                        break
                    if not prev_cell:  # 空セルはスキップ
                        continue
                    # 統計データじゃない非空セルが出てきたら判定継続せず終了
                    break
                
                # 直後に統計データがあるか（次5セル以内）
                next_ok = False
                for fwd in range(1, 8):
                    if idx + fwd >= len(stats_section):
                        break
                    next_cell = stats_section[idx + fwd]
                    if is_stats_data(next_cell):
                        next_ok = True
                        break
                    if not next_cell:
                        continue
                    break
                
                if prev_ok and next_ok:
                    horse_boundaries.append(idx + 1)
                    found = True
                    break
        
        if not found:
            print(f"    [DEBUG] 馬番{target_umaban}の終了マーカーが見つからない（フォールバック）")
            per_horse = len(stats_section) // num_horses
            horse_boundaries.append(target_umaban * per_horse)
    
    horse_boundaries.append(len(stats_section))
    
    print(f"    [DEBUG] 馬データ境界: {horse_boundaries[:5]}...{horse_boundaries[-3:]} (合計{len(horse_boundaries)}個)")
    
    # 各馬に統計データを割り当て
    for idx, horse in enumerate(basic_info_horses):
        start = horse_boundaries[idx]
        end = horse_boundaries[idx + 1]
        horse_stats = stats_section[start:end]
        non_empty_stats = [c for c in horse_stats if c]
        horse["stats_raw"] = " | ".join(non_empty_stats)
        del horse["_basic_end_idx"]
    
    return {
        "race_id": race_id,
        "race_name": race_name,
        "horses": basic_info_horses,
        "horse_count": len(basic_info_horses),
        "extracted_at": datetime.now().isoformat(),
    }


def process_one_race(driver, race):
    race_id = race["race_id"]
    race_name = race.get("race_name", "")
    date_str = race.get("date", "")
    venue = race_id_to_venue(race_id)
    date_tab_text = date_to_tab_text(date_str)
    
    print(f"\n  処理: {race_id} {race_name}")
    print(f"    日付: {date_str} → タブ「{date_tab_text}」")
    print(f"    競馬場: {venue}")
    
    try:
        driver.get("https://umarengod.com/srch6.php")
        time.sleep(4)
    except Exception as e:
        print(f"    ❌ トップアクセス失敗: {e}")
        return None
    
    if not date_tab_text:
        print(f"    ❌ 日付変換失敗")
        return None
    
    try:
        d = datetime.strptime(date_str, "%Y%m%d")
        date_iso = d.strftime("%Y-%m-%d")
    except Exception:
        print(f"    ❌ 日付ISO変換失敗")
        return None
    
    soup = BeautifulSoup(driver.page_source, "html.parser")
    tab_func_template = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "srch6_post_tab" in href and date_iso in href:
            tab_func_template = href
            break
    
    if venue:
        if tab_func_template:
            func_call = tab_func_template.replace("javascript:", "")
            func_call = re.sub(r'"[^"]*"(\s*,\s*2021)', f'"{venue}"\\1', func_call)
            print(f"    JS実行: {func_call}")
            try:
                driver.execute_script(func_call)
                time.sleep(4)
                print(f"    ✅ タブ切り替え（JS関数）: {venue}")
            except Exception as e:
                print(f"    ❌ JS実行失敗: {e}")
                return None
        else:
            func_call = f'srch6_post_tab(0,0,"{date_iso}","{venue}",2021)'
            print(f"    JS実行(組立): {func_call}")
            try:
                driver.execute_script(func_call)
                time.sleep(4)
                print(f"    ✅ タブ切り替え（JS組立）: {venue}")
            except Exception as e:
                print(f"    ❌ JS実行失敗: {e}")
                return None
    else:
        print(f"    ⚠️ 競馬場不明、日付タブのみクリック")
        if date_tab_text:
            click_link_by_text(driver, date_tab_text, "日付タブ")
    
    race_links = find_available_links(driver, "race")
    print(f"    利用可能なレース: {race_links}")
    
    def normalize_race_candidates(name):
        candidates = [name]
        if name.endswith("S"):
            candidates.append(name[:-1] + "ステークス")
            candidates.append(name[:-1] + "Ｓ")
        if name.endswith("ステークス"):
            candidates.append(name[:-5] + "S")
        if name.endswith("Ｓ"):
            candidates.append(name[:-1] + "S")
            candidates.append(name[:-1] + "ステークス")
        base = re.sub(r'[SＳ]$|ステークス$|杯$', '', name)
        if base and base != name:
            candidates.append(base)
        return candidates
    
    target_candidates = normalize_race_candidates(race_name)
    print(f"    レース名候補: {target_candidates}")
    
    matched_link = None
    for cand in target_candidates:
        if cand in race_links:
            matched_link = cand
            print(f"    ✅ 完全一致: {cand}")
            break
    
    if not matched_link:
        for cand in target_candidates:
            if not cand:
                continue
            for rl in race_links:
                if cand in rl or rl in cand:
                    matched_link = rl
                    print(f"    部分一致: {cand} ⇔ {rl}")
                    break
            if matched_link:
                break
    
    if not matched_link:
        print(f"    ❌ レース名「{race_name}」が見つかりません（候補: {race_links}）")
        return None
    
    if not click_link_by_text(driver, matched_link, "レース"):
        return None
    
    save_screenshot(driver, f"race_{race_id}")
    
    data = extract_race_data(driver, race_id, race_name)
    if data:
        print(f"    ✅ {data.get('horse_count', 0)}頭分のデータ取得")
    else:
        print(f"    ❌ データ抽出失敗")
    
    return data


def main():
    print("=" * 60)
    print(f"umarengod v2 - {datetime.now()}")
    print("=" * 60)
    
    future_races = load_races()
    print(f"\n未来レース数: {len(future_races)}")
    
    if not future_races:
        print("❌ 未来レースなし。終了")
        return
    
    special_races = [r for r in future_races if 
                     "S" in r.get("race_name", "") or 
                     "Ｓ" in r.get("race_name", "") or
                     "ステークス" in r.get("race_name", "") or
                     "賞" in r.get("race_name", "") or
                     "特別" in r.get("race_name", "") or
                     "杯" in r.get("race_name", "")]
    test_races = special_races[:3]
    print(f"テスト対象（最初の特別レース3つ）: {[r.get('race_name') for r in test_races]}")
    
    driver = None
    all_results = {
        "updated_at": datetime.now().isoformat(),
        "total_races": len(test_races),
        "races": {},
        "errors": [],
    }
    
    try:
        driver = create_driver()
        print("✅ Driver作成成功")
        
        for race in test_races:
            try:
                data = process_one_race(driver, race)
                if data:
                    all_results["races"][race["race_id"]] = data
                else:
                    all_results["errors"].append({
                        "race_id": race["race_id"],
                        "race_name": race.get("race_name"),
                        "reason": "データ取得失敗",
                    })
            except Exception as e:
                print(f"    ❌ 例外: {e}")
                all_results["errors"].append({
                    "race_id": race["race_id"],
                    "race_name": race.get("race_name"),
                    "reason": str(e),
                })
            
            time.sleep(3)
        
        output_file = OUTPUT_DIR / "test_umarengod_v2.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 結果保存: {output_file}")
        
        print("\n" + "=" * 60)
        print("実験完了")
        print("=" * 60)
        print(f"成功: {len(all_results['races'])}/{len(test_races)} レース")
        print(f"エラー: {len(all_results['errors'])}")
        for race_id, data in all_results["races"].items():
            print(f"  {race_id} {data.get('race_name')}: {data.get('horse_count', 0)}頭")
        
    except Exception as e:
        print(f"\n❌ FATAL: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        if driver:
            driver.quit()


main()
