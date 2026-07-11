#!/usr/bin/env python3
"""
オッズ常駐監視 v3（Discord通知 + Gistライブ配信 + 30分毎コミット）
- 発走まで動的間隔でオッズ記録
  90分超:60分 / 30-90分:15分 / 15-30分:3分 / 15分以内:1分
- 🔥急落を即時Discord通知 / 📋発走5分前サマリー
- 毎サイクル(約60秒毎)Gistへ最新データPATCH → サイトがポーリングしてライブ表示
- 30分毎に odds_timeline.json をリポジトリへコミット（永続記録）

必要環境変数:
  DISCORD_WEBHOOK_URL … Discord通知先（未設定なら通知スキップ）
  GIST_TOKEN          … gist権限のPAT（未設定ならライブ配信スキップ）
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
GIST_URL_FILE = OUTPUT_DIR / "gist_url.txt"
END_OF_DAY = "16:40"
MAX_RUN_SEC = 5 * 3600 + 40 * 60
COMMIT_INTERVAL_SEC = 1800   # 30分毎にリポジトリコミット
LIVE_PUSH_MIN_SEC = 55       # Gist更新の最短間隔
LIVE_SNAPSHOT_KEEP = 25      # ライブ配信に含める直近スナップショット数

DETECT_WINDOW_MIN = 25
DROP_THRESHOLD = 0.15
MIN_POPULARITY = 5
SUMMARY_BEFORE_MIN = 5

GIST_FILENAME = "umayomi_live.json"


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
    """単勝・複勝オッズ取得（馬番はclass/ヘッダーで厳密特定。枠番誤検出対策）"""
    url = f'https://race.netkeiba.com/odds/index.html?race_id={race_id}&type=b1'
    try:
        driver.get(url)
        time.sleep(4)
        soup = BeautifulSoup(driver.page_source, "html.parser")
        table = soup.select_one("table.RaceOdds_HorseList_Table, table.Odds_Table")
        if not table:
            return {}
        rows = table.find_all("tr")
        if len(rows) < 2:
            return {}

        # ヘッダーから列位置を特定（「枠」「枠番」は不一致、「馬番」のみ一致）
        headers = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        uma_idx = tan_idx = fuku_idx = -1
        for i, h in enumerate(headers):
            if "馬番" in h and uma_idx == -1:
                uma_idx = i
            elif ("単勝" in h or "オッズ" in h) and tan_idx == -1:
                tan_idx = i
            elif "複勝" in h and fuku_idx == -1:
                fuku_idx = i

        def dec(s):
            return float(s) if re.match(r"^\d+\.\d+$", s) else None

        odds = {}
        data_rows = 0
        for row in rows[1:]:
            cols = row.find_all("td")
            if len(cols) < 3:
                continue
            data_rows += 1
            umaban = ""
            # 戦略1: class に Umaban を含むセル（netkeiba標準マークアップ）
            uc = row.select_one('td[class*="Umaban"], td[class*="umaban"]')
            if uc:
                t = uc.get_text(strip=True)
                if t.isdigit():
                    umaban = t
            # 戦略2: ヘッダー「馬番」列
            if not umaban and 0 <= uma_idx < len(cols):
                t = cols[uma_idx].get_text(strip=True)
                if t.isdigit():
                    umaban = t
            if not umaban:
                continue
            tan = None
            fuku = None
            if 0 <= tan_idx < len(cols):
                tan = dec(cols[tan_idx].get_text(strip=True))
            if 0 <= fuku_idx < len(cols):
                m = re.match(r"^(\d+\.\d+)", cols[fuku_idx].get_text(strip=True))
                if m:
                    fuku = float(m.group(1))
            if tan is None:
                for col in cols:
                    v = dec(col.get_text(strip=True))
                    if v is not None:
                        tan = v
                        break
            if tan is not None:
                odds[umaban] = {"tan": tan, "fuku": fuku}

        # 自己検証: 頭数に対して馬番が8以下しか無い＝枠番疑い → 破棄して警告
        if odds and data_rows >= 9:
            if max(int(u) for u in odds.keys()) <= 8:
                print(f"    [WARN] 枠番誤検出の疑い（{data_rows}行中 max馬番{max(int(u) for u in odds.keys())}）→ このスナップショット破棄")
                return {}
        if odds and len(odds) < data_rows * 0.7:
            print(f"    [WARN] 取得数不足 {len(odds)}/{data_rows}行")
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
        print("[Discord通知済]")
    except Exception as e:
        print(f"Discord送信失敗: {e}")


def snapshot_near(entries, target_minutes_ago, latest_t):
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


# ============ Gist ライブ配信 ============
class GistLive:
    def __init__(self):
        self.token = os.environ.get("GIST_TOKEN", "").strip()
        self.gist_id = None
        self.raw_url = None
        self.last_push = 0
        self.pending_commit_urlfile = False
        if not self.token:
            print("GIST_TOKEN 未設定 → ライブ配信スキップ")
            return
        self._ensure_gist()

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json"}

    def _ensure_gist(self):
        if GIST_URL_FILE.exists():
            try:
                gid, raw = GIST_URL_FILE.read_text().strip().split("|", 1)
                self.gist_id, self.raw_url = gid, raw
                print(f"Gist再利用: {gid}")
                return
            except Exception:
                pass
        try:
            r = requests.post("https://api.github.com/gists", headers=self._headers(),
                              json={"description": "umayomi live odds",
                                    "public": False,
                                    "files": {GIST_FILENAME: {"content": "{}"}}},
                              timeout=15)
            r.raise_for_status()
            j = r.json()
            self.gist_id = j["id"]
            owner = j["owner"]["login"]
            self.raw_url = f"https://gist.githubusercontent.com/{owner}/{self.gist_id}/raw/{GIST_FILENAME}"
            GIST_URL_FILE.write_text(f"{self.gist_id}|{self.raw_url}")
            self.pending_commit_urlfile = True
            print(f"Gist新規作成: {self.gist_id}")
        except Exception as e:
            print(f"Gist作成失敗（ライブ配信なしで続行）: {e}")
            self.token = ""

    def push(self, timeline, races):
        if not self.token or not self.gist_id:
            return
        if time.time() - self.last_push < LIVE_PUSH_MIN_SEC:
            return
        live = {}
        for race in races:
            rid = race["race_id"]
            rec = timeline.get(rid)
            if not rec:
                continue
            mins = (race["post_time"] - now_jst()).total_seconds() / 60
            if mins < -3 or mins > 45:   # 発走45分前〜発走後3分だけ配信
                continue
            live[rid] = {
                "label": rec["label"],
                "post_time": rec["post_time"],
                "snapshots": rec["snapshots"][-LIVE_SNAPSHOT_KEEP:],
            }
        payload = {"updated": now_jst().isoformat(), "races": live}
        try:
            requests.patch(f"https://api.github.com/gists/{self.gist_id}",
                           headers=self._headers(),
                           json={"files": {GIST_FILENAME: {
                               "content": json.dumps(payload, ensure_ascii=False)}}},
                           timeout=15)
            self.last_push = time.time()
            print(f"  [live] Gist更新 {len(live)}レース")
        except Exception as e:
            print(f"  [live] Gist更新失敗: {e}")


def git_commit(extra_paths=None):
    try:
        paths = [str(TIMELINE_FILE)] + (extra_paths or [])
        subprocess.run(["git", "add"] + paths, check=True)
        r = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if r.returncode == 0:
            return
        subprocess.run(["git", "commit", "-m", "odds timeline update"], check=True)
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
    # 当日以外の記録を削除（盤面の鮮度維持・過去の誤データ排除）
    today_prefix = now_jst().strftime("%Y-%m-%d")
    timeline = {rid: rec for rid, rec in timeline.items()
                if str(rec.get("post_time", "")).startswith(today_prefix)}

    gist = GistLive()
    driver = create_driver()
    notified = set()
    summarized = set()
    next_fetch = {r["race_id"]: now_jst() for r in races}
    last_commit = time.time()

    # gist_url.txt を初回だけコミット（サイトがURLを知るため）
    if gist.pending_commit_urlfile:
        git_commit(extra_paths=[str(GIST_URL_FILE)])

    try:
        while now_jst() < end_time and (time.time() - start) < MAX_RUN_SEC:
            active = [r for r in races if now_jst() < r["post_time"]]
            if not active:
                print("全レース発走済み。終了")
                break
            fetched_any = False
            for race in active:
                rid = race["race_id"]
                mins = (race["post_time"] - now_jst()).total_seconds() / 60
                if rid not in summarized and mins <= SUMMARY_BEFORE_MIN + 1:
                    odds = fetch_odds(driver, rid)
                    if odds:
                        timeline.setdefault(rid, {"label": race["label"],
                                                  "post_time": race["post_time"].isoformat(),
                                                  "snapshots": []})["snapshots"].append(
                            {"t": now_jst().isoformat(), "odds": odds})
                        fetched_any = True
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
                    fetched_any = True
                next_fetch[rid] = now_jst() + timedelta(seconds=interval_for(mins))
                print(f"  {race['label']} 残{mins:.0f}分 次回{interval_for(mins)}秒後")
            if fetched_any:
                gist.push(timeline, races)
            if time.time() - last_commit > COMMIT_INTERVAL_SEC:
                TIMELINE_FILE.write_text(
                    json.dumps(timeline, ensure_ascii=False, indent=1), encoding="utf-8")
                git_commit()
                last_commit = time.time()
            time.sleep(15)
    finally:
        driver.quit()
        TIMELINE_FILE.write_text(
            json.dumps(timeline, ensure_ascii=False, indent=1), encoding="utf-8")
        git_commit()
        print("終了")


main()
