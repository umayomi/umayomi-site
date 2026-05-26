#!/usr/bin/env python3
"""
umarengod.com スクレイピング実験スクリプト

目的:
- umarengod.com の出馬表ページからデータが取得可能か検証する
- 段階的にテストして、どこまで動くかを確認する
- 既存の scrape.py には影響を与えない独立した実験用コード

実行:
- GitHub Actions の手動実行のみ
- 失敗してもエラーで止まらず、各段階のログを残す
"""

import json
import time
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


# 出力先（実験データ専用）
OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(exist_ok=True)

SCREENSHOT_DIR = Path("data/test_screenshots")
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def create_driver():
    """既存scrape.pyと同じ設定でdriver作成"""
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,3000')  # 縦長にして全体を見やすく
    options.add_argument('--lang=ja-JP')
    options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    
    if HAS_WDM:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    else:
        driver = webdriver.Chrome(options=options)
    
    return driver


def save_screenshot(driver, name):
    """スクリーンショット保存"""
    try:
        path = SCREENSHOT_DIR / f"{name}.png"
        driver.save_screenshot(str(path))
        print(f"  [スクショ保存] {path}")
        return True
    except Exception as e:
        print(f"  [スクショ失敗] {e}")
        return False


def save_html(driver, name):
    """HTML保存（デバッグ用）"""
    try:
        path = SCREENSHOT_DIR / f"{name}.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print(f"  [HTML保存] {path}")
        return True
    except Exception as e:
        print(f"  [HTML保存失敗] {e}")
        return False


def stage_1_access(driver):
    """段階1: サイトにアクセスできるか"""
    print("\n" + "="*60)
    print("段階1: サイトアクセス")
    print("="*60)
    
    url = "https://umarengod.com/srch6.php"
    print(f"  URL: {url}")
    
    try:
        driver.get(url)
        time.sleep(5)
        
        title = driver.title
        html_size = len(driver.page_source)
        
        print(f"  ✅ アクセス成功")
        print(f"     タイトル: {title}")
        print(f"     HTML size: {html_size}")
        
        save_screenshot(driver, "stage1_initial")
        save_html(driver, "stage1_initial")
        
        return True
    except Exception as e:
        print(f"  ❌ アクセス失敗: {e}")
        return False


def stage_2_find_tabs(driver):
    """段階2: 日付タブと競馬場タブが見つかるか"""
    print("\n" + "="*60)
    print("段階2: タブ要素の検出")
    print("="*60)
    
    soup = BeautifulSoup(driver.page_source, "html.parser")
    
    # 日付タブ（例: "5月31日(日)"）を探す
    print("\n[日付タブを探す]")
    date_tabs = []
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        # 「N月N日(曜)」のパターン
        if "月" in text and "日" in text and "(" in text:
            date_tabs.append({"text": text, "href": href})
            print(f"  発見: {text} -> {href}")
    
    if not date_tabs:
        print("  ⚠️  日付タブが見つかりません")
    
    # 競馬場タブ（東京・京都など）を探す
    print("\n[競馬場タブを探す]")
    venue_tabs = []
    venues = ["東 京", "京 都", "中 山", "阪 神", "中 京", "新 潟", "福 島", "小 倉", "札 幌", "函 館",
              "東京", "京都", "中山", "阪神", "中京", "新潟", "福島", "小倉", "札幌", "函館"]
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        if text in venues:
            venue_tabs.append({"text": text, "href": href})
            print(f"  発見: {text} -> {href}")
    
    if not venue_tabs:
        print("  ⚠️  競馬場タブが見つかりません")
    
    # レース名リンクを探す
    print("\n[レース名リンクを探す]")
    race_links = []
    for a in soup.find_all("a"):
        text = a.get_text(strip=True)
        href = a.get("href", "")
        # 「srch6_post_sel」を含むjavascriptリンク
        if "srch6_post_sel" in href:
            race_links.append({"text": text, "href": href})
            print(f"  発見: {text} -> {href[:80]}")
    
    if not race_links:
        print("  ⚠️  レース名リンクが見つかりません")
    
    return {
        "date_tabs": date_tabs,
        "venue_tabs": venue_tabs,
        "race_links": race_links,
    }


def stage_3_click_race(driver, race_name):
    """段階3: レース名リンクをクリックしてみる"""
    print("\n" + "="*60)
    print(f"段階3: レースクリック - {race_name}")
    print("="*60)
    
    # 方法A: SeleniumのLINK_TEXTでクリック
    try:
        print(f"\n[方法A: LINK_TEXT='{race_name}' でクリック]")
        link = driver.find_element(By.LINK_TEXT, race_name)
        print(f"  リンク発見: {link.get_attribute('href')[:80]}")
        link.click()
        time.sleep(5)
        
        save_screenshot(driver, "stage3_after_click")
        save_html(driver, "stage3_after_click")
        
        new_html_size = len(driver.page_source)
        print(f"  クリック後HTML size: {new_html_size}")
        
        # 出馬表らしいテーブルがあるか確認
        soup = BeautifulSoup(driver.page_source, "html.parser")
        
        # 馬名や騎手っぽいテキストがあるか
        if "登録馬" in driver.page_source or "馬名" in driver.page_source:
            print(f"  ✅ 出馬表ページに遷移したっぽい！")
            return True
        else:
            print(f"  ⚠️  まだ出馬表が見えない")
            return False
        
    except Exception as e:
        print(f"  ❌ 方法A失敗: {e}")
        return False


