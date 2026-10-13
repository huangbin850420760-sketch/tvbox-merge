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
TEST_WORD = "庆余年"  # CMS 播放级验证测试词（与 App 端一致）
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.environ.get("MERGE_OUT_DIR", HERE)
OUT_JSON = os.path.join(OUT_DIR, "merged.json")
OUT_REPORT = os.path.join(OUT_DIR, "report.md")

BASE_RE = re.compile(r"[A-Za-z0]{8}\*\*")
EMOJI_RE = re.compile(r'[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F️]')
DECOR_RE = re.compile(r'4K|8K|超清|高清|HD|蓝光|备用\d*|专线|高速|优选|官源|ROM|影视|剧场|影院|大全|合集', re.I)
NUM_RE = re.compile(r'[①②③④⑤⑥⑦⑧⑨⑩0-9⓪ⅠⅡⅢ一二三四五六七八九]')
SEP_RE = re.compile(r'[\s\-—_|·/\\()（）\[\]【】*×.。:：,，、+]')


def norm_name(n):
    """站名归一化：去 emoji/序号/装饰词，用于同名不同写的判重"""
    n = EMOJI_RE.sub('', str(n or ''))
    n = NUM_RE.sub('', n)
    n = DECOR_RE.sub('', n)
    n = SEP_RE.sub('', n)
    return n.strip().lower()


def api_class(api):
    """api 指纹：csp_XYZ 取类名 / 路径取末段文件名，识别'同爬虫引擎'"""
    api = str(api or '')
    m = re.search(r'csp_([A-Za-z0-9]+)', api)
    if m:
        return m.group(1)
    seg = api.split('?')[0].rstrip('/').split('/')[-1]
    return seg if 0 < len(seg) <= 40 else api[:60]


def ext_site_urls(ext_s):
    """ext 里的业务站点 URL 特征（域名+路径前40），用于识别同站点多线路"""
    urls = re.findall(r'https?://[^\s"\'\\,]+', ext_s)
    out = set()
    for u in urls:
        p = urllib.parse.urlparse(u)
        if any(k in p.netloc for k in ('github.com', 'githubusercontent', 'jsdelivr', 'gitee.com',
                                        'gh-proxy', 'ghproxy', '127.0.0.1', 'localhost')):
            continue  # 公共代码托管CDN/本地代理不是业务站点（gitlab 不排除：大量源站配置托管在 gitlab）
        out.add(p.netloc + (p.path.rstrip('/')[:40] if len(p.path) > 1 else ''))
    return out


def http_get(url, timeout=TIMEOUT, retries=1):
    """GET，失败重试（GitHub Actions runner 在海外，对国内源放宽一次重试降低误杀）"""
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, timeout=timeout, headers={"User-Agent": UA},
                             allow_redirects=True, verify=False)
            return r
        except Exception:
            if attempt < retries:
                import time
                time.sleep(2)
    return None


def http_ok(url):
    """可达性检查（GET range 轻量探测，失败重试一次防海外视角误杀）"""
    for attempt in range(2):
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": UA, "Range": "bytes=0-63"},
                             allow_redirects=True, verify=False)
            if r.status_code in (200, 206, 302, 304):
                return True
        except Exception:
            pass
        import time
        time.sleep(1)
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


def pan_type(name, api):
    """网盘类型识别：网盘是基础设施，多入口收敛（每类型留2个），凭证字段由设备端全局注入接管"""
    s = (str(name) + "|" + str(api)).lower()
    if "quark" in s or "夸克" in s: return "quark"
    if "aliyun" in s or "alipan" in s or "ali_token" in s or "阿里" in s: return "ali"
    if "uc" in s and ("盘" in s or "uc_" in s or "uc盘" in s): return "uc"
    if "115" in s: return "115"
    if "baidu" in s or "百度" in s: return "baidu"
    if "thunder" in s or "迅雷" in s: return "thunder"
    if "pikpak" in s: return "pikpak"
    if "网盘" in s or "盘搜" in s: return "pan_misc"
    return None


