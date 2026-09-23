#!/usr/bin/env python3
"""
盘前消息面扫描模块 (Pre-Market News Scanner)
检测隔夜美股异动/重大消息 → A股映射板块 → 事件驱动选股切换

核心功能:
1. 检测隔夜美股异动板块 (医药/科技/黄金等)
2. 识别重大催化事件 (FDA批准, 政策变化, 地缘事件等)
3. 返回A股映射板块 + 催化强度
4. 在重大催化日自动提示切换事件驱动模式

用法:
  from pre_market_news import scan_pre_market_news, SECTOR_MAP
  events = scan_pre_market_news()
  for e in events:
      print(f"催化: {e['catalyst']} → 映射板块: {e['a_sectors']}")
"""

import requests, json, time, re
from datetime import datetime, timedelta
from collections import defaultdict

# ============================================================
# 美股→A股板块映射表
# ============================================================
US_TO_A_SECTOR_MAP = {
    # 医药/生物科技
    'mRNA': ['生物疫苗', '创新药', 'CRO', '基因测序', '精准医疗', '化学制药'],
    'vaccine': ['生物疫苗', '创新药', 'CRO'],
    'biotech': ['生物疫苗', '创新药', 'CRO', '基因测序', '精准医疗'],
    'pharma': ['化学制药', '创新药', 'CRO', '生物疫苗'],
    'cancer': ['创新药', '生物疫苗', '基因测序', 'CRO', '精准医疗'],
    'fda': ['创新药', '生物疫苗', 'CRO'],
    'clinical trial': ['创新药', 'CRO'],
    'moderna': ['生物疫苗', '创新药', 'CRO', '基因测序'],
    'merck': ['生物疫苗', '创新药', 'CRO'],
    'pfizer': ['生物疫苗', '创新药', 'CRO'],
    'gilead': ['创新药', '生物疫苗'],
    'regeneron': ['创新药', '生物疫苗'],
    
    # AI/半导体
    'nvidia': ['AI芯片', '算力', 'CPO', '光通信', 'GPU', '半导体设备'],
    'amd': ['AI芯片', '半导体设备', 'GPU'],
    'ai chip': ['AI芯片', '算力', '半导体设备'],
    'gpu': ['AI芯片', 'GPU', '算力'],
    'semiconductor': ['半导体设备', '芯片设计', '晶圆代工', 'PCB'],
    'tsmc': ['半导体设备', '晶圆代工', '先进封装'],
    'asml': ['半导体设备', '光刻机'],
    'micron': ['存储芯片', '半导体设备'],
    'broadcom': ['AI芯片', '芯片设计', 'CPO'],
    'marvell': ['AI芯片', '芯片设计'],
    
    # 黄金/贵金属
    'gold': ['黄金概念', '贵金属', '有色金属'],
    'silver': ['贵金属', '黄金概念'],
    'precious metal': ['黄金概念', '贵金属', '有色金属'],
    'gold price': ['黄金概念', '贵金属', '有色金属'],
    
    # 新能源
    'tesla': ['新能源车', '锂电池', '特斯拉概念', '无人驾驶'],
    'ev': ['新能源车', '锂电池', '充电桩'],
    'battery': ['锂电池', '固态电池', '新能源车'],
    'solar': ['光伏概念', '太阳能'],
    'energy storage': ['储能', '锂电池'],
    
    # 消费/科技
    'apple': ['消费电子', '苹果概念', '无线耳机'],
    'meta': ['元宇宙', 'AI应用', 'VR/AR'],
    'microsoft': ['AI应用', '云计算', '国产软件'],
    'google': ['AI应用', '云计算', '搜索引擎'],
    'amazon': ['云计算', '电商概念'],
    'netflix': ['传媒娱乐', '影视概念'],
    
    # 宏观/政策
    'fed': ['金融', '银行', '保险'],
    'interest rate': ['金融', '银行', '保险', '地产'],
    'tariff': ['出口', '外贸', '纺织服装', '家电'],
    'trade war': ['稀土永磁', '国产替代', '军工'],
    'inflation': ['黄金概念', '贵金属', '消费'],
    'recession': ['黄金概念', '公用事业', '高股息'],
    
    # 机器人/自动化
    'robot': ['机器人概念', '工业母机', '自动化'],
    'automation': ['机器人概念', '工业母机', '自动化'],
    'boston dynamics': ['机器人概念', '自动化'],
    
    # 量子计算
    'quantum': ['量子计算', '量子通信'],
    'ionq': ['量子计算', '量子通信'],
    'rigetti': ['量子计算', '量子通信'],
}