def stage_4_extract_table(driver):
    """段階4: テーブルからデータ抽出を試みる"""
    print("\n" + "="*60)
    print("段階4: テーブルデータ抽出")
    print("="*60)
    
    soup = BeautifulSoup(driver.page_source, "html.parser")
    tables = soup.find_all("table")
    print(f"  ページ内のテーブル数: {len(tables)}")
    
    if not tables:
        print("  ❌ テーブルなし")
        return None
    
    # 最も馬データらしいテーブルを探す
    best_table = None
    best_score = 0
    
    for i, table in enumerate(tables):
        text = table.get_text()
        score = 0
        # 出馬表っぽいキーワードでスコアリング
        keywords = ["馬名", "騎手", "斤量", "性齢", "調教師", "出走間隔"]
        for kw in keywords:
            if kw in text:
                score += 1
        
        rows = table.find_all("tr")
        if score >= 3 and len(rows) >= 5:
            print(f"  テーブル{i}: スコア={score}, 行数={len(rows)}")
            if score > best_score:
                best_table = table
                best_score = score
    
    if not best_table:
        print("  ⚠️  出馬表らしいテーブルが見つかりません")
        return None
    
    print(f"\n  ✅ 出馬表テーブル発見 (score={best_score})")
    
    # データ抽出を試す
    rows = best_table.find_all("tr")
    if len(rows) < 2:
        return None
    
    # ヘッダー
    header_cells = rows[0].find_all(["th", "td"])
    headers = [cell.get_text(strip=True) for cell in header_cells]
    print(f"\n  ヘッダー: {headers}")
    
    # 最初の数頭分のデータ
    horses = []
    for row in rows[1:6]:  # 最初の5頭だけサンプル
        cells = row.find_all("td")
        if not cells:
            continue
        horse_data = [cell.get_text(strip=True) for cell in cells]
        horses.append(horse_data)
        print(f"  馬: {horse_data[:5]}...")  # 最初の5項目だけ表示
    
    return {
        "headers": headers,
        "horses_sample": horses,
        "total_rows": len(rows) - 1,
    }


def main():
    print("="*60)
    print(f"umarengod スクレイピング実験 - {datetime.now()}")
    print("="*60)
    
    driver = None
    results = {
        "timestamp": datetime.now().isoformat(),
        "stages": {},
    }
    
    try:
        driver = create_driver()
        print("✅ Driver作成成功")
        
        # 段階1: アクセス
        stage1_ok = stage_1_access(driver)
        results["stages"]["stage1_access"] = stage1_ok
        if not stage1_ok:
            print("\n❌ 段階1失敗のため終了")
            return
        
        # 段階2: タブ検出
        tabs_info = stage_2_find_tabs(driver)
        results["stages"]["stage2_tabs"] = {
            "date_count": len(tabs_info["date_tabs"]),
            "venue_count": len(tabs_info["venue_tabs"]),
            "race_count": len(tabs_info["race_links"]),
            "race_samples": [r["text"] for r in tabs_info["race_links"][:5]],
        }
        
        # 段階3: レースクリック（見つかった最初のレースで試す）
        if tabs_info["race_links"]:
            first_race = tabs_info["race_links"][0]["text"]
            print(f"\n  ※ 最初に見つかったレース「{first_race}」でテスト")
            click_ok = stage_3_click_race(driver, first_race)
            results["stages"]["stage3_click"] = {
                "race_name": first_race,
                "success": click_ok,
            }
            
            # 段階4: テーブル抽出（クリック成功した場合のみ）
            if click_ok:
                table_data = stage_4_extract_table(driver)
                results["stages"]["stage4_extract"] = {
                    "success": table_data is not None,
                    "data": table_data,
                }
        else:
            print("\n⚠️  レースリンクなしのため段階3スキップ")
        
        # 結果保存
        output_file = OUTPUT_DIR / "test_umarengod.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 結果を保存: {output_file}")
        
        print("\n" + "="*60)
        print("実験完了")
        print("="*60)
        print(f"段階1 (アクセス): {results['stages'].get('stage1_access')}")
        print(f"段階2 (タブ検出): {results['stages'].get('stage2_tabs')}")
        print(f"段階3 (クリック): {results['stages'].get('stage3_click')}")
        print(f"段階4 (抽出): {'成功' if results['stages'].get('stage4_extract', {}).get('success') else '失敗'}")
        
    except Exception as e:
        print(f"\n❌ FATAL: {e}")
        import traceback
        traceback.print_exc()
        results["fatal_error"] = str(e)
        
        # エラーでも結果保存
        try:
            output_file = OUTPUT_DIR / "test_umarengod.json"
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
        except:
            pass
    
    finally:
        if driver:
            driver.quit()


main()
