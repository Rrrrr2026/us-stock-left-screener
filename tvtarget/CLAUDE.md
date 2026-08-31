# tv-target-screen（给 Claude Code 的上下文）

独立小管线：筛出美股中「分析师目标价 Min/Avg/Max 全部高于现价」的股票（等价条件：
`price_target_low > close`）。三步：`fetch_screen.py`（唯一联网步骤，抓 TradingView
scanner 接口写 `data/screen.json`）→ `build_page.py`（生成自包含的 `output/index.html`
交互筛选页 + `output/watchlist.txt` TradingView 导入文件）→ 可选 `make_excel.py`。
细节、字段口径、cron 示例见 README.md。

数据 schema（`data/screen.json`）：
`{fetchedAt, totalCovered, sectors:[...], rows:[[symbol,name,secIdx,close,tlow,tavg,thigh,tmed,mark,ntotal,nsb,nb,nh,ns,nss,mcapB],...]}`
（rows 按市值降序；mcapB 单位十亿美元；nsb..nss 为 强烈买入..强烈卖出 人数。）

改模板注意：`web/page_template.html` 里 `/*__DATA__*/`、`__N_MATCH__`、`__N_TOTAL__`、
`__FETCH_DATE__` 是 build_page.py 的注入点，别删；页面 JS 中 rows 的列序与上面 schema 一一对应。

典型集成任务（用户可能会要求）：
- 把 `output/index.html` 挂到现有网站的某个路由，并加 nginx/静态托管配置；
- 加 cron/systemd timer 每个交易日收盘后刷新；
- 把 `data/screen.json` 入库到交易系统共用的数据库，供左侧/右侧策略当候选池；
- 调整筛选口径（fetch_screen.py 顶部参数）或页面默认排序/预设。

约束：非官方接口，保持低频（每天一次级别）；`fetch_screen.py` 未在真实服务器实测过
（沙箱不可直连），首次跑通前别串进关键路径。
