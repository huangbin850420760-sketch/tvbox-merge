# TVBox 每日自动融合服务

每天自动：拉取源清单 → 探活（含加密配置解密）→ 融合去重 → jar/ext 可达性验证 → 发布 `merged.json`。

提供两种部署方式（二选一，也可都跑）：

## 方式 A：GitHub Actions（推荐，零运维）

推送到 GitHub **公开仓库**后自动每天（北京时间 05:30）跑，产物 raw 链接即 TVBox 配置地址：

```
https://raw.githubusercontent.com/<你的用户名>/<仓库名>/main/merged.json
```

> 为什么必须公开仓库：私有仓库的 raw 链接需要登录 token，TVBox 无法匿名拉取；且免费账户的 GitHub Pages 也仅公开仓库可用。内容只是公开源的配置聚合，无隐私数据。
> 国内访问 raw 慢可加代理前缀（第三方代理，可用性自负）：
> `https://gh.aptv.app/https://raw.githubusercontent.com/<user>/<repo>/main/merged.json`

优点：不占任何设备、GitHub 服务器定时执行、免费（公开仓库 Actions 不限量）。

## 方式 B：Docker 自建（家里 NAS/服务器）

```bash
docker compose up -d --build
```

- TVBox 配置地址：`http://<运行docker的设备IP>:8000/merged.json`
- 每天自动重融合（`MERGE_INTERVAL` 可调，秒），产物持久化在 `./out/`
- 优点：国内直连快、完全自主；局限：仅局域网设备可访问（要分享给外网需公网 IP 或内网穿透）

## 文件说明

| 文件 | 作用 |
|---|---|
| `sources.json` | 源清单（56 条，与 App 内置一致；可自行增删） |
| `merge.py` | 融合器：明文/XX**+Base64/2423-AES-CBC 三形态解密、补 site 级 jar、`./` 相对路径转绝对、双指纹去重、key 冲突加序号、jar/ext HTTP 可达性剔除（`MERGE_OUT_DIR` 环境变量指定产物目录） |
| `merged.json` | 产物（每天自动更新） |
| `report.md` | 融合统计报告 |
| `Dockerfile` / `entrypoint.sh` / `docker-compose.yml` | 方式 B 的自建部署 |

## 与 App 内融合的关系

- 云端：每日定时、全配置融合、HTTP 级验证（jar 挂则其下站源必死）——**一个链接所有设备共用，可分享**
- App 内"一键融合"：设备端实时探活结果、站源级真实搜索实测——**更深但耗设备资源**
- 两者兼容：App 内源库把本仓库 raw 链接加进去即可参与探活/自动切换

## 本地运行

```bash
pip install requests pycryptodome
python merge.py   # 产出 merged.json / report.md
```
