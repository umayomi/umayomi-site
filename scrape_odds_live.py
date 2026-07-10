#!/usr/bin/env python3
"""
オッズ常駐監視 v2（Discord通知メイン）
- 発走まで動的間隔でオッズ記録
  90分超:60分 / 30-90分:15分 / 15-30分:3分 / 15分以内:1分
- 🔥急落を即時Discord通知
- 📋発走5分前に急落/高騰サマリーをDiscord通知
- odds_timeline.json は終了時に1回だけコミット（Vercelデプロイ節約）

必要環境変数: DISCORD_WEBHOOK_URL
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

try:
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_WDM = True
except ImportError:
    HAS_WDM = False

JST = timezone(timedelta(hours=9))
OUTPUT_DIR = Path("data")
TIMELINE_FILE = OUTPUT_DIR / "odds_timeline.json"
END_OF_DAY = "16:40"
MAX_RUN_SEC = 5 * 3600 + 40 * 60

DETECT_WINDOW_MIN = 25    # 急落判定の比較窓
DROP_THRESHOLD = 0.15     # 単勝15%以上の下落で即時通知
MIN_POPULARITY = 5        # 5番人気以下のみ即時通知対象
SUMMARY_BEFORE_MIN = 5    # 発走何分前にサマリー送信するか


def now_jst():
    return datetime.now(JST)


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


def load_today_races():
    races_file = OUTPUT_DIR / "races.json"
    if not races_file.exists():
        print("races.json なし")
        return []
    with open(races_file, encoding="utf-8") as f:
        data = json.load(f)
    today = now_jst().strftime("%Y%m%d")
    result = []
    for race_id, race in data.get("races", {}).items():
        if race.get("date") != today:
            continue
        info = race.get("course_info", "")
        m = re.search(r"(\d{1,2}):(\d{2})発走", info)
        if m:
            post = now_jst().replace(hour=int(m.group(1)), minute=int(m.group(2)),
                                     second=0, microsecond=0)
        else:
            rnum = int(race_id[10:12]) if race_id[10:12].isdigit() else 6
            base = now_jst().replace(hour=9, minute=50, second=0, microsecond=0)
            post = base + timedelta(minutes=35 * (rnum - 1))
        venue_map = {"01":"札幌","02":"函館","03":"福島","04":"新潟","05":"東京",
                     "06":"中山","07":"中京","08":"京都","09":"阪神","10":"小倉"}
        venue = venue_map.get(race_id[4:6], "")
        rnum = race_id[10:12].lstrip("0")
        label = f"{venue}{rnum}R {race.get('race_name', '')}"
        result.append({"race_id": race_id, "label": label, "post_time": post})
    result.sort(key=lambda r: r["post_time"])
    return result


def fetch_odds(driver, race_id):
    url = f'https://race.netkeiba.com/odds/index.html?race_id={race_id}&type=b1'
    try:
        driver.get(url)
        time.sleep(4)
        soup = BeautifulSoup(driver.page_source, "html.parser")
        table = soup.select_one("table.RaceOdds_HorseList_Table, table.Odds_Table")
        if not table:
            return {}
        odds = {}
        for row in table.find_all("tr")[1:]:
            cols = row.find_all("td")
            if len(cols) < 4:
                continue
            umaban = ""
            for col in cols[:3]:
                t = col.get_text(strip=True)
                if t.isdigit():
                    umaban = t
                    break
            if not umaban:
                continue
            vals = []
            for col in cols:
                t = col.get_text(strip=True)
                if re.match(r"^\d+\.\d+$", t):
                    vals.append(float(t))
                else:
                    mm = re.match(r"^(\d+\.\d+)\s*-\s*(\d+\.\d+)$", t)
                    if mm:
                        vals.append(float(mm.group(1)))
            if vals:
                odds[umaban] = {"tan": vals[0], "fuku": vals[1] if len(vals) > 1 else None}
        return odds
    except Exception as e:
        print(f"    odds error {race_id}: {e}")
        return {}


def interval_for(minutes_to_post):
    if minutes_to_post > 90:
        return 3600
    if minutes_to_post > 30:
        return 900
    if minutes_to_post > 15:
        return 180
    return 60


def popularity_rank(odds_map, umaban):
    pairs = sorted([(u, d["tan"]) for u, d in odds_map.items()], key=lambda x: x[1])
    for rank, (u, _) in enumerate(pairs, 1):
        if u == umaban:
            return rank
    return 99


def notify_discord(msg):
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        print(f"[通知スキップ]\n{msg}")
        return
    try:
        requests.post(url, json={"content": msg}, timeout=10)
        print(f"[Discord通知済]")
    except Exception as e:
        print(f"Discord送信失敗: {e}")


def snapshot_near(entries, target_minutes_ago, latest_t):
    """latest からおよそ target_minutes_ago 前に最も近いスナップショットを返す"""
    best, best_diff = None, None
    for e in entries:
        et = datetime.fromisoformat(e["t"])
        diff = abs((latest_t - et).total_seconds() - target_minutes_ago * 60)
        if best is None or diff < best_diff:
            best, best_diff = e, diff
    return best


def detect_surge(race, timeline, notified):
    entries = timeline.get(race["race_id"], {}).get("snapshots", [])
    if len(entries) < 2:
        return
    latest = entries[-1]
    latest_t = datetime.fromisoformat(latest["t"])
    base = snapshot_near(entries[:-1], DETECT_WINDOW_MIN, latest_t)
    if not base:
        return
    for umaban, cur in latest["odds"].items():
        key = f"{race['race_id']}-{umaban}"
        if key in notified:
            continue
        prev = base["odds"].get(umaban)
        if not prev or not prev.get("tan") or not cur.get("tan"):
            continue
        drop = (prev["tan"] - cur["tan"]) / prev["tan"]
        if drop < DROP_THRESHOLD:
            continue
        if popularity_rank(latest["odds"], umaban) < MIN_POPULARITY:
            continue
        if prev.get("fuku") and cur.get("fuku") and not (cur["fuku"] < prev["fuku"]):
            continue
        mins_left = int((race["post_time"] - now_jst()).total_seconds() // 60)
        rank = popularity_rank(latest["odds"], umaban)
        notify_discord(
            f"🔥 **急落検知** {race['label']}（発走まで{mins_left}分）\n"
            f"馬番**{umaban}**（現{rank}番人気）\n"
            f"単勝 {prev['tan']} → **{cur['tan']}**（-{drop*100:.0f}%）複勝も同時下落\n"
            f"パドック筋・関係者買いの可能性"
        )
        notified.add(key)


def send_summary(race, timeline):
    entries = timeline.get(race["race_id"], {}).get("snapshots", [])
    if len(entries) < 2:
        return
    latest = entries[-1]
    latest_t = datetime.fromisoformat(latest["t"])
    base = snapshot_near(entries[:-1], 30, latest_t)
    if not base:
        return
    moves = []
    for umaban, cur in latest["odds"].items():
        prev = base["odds"].get(umaban)
        if not prev or not prev.get("tan") or not cur.get("tan"):
            continue
        chg = (cur["tan"] - prev["tan"]) / prev["tan"]
        r_prev = popularity_rank(base["odds"], umaban)
        r_now = popularity_rank(latest["odds"], umaban)
        moves.append((umaban, prev["tan"], cur["tan"], chg, r_prev, r_now))
    if not moves:
        return
    drops = sorted([m for m in moves if m[3] < 0], key=lambda x: x[3])[:3]
    rises = sorted([m for m in moves if m[3] > 0], key=lambda x: -x[3])[:3]
    lines = [f"📋 **締切直前サマリー** {race['label']}（30分前比）"]
    if drops:
        lines.append("▼ 売れた馬（オッズ下落）")
        for u, p, c, chg, rp, rn in drops:
            arrow = f"{rp}→{rn}人気" if rp != rn else f"{rn}人気"
            lines.append(f"  {u}番: {p} → **{c}** ({chg*100:+.0f}%) {arrow}")
    if rises:
        lines.append("▲ 見放された馬（オッズ上昇）")
        for u, p, c, chg, rp, rn in rises:
            arrow = f"{rp}→{rn}人気" if rp != rn else f"{rn}人気"
            lines.append(f"  {u}番: {p} → {c} ({chg*100:+.0f}%) {arrow}")
    notify_discord("\n".join(lines))


def git_commit():
    try:
        subprocess.run(["git", "add", str(TIMELINE_FILE)], check=True)
        r = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if r.returncode == 0:
            return
        subprocess.run(["git", "commit", "-m", "odds timeline (daily)"], check=True)
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("✅ commit & push")
    except Exception as e:
        print(f"git error: {e}")


def main():
    start = time.time()
    races = load_today_races()
    print(f"本日のレース: {len(races)}件")
    if not races:
        return
    end_h, end_m = map(int, END_OF_DAY.split(":"))
    end_time = now_jst().replace(hour=end_h, minute=end_m, second=0)

    timeline = {}
    if TIMELINE_FILE.exists():
        try:
            timeline = json.loads(TIMELINE_FILE.read_text(encoding="utf-8"))
        except Exception:
            timeline = {}

    driver = create_driver()
    notified = set()
    summarized = set()
    next_fetch = {r["race_id"]: now_jst() for r in races}

    try:
        while now_jst() < end_time and (time.time() - start) < MAX_RUN_SEC:
            active = [r for r in races if now_jst() < r["post_time"]]
            if not active:
                print("全レース発走済み。終了")
                break
            for race in active:
                rid = race["race_id"]
                mins = (race["post_time"] - now_jst()).total_seconds() / 60
                # サマリー送信タイミング（発走5分前を過ぎたら1回）
                if rid not in summarized and mins <= SUMMARY_BEFORE_MIN + 1:
                    odds = fetch_odds(driver, rid)
                    if odds:
                        timeline.setdefault(rid, {"label": race["label"],
                                                  "post_time": race["post_time"].isoformat(),
                                                  "snapshots": []})["snapshots"].append(
                            {"t": now_jst().isoformat(), "odds": odds})
                    send_summary(race, timeline)
                    summarized.add(rid)
                    next_fetch[rid] = now_jst() + timedelta(seconds=60)
                    continue
                if now_jst() < next_fetch[rid]:
                    continue
                odds = fetch_odds(driver, rid)
                if odds:
                    rec = timeline.setdefault(rid, {"label": race["label"],
                                                    "post_time": race["post_time"].isoformat(),
                                                    "snapshots": []})
                    rec["snapshots"].append({"t": now_jst().isoformat(), "odds": odds})
                    detect_surge(race, timeline, notified)
                next_fetch[rid] = now_jst() + timedelta(seconds=interval_for(mins))
                print(f"  {race['label']} 残{mins:.0f}分 次回{interval_for(mins)}秒後")
            time.sleep(15)
    finally:
        driver.quit()
        TIMELINE_FILE.write_text(
            json.dumps(timeline, ensure_ascii=False, indent=1), encoding="utf-8")
        git_commit()
        print("終了（コミットは1日1回）")


main()
