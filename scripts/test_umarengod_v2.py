#!/usr/bin/env python3
"""
umarengod.com スクレイピング v2

改善点:
- races.json から未来レースを取得して連動
- 日付タブ・競馬場タブ・レースリンクを順番にクリック
- 取得データを構造化して保存
- エラー耐性とログ強化
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


# 競馬場コード → 名前
VENUE_CODE_MAP = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}

# 曜日マップ
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
    """races.json から未来レースを読み込む"""
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
    """race_id (12桁) から競馬場名を取得"""
    if len(race_id) < 4:
        return None
    venue_code = race_id[4:6]
    return VENUE_CODE_MAP.get(venue_code)


def date_to_tab_text(date_str):
    """20260531 → 5月31日(日)"""
    try:
        d = datetime.strptime(date_str, "%Y%m%d")
        weekday = WEEKDAY_MAP[d.weekday()]
        return f"{d.month}月{d.day}日({weekday})"
    except Exception:
        return None


def click_link_by_text(driver, text, what="リンク"):
    """テキスト一致でリンククリック"""
    try:
        link = driver.find_element(By.LINK_TEXT, text)
        link.click()
        time.sleep(3)
        print(f"    ✅ {what}クリック成功: {text}")
        return True
    except Exception as e:
        print(f"    ❌ {what}クリック失敗 ({text}): {type(e).__name__}")
        return False


def find_available_links(driver, link_type="all"):
    """ページ内の特定種類のリンクを探す"""
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
    """出馬表テーブルからデータを抽出"""
    soup = BeautifulSoup(driver.page_source, "html.parser")
    tables = soup.find_all("table")
    
    # 出馬表らしいテーブルを探す
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
    
    # 行ごとに馬データを抽出
    rows = best_table.find_all("tr")
    horses = []
    
    # ヘッダー行
    header_row_idx = None
    for i, row in enumerate(rows):
        cells = row.find_all(["th", "td"])
        cell_texts = [c.get_text(strip=True) for c in cells]
        if "馬名" in cell_texts or "馬\u3000名" in cell_texts or any("馬\xa0名" in t for t in cell_texts):
            header_row_idx = i
            break
    
    if header_row_idx is None:
        print(f"    ⚠️ ヘッダー行が見つかりません")
        # フォールバック: テーブル全体を生テキストで保存
        return {
            "race_id": race_id,
            "race_name": race_name,
            "raw_text": best_table.get_text("|", strip=True)[:5000],  # 5000文字制限
            "horses": [],
            "extracted_at": datetime.now().isoformat(),
            "note": "ヘッダー検出失敗。raw_textから後処理が必要",
        }
    
    print(f"    ✅ ヘッダー行: {header_row_idx}行目")
    
    # 馬データ行をパース
    # umarengodのデータ行は「枠・馬番・馬名・性齢・斤量・出走間隔・騎手・調教師」が並ぶ
    current_horse = None
    
    for row in rows[header_row_idx + 1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        
        cell_texts = [c.get_text(strip=True) for c in cells]
        
        # 馬番（数字のみのセル）が先頭にあるか確認
        # 通常 ['', '1', '馬名', '牡4', '58.0', '中1週', '騎手', '調教師', ...] のような構造
        # 空セルをスキップしながら馬番を探す
        non_empty = [t for t in cell_texts if t]
        if not non_empty:
            continue
        
        # 最初の数字が馬番の可能性
        first_num = None
        first_num_idx = -1
        for i, t in enumerate(cell_texts):
            if t and t.isdigit():
                first_num = t
                first_num_idx = i
                break
        
        if first_num is None:
            continue
        
        # 馬番後の項目を取得
        after = cell_texts[first_num_idx + 1:]
        after_non_empty = [t for t in after if t]
        
        if len(after_non_empty) < 5:
            continue
        
        horse = {
            "umaban": first_num,
            "horse_name": after_non_empty[0] if len(after_non_empty) > 0 else "",
            "sex_age": after_non_empty[1] if len(after_non_empty) > 1 else "",
            "weight_carried": after_non_empty[2] if len(after_non_empty) > 2 else "",
            "interval": after_non_empty[3] if len(after_non_empty) > 3 else "",
            "jockey": after_non_empty[4] if len(after_non_empty) > 4 else "",
            "trainer": after_non_empty[5] if len(after_non_empty) > 5 else "",
            # 残りの統計データ全部
            "stats_raw": "|".join(after_non_empty[6:]) if len(after_non_empty) > 6 else "",
        }
        
        # 馬名がそれっぽいか確認（漢字・カタカナを含む）
        if not re.search(r'[\u30a0-\u30ff\u4e00-\u9faf]', horse["horse_name"]):
            continue
        
        horses.append(horse)
    
    return {
        "race_id": race_id,
        "race_name": race_name,
        "horses": horses,
        "horse_count": len(horses),
        "extracted_at": datetime.now().isoformat(),
    }


def process_one_race(driver, race):
    """1レース分の処理"""
    race_id = race["race_id"]
    race_name = race.get("race_name", "")
    date_str = race.get("date", "")
    venue = race_id_to_venue(race_id)
    date_tab_text = date_to_tab_text(date_str)
    
    print(f"\n  処理: {race_id} {race_name}")
    print(f"    日付: {date_str} → タブ「{date_tab_text}」")
    print(f"    競馬場: {venue}")
    
    # 1. トップページに戻る
    try:
        driver.get("https://umarengod.com/srch6.php")
        time.sleep(4)
    except Exception as e:
        print(f"    ❌ トップアクセス失敗: {e}")
        return None
    
    # 2. 日付タブをクリック
    if not date_tab_text:
        print(f"    ❌ 日付変換失敗")
        return None
    
    date_links = find_available_links(driver, "date")
    if date_tab_text not in date_links:
        print(f"    ⚠️ 日付タブにない: {date_tab_text} (利用可能: {date_links})")
        # デフォルト日付で動くかもしれないので続行
    else:
        if not click_link_by_text(driver, date_tab_text, "日付タブ"):
            return None
    
    # 3. 競馬場タブをクリック（存在すれば）
    venue_links = find_available_links(driver, "venue")
    print(f"    利用可能な競馬場タブ: {venue_links}")
    
    if venue and venue_links:
        # スペース正規化して照合
        clicked = False
        for vl in venue_links:
            vl_normalized = vl.replace(" ", "").replace("\u3000", "").replace("\xa0", "")
            if vl_normalized == venue:
                if click_link_by_text(driver, vl, "競馬場タブ"):
                    clicked = True
                    break
        if not clicked:
            print(f"    ⚠️ 競馬場タブ「{venue}」が見つからない（検出: {venue_links}）")
    
    # 4. レース名リンクをクリック
    race_links = find_available_links(driver, "race")
    print(f"    利用可能なレース: {race_links}")
    
    # レース名の表記揺れに対応（「S」⇔「ステークス」など）
    def normalize_race_candidates(name):
        candidates = [name]
        # 「アハルテケS」→「アハルテケステークス」
        if name.endswith("S"):
            candidates.append(name[:-1] + "ステークス")
            candidates.append(name[:-1] + "Ｓ")
        # 「アハルテケステークス」→「アハルテケS」
        if name.endswith("ステークス"):
            candidates.append(name[:-5] + "S")
        # 全角S対応
        if name.endswith("Ｓ"):
            candidates.append(name[:-1] + "S")
            candidates.append(name[:-1] + "ステークス")
        # 共通の接頭辞を作成（最後の特殊文字を除去）
        base = re.sub(r'[SＳ]$|ステークス$|杯$', '', name)
        if base and base != name:
            candidates.append(base)
        return candidates
    
    target_candidates = normalize_race_candidates(race_name)
    print(f"    レース名候補: {target_candidates}")
    
    matched_link = None
    # 完全一致を優先
    for cand in target_candidates:
        if cand in race_links:
            matched_link = cand
            print(f"    ✅ 完全一致: {cand}")
            break
    
    # 部分一致でフォールバック
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
    
    # 5. データ抽出
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
    
    # races.json から未来レース取得
    future_races = load_races()
    print(f"\n未来レース数: {len(future_races)}")
    
    if not future_races:
        print("❌ 未来レースなし。終了")
        return
    
    # 確認用：最初の3レースだけテスト
    test_races = future_races[:3]
    print(f"テスト対象（最初の3レース）: {[r.get('race_name') for r in test_races]}")
    
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
            
            time.sleep(3)  # マナー
        
        # 結果保存
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
