# 美股「左侧支撑位」筛选 + 基本面交叉 + 交互监控台

在**高景气 GICS 板块**内,自动发现全美股(市值≥$100M, 约4100只)里正回踩支撑 / 接近前低的**左侧机会**,拉取基本面并技术×基本面交叉打分,再对每只候选做**支撑回踩事件回测**给出买卖点建议(买入区/止损/目标梯子+历史胜率),在一个**全中文、可交互**的监控台里一屏看全。数据源:**Yahoo Finance (yfinance)**。价格单位美元。

> ⚠️ **免责声明**:本系统仅做技术/基本面数据的自动化整理与形态筛选,**不构成任何投资建议**。"左侧买入"风险天然更高(可能继续下跌或破位)。买卖点与胜率为历史回测统计,不构成对未来的保证。所有标的需**人工复核**,自负盈亏与风控。

---

## 在线查看 (GitHub Pages)
**https://rrrrr2026.github.io/us-stock-left-screener/**(任何设备浏览器直接打开)

## 一分钟上手
```bash
pip install -r requirements.txt
py -3 run_pipeline.py           # 抓全美股数据 -> 打分 -> 买卖点回测 -> 导出看板 (约10-30分钟)
# 然后双击打开 dashboard/index.html
```
更新线上数据:双击 `auto_update.bat`(跑完整流水线并推送 docs/ 到 GitHub, Pages 1-2分钟后刷新)。已配置计划任务「美股左侧监控台每日更新」工作日 08:00 自动运行。

## 结构
- `screener/` — config / datasource(yfinance) / indicators / module1_industry(板块景气) / module2_tech(技术左侧) / module3_fundamentals / module4_crossscore / tradeplan(买卖点回测) / module6_profile(深度档案) / db / export_data
- `dashboard/index.html` — 全中文交互看板 (Tailwind + ECharts, 中英双语/明暗主题)
- `docs/` — GitHub Pages 托管目录
- `ALGORITHMS.md` — 全部算法公式与参数说明(含 v2 升级说明)

## 说明
- **股票池**:NASDAQ 官方筛选器全美股(市值≥$100M, 剔除优先股/挂牌债券/权证/SPAC单位);板块以 Yahoo GICS 口径为准(NASDAQ 分类仅兜底)。可在 `screener/config.py` 调整。
- **板块景气**:用 11 个 SPDR 行业 ETF(XLK/XLF/XLV…)作板块指数代理算 趋势/动量,成分股算广度;基准 SPY。
- **技术打分**:通道下轨/前低/均线/超跌背离/回撤 + v2 新增 支撑强度/趋势规整/相对强度(vs SPY)。
- **基本面**:来自 `yf.info`(PE/PB/ROE/毛利/增长/股息/FCF收益率等);PE 分位为候选池内横截面分位,并与所属板块中位对比;数据覆盖不足会折价。
- **买卖点建议**:详情弹窗内,基于本股近2年支撑回踩事件回测 + 全池先验贝叶斯收缩,给出 建议买入区/止损位/三档目标(+胜率+中位到达天数)/盈亏比。
- 每只失败只跳过并记录,不中断整轮。
