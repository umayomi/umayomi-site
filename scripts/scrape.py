#!/usr/bin/env python3
"""
ウマヨミ - レースデータ取得スクリプト
GitHub Actionsで定期実行され、data/races.json を更新する
"""

import json
import time
import re
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("❌ 必要なライブラリがありません: pip install requests beautifulsoup4 lxml")
    sys.exit(1)

# 設定
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}
SLEEP_SEC = 2.0  # サーバー負荷軽減のための待ち時間
TIMEOUT = 20
OUTPUT_DIR = Path("data")
OUTPUT_DIR.mkdir(exist_ok=True)


def fetch(url, encoding="EUC-JP"):
    """指定URLを取得"""
    print(f"  📡 GET {url}")
    try:
        res = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        res.encoding = encoding
        res.raise_for_status()
        time.sleep(SLEEP_SEC)
        return BeautifulSoup(res.text, "lxml")
    except Exception as e:
        print(f"  ⚠️ 取得失敗: {e}")
        return None


def get_target_dates():
    """対象開催日（今週末＋来週末）を計算"""
    today = datetime.now()
    dates = []

    for week_offset in [0, 1]:
        days_until_saturday = (5 - today.weekday()) % 7
        if days_until_saturday == 0 and today.weekday() != 5:
            days_until_saturday = 7
        saturday = today + timedelta(days=days_until_saturday + 7 * week_offset)
        sunday = saturday + timedelta(days=1)
        dates.append(saturday.strftime("%Y%m%d"))
        dates.append(sunday.strftime("%Y%m%d"))

    return list(set(dates))


def fetch_race_list(date_str):
    """指定日のレース一覧を取得"""
    url = f"https://race.netkeiba.com/top/race_list.html?kaisai_date={date_str}"
    soup = fetch(url)
    if not soup:
        return []

    races = []
    venue_blocks = soup.select(".RaceList_DataList")
    for vblock in venue_blocks:
        venue_h = vblock.select_one(".RaceList_DataHeader")
        if not venue_h:
            continue
        venue_name = venue_h.get_text(strip=True).split()[0] if venue_h else "?"

        for item in vblock.select(".RaceList_DataItem"):
            link = item.select_one("a")
            if not link:
                continue
            href = link.get("href", "")
            m = re.search(r"race_id=(\d+)", href)
            if not m:
                continue
            race_id = m.group(1)

            race_num = item.select_one(".RaceList_Itemnumber")
            race_name = item.select_one(".RaceList_ItemTitle")
            race_time = item.select_one(".RaceList_Itemtime")

            races.append({
                "race_id": race_id,
                "venue": venue_name,
                "race_num": race_num.get_text(strip=True) if race_num else "",
                "race_name": race_name.get_text(strip=True) if race_name else "",
                "start_time": race_time.get_text(strip=True) if race_time else "",
                "date": date_str,
            })

    print(f"  ✅ {date_str}: {len(races)}レース取得")
    return races


def fetch_race_horses(race_id):
    """指定レースの出走馬一覧を取得"""
    url = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"
    soup = fetch(url)
    if not soup:
        return []

    horses = []
    for row in soup.select("tr.HorseList"):
        try:
            waku_el = row.select_one("td.Waku span")
            num_el = row.select_one("td.Umaban")
            name_el = row.select_one("td.HorseInfo span.HorseName a")
            jockey_el = row.select_one("td.Jockey a")
            trainer_el = row.select_one("td.Trainer a")
            weight_el = row.select_one("td.Weight")
            odds_el = row.select_one("td.Popular span")
            pop_el = row.select_one("td.Popular_Ninki span")

            horse = {
                "waku": int(waku_el.get_text(strip=True)) if waku_el else 0,
                "num": int(num_el.get_text(strip=True)) if num_el else 0,
                "name": name_el.get_text(strip=True) if name_el else "",
                "jockey": jockey_el.get_text(strip=True) if jockey_el else "",
                "trainer": trainer_el.get_text(strip=True) if trainer_el else "",
                "weight": weight_el.get_text(strip=True) if weight_el else "",
                "odds": float(odds_el.get_text(strip=True)) if odds_el and odds_el.get_text(strip=True).replace('.', '').isdigit() else 0.0,
                "pop": int(pop_el.get_text(strip=True)) if pop_el and pop_el.get_text(strip=True).isdigit() else 0,
            }
            if horse["name"]:
                horses.append(horse)
        except Exception as e:
            print(f"    ⚠️ 1頭スキップ: {e}")
            continue

    print(f"    ✅ {len(horses)}頭取得")
    return horses


def calculate_ai_index(horse):
    """簡易AI指数を計算"""
    base = 70
    pop = horse.get("pop", 18)
    odds = horse.get("odds", 100)
    if pop > 0:
        base += max(0, 30 - (pop - 1) * 3)
    if 0 < odds < 5:
        base += 5
    elif 0 < odds < 10:
        base += 2
    return min(99, max(50, base))


def convert_to_site_format(race_meta, horses):
    """サイトで使うフォーマットに変換"""
    if not horses:
        return None

    styles = ["逃げ", "先行", "差し", "追込"]
    site_horses = []
    for i, h in enumerate(horses):
        ai_idx = calculate_ai_index(h)
        weight_match = re.search(r"\d+", h.get("weight", "0"))
        site_horses.append({
            "waku": h["waku"],
            "num": h["num"],
            "name": h["name"],
            "jockey": h["jockey"],
            "trainer": h.get("trainer", ""),
            "idx": ai_idx,
            "odds": h["odds"],
            "pop": h["pop"],
            "oi": min(5, max(1, ai_idx // 20)),
            "prev": 0,
            "rs": styles[i % 4],
            "weight": int(weight_match.group()) if weight_match else 470,
            "sim_win": max(0, (ai_idx - 65) * 2),
            "sim_rank": 0,
        })

    ranked = sorted(site_horses, key=lambda x: -(x["sim_win"] + x["idx"] * 0.2))
    for r, h in enumerate(ranked):
        h["sim_rank"] = r + 1

    return {
        "race_id": race_meta["race_id"],
        "venue": race_meta["venue"],
        "race_num": race_meta["race_num"],
        "race_name": race_meta["race_name"],
        "start_time": race_meta["start_time"],
        "date": race_meta["date"],
        "horses": site_horses,
        "updated_at": datetime.now().isoformat(),
    }


def main():
    print(f"\n{'=' * 60}")
    print(f"  ウマヨミ データ取得開始 - {datetime.now()}")
    print(f"{'=' * 60}\n")

    dates = get_target_dates()
    print(f"📅 対象日付: {dates}\n")

    all_races = {}
    for date_str in sorted(dates):
        print(f"\n📅 {date_str} のレース一覧取得...")
        races = fetch_race_list(date_str)

        target_races = [r for r in races if r["race_num"] in ["11", "10", "12"]]
        if not target_races:
            target_races = races[:3]

        print(f"  📝 取得対象: {len(target_races)}レース")

        for r in target_races:
            print(f"\n  🏇 {r['venue']} {r['race_num']} {r['race_name']}")
            horses = fetch_race_horses(r["race_id"])
            if horses:
                race_data = convert_to_site_format(r, horses)
                if race_data:
                    all_races[r["race_id"]] = race_data

    output_file = OUTPUT_DIR / "races.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": datetime.now().isoformat(),
            "race_count": len(all_races),
            "races": all_races,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n{'=' * 60}")
    print(f"  ✅ 完了: {len(all_races)}レースを {output_file} に保存")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ エラー発生: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