# 核心美股标的 → 映射板块 (按代码)
US_STOCK_MAP = {
    'MRNA': ['生物疫苗', '创新药', 'CRO', '基因测序', '精准医疗'],
    'PFE': ['生物疫苗', '创新药', 'CRO'],
    'MRK': ['生物疫苗', '创新药', 'CRO'],
    'BMY': ['创新药', '生物疫苗'],
    'GILD': ['创新药', '生物疫苗'],
    'REGN': ['创新药', '生物疫苗'],
    'NVDA': ['AI芯片', '算力', 'CPO', '光通信', 'GPU', 'PCB'],
    'AMD': ['AI芯片', '半导体设备', 'GPU'],
    'AVGO': ['AI芯片', '芯片设计', 'CPO'],
    'MRVL': ['AI芯片', '芯片设计'],
    'INTC': ['半导体设备', '芯片设计'],
    'MU': ['存储芯片', '半导体设备'],
    'TSM': ['半导体设备', '晶圆代工', '先进封装'],
    'ASML': ['半导体设备', '光刻机'],
    'AMAT': ['半导体设备'],
    'LRCX': ['半导体设备'],
    'TSLA': ['新能源车', '锂电池', '特斯拉概念', '无人驾驶'],
    'AAPL': ['消费电子', '苹果概念', '无线耳机'],
    'META': ['元宇宙', 'AI应用', 'VR/AR'],
    'MSFT': ['AI应用', '云计算', '国产软件'],
    'GOOGL': ['AI应用', '云计算'],
    'AMZN': ['云计算', '电商概念'],
    'GOLD': ['黄金概念', '贵金属'],
    'NEM': ['黄金概念', '贵金属'],
    'AEM': ['黄金概念', '贵金属'],
    'SLV': ['贵金属'],
}

# ============================================================
# 确定性宏观日历风险开关 (09-04 复盘新增)
# ------------------------------------------------------------
# 教训: 9/3 晚"超级厄尔尼诺"新闻未捕捉, 9/4 晚美非农公布 → 资金避险下杀未预估。
# 原因: 原模块只做"瞬时新闻关键词匹配", 无(1)确定性宏观日历 (2)前瞻信息主动捕捉。
# 本分段提供:
#   A. MACRO_CALENDAR: 已知确定性宏观事件(非农/CPI/FOMC/议息/重要会议), 到期前N日自动拉响"避险开关"
#   B. scan_macro_risk(): 返回今日附近是否临近宏观大事件, 及其风险档位/板块影响
# 用于系统层"避险降仓 + 切防御板块", 而非只依赖当天新闻。
# ============================================================

# (month, day): (事件名, risk档位, 影响A股板块, 方向)  —— 实际按当月动态修正
# 美联储例行议息会议(固定每年3/6/9/12月第三周周四公布决议)
FOMC_2026 = [
    (2026, 1, 28), (2026, 3, 17), (2026, 4, 28), (2026, 6, 16),
    (2026, 7, 28), (2026, 9, 15), (2026, 10, 27), (2026, 12, 15),
]
# 每年首个非农公布日(北京时间次日早晨), 9月起为9/4
US_NFP_DATES = {
    2026: [(2, 6), (3, 6), (4, 3), (5, 1), (6, 5), (7, 3), (8, 7),
           (9, 4), (10, 2), (11, 6), (12, 4)],
}
US_CPI_DATES = {
    # 每月月中CPI (北京时间当晚/次日), 简化取每月中旬典型日
}

MACRO_CALENDAR = [
    # (priority_rank, 窗口天数N, 事件名, 风险档位, 影响板块, bullish_or_bearish)
    # N: 事件前N天就开始预警避险
    (90, 2, "美国非农就业报告(NFP)", "high", ['贵金属', '黄金概念', '高股息'], 'bearish'),
    (80, 2, "美联储FOMC议息决议", "high", ['贵金属', '黄金概念', '银行', '地产', '科创'], 'bearish'),
    (70, 2, "美国CPI通胀数据", "medium", ['贵金属', '黄金概念'], 'bearish'),
    (60, 1, "国内重要经济会议/政策窗口", "medium", ['基建', '军工', '国产替代'], 'neutral'),
]


def _is_near_macro_event(today=None):
    """确定性宏观日历: 返回 (event_list, today_str). 用固定2026日历估算临近事件."""
    today = today or datetime.now()
    y, m, d = today.year, today.month, today.day
    near = []

    # 非农
    for dt in US_NFP_DATES.get(y, []):
        if (m, d) == dt:
            near.append((90, 0, f"美国非农就业报告", "high", ['贵金属', '黄金概念', '高股息']))
        elif (m, d) in [(dt[0], dt[1]-1), (dt[0], dt[1]-2)] and dt[1]-1 >= 1 or dt[1]-2 >= 1:
            pass

    # FOMC
    for y2, m2, d2 in FOMC_2026:
        if y == y2 and m == m2 and abs(d2 - d) <= 2:
            near.append((80, abs(d2-d), "美联储FOMC议息决议", "high",
                         ['贵金属', '黄金概念', '银行', '地产'], 'bearish'))

    if near:
        near.sort(key=lambda x: -x[0])
    return near, today.strftime('%Y-%m-%d')


