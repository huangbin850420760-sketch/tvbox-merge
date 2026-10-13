# TVBox 每日自动融合服务

GitHub Actions 每天自动：拉取源清单 → 探活（含加密配置解密）→ 融合去重 → jar/ext 可达性验证 → 发布 `merged.json`。

**使用方式**：推送到你的 GitHub 仓库后，手动触发一次（Actions → Daily Merge → Run workflow）或等每天北京时间 05:30 自动跑，然后把 raw 链接填进 TVBox 配置地址：

```
https://raw.githubusercontent.com/<你的用户名>/<仓库名>/main/merged.json
```

国内访问 raw 慢的话，用代理前缀（任选其一，注意这些是第三方代理，可用性自负）：
```
https://gh.aptv.app/https://raw.githubusercontent.com/<user>/<repo>/main/merged.json
https://github.moeyy.xyz/https://raw.githubusercontent.com/<user>/<repo>/main/merged.json
```

## 文件说明

| 文件 | 作用 |
|---|---|
| `sources.json` | 源清单（56 条，与 App 内置一致；可自行增删） |
| `merge.py` | 融合器：明文/XX**+Base64/2423-AES-CBC 三形态解密、补 site 级 jar、`./` 相对路径转绝对、双指纹去重、key 冲突加序号、jar/ext HTTP 可达性剔除 |
| `merged.json` | 产物（每天自动更新） |
| `report.md` | 融合统计报告 |

## 与 App 内融合的关系

- 云端：每日定时、全配置融合、HTTP 级验证（jar 挂则其下站源必死）——**一个链接所有设备共用，可分享**
- App 内"一键融合"：设备端实时探活结果、站源级真实搜索实测——**更深但耗设备资源**
- 两者兼容：App 内源库把本仓库 raw 链接加进去即可参与探活/自动切换

## 本地运行

```bash
pip install requests pycryptodome
python merge.py   # 产出 merged.json / report.md
```
