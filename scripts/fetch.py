"""ジャンルごとにふるさと納税の返礼品を取得し data/raw/<genre>.json に保存する。

APIは1つの検索条件で最大100ページ(3000件)までしか取れないので、寄付額の範囲で検索を区切り、
3000件を超える範囲はさらに半分に分けて取る。こうすると重複なく全件を取れる。
使い方: python scripts/fetch.py [ジャンル名 ...]   (例: python scripts/fetch.py rice meat。省略すると全ジャンル)
結果は logs/fetch-<日付>.txt にも書く(件数・エラー・所要時間)。
"""
import http.client
import json
import re
import sys
import time
import urllib.error
from datetime import datetime

import genres
from common import JST, ROOT, load_env, search

sys.stdout.reconfigure(encoding="utf-8")

GENRES = {g["key"]: g["rakuten"] for g in genres.ACTIVE}  # ジャンル名 -> 楽天のジャンル番号(genres.py)
MAX_ITEMS = 3000  # 1つの検索条件で取れる上限(30件 × 100ページ)
MAX_PRICE = 10_000_000
INTERVAL = 1.05  # APIの制限は1秒に1回。呼び出しの開始どうしをこの秒数だけ空ける(11/1 に 1.1 から。429 が出たら戻す)
# 保存する項目(サイトの生成と履歴で使うものだけ。全項目だと1件4KBほどになる)
# affiliateUrl は取らない: アフィリエイトIDを付けて呼ぶと itemUrl も同じアフィリエイトのURLになる(全件同じだった。raw が約25%小さくなる)
KEEP = ["itemCode", "itemName", "itemPrice", "itemPriceMin1", "itemPriceMax1", "itemUrl",
        "shopName", "reviewAverage", "reviewCount", "availability"]
# APIに返してもらう項目(応答が約3分の1になり速くなる)。説明文 itemCaption は「内容量」の部分だけ cap として残す
ELEMENTS = ",".join(KEEP + ["mediumImageUrls", "itemCaption", "count", "pageCount"])
# 説明文の「内容量」の見出し(「内容量・サイズ等」「内容量】」「容量/原産地」など)と、次の項目の見出し
CAP_HEAD = [re.compile(r"内容量(?:・サイズ等?|について)?\s*[】\]:：]?"), re.compile(r"(?:内容|容量)(?:・サイズ等?|について|/原産地)?\s*[】\]:：]?")]
CAP_END = re.compile(r"【|■|原材料|賞味期限|消費期限|配送|発送|お届け時期|アレルギー|原産地|産地|保存|製造|提供|事業者|説明|地場産品|注意")
WARN_RATIO = 0.8  # 前回よりこの割合を下回ったら警告する
KEEP_OLD_RATIO = 0.5  # これを下回ったら取得の失敗とみなし、前回のデータを残す

# OSError は本文を読んでいる途中の SSL の切断など(URLError に包まれずに来る。2時間の取得を1回の切断で止めない)
RETRYABLE = (urllib.error.URLError, http.client.HTTPException, OSError, json.JSONDecodeError)
stats = {"calls": 0, "errors": 0, "failed": []}  # failed: 取れなかったページがある寄付額の範囲 (lo, hi)
_last_call = 0.0


def call(params):
    """APIを呼ぶ。混雑(429)・5xx・通信の切断・空の応答は楽天側の一時的な不調なので、待って再試行する。"""
    global _last_call
    for attempt in range(4):
        time.sleep(max(0.0, _last_call + INTERVAL - time.monotonic()))
        _last_call = time.monotonic()
        stats["calls"] += 1
        try:
            return search({**params, "elements": ELEMENTS})
        except RETRYABLE as e:
            code = getattr(e, "code", type(e).__name__)
            retryable = not isinstance(e, urllib.error.HTTPError) or code == 429 or code >= 500
            if retryable and attempt < 3:
                time.sleep(10 * (attempt + 1))
                continue
            stats["errors"] += 1
            print(f"  {params}: エラー {code}(飛ばします)")
            return None