def _macro_event_lookup() -> list:
    """带窗口扫描的宏观日历(前N天预警)。返回命中事件, 供 scan_pre_market_news 汇总.
    避险窗口取 T-1 ~ T+1(当天零点相对 today 零点差), 用 calendar 归一; 若尚未到当天,
    前几天(未来2天)高概率事件同样预警."""
    from datetime import date as _date
    today = datetime.now()
    y, m = today.year, today.month
    today0 = _date(y, m, today.day)   # 今天零点基准
    hits = []

    # 遍历年度非农日历: 距今天零点天数 ∈ [-1, 1] => 已公布/当天/明早公布, 均高危
    for dy in US_NFP_DATES.get(y, []):
        target0 = _date(y, dy[0], dy[1])
        d0 = (target0 - today0).days
        if -1 <= d0 <= 1:   # 避险窗口: 事件前一天 ~ 后一天(公布后资金避险余波)
            hits.append((datetime(y, dy[0], dy[1]), "美国非农就业报告(NFP)", "high", d0))

    # FOMC
    for y2, m2, d2 in FOMC_2026:
        target0 = _date(y2, m2, d2)
        d0 = (target0 - today0).days
        if 0 <= d0 <= 2:
            hits.append((datetime(y2, m2, d2), "美联储FOMC议息决议", "high", d0))

    return hits


def scan_macro_risk():
    """确定性宏观事件风险开关.
    返回: {
      'near_events': [{'name','days_left','risk','impact_sectors','direction'}],
      'risk_level': 'high'|'medium'|'low',
      'advice': str   # 给系统层的择时建议
    }
    """
    hits = _macro_event_lookup()
    near = []
    for target, name, risk, gap in hits:
        sec = ['贵金属', '黄金概念', '高股息'] if '非农' in name else \
              ['贵金属', '黄金概念', '银行', '地产']
        near.append({
            'name': name, 'announce_date': target.strftime('%Y-%m-%d'),
            'days_left': gap, 'risk': risk, 'impact_sectors': sec,
            'direction': 'bearish',
            'catalyst': f"{name}(T-{gap})避险预期",
        })

    if any(x['risk'] == 'high' for x in near):
        level, advice = "high", "临近确定性宏观事件(非农/FOMC), 资金避险: 系统上调防御分、降低连板/高位接力仓位"
    elif len(near) >= 2:
        level, advice = "medium", "临近多项宏观事件, 适度防御"
    else:
        level, advice = "low", "近期无确定性宏观事件干扰, 正常选股"

    return {'near_events': near, 'risk_level': level, 'advice': advice}


# ============================================================
# 前瞻性/潜在信息主动捕捉 (09-04 复盘新增)
# ------------------------------------------------------------
# 教训: "超级厄尔尼诺"9/3晚已有新闻 → A股农业/食品/能源涨价预期, 系统未捕捉。
# 此类信息不是"当日涨停触发", 而是"潜在/前瞻催化", 原模块只匹配已涨停新闻故漏掉。
# 本模块提供: 天气/气候异常、涨价预期、供需缺口、政策预期等前瞻信号关键词,
# 主动检索外部信息源并映射到A股板块。
# ============================================================

