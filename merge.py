#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TVBox 多配置云端融合器：拉取源清单 -> 配置探活(含解密) -> 融合去重 -> HTTP 级站源验证 -> 发布 merged.json
 与 App 内 ConfigMerger 逻辑对齐：补 site 级 jar / "./" 相对路径转绝对 / 双指纹去重 / key 冲突加序号
 云端验证层：jar 或 ext(http) 不可达的站源剔除（jar 挂则其下所有 spider 源必死）
"""
import base64
import concurrent.futures as cf
import datetime
import json
import os
import re
import sys
import urllib.parse

import requests
from Crypto.Cipher import AES

UA = "okhttp/3.15"
TIMEOUT = 12
FETCH_POOL = 8
MAX_SITES = 800
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.environ.get("MERGE_OUT_DIR", HERE)
OUT_JSON = os.path.join(OUT_DIR, "merged.json")
OUT_REPORT = os.path.join(OUT_DIR, "report.md")

BASE_RE = re.compile(r"[A-Za-z0]{8}\*\*")


def http_get(url, timeout=TIMEOUT):
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": UA},
                         allow_redirects=True, verify=False)
        return r
    except Exception:
        return None


def http_ok(url):
    """可达性检查（GET range 轻量探测）"""
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": UA, "Range": "bytes=0-63"},
                         allow_redirects=True, verify=False)
        return r.status_code in (200, 206, 302, 304)
    except Exception:
        return False


def right_pad(s, ch, length):
    s = s.strip()
    if len(s) > length:
        return s[:length]
    return s + ch * (length - len(s))


def unhex(src):
    return bytes.fromhex(src)


def find_result(body):
    """复刻 ApiConfig.FindResult：明文直返 / XX**+Base64 / 2423 AES-CBC-PKCS7"""
    content = body.strip()
    try:
        json.loads(content)
        return content
    except Exception:
        pass
    try:
        m = BASE_RE.search(content)
        if m:
            content = content[content.index(m.group()) + 10:]
            content = base64.b64decode(content).decode("utf-8", "ignore")
            try:
                json.loads(content)
                return content
            except Exception:
                pass
        if content.startswith("2423"):
            data_hex = content[content.index("2324") + 4: len(content) - 26]
            inner = unhex(content).decode("utf-8", "ignore").lower()
            key = right_pad(inner[inner.index("$#") + 2: inner.index("#$")], "0", 16)
            iv = right_pad(inner[-13:], "0", 16)
            cipher = AES.new(key.encode(), AES.MODE_CBC, iv.encode())
            plain = cipher.decrypt(bytes.fromhex(data_hex))
            pad = plain[-1]
            if 0 < pad <= 16:
                plain = plain[:-pad]
            return plain.decode("utf-8", "ignore")
    except Exception:
        pass
    return None


def absolutize(base_url, ref):
    if not ref:
        return ref or ""
    if isinstance(ref, (dict, list)):
        return ref
    ref = str(ref).strip()
    if ref.startswith("./"):
        return urllib.parse.urljoin(base_url, ref)
    return ref


class Merger:
    def __init__(self):
        self.sites = []
        self.used_keys = set()
        self.seen_name_api = set()
        self.seen_api_ext = set()
        self.parses = []
        self.seen_parse = set()
        self.lives = None
        self.wallpaper = ""
        self.stats = {"configs": 0, "dup": 0, "dropped_dead": 0, "final": 0}

    def merge_one(self, root, config_url):
        self.stats["configs"] += 1
        spider = root.get("spider") or ""
        global_spider = ""
        if isinstance(spider, str) and spider.strip():
            parts = spider.strip().split(";md5;")
            jar_url = absolutize(config_url, parts[0])
            global_spider = jar_url + (";md5;" + parts[1].strip() if len(parts) > 1 else "")
        for site in root.get("sites", []):
            if len(self.sites) >= MAX_SITES:
                return
            try:
                key = str(site.get("key", "")).strip()
                api = site.get("api", "")
                if not key or not api:
                    continue
                if str(site.get("hide", "")) == "1":
                    continue
                name = str(site.get("name") or key).strip() or key
                api = absolutize(config_url, api)
                ext = site.get("ext", "")
                if isinstance(ext, (dict, list)):
                    ext_s = json.dumps(ext, ensure_ascii=False)
                else:
                    ext_s = absolutize(config_url, str(ext or "").strip())
                jar = absolutize(config_url, str(site.get("jar") or "").strip())

                fp1 = name + "|" + api
                if fp1 in self.seen_name_api:
                    self.stats["dup"] += 1
                    continue
                fp2 = api + "|" + ext_s[:256]
                if ext_s and fp2 in self.seen_api_ext:
                    self.stats["dup"] += 1
                    continue
                self.seen_name_api.add(fp1)
                if ext_s:
                    self.seen_api_ext.add(fp2)

                fkey, n = key, 2
                while fkey in self.used_keys:
                    fkey = f"{key}_{n}"
                    n += 1
                self.used_keys.add(fkey)

                ns = {"key": fkey, "name": name,
                      "type": site.get("type", 3), "api": api,
                      "searchable": site.get("searchable", 1),
                      "quickSearch": site.get("quickSearch", 1),
                      "filterable": site.get("filterable", 0)}
                if site.get("playerUrl"):
                    ns["playerUrl"] = site["playerUrl"]
                if site.get("style"):
                    ns["style"] = site["style"]
                if site.get("categories"):
                    ns["categories"] = site["categories"]
                if ext_s:
                    ns["ext"] = ext_s
                if jar:
                    ns["jar"] = jar
                elif global_spider:
                    ns["jar"] = global_spider
                self.sites.append(ns)
            except Exception:
                continue
        if self.lives is None and root.get("lives"):
            self.lives = root["lives"]
        for p in root.get("parses", []):
            try:
                purl = p.get("url", "")
                if not purl or purl in self.seen_parse:
                    continue
                self.seen_parse.add(purl)
                self.parses.append(p)
            except Exception:
                pass
        if not self.wallpaper and root.get("wallpaper"):
            self.wallpaper = root["wallpaper"]

    def verify_and_drop(self):
        """云端轻验证：jar 不可达 -> 其下 type3 spider 源全剔；ext 为 http 且不可达 -> 剔"""
        jar_urls = set()
        ext_urls = set()
        for s in self.sites:
            if s.get("jar"):
                jar_urls.add(str(s["jar"]).split(";md5;")[0])
            e = str(s.get("ext", ""))
            if e.startswith("http"):
                ext_urls.add(e)
        print(f"verifying {len(jar_urls)} jars, {len(ext_urls)} ext urls ...")
        dead_jars, dead_exts = set(), set()
        with cf.ThreadPoolExecutor(FETCH_POOL) as pool:
            for url, ok in zip(jar_urls, pool.map(http_ok, jar_urls)):
                if not ok:
                    dead_jars.add(url)
            for url, ok in zip(ext_urls, pool.map(http_ok, ext_urls)):
                if not ok:
                    dead_exts.add(url)
        kept = []
        for s in self.sites:
            jar = str(s.get("jar", "")).split(";md5;")[0]
            if s.get("type") == 3 and jar and jar in dead_jars:
                self.stats["dropped_dead"] += 1
                continue
            e = str(s.get("ext", ""))
            if e in dead_exts:
                self.stats["dropped_dead"] += 1
                continue
            kept.append(s)
        self.sites = kept


def main():
    sources_file = os.path.join(HERE, "sources.json")
    with open(sources_file, encoding="utf-8") as f:
        lib = json.load(f)
    urls = [s["url"] for s in lib["sources"] if str(s["url"]).startswith("http")]
    print(f"probing {len(urls)} configs ...")
    merger = Merger()
    alive = 0

    def fetch_one(url):
        r = http_get(url, timeout=15)
        if r is None or r.status_code != 200 or len(r.content) < 200:
            return url, None
        return url, find_result(r.text)

    with cf.ThreadPoolExecutor(FETCH_POOL) as pool:
        for url, decoded in pool.map(fetch_one, urls):
            if not decoded:
                continue
            try:
                root = json.loads(decoded)
            except Exception:
                continue
            if not isinstance(root, dict) or "sites" not in root:
                continue
            alive += 1
            merger.merge_one(root, url)

    print(f"alive configs: {alive}, merged sites: {len(merger.sites)}, dup dropped: {merger.stats['dup']}")
    merger.verify_and_drop()
    merger.stats["final"] = len(merger.sites)

    out = {"spider": "", "sites": merger.sites}
    if merger.lives is not None:
        out["lives"] = merger.lives
    if merger.parses:
        out["parses"] = merger.parses
    if merger.wallpaper:
        out["wallpaper"] = merger.wallpaper
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    rep = (f"# 融合报告 {now}\n\n- 配置源可用：{alive}/{len(urls)}\n"
           f"- 融合站源：{merger.stats['final']}（去重剔除 {merger.stats['dup']}，"
           f"jar/ext 失效剔除 {merger.stats['dropped_dead']}，上限 {MAX_SITES}）\n")
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        f.write(rep)
    print(rep)
    if merger.stats["final"] == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