# 云端无用户凭证：ext 里的网盘凭证字段删除（过时 token 有害），设备端融合时注入全局值
PAN_FIELDS = ("ali_token", "aliToken", "ali_open_token", "aliyuntoken", "ali_refresh_token",
              "quark_cookie", "quarkCookie", "quark", "bkquark_cookie",
              "uc_cookie", "ucCookie", "pan115_cookie", "baidu_cookie", "bdstoken")


def strip_pan_fields(ext_s):
    """ext 为 JSON 字符串时删除网盘凭证字段"""
    if not ext_s or not ext_s.strip().startswith("{"):
        return ext_s, False
    try:
        obj = json.loads(ext_s)
        if not isinstance(obj, dict):
            return ext_s, False
        changed = False
        for f in PAN_FIELDS:
            if f in obj:
                del obj[f]
                changed = True
        return (json.dumps(obj, ensure_ascii=False), True) if changed else (ext_s, False)
    except Exception:
        return ext_s, False


class Merger:
    def __init__(self):
        self.sites = []
        self.used_keys = set()
        self.seen_name_api = set()
        self.seen_api_ext = set()
        self.seen_norm = set()       # 归一化站名 + 爬虫类 指纹（emoji/装饰词差异判重）
        self.line_count = {}         # 同站点多线路收敛计数（每站点最多保留 2 条）
        self._line_names = {}        # 站点URL -> 已保留的归一化名集合
        self.pan_count = {}          # 网盘类型 -> 已保留入口数（每类型留 2）
        self.parses = []
        self.seen_parse = set()
        self.lives = None
        self.wallpaper = ""
        self.stats = {"configs": 0, "dup": 0, "dup_norm": 0, "dup_line": 0, "dup_pan": 0,
                      "dropped_dead": 0, "final": 0}

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
                try:
                    stype = int(site.get("type", 3))
                except Exception:
                    stype = 3
                api = absolutize(config_url, api)
                ext = site.get("ext", "")
                if isinstance(ext, (dict, list)):
                    ext_s = json.dumps(ext, ensure_ascii=False)
                else:
                    ext_s = absolutize(config_url, str(ext or "").strip())
                jar = absolutize(config_url, str(site.get("jar") or "").strip())

                # 网盘类源收敛：同一网盘最多保留 2 个入口；凭证字段剥离（设备端全局注入接管）
                ptype = pan_type(name, api)
                if ptype:
                    c = self.pan_count.get(ptype, 0)
                    if c >= 2:
                        self.stats["dup_pan"] += 1
                        continue
                    self.pan_count[ptype] = c + 1
                    ext_s, _ = strip_pan_fields(ext_s)

                # 判重1：同爬虫类 + 归一化同名（emoji/装饰词/_N 后缀差异全部命中）
                norm_fp = api_class(api) + "|" + norm_name(name)
                if norm_fp in self.seen_norm:
                    self.stats["dup_norm"] += 1
                    continue

                # 判重2：同站点多线路收敛（ext 内同业务站点 URL 且站名同源，每站点留 2 条）
                line_hit = False
                for u in ext_site_urls(ext_s):
                    self.line_count[u] = self.line_count.get(u, 0) + 1
                    if self.line_count[u] > 2 and norm_name(name) and any(
                            norm_name(name) in prev or prev in norm_name(name)
                            for prev in self._line_names.get(u, set())):
                        line_hit = True
                    self._line_names.setdefault(u, set()).add(norm_name(name))
                if line_hit:
                    self.stats["dup_line"] += 1
                    continue

                # 判重3：原双指纹
                fp1 = name + "|" + api
                if fp1 in self.seen_name_api:
                    self.stats["dup"] += 1
                    continue
                fp2 = api + "|" + ext_s[:256]
                if ext_s and fp2 in self.seen_api_ext:
                    self.stats["dup"] += 1
                    continue
                self.seen_name_api.add(fp1)
                self.seen_norm.add(norm_fp)
                if ext_s:
                    self.seen_api_ext.add(fp2)

                fkey, n = key, 2
                while fkey in self.used_keys:
                    fkey = f"{key}_{n}"
                    n += 1
                self.used_keys.add(fkey)

                ns = {"key": fkey, "name": name,
                      "type": stype, "api": api,
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

    def verify_cms(self):
        """type 0/1 CMS 源播放级验证（云端可做：搜索->详情含播放列表才保留）；
        流探测因海外视角只做统计不做剔除依据。"""
        cms = [s for s in self.sites if s.get("type") in (0, 1)]
        if not cms:
            self.stats["cms_ok"] = self.stats["cms_total"] = 0
            return
        print(f"play-verifying {len(cms)} cms sources ...")
        ok_ids, stream_ok = set(), 0

        def check(s):
            try:
                api = str(s["api"]).split("?")[0]
                wd = urllib.parse.quote(TEST_WORD)
                # 搜索（苹果CMS：json 接口带 ac=videolist；xml 接口 /at/xml/ 直接 wd）
                r = http_get(f"{api}?ac=videolist&wd={wd}", timeout=15)
                if r is None or r.status_code != 200:
                    r = http_get(f"{api}?wd={wd}", timeout=15)
                if r is None or r.status_code != 200:
                    return s["key"], False, None, "search_fail"
                body = r.text
                vod_id = None
                try:
                    j = json.loads(body)
                    lst = j.get("list") or []
                    if lst:
                        v = lst[0].get("vod_id")
                        vod_id = str(v) if v is not None else None
                except Exception:
                    # XML 接口：<video><id>123</id>...</video>（DOTALL 防标签间换行）
                    m = re.search(r'<video>.*?<id>(\d+)</id>', body, re.S)
                    if m:
                        vod_id = m.group(1)
                    else:
                        m2 = re.search(r'<id>(\d+)</id>', body)
                        if m2:
                            vod_id = m2.group(1)
                if vod_id is None:
                    return s["key"], False, None, "no_result"
                # 详情：文本级搜播放地址（json/xml 通吃）
                d = http_get(f"{api}?ac=videolist&ids={vod_id}", timeout=15) or \
                    http_get(f"{api}?ac=detail&ids={vod_id}", timeout=15) or \
                    http_get(f"{api}?ids={vod_id}", timeout=15)
                if d is None or d.status_code != 200 or ("m3u8" not in d.text and "mp4" not in d.text):
                    return s["key"], False, None, "detail_fail"
                m = re.search(r'(https?://[^\s"\'<>]+?\.(?:m3u8|mp4)[^\s"\'<>]*)', d.text)
                return s["key"], True, (m.group(1) if m else None), "ok"
            except Exception:
                return s["key"], False, None, "exception"

        fail_reasons = {}
        with cf.ThreadPoolExecutor(FETCH_POOL) as pool:
            for key, ok, play, reason in pool.map(check, cms):
                if ok:
                    ok_ids.add(key)
                    if play and http_ok(play):
                        stream_ok += 1
                else:
                    fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
        print("cms fail reasons:", fail_reasons)
        self.stats["cms_total"] = len(cms)
        self.stats["cms_ok"] = len(ok_ids)
        self.stats["cms_stream_ok"] = stream_ok
        self.stats["cms_fail_reasons"] = fail_reasons
        # 云端不剔除 CMS 源：海外视角下 search_fail 可能只是拒海外（设备端真实视角才做剔除）
        # 仅剔除结构性死亡：接口彻底消失（域名解析失败级别的 exception 也不剔，保守全保留）


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
    merger.verify_cms()
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
    cms_line = ""
    if merger.stats.get("cms_total"):
        reasons = merger.stats.get("cms_fail_reasons", {})
        cms_line = (f"；CMS播放级探测 {merger.stats['cms_ok']}/{merger.stats['cms_total']} 通过"
                    f"（流可达 {merger.stats.get('cms_stream_ok', 0)}；失败分布 {reasons}——"
                    f"海外视角仅统计不剔除，剔除由设备端真实视角决定）\n")
    rep = (f"# 融合报告 {now}\n\n- 配置源可用：{alive}/{len(urls)}\n"
           f"- 融合站源：{merger.stats['final']}（精确去重 {merger.stats['dup']}，"
           f"同名异写去重 {merger.stats['dup_norm']}，多线路收敛 {merger.stats['dup_line']}，"
           f"网盘入口收敛 {merger.stats['dup_pan']}，"
           f"失效剔除 {merger.stats['dropped_dead']}，上限 {MAX_SITES}）\n{cms_line}")
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        f.write(rep)
    print(rep)
    if merger.stats["final"] == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