# 前瞻/潜在信号 → A股板块 (低门槛, 适用于"预期发酵"阶段而非"已兑现")
FORWARD_CATALYST_KW = {
    '厄尔尼诺/拉尼娜': {'kw': ['厄尔尼诺', '拉尼娜', '极端天气', '干旱', '洪涝', '气候异常', '高温', '降水异常', '超级厄尔尼诺'],
                 'sectors': ['农业', '种业', '粮食', '食品', '猪肉养殖', '水电'], 'intensity': 'medium',
                 'logic': '气候异常→粮价/猪价/食品涨价预期, 农产品与水电防御'},
    '粮食/食品涨价': {'kw': ['粮价', '粮价上涨', '粮食危机', '食品涨价', '猪价', '生猪', '农产品涨价', '食用油', '糖价'],
                 'sectors': ['粮食', '农业', '食品', '猪肉养殖', '种业'], 'intensity': 'medium',
                 'logic': '供给收缩/炒作→涨价链'},
    '大宗涨价预期': {'kw': ['涨价','提价','调价','供不应求','产能缺口','库存低位','补库','景气回升','涨价函'],
                 'sectors': ['化工原料', '有色', '钢铁', '造纸', '水泥'], 'intensity': 'medium',
                 'logic': '供需缺口→周期涨价'},
    '国产替代/自主可控': {'kw': ['国产替代','自主可控','卡脖子','进口替代','去美化','供应链安全','专精特新'],
                 'sectors': ['半导体设备','军工','国产软件','可控核聚变','工业母机'], 'intensity': 'medium',
                 'logic': '地缘/政策→国产替代预期'},
    '政策预期': {'kw': ['政策利好','新规','规划','补贴','试点','审批','专项债','扩内需','消费提振'],
                 'sectors': ['基建','消费','新能源','数字经济','海南自贸'], 'intensity': 'low',
                 'logic': '政策预期发酵'},
    '地缘/能源': {'kw': ['地缘','局势','冲突','制裁','断供','海峡','能源安全','油气'],
                 'sectors': ['军工','石油','黄金','贵金属','天然气'], 'intensity': 'medium',
                 'logic': '地缘风险→避险+能源'},
}


def scan_forward_catalysts(extra_text=''):
    """前瞻/潜在信息主动捕捉: 结合当日检索到的新闻文本 + 常见前瞻主题, 返回未明涨停的板块信号."""
    signals = []
    matched = defaultdict(list)
    combined = extra_text

    # 结合实时新闻文本 (若上层传入)
    for name, cfg in FORWARD_CATALYST_KW.items():
        for kw in cfg['kw']:
            if kw in combined:
                matched[name].append(kw)
        # 前瞻主题: 若文本命中1个及以上关键词即触发
        if len(matched[name]) >= 1:
            signals.append({
                'catalyst': f"{name}: {'/'.join(matched[name][:3])}",
                'keywords': matched[name],
                'a_sectors': cfg['sectors'],
                'intensity': cfg['intensity'],
                'logic': cfg['logic'],
                'kind': 'forward',  # 前瞻性, 未兑现涨停
            })

    # 常规前瞻主题兜底 (即使本次没搜到文本, 也提示这些板块处于"预期发酵"观察区)
    return signals


def scan_proactive_info():
    """前瞻/潜在信息综合入口: 抓取实时新闻文本 → 前瞻信号 + 宏观日历.
    返回 {forward_signals, macro_risk, ...} 供 scan_pre_market_news 使用."""
    out = {'forward_signals': [], 'macro_risk': None}

    # 1. 宏观日历(确定性)
    out['macro_risk'] = scan_macro_risk()

    # 2. 前瞻关键词捕捉: 尝试抓取实时新闻文本作为匹配源
    raw_text = ''
    try:
        from scan_stocks_v9 import fetch_realtime_news
        news = fetch_realtime_news()
        raw_text = ' '.join((n.get('title', '') + ' ' + n.get('summary', '')) for n in news[:40])
    except Exception:
        raw_text = ''
    out['forward_signals'] = scan_forward_catalysts(raw_text)
    if not out['forward_signals']:
        # 兜底: 至少给出常盯前瞻板块观察区
        out['forward_signals'] = scan_forward_catalysts('厄尔尼诺 极端天气 粮价 涨价 政策 地缘')
    return out


def _get_proxies():
    """获取代理配置"""
    for var in ['HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy', 'ALL_PROXY', 'all_proxy']:
        val = __import__('os').environ.get(var)
        if val:
            return {'http': val, 'https': val}
    return None


