"""全スクリプト共通: パス・タイムゾーン・.env の読み込み・楽天APIの呼び出し。"""
import json
import os
import urllib.parse
import urllib.request
from datetime import timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
ENDPOINT = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701"


def load_env():
    env_path = ROOT / ".env"
    if not env_path.exists():  # GitHub Actions では Secrets が環境変数で渡される
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def search(params):
    """楽天市場の商品検索APIを1ページ分呼ぶ。"""
    site = os.environ.get("RAKUTEN_ALLOWED_SITE", "https://example.com/")
    query = {
        "applicationId": os.environ["RAKUTEN_APP_ID"],
        "affiliateId": os.environ.get("RAKUTEN_AFFILIATE_ID", ""),
        "hits": 30,
        "format": "json",
        **params,
    }
    req = urllib.request.Request(
        ENDPOINT + "?" + urllib.parse.urlencode(query),
        headers={"accessKey": os.environ["RAKUTEN_ACCESS_KEY"], "Referer": site, "Origin": site.rstrip("/")},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.load(res)
