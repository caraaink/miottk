"""
api/feed.py — Vercel Serverless Function (Python).

Endpoint:
    /api/feed?unique_id=<handle>&count=1&key=<API_KEY>

Balasannya berbentuk sama dengan yang sudah kita pakai di form target hotsuite:

    {
      "code": 0,
      "data": {
        "videos": [
          {"video_id": "...", "title": "...", "play": "...", "create_time": 123}
        ]
      }
    }

Konfigurasi target di hotsuite:
    api_url      : https://<proyek>.vercel.app/api/feed?unique_id={handle}&key=RAHASIA
    path_items   : data.videos
    path_id      : video_id
    path_media   : play
    path_caption : title
    path_time    : create_time

Variabel lingkungan di Vercel:
    API_KEY   wajib. Kalau kosong, endpoint terbuka untuk umum.
"""

import os
import re
import json
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import requests

EMBED = "https://www.tiktok.com/embed/@{handle}"
API_KEY = os.environ.get("API_KEY", "").strip()

UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

HEADERS = {
    "User-Agent": UA_MOBILE,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
    "Referer": "https://www.tiktok.com/",
}

KV_RE = re.compile(r'"(id|desc|playAddr|downloadAddr|cover|createTime|duration)"\s*:\s*'
                   r'("(?:[^"\\]|\\.)*"|\d+)')


def time_from_id(vid):
    try:
        ts = int(vid) >> 32
    except (TypeError, ValueError):
        return 0
    return ts if 1451606400 < ts < 4102444800 else 0


def bersih_handle(raw):
    h = (raw or "").strip()
    if "tiktok.com" in h:
        h = h.split("tiktok.com", 1)[1]
    h = h.split("?", 1)[0].split("#", 1)[0].strip("/")
    if "/" in h:
        h = h.split("/", 1)[0]
    return h.lstrip("@")


def id_milik(html, handle):
    pat = re.compile(r"@" + re.escape(handle) + r"/video/(\d{17,20})", re.I)
    return {v: time_from_id(v) for v in pat.findall(html) if time_from_id(v)}


def bongkar(html):
    catatan = {}
    kini = None
    for m in KV_RE.finditer(html):
        kunci, mentah = m.group(1), m.group(2)
        try:
            nilai = json.loads(mentah) if mentah.startswith('"') else int(mentah)
        except Exception:
            continue
        if kunci == "id" and isinstance(nilai, str) and nilai.isdigit() \
                and 17 <= len(nilai) <= 20:
            catatan.setdefault(nilai, {"id": nilai})
            kini = catatan[nilai]
            continue
        if kini is not None and kunci != "id":
            kini.setdefault(kunci, nilai)
    return catatan


def ambil_feed(handle, count):
    r = requests.get(EMBED.format(handle=handle), headers=HEADERS, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError("halaman embed HTTP %s" % r.status_code)

    html = r.text
    if "_wafchallengeid" in html:
        raise RuntimeError("kena tantangan WAF")

    punya = id_milik(html, handle)
    if not punya:
        raise RuntimeError("tidak ada tautan video milik handle ini")

    semua = bongkar(html)
    keluar = []
    for vid, ts in sorted(punya.items(), key=lambda kv: kv[1], reverse=True)[:count]:
        c = semua.get(vid, {})
        keluar.append({
            "video_id": vid,
            "title": c.get("desc") or "",
            "play": c.get("playAddr") or c.get("downloadAddr") or "",
            "cover": c.get("cover") or "",
            "duration": c.get("duration") or 0,
            "create_time": c.get("createTime") or ts,
            "permalink": "https://www.tiktok.com/@%s/video/%s" % (handle, vid),
            "author": handle,
        })
    return keluar


class handler(BaseHTTPRequestHandler):

    def _kirim(self, kode, badan):
        isi = json.dumps(badan, ensure_ascii=False).encode("utf-8")
        self.send_response(kode)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(isi)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(isi)

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)

        def satu(nama, bawaan=""):
            v = q.get(nama)
            return v[0] if v else bawaan

        if API_KEY:
            dikirim = self.headers.get("x-api-key") or satu("key")
            if (dikirim or "").strip() != API_KEY:
                return self._kirim(401, {"code": -1, "msg": "API key tidak valid.",
                                         "data": None})

        handle = bersih_handle(satu("unique_id") or satu("handle"))
        if not handle:
            return self._kirim(400, {"code": -1, "msg": "unique_id wajib diisi.",
                                     "data": None})

        try:
            count = max(1, min(int(satu("count", "1")), 30))
        except ValueError:
            count = 1

        t0 = time.time()
        try:
            videos = ambil_feed(handle, count)
        except Exception as exc:
            return self._kirim(502, {"code": -1, "msg": str(exc), "data": None})

        if not videos:
            return self._kirim(404, {"code": -1, "msg": "Tidak ada postingan terbaca.",
                                     "data": None})

        return self._kirim(200, {
            "code": 0,
            "msg": "success",
            "ms": int((time.time() - t0) * 1000),
            "data": {"videos": videos, "hasMore": len(videos) >= count},
        })
