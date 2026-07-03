# 美股「左侧支撑位」筛选 + 基本面交叉 + 交互监控台

在**高景气 GICS 板块**内,自动发现标普500里正回踩支撑 / 接近前低的**左侧机会**,拉取基本面并技术×基本面交叉打分,在一个**全中文、可交互**的监控台里一屏看全。数据源:**Yahoo Finance (yfinance)**。价格单位美元。

> ⚠️ **免责声明**:本系统仅做技术/基本面数据的自动化整理与形态筛选,**不构成任何投资建议**。"左侧买入"风险天然更高(可能继续下跌或破位)。所有标的需**人工复核**,自负盈亏与风控。

---

## 在线查看 (GitHub Pages)
**https://rrrrr2026.github.io/us-stock-left-screener/**(任何设备浏览器直接打开)

## 一分钟上手
```bash
pip install -r requirements.txt
python run_pipeline.py          # 抓标普500数据 -> 打分 -> 导出看板 (约1-2分钟)
# 然后双击打开 dashboard/index.html
```
更新线上数据:跑完 `run_pipeline.py` 后双击 `auto_update.bat`(会推送 docs/ 到 GitHub, 1-2分钟后 Pages 刷新)。

## 结构
- `screener/` — config / datasource(yfinance) / indicators / module1_industry(板块景气) / module2_tech(技术左侧) / module3_fundamentals / module4_crossscore / db / export_data
- `dashboard/index.html` — 全中文交互看板 (Tailwind + ECharts)
- `docs/` — GitHub Pages 托管目录

## 说明
- **股票池**:标普500(约503只),名单+GICS板块来自 GitHub 数据集 CSV(维基备份、内置兜底)。可在 `screener/config.py` 调整。
- **板块景气**:用 11 个 SPDR 行业 ETF(XLK/XLF/XLV…)作板块指数代理算 趋势/动量,成分股算广度;基准 SPY。
- **基本面**:来自 `yf.info`(PE/PB/ROE/毛利/增长/股息等);PE 分位为在标普500内的横截面分位,PE 还与所属板块中位对比。
- 每只失败只跳过并记录,不中断整轮。