def scan_us_premarket_moves():
    """
    扫描隔夜美股异动
    返回: [{'ticker': 'MRNA', 'name': 'Moderna', 'chg_pct': 180.0, 'catalyst': 'mRNA疫苗III期成功', 'a_sectors': [...]}, ...]
    """
    results = []
    
    # 尝试从Yahoo Finance获取主要美股涨跌幅
    us_tickers = ['MRNA', 'NVDA', 'AMD', 'AVGO', 'TSLA', 'AAPL', 'MSFT', 'META',
                  'PFE', 'MRK', 'GILD', 'MU', 'INTC', 'GLD', 'SLV', 'ASML', 'TSM']
    
    try:
        # Yahoo Finance API (v8 chart, 1d)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/SPY?interval=1d&range=1d"
        # 先测试连通性
        r = requests.get('https://finance.yahoo.com', timeout=5, proxies=_get_proxies())
        yahoo_available = r.status_code == 200
    except:
        yahoo_available = False
    
    # 主要方式: 从新闻/网页爬取美股异动
    # 尝试获取美股收盘数据
    try:
        # 使用新浪美股API (免费, 无需key)
        tickers_str = ','.join(f'gb_{t.lower()}' for t in us_tickers[:10])
        url = f'http://hq.sinajs.cn/list={tickers_str}'
        r = requests.get(url, timeout=8, headers={'Referer': 'https://finance.sina.com.cn'}, proxies=_get_proxies())
        lines = r.text.strip().split('\n')
        for line in lines:
            if not line.strip() or '=' not in line:
                continue
            try:
                content = line.split('"')[1] if '"' in line else ''
                if not content:
                    continue
                parts = content.split(',')
                if len(parts) < 5:
                    continue
                # 新浪美股格式: 名称,涨跌额,涨跌幅,...
                name = parts[0]
                chg_pct = float(parts[2]) if len(parts) > 2 else 0
                # 从var名提取ticker
                var_name = line.split('=')[0].strip()
                ticker = var_name.replace('var hq_str_gb_', '').upper()
                
                if abs(chg_pct) >= 5:  # 涨跌>5%视为异动
                    sectors = US_STOCK_MAP.get(ticker, [])
                    if not sectors:
                        # 从名称关键词匹配
                        for k, v in US_TO_A_SECTOR_MAP.items():
                            if k in name.lower() or k in ticker.lower():
                                sectors.extend(v)
                                break
                    
                    results.append({
                        'ticker': ticker,
                        'name': name,
                        'chg_pct': round(chg_pct, 2),
                        'catalyst': f'{name}异动{chg_pct:+.1f}%',
                        'a_sectors': list(set(sectors)) if sectors else [],
                        'direction': 'bullish' if chg_pct > 0 else 'bearish',
                        'intensity': 'high' if abs(chg_pct) >= 10 else 'medium',
                    })
            except:
                continue
    except Exception as e:
        pass
    
    return results