def fetch_range(gid, lo, hi, items):
    # 同じ寄付額が多い範囲は、価格順だとページごとに並びが入れ替わり、重複と取りこぼしが出る(11/5 肉 30,000円で 1,484件中 376件)。
    # 範囲は minPrice/maxPrice で区切り済みなので、範囲内は並びが安定する更新日時順で取る
    base = {"genreId": gid, "keyword": "ふるさと納税", "minPrice": lo, "maxPrice": hi, "sort": "+updateTimestamp"}
    data = call({**base, "page": 1})
    if not data:
        stats["failed"].append((lo, hi))
        return
    count = data.get("count", 0)
    if count > MAX_ITEMS and hi > lo:
        mid = (lo + hi) // 2
        fetch_range(gid, lo, mid, items)
        fetch_range(gid, mid + 1, hi, items)
        return
    if count > MAX_ITEMS:
        print(f"  ::warning::genre {gid} {lo:,}円ちょうどが{count}件あり、{MAX_ITEMS}件までしか取れません")
    pages = [data] + [call({**base, "page": p}) for p in range(2, data.get("pageCount", 1) + 1)]
    if not all(pages):
        stats["failed"].append((lo, hi))
    for d in pages:
        for e in (d or {}).get("Items", []):
            it = e.get("Item", e)
            items[it["itemCode"]] = it
    print(f"  genre {gid} {lo:,}〜{hi:,}円: {count}件(累計 {len(items)}件)")


def content_of(caption):
    """説明文から「内容量」の部分(最大150字)。見出しが無ければ空。"""
    for head in CAP_HEAD:
        m = head.search(caption or "")
        if m:
            text = re.sub(r"^\s*(?:内容量\s*[:：]?)?", "", caption[m.end():m.end() + 150])
            end = CAP_END.search(text)
            return (text[:end.start()] if end else text).strip()
    return ""


def slim(it):
    out = {k: it.get(k) for k in KEEP}
    imgs = [(u or {}).get("imageUrl", "") for u in (it.get("mediumImageUrls") or [])[:3]]
    out["img"] = imgs[0] if imgs else ""
    out["imgs"] = [u for u in imgs[1:] if u]  # 2枚目以降(SNS のカルーセルで1位の写真を2枚見せる用)
    out["cap"] = content_of(it.get("itemCaption"))
    return out


def main():
    load_env()
    names = sys.argv[1:] or list(GENRES)
    unknown = [n for n in names if n not in GENRES]
    if unknown:
        sys.exit(f"不明なジャンル: {' '.join(unknown)}(使えるのは {' '.join(GENRES)})")
    out_dir = ROOT / "data" / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(JST)
    log = [f"# 取得 {now:%Y-%m-%d %H:%M} (JST)"]
    for name in names:
        print(f"[{name}]")
        start = time.monotonic()
        stats.update(calls=0, errors=0, failed=[])
        items = {}
        for gid in GENRES[name]:
            fetch_range(gid, 1, MAX_PRICE, items)
        rows = [slim(it) for it in items.values() if "【ふるさと納税】" in it["itemName"]]
        path = out_dir / f"{name}.json"
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        prev = len(old) or None
        # 取れなかったページがある寄付額の範囲は、前回のデータで補う(その回だけランキング・履歴から消えないように)
        got = {r["itemCode"] for r in rows}
        filled = [r for r in old if r["itemCode"] not in got and any(lo <= r["itemPrice"] <= hi for lo, hi in stats["failed"])]
        rows += filled
        line = (f"{name}: {len(rows):,}件" + (f"(前回 {prev:,}件)" if prev else "")
                + f" / API {stats['calls']:,}回 / エラー {stats['errors']}回" + (f"(前回のデータで{len(filled)}件補った)" if filled else "")
                + f" / {(time.monotonic() - start) / 60:.0f}分")
        if prev and len(rows) < prev * KEEP_OLD_RATIO:
            line += " … 前回の半分未満のため取得失敗とみなし、前回のデータを残しました"
        else:
            if prev and len(rows) < prev * WARN_RATIO:
                line += " … 警告: 前回より大きく減っています"
            path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        print(f"[{name}] {line}")
        log.append(line)
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    with (log_dir / f"fetch-{now:%Y-%m-%d}.txt").open("a", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n\n")


if __name__ == "__main__":
    main()
