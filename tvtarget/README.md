# tv-target-screen — 分析师目标价全高于现价（美股筛选管线）

筛出美股中「分析师一年期目标价 **Min / Avg / Max 全部高于现价**」的股票。
判定只需一条：**最低目标价 Min > 现价**（Min 在上方 ⟹ 三者全在上方）。

由 Claude (Cowork) 于 2026-08-30 构建，首个快照（1236 / 2479 只）已含在 `data/` 与 `output/` 中，
数值曾与 TradingView 个股 Forecasts 页逐位核对（NBIX、AVGO）。

## 文件

```
fetch_screen.py          抓取 TradingView 筛选接口 → data/screen.json（唯一联网步骤）
build_page.py            data/screen.json → output/index.html + output/watchlist.txt
make_excel.py            （可选）→ output/筛选明细.xlsx，需 openpyxl
web/page_template.html   页面模板（自包含；数据以 /*__DATA__*/ 注入）
data/screen.json         最近一次抓取的快照
output/                  构建产物，可直接部署
```

## 用法

```bash
pip install requests            # make_excel.py 另需 openpyxl
python3 fetch_screen.py         # 刷新数据（约 6 个分页请求，几秒钟）
python3 build_page.py           # 重新生成网页 + watchlist
```

部署 = 把 `output/index.html` 放到网站任意路径（单文件、无外部依赖，仅从
Google Fonts 拉一个等宽字体，断网也能正常显示）。`output/watchlist.txt`
在 TradingView 自选列表菜单「导入列表…」导入即成一个按行业分组的板块。

### 定时刷新（美股收盘后跑一次即可）

```cron
# 服务器为 UTC 时：美股收盘 20:00/21:00 UTC，取 22:00 UTC 稳妥
0 22 * * 1-5  cd /path/to/tv-target-screen && python3 fetch_screen.py && python3 build_page.py
```

## 可调参数

`fetch_screen.py` 顶部：`MIN_ANALYSTS`（默认 5）、`EXCHANGES`、分页限速。
页面端的筛选（Min/Avg 上行空间、分析师数、市值、板块、搜索）都是浏览器里实时的，无需改代码。

## 数据与口径

- 接口：`POST https://scanner.tradingview.com/america/scan`（TradingView 个股
  Forecasts 页同源的公开接口，无需登录）。字段：`price_target_low/average/high/median`、
  `recommendation_mark/total/buy/over/hold/under/sell`。
- 服务端过滤：NASDAQ/NYSE/AMEX、主要上市、评级分析师 ≥ MIN_ANALYSTS、存在目标价；
  本地再过滤：普通股或 ADR（剔除优先股）、USD 计价、`price_target_low > close`。
- 「分析师数」= 给出**评级**的人数；给出**目标价**的人数可能略少（NBIX：30 vs 27）。
- 评级映射：均值 ≤1.5 强烈买入，≤2.5 买入，≤3.5 持有，≤4.5 卖出。

## 注意

- `fetch_screen.py` 是在无法直连该接口的沙箱里写的：请求体与浏览器里验证过的完全一致，
  但脚本本身未从服务器实测。若遇 4xx，先检查响应文本；一般带上脚本里的 UA/Origin 头即可。
- 这是非官方接口，字段可能变化；保持低频（每天 1 次），仅个人使用。
- 分析师目标价整体偏乐观（此条件命中约一半覆盖股）；低价小盘常因个别极端目标价排名靠前。
  不构成投资建议。