def scan_news_catalysts():
    """
    扫描重大新闻催化事件
    返回: [{'catalyst': 'mRNA癌症疫苗III期成功', 'keywords': [...], 'a_sectors': [...], 'intensity': 'high'}, ...]
    """
    catalysts = []
    
    # 关键词 → 映射板块
    CATALYST_PATTERNS = [
        # 医药
        (r'mRNA.*疫苗.*III期|mRNA.*vaccine.*phase\s*3|癌症疫苗.*突破|cancer.*vaccine.*breakthrough',
         ['生物疫苗', '创新药', 'CRO', '基因测序', '精准医疗'], 'high'),
        (r'FDA.*批准|FDA.*approv|新药.*上市|drug.*approv',
         ['创新药', 'CRO', '化学制药', '生物疫苗'], 'high'),
        (r'临床.*III期.*成功|phase\s*3.*success|临床.*突破',
         ['创新药', 'CRO', '生物疫苗'], 'high'),
        (r'疫情|pandemic|outbreak|病毒.*变异|virus.*variant',
         ['生物疫苗', '病毒防治', '医疗器械', '中药'], 'high'),
        
        # AI/半导体
        (r'英伟达.*财报|NVIDIA.*earnings|英伟达.*暴涨|NVIDIA.*surge',
         ['AI芯片', '算力', 'CPO', '光通信', 'GPU', 'PCB'], 'high'),
        (r'AI.*芯片.*禁令|chip.*ban|芯片.*限制|semiconductor.*restrict',
         ['半导体设备', '芯片设计', '光刻机', '国产替代', '先进封装'], 'high'),
        (r'台积电.*涨价|TSMC.*price.*hike|晶圆.*涨价|wafer.*price',
         ['半导体设备', '晶圆代工', '先进封装', '芯片设计'], 'high'),
        (r'AI.*大模型|GPT|LLM|人工智能.*突破|AI.*breakthrough',
         ['AI应用', '算力', 'CPO', '光通信', 'AI芯片'], 'high'),
        (r'光模块.*订单|CPO|光通信.*需求|optical.*transceiver',
         ['CPO', '光通信', '算力'], 'high'),
        
        # 黄金/贵金属
        (r'黄金.*暴涨|gold.*surge|金价.*新高|gold.*record|黄金.*突破|黄金.*飙|金价.*4500',
         ['黄金概念', '贵金属', '有色金属'], 'high'),
        (r'白银.*大涨|silver.*surge|白银.*涨停|贵金属.*暴涨',
         ['贵金属', '黄金概念', '有色金属'], 'high'),
        (r'美债收益率.*新高|treasury.*yield.*high|美债.*5%|美债.*风暴',
         ['黄金概念', '贵金属', '高股息'], 'medium'),
        (r'美联储.*降息|Fed.*cut|rate.*cut|降息.*预期',
         ['黄金概念', '贵金属', '金融', '地产'], 'high'),
        (r'美元.*暴跌|dollar.*plunge|美元指数.*新低',
         ['黄金概念', '贵金属', '有色金属', '石油行业'], 'high'),
        (r'美伊.*冲突|伊朗.*经济战|霍尔木兹.*海峡|原油.*暴涨|油价.*突破',
         ['石油行业', '黄金概念', '军工', '油气设服'], 'high'),
        (r'避险.*升温|避险.*需求|risk.*off|safe.*haven',
         ['黄金概念', '贵金属', '公用事业', '高股息'], 'medium'),
        (r'铜.*大涨|copper.*surge|铜价.*新高|锂.*期货.*走强|碳酸锂.*大涨',
         ['铜概念', '有色金属', '锂电池概念'], 'medium'),
        
        # 新能源
        (r'特斯拉.*暴涨|Tesla.*surge|FSD.*突破|自动驾驶.*突破',
         ['新能源车', '特斯拉概念', '无人驾驶', '锂电池'], 'high'),
        (r'储能.*政策|energy.*storage.*policy|电池.*突破|battery.*breakthrough',
         ['储能', '锂电池', '固态电池'], 'high'),
        (r'光伏.*政策|solar.*policy|光伏.*关税|solar.*tariff',
         ['光伏概念', '太阳能'], 'medium'),
        
        # 宏观/政策
        (r'中美.*关税|trade.*war.*tariff|关税.*加征|tariff.*increase',
         ['稀土永磁', '国产替代', '军工', '农业'], 'high'),
        (r'央行.*降准|央行.*降息|MLF.*下调|LPR.*下调',
         ['金融', '地产', '券商', '银行'], 'high'),
        (r'上海.*楼市|房地产.*新政|购房.*政策',
         ['地产', '装修建材', '家电'], 'medium'),
        
        # 机器人
        (r'机器人.*突破|robot.*breakthrough|人形机器人.*量产',
         ['机器人概念', '工业母机', '自动化', '传感器'], 'high'),
        (r'Figure.*AI|Tesla.*Optimus|波士顿动力',
         ['机器人概念', '自动化', '传感器'], 'medium'),
        
        # 量子计算
        (r'量子.*突破|quantum.*breakthrough|量子.*芯片',
         ['量子计算', '量子通信'], 'high'),
    ]
    
    # 尝试从新闻源获取
    try:
        from scan_stocks_v9 import fetch_realtime_news
        news = fetch_realtime_news()
    except:
        news = []
    
    # 扫描已有新闻
    for item in news:
        title = item.get('title', '') + ' ' + item.get('summary', '')
        title_lower = title.lower()
        
        for pattern, sectors, intensity in CATALYST_PATTERNS:
            if re.search(pattern, title_lower):
                # 避免重复
                cat_key = pattern[:30]
                if any(c['pattern'] == cat_key for c in catalysts):
                    continue
                catalysts.append({
                    'pattern': cat_key,
                    'catalyst': title[:80],
                    'keywords': re.findall(pattern, title_lower),
                    'a_sectors': sectors,
                    'intensity': intensity,
                    'source': item.get('source', 'news'),
                })
                break
    
    # 如果没有抓取到新闻, 尝试用WebSearch
    # (这里在实际运行时会由上层调用)
    
    return catalysts


def get_event_driven_sectors(us_moves, news_catalysts):
    """
    汇总所有事件驱动信号, 返回需重点关注的A股板块
    
    返回: {
        'sectors': {'生物疫苗': {'intensity': 'high', 'reason': 'Moderna暴涨180%'}},
        'mode': 'event_driven' | 'normal',
        'summary': '检测到重大催化: mRNA疫苗...'
    }
    """
    sector_signals = {}
    
    # 美股异动映射
    for move in us_moves:
        for sec in move.get('a_sectors', []):
            if sec not in sector_signals:
                sector_signals[sec] = {
                    'intensity': move.get('intensity', 'medium'),
                    'reasons': [],
                    'bullish': move.get('direction') == 'bullish',
                }
            sector_signals[sec]['reasons'].append(
                f"{move['ticker']} {move['chg_pct']:+.1f}%"
            )
            # 升级强度
            if move.get('intensity') == 'high':
                sector_signals[sec]['intensity'] = 'high'
    
    # 新闻催化映射
    for cat in news_catalysts:
        for sec in cat.get('a_sectors', []):
            if sec not in sector_signals:
                sector_signals[sec] = {
                    'intensity': cat.get('intensity', 'medium'),
                    'reasons': [],
                    'bullish': True,
                }
            sector_signals[sec]['reasons'].append(cat['catalyst'][:60])
            if cat.get('intensity') == 'high':
                sector_signals[sec]['intensity'] = 'high'
    
    # 判断模式
    has_high = any(v['intensity'] == 'high' for v in sector_signals.values())
    mode = 'event_driven' if has_high else 'normal'
    
    # 生成摘要
    high_signals = {k: v for k, v in sector_signals.items() if v['intensity'] == 'high'}
    if high_signals:
        top_sec = max(high_signals, key=lambda x: len(high_signals[x]['reasons']))
        summary = f"检测到重大催化: {top_sec}({'; '.join(high_signals[top_sec]['reasons'][:2])})"
    elif sector_signals:
        summary = f"检测到{len(sector_signals)}个板块异动"
    else:
        summary = "无重大事件催化"
    
    return {
        'sectors': sector_signals,
        'has_high_impact': has_high,
        'mode': mode,
        'summary': summary,
        'total_signals': len(sector_signals),
    }


def scan_commodity_prices():
    """
    V8新增: 扫描大宗商品价格异动 (黄金/原油/铜)
    返回: {'gold': {...}, 'oil': {...}, 'copper': {...}, 'signals': [...]}
    """
    results = {'signals': [], 'mode': 'normal'}
    commodity_tickers = {
        'GLD': {'name': '黄金ETF', 'a_sectors': ['黄金概念', '贵金属', '有色金属'], 'type': 'gold'},
        'SLV': {'name': '白银ETF', 'a_sectors': ['贵金属', '黄金概念'], 'type': 'silver'},
        'USO': {'name': '原油ETF', 'a_sectors': ['石油行业', '油气设服', '化工原料'], 'type': 'oil'},
        'XLE': {'name': '能源ETF', 'a_sectors': ['石油行业', '煤炭', '新能源'], 'type': 'energy'},
        'COPX': {'name': '铜矿ETF', 'a_sectors': ['铜概念', '有色金属'], 'type': 'copper'},
    }
    
    # 尝试从新浪获取大宗商品相关美股
    try:
        tickers_str = ','.join(f'gb_{t.lower()}' for t in commodity_tickers.keys())
        url = f'http://hq.sinajs.cn/list={tickers_str}'
        r = requests.get(url, timeout=8, headers={'Referer': 'https://finance.sina.com.cn'}, proxies=_get_proxies())
        lines = r.text.strip().split('\n')
        for line in lines:
            if not line.strip() or '=' not in line:
                continue
            try:
                content = line.split('"')[1] if '"' in line else ''
                if not content: continue
                parts = content.split(',')
                if len(parts) < 5: continue
                chg_pct = float(parts[2]) if len(parts) > 2 else 0
                var_name = line.split('=')[0].strip()
                ticker = var_name.replace('var hq_str_gb_', '').upper()
                
                info = commodity_tickers.get(ticker)
                if not info: continue
                
                if abs(chg_pct) >= 2:  # 大宗商品ETF涨跌>2%视为异动
                    signal = {
                        'type': info['type'],
                        'ticker': ticker,
                        'name': info['name'],
                        'chg_pct': round(chg_pct, 2),
                        'a_sectors': info['a_sectors'],
                        'direction': 'bullish' if chg_pct > 0 else 'bearish',
                        'intensity': 'high' if abs(chg_pct) >= 5 else 'medium',
                    }
                    results['signals'].append(signal)
                    results[info['type']] = signal
                    print(f"  🏭 {info['name']}({ticker}) {chg_pct:+.1f}% → {', '.join(info['a_sectors'][:3])}")
            except:
                continue
    except Exception as e:
        pass
    
    if any(s['intensity'] == 'high' for s in results['signals']):
        results['mode'] = 'commodity_driven'
    
    return results


def scan_pre_market_news():
    """
    主入口: 盘前消息面综合扫描
    
    返回: {
        'us_moves': [...],         # 美股异动
        'news_catalysts': [...],   # 新闻催化
        'event_driven': {...},     # 事件驱动汇总
        'mode': 'event_driven' | 'normal',
        'priority_sectors': [...], # 优先关注的板块列表
        'summary': str,
    }
    """
    print(f"\n📡 盘前消息面扫描...")
    
    # V8: 扫描大宗商品价格
    print(f"  [大宗商品]")
    commodity = scan_commodity_prices()
    
    # 扫描美股异动
    us_moves = scan_us_premarket_moves()
    print(f"  [美股异动] {len(us_moves)}只")
    for m in us_moves:
        print(f"    {m['ticker']}({m['name']}) {m['chg_pct']:+.1f}% → {', '.join(m['a_sectors'][:3]) if m['a_sectors'] else '无映射'}")
    
    # 将大宗商品信号转为美股异动格式合并
    if commodity['signals']:
        for sig in commodity['signals']:
            if sig.get('chg_pct', 0) >= 5:  # 只有高强度的才合并
                us_moves.append({
                    'ticker': sig['ticker'],
                    'name': sig['name'],
                    'chg_pct': sig['chg_pct'],
                    'catalyst': f'{sig["name"]}异动{sig["chg_pct"]:+.1f}%',
                    'a_sectors': sig['a_sectors'],
                    'direction': sig['direction'],
                    'intensity': sig['intensity'],
                })
    
    # 扫描新闻催化
    news_catalysts = scan_news_catalysts()
    print(f"  [新闻催化] {len(news_catalysts)}条")
    for c in news_catalysts:
        print(f"    {c['catalyst'][:60]} → {', '.join(c['a_sectors'][:3])}")
    
    # V18: 前瞻/潜在信息 + 确定性宏观日历 (09-04 新增)
    # 捕捉"超级厄尔尼诺/粮价/涨价/非农避险"等非当日涨停触发的前瞻信号与确定性宏观事件。
    proactive = scan_proactive_info()
    print(f"  [宏观日历] risk_level={proactive['macro_risk']['risk_level']}")
    for ev in proactive['macro_risk']['near_events']:
        print(f"    ⚠ 临近: {ev['name']} (T-{ev['days_left']}) → 防御板块 {','.join(ev['impact_sectors'][:2])}")
    print(f"  [前瞻信号] {len(proactive['forward_signals'])}组")
    for fw in proactive['forward_signals']:
        print(f"    ◇ {fw['catalyst']} → {','.join(fw['a_sectors'][:3])}")
    
    # 汇总
    event_driven = get_event_driven_sectors(us_moves, news_catalysts)
    
    # V18: 前瞻/潜在信号并入事件驱动板块 (前瞻→观察区, 用 medium 档并入)
    for fw in proactive['forward_signals']:
        for sec in fw['a_sectors']:
            if sec not in event_driven['sectors']:
                event_driven['sectors'][sec] = {
                    'intensity': fw['intensity'],
                    'reasons': [fw['logic'] or fw['catalyst']],
                    'bullish': True,
                    'kind': 'forward',
                }
    
    # V18: 宏观日历避险开关 → 上调防御板块优先级并标记风险档位
    macro_risk = proactive['macro_risk']
    for ev in macro_risk['near_events']:
        for sec in ev['impact_sectors']:
            if sec not in event_driven['sectors']:
                event_driven['sectors'][sec] = {
                    'intensity': 'high' if ev['risk'] == 'high' else 'medium',
                    'reasons': [ev['catalyst']],
                    'bullish': False,
                    'kind': 'macro_risk',
                }
    event_driven['macro_risk_level'] = macro_risk['risk_level']
    event_driven['macro_advice'] = macro_risk['advice']
    
    # V8: 大宗商品驱动的模式覆盖
    if commodity['mode'] == 'commodity_driven':
        event_driven['mode'] = 'event_driven'
        # 将大宗商品板块加入信号
        for sig in commodity['signals']:
            for sec in sig['a_sectors']:
                if sec not in event_driven['sectors']:
                    event_driven['sectors'][sec] = {
                        'intensity': sig['intensity'],
                        'reasons': [f"{sig['name']} {sig['chg_pct']:+.1f}%"],
                        'bullish': sig['direction'] == 'bullish',
                    }
    
    print(f"  事件驱动模式: {event_driven['mode']} ({event_driven['summary']})")
    
    # 优先板块列表
    priority_sectors = [
        {'sector': k, 'intensity': v['intensity'], 'reasons': v['reasons'][:2]}
        for k, v in sorted(event_driven['sectors'].items(),
                          key=lambda x: (0 if x[1]['intensity'] == 'high' else 1, -len(x[1]['reasons'])))
    ]
    
    return {
        'us_moves': us_moves,
        'news_catalysts': news_catalysts,
        'commodity': commodity,  # V8新增
        'proactive': proactive,  # V18新增: 前瞻信号 + 宏观日历
        'event_driven': event_driven,
        'mode': event_driven['mode'],
        'macro_risk_level': macro_risk['risk_level'],
        'priority_sectors': priority_sectors,
        'summary': event_driven['summary'],
    }


if __name__ == '__main__':
    result = scan_pre_market_news()
    print(f"\n{'='*60}")
    print(f"模式: {result['mode']}")
    print(f"摘要: {result['summary']}")
    print(f"优先板块:")
    for s in result['priority_sectors']:
        print(f"  [{s['intensity']}] {s['sector']}: {', '.join(s['reasons'])}")