#!/usr/bin/env python3
"""
沪深主板选股扫描 v9.5 — 动态权重+走势适配+多条件组合+一票否决优化版
==========================================================================
【v9.5核心优化】
1. 动态条件权重: 根据市场走势(bull/bear/crash/range)动态调整各条件权重
   - 下跌市: 条件1/2/7降权或清零，条件9(MACD金叉)大幅加权
   - 上升市: 条件7/8/10加权，条件9降权
   - 震荡市: 条件8/9/10均衡加权
2. 多条件组合奖励: 高胜率条件叠加(4+9/9+10等)额外加分
3. 高胜率条件强制加分: 条件9(+35)/条件4(+25)/条件10(+22)确保排名靠前
4. 基本面一票否决: 净利润暴跌>50%/负债率>85%/PE<0/营收净利润双降直接排除
5. 条件5收紧: 增加当日涨幅>0%过滤，排除诱多低开股
6. TOP10走势适配排序: 优先入选走势适配的高胜率条件股票
7. 概率模型优化: 基于回测胜率的条件概率+组合加成

【v9保留】3条新高胜率选股条件 + 条件3/5优化 + 基本面5维加分
【v8保留】9项核心优化
"""

import json, math, re, time, requests
from datetime import datetime
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
try:
    from mootdx.quotes import Quotes
except ImportError:
    Quotes = None

# v10.0: 导入涨停基因增强模块
try:
    from surge_predictor import (
        calc_limit_gene_score,
        predict_next_day_gain,
        cond13_launch_pattern,
        cond14_acceleration,
        is_surge_candidate,
        cond15_shrink_wash,
        cond16_small_yang_charge,
        cond17_breakout_high,
        cond18_high_amp_wash,
        cond19_strong_bounce,
    )
    SURGE_MODULE_LOADED = True
except ImportError:
    SURGE_MODULE_LOADED = False
    print("\u26a0\ufe0f surge_predictor模块未找到，涨停增强功能不可用")

# v9.5: 手动指定通达信服务器（自动检测在部分网络环境下会失败）
TDX_SERVERS = [
    ('119.147.212.81', 7709),
    ('221.231.141.60', 7709),
    ('101.133.214.242', 7709),
    ('58.63.254.191', 7709),
]

# v9.5: 远程环境通达信协议(TCP 7709)被代理阻断，禁用mootdx
# 本地运行时取消下方注释即可恢复mootdx
client = None
# for host, port in TDX_SERVERS:
#     try:
#         client = Quotes.factory(market='std', server=(host, port))
#         print(f"通达信服务器连接成功: {host}:{port}")
#         break
#     except Exception as e:
#         print(f"服务器{host}:{port}连接失败: {e}")
#         continue

# ============ 消息面板块关键词（7/7交易日最新热点） ============
NEWS_SECTOR_KW = {
    # ===== 08.23 周末热议信息汇总 重点板块（按消息面强度排序） =====
    # TOP1: 半导体材料（长存招股书量价齐升：靶材/气体/化学品/光刻胶/硅片）
    "半导体材料": {
        "bonus": 16,
        "keywords": ["江丰电子","南大光电","沪硅产业","神工股份","雅克科技","安集科技",
                      "华海诚科","鼎龙股份","立昂微","TCL中环","有研新材","江化微",
                      "晶瑞电材","阿石创","隆华科技","金宏气体","华特气体","凯美特气",
                      "万业企业","清溢光电","路维光电",
                      "半导体材料","光刻胶","靶材","硅片","电子特气","前驱体",
                      "抛光","光掩膜","CMP","高端材料","国产替代"],
        "style": "growth",
    },
    # TOP2: NAND存储（长存 NAND 钨→钼工艺变更，设备端受益）
    "存储/NAND设备": {
        "bonus": 15,
        "keywords": ["中微公司","北方华创","拓荆科技","芯源微","盛美上海","华海清科",
                      "江波龙","兆易创新","北京君正","东芯股份","德明利","佰维存储",
                      "NAND","DRAM","存储","3D NAND","钨","钼","沉积设备","刻蚀设备",
                      "存储器","闪存","HBM"],
        "style": "growth",
    },
    # TOP3: AI服务器/英伟达链（服务器涨价>15% + Vera Rubin全面量产）
    "AI服务器/英伟达链": {
        "bonus": 15,
        "keywords": ["工业富联","浪潮信息","紫光股份","中科曙光","寒武纪","海光信息",
                      "沪电股份","胜宏科技","东山精密","华勤技术","沪光股份",
                      "AI服务器","英伟达","Vera","Rubin","GB300","GPU服务器",
                      "液冷","铜连接","高速连接","服务器代工","算力芯片"],
        "style": "growth",
    },
    # TOP4: ABF载板（2026-2028缺货恶化 7%→14%→19%）
    "ABF载板": {
        "bonus": 14,
        "keywords": ["深南电路","兴森科技","生益科技","联瑞新材","崇达技术","华正新材",
                      "ABF","载板","IC载板","封装基板","BT基板","ABF膜","味之素",
                      "PCB","覆铜板","基材"],
        "style": "growth",
    },
    # TOP5: AI电力/算电协同（绿色算力大会13项签约1361亿 + 英伟达入股电力开发）
    "AI电力/算电协同": {
        "bonus": 14,
        "keywords": ["协鑫能科","润泽科技","科华数据","奥飞数据","光环新网","宝信软件",
                      "内蒙华电","申菱环境","英维克","高澜股份","麦格米特","金盘科技",
                      "算力","数据中心","IDC","绿电","算电协同","柴发","变压器",
                      "UPS","电源","电网","电力运营商"],
        "style": "growth",
    },
    # TOP6: 国际金价（4600美元/盎司 三个月新高）
    "黄金/贵金属": {
        "bonus": 13,
        "keywords": ["山东黄金","赤峰黄金","紫金矿业","中金黄金","银泰黄金","招金黄金",
                      "湖南黄金","山金国际","四川黄金","西部黄金",
                      "黄金","金价","贵金属","白银","金矿","避险"],
        "style": "value",
    },
    # TOP7: 特斯拉/无人驾驶（Cybercab 9/3发布，无方向盘）
    "特斯拉/无人驾驶": {
        "bonus": 12,
        "keywords": ["拓普集团","三花智控","旭升集团","新泉股份","岱美股份","嵘泰股份",
                      "伯特利","经纬恒润","文灿股份",
                      "特斯拉","Cybercab","robotaxi","无人驾驶","智能驾驶",
                      "一体化压铸","线控底盘","自动驾驶"],
        "style": "growth",
    },
    # TOP8: 国产叉车出口（全球爆单，订单排至Q4）
    "叉车/工程机械": {
        "bonus": 11,
        "keywords": ["安徽合力","杭叉集团","浙江鼎力","中联重科","徐工机械","三一重工",
                      "叉车","工业车辆","工程机械","电动叉车","高空作业平台","出口"],
        "style": "value",
    },
    # TOP9: 3D打印（海外订单首超国内，出海拐点）
    "3D打印": {
        "bonus": 10,
        "keywords": ["铂力特","华曙高科","金橙子","光韵达","钢研高纳",
                      "3D打印","增材制造","3D打印设备","SLM","粉末","激光成形"],
        "style": "growth",
    },
    # TOP10: 阿里AI基建（配股募资约800亿港元全部投入AI建设）
    "阿里AI基建": {
        "bonus": 10,
        "keywords": ["数据港","万国数据","润建股份","奥飞数据","城地香江","科华数据",
                      "阿里","阿里云","飞书","数据中心","IDC","资本开支","AI建设",
                      "铜线","光模块"],
        "style": "growth",
    },
    # 通信网络（国常会政策：基础/空间/国际/融合网络）
    "通信网络": {
        "bonus": 8,
        "keywords": ["中国移动","中国电信","中国联通","中兴通讯","海格通信","亨通光电",
                      "中天科技","烽火通信",
                      "通信网络","信息通信","卫星互联网","算力网络","光通信"],
        "style": "value",
    },
    # 中期逻辑：超跌/中报（保留稳健板块）
    "中报预增/业绩": {
        "bonus": 8,
        "keywords": ["业绩预增","业绩增长","半年报","中报","净利润增长","预增",
                      "预盈","扭亏","超预期"],
        "style": "value",
    },
    # ===== 08.24 盘中资金主线（主力净流入：煤炭/有色/锂/石化/保险） =====
    "煤炭/动力煤": {
        "bonus": 15,
        "keywords": ["中国神华","陕西煤业","兖矿能源","冀中能源","山西焦煤","平煤股份",
                      "山煤国际","潞安环能","淮北矿业","晋控煤业","广汇能源","电投能源",
                      "煤炭","动力煤","焦煤","焦炭","煤矿","煤化工"],
        "style": "value",
    },
    "有色/铜/钼": {
        "bonus": 15,
        "keywords": ["紫金矿业","洛阳钼业","江西铜业","铜陵有色","云南铜业","西部矿业",
                      "金钼股份","盛屯矿业","北方铜业","河钢资源","白银有色",
                      "有色金属","铜","钼","工业金属","小金属","金属制品"],
        "style": "value",
    },
    "锂/能源金属": {
        "bonus": 14,
        "keywords": ["天齐锂业","赣锋锂业","雅化集团","盛新锂能","中矿资源","永兴材料",
                      "融捷股份","江特电机","盐湖股份","藏格矿业","西藏矿业",
                      "锂矿","碳酸锂","氢氧化锂","锂盐","能源金属","锂电池材料"],
        "style": "value",
    },
    "石油石化/红利": {
        "bonus": 12,
        "keywords": ["中国石油","中国石化","中国海油","中海油服","海油工程","中曼石油",
                      "新潮能源","通源石油","石油石化","油气开采","炼化","成品油"],
        "style": "value",
    },
    "保险/非银红利": {
        "bonus": 10,
        "keywords": ["中国平安","中国太保","中国人寿","新华保险","中国人保","天茂集团",
                      "保险","非银金融","券商","银行","银行股","红利"],
        "style": "value",
    },
}

# ============ 辨识度关键词库（基本面加分用） ============
# 行业龙头/细分龙头名称特征
LEADER_KEYWORDS = {
    "科技龙头": ["中芯国际","寒武纪","海光信息","北方华创","中微公司","长电科技","韦尔股份",
                 "兆易创新","京东方","TCL科技","立讯精密","歌尔股份","蓝思科技","工业富联"],
    "新能源龙头": ["宁德时代","比亚迪","阳光电源","隆基绿能","通威股份","恩捷股份","天赐材料",
                   "赣锋锂业","天齐锂业","华友钴业"],
    "医药龙头": ["恒瑞医药","药明康德","迈瑞医疗","爱尔眼科","片仔癀","云南白药","长春高新"],
    "消费龙头": ["贵州茅台","五粮液","泸州老窖","伊利股份","海天味业","美的集团","格力电器",
                 "海尔智家","安井食品","牧原股份","温氏股份"],
    "金融龙头": ["招商银行","宁波银行","中国平安","中信证券","东方财富","华泰证券"],
    "周期龙头": ["中国石油","中国石化","万华化学","紫金矿业","中国铝业","宝钢股份",
                 "海螺水泥","中国神华","陕西煤业"],
}

# 细分领域关键词（名称含这些词的辨识度高）
NICHE_KEYWORDS = [
    "半导体","芯片","存储","光模块","机器人","算力","AI","液冷","HBM","先进封装",
    "固态电池","钙钛矿","TOPCon","HJT","一体化压铸","一体化","谐波","减速器","丝杠",
    "卫星","航天","航空","军工","创新药","CXO","医疗器械","血制品","磷化工","氟化工",
]


# ============ v11.5: 美股映射模块 ============
def fetch_us_market_data():
    """
    v11.5: 获取隔夜美股关键指数数据（Yahoo Finance）
    返回: {'nasdaq': pct, 'dow': pct, 'philly_semi': pct}
    """
    symbols = {
        '^IXIC': 'nasdaq',
        '^DJI': 'dow',
        '^SOX': 'philly_semi',
    }
    result = {}
    for symbol, key in symbols.items():
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
            r = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            data = r.json()
            if data.get('chart', {}).get('result'):
                timestamps = data['chart']['result'][0]['timestamp']
                closes = data['chart']['result'][0]['indicators']['quote'][0]['close']
                valid = [(t, c) for t, c in zip(timestamps, closes) if c is not None]
                if len(valid) >= 2:
                    latest = valid[-1][1]
                    prev = valid[-2][1]
                    pct = (latest - prev) / prev * 100
                    result[key] = round(pct, 2)
                else:
                    result[key] = 0
            else:
                result[key] = 0
        except Exception as e:
            result[key] = 0
    return result


def us_market_mapping(us_data):
    """
    v11.5: 美股映射分析 -> A股趋势/板块调整
    返回: (trend_override, max_pct_filter, sector_bonus_dict, mapping_desc)
    """
    nasdaq = us_data.get('nasdaq', 0)
    dow = us_data.get('dow', 0)
    philly = us_data.get('philly_semi', 0)

    trend_override = None
    max_pct_filter = 3.0
    sector_bonus = {}
    triggers = []

    # 纳指暴涨 (>2%) — 科技映射最强
    if nasdaq >= 2.0:
        triggers.append(f"纳指+{nasdaq:.1f}%")
        trend_override = 'bull'
        max_pct_filter = 8.0
        sector_bonus['半导体设备/材料'] = 30
        sector_bonus['存储芯片'] = 35
        sector_bonus['算力基础设施'] = 25
        sector_bonus['算力/AI服务器'] = 25
        sector_bonus['光模块/CPO'] = 20
        sector_bonus['英伟达概念'] = 20
        sector_bonus['PCB/电子材料'] = 15
        sector_bonus['人形机器人'] = 15
        sector_bonus['苹果概念'] = 15
        sector_bonus['创新药'] = 10

    # 费城半导体暴涨 (>5%) — 半导体映射
    elif philly >= 5.0:
        triggers.append(f"费城半导体+{philly:.1f}%")
        trend_override = 'bull'
        max_pct_filter = 8.0
        sector_bonus['半导体设备/材料'] = 35
        sector_bonus['存储芯片'] = 35
        sector_bonus['算力基础设施'] = 20
        sector_bonus['算力/AI服务器'] = 20
        sector_bonus['PCB/电子材料'] = 15

    # 纳指大跌 (<-2%) — 避险情绪
    elif nasdaq <= -2.0:
        triggers.append(f"纳指{nasdaq:.1f}%")
        trend_override = 'bear'
        max_pct_filter = 2.0
        sector_bonus['黄金/贵金属'] = 20
        sector_bonus['大金融'] = 10
        sector_bonus['养殖/农业'] = 10

    # 道指独涨（价值风格映射）
    elif dow >= 1.5 and nasdaq < 0.5:
        triggers.append(f"道指+{dow:.1f}%(价值)")
        sector_bonus['大金融'] = 15
        sector_bonus['黄金/贵金属'] = 10
        sector_bonus['中报预增/业绩'] = 10

    desc = " | ".join(triggers) if triggers else "美股无明显映射"
    return trend_override, max_pct_filter, sector_bonus, desc


# ============ 优化1: 风格判断 ============
def detect_market_style(tencent_data):
    """通过取样关键板块龙头涨跌幅判断市场风格"""
    value_proxies = {
        '601318': '中国平安(保险)', '002458': '益生股份(养殖)',
        '002407': '多氟多(氟化工)', '600276': '恒瑞医药(医药)',
        '000876': '新希望(猪肉)',
    }
    growth_proxies = {
        '688981': '中芯国际(半导体)', '300308': '中际旭创(光模块)',
        '002129': 'TCL中环(半导体)', '688256': '寒武纪(AI)',
        '300274': '阳光电源(光伏)',
    }
    v_pct = []; g_pct = []
    for code in value_proxies:
        td = tencent_data.get(code)
        if td and td.get('pct') is not None: v_pct.append(td['pct'])
    for code in growth_proxies:
        td = tencent_data.get(code)
        if td and td.get('pct') is not None: g_pct.append(td['pct'])
    v_avg = sum(v_pct)/len(v_pct) if v_pct else 0
    g_avg = sum(g_pct)/len(g_pct) if g_pct else 0
    style = 'value' if v_avg > g_avg else 'growth'
    return style, v_avg, g_avg

# ============ 优化6: 大盘趋势过滤 ============
def detect_market_trend(tencent_data):
    """
    v9.7: 检测大盘趋势状态 + 风险评估
    返回: (trend, risk_level, risk_reason)
    trend: bull/bear/crash/range
    risk_level: 0(正常)/1(谨慎)/2(高风险)/3(空仓)
    """
    sh_idx = tencent_data.get('000001')
    sz_idx = tencent_data.get('399001')
    
    # 上证涨跌幅
    sh_pct = sh_idx.get('pct', 0) if sh_idx else 0
    sz_pct = sz_idx.get('pct', 0) if sz_idx else 0
    
    # 涨跌家数统计（通过权重股近似）
    all_pcts = []
    for code, td in tencent_data.items():
        if td and td.get('pct') is not None:
            all_pcts.append(td['pct'])
    
    up_count = sum(1 for p in all_pcts if p > 0)
    down_count = sum(1 for p in all_pcts if p < 0)
    total = len(all_pcts) if all_pcts else 1
    up_ratio = up_count / total if total > 0 else 0.5
    
    # 判断趋势
    avg_pct = (sh_pct + sz_pct) / 2 if sz_idx else sh_pct
    if sh_pct < -2 or avg_pct < -2.5:
        trend = 'crash'
    elif sh_pct < -0.5 or avg_pct < -0.8:
        trend = 'bear'
    elif sh_pct < 0.5 and avg_pct < 0.5:
        trend = 'range'
    else:
        trend = 'bull'
    
    # v9.7: 多维度风险评估
    risk_level = 0
    risk_reasons = []
    
    # 维度1: 大盘跌幅
    if sh_pct < -3:
        risk_level = max(risk_level, 3)
        risk_reasons.append(f"上证暴跌{sh_pct:.1f}%")
    elif sh_pct < -1.5:
        risk_level = max(risk_level, 2)
        risk_reasons.append(f"上证大跌{sh_pct:.1f}%")
    elif sh_pct < -0.8:
        risk_level = max(risk_level, 1)
        risk_reasons.append(f"上证下跌{sh_pct:.1f}%")
    
    # 维度2: 涨跌比（普跌=高风险）
    if up_ratio < 0.15:
        risk_level = max(risk_level, 3)
        risk_reasons.append(f"涨跌比{up_ratio:.0%}(普跌)")
    elif up_ratio < 0.25:
        risk_level = max(risk_level, 2)
        risk_reasons.append(f"涨跌比{up_ratio:.0%}(多数下跌)")
    elif up_ratio < 0.35:
        risk_level = max(risk_level, 1)
        risk_reasons.append(f"涨跌比{up_ratio:.0%}")
    
    # 维度3: 深成指同步下跌（确认系统性风险）
    if sz_idx and sz_pct < -1.5 and sh_pct < -1:
        risk_level = max(risk_level, 2)
        risk_reasons.append(f"深成指同步跌{sz_pct:.1f}%")
    
    # 维度4: 权重护盘但个股暴跌（虚假安全）
    if sh_pct > -0.5 and up_ratio < 0.3:
        risk_level = max(risk_level, 2)
        risk_reasons.append("权重护盘，个股普跌")
    
    risk_reason = ' | '.join(risk_reasons) if risk_reasons else '正常'
    return trend, risk_level, risk_reason, up_ratio


# ============ v10.4: 市场情绪量化 + 板块温度检测 ============

def detect_market_mood(tencent_data):
    """
    v10.4: 市场情绪量化指标（0-100分）
    综合涨跌家数比、涨跌停比、大盘涨跌幅、量能变化
    返回: (mood_score, mood_label, mood_details)
    mood_label: 'panic'(恐慌<20) / 'caution'(谨慎20-40) / 'neutral'(中性40-60) / 'optimistic'(乐观60-80) / 'greedy'(贪婪>80)
    """
    sh_idx = tencent_data.get('000001')
    sz_idx = tencent_data.get('399001')
    sh_pct = sh_idx.get('pct', 0) if sh_idx else 0
    sz_pct = sz_idx.get('pct', 0) if sz_idx else 0

    all_pcts = [td['pct'] for code, td in tencent_data.items() if td and td.get('pct') is not None and code not in ('000001', '399001', '399006', '000688')]
    if not all_pcts:
        return 50, 'neutral', {}

    up_count = sum(1 for p in all_pcts if p > 0)
    down_count = sum(1 for p in all_pcts if p < 0)
    zt_count = sum(1 for p in all_pcts if p >= 9.8)
    dt_count = sum(1 for p in all_pcts if p <= -9.8)
    total = len(all_pcts)

    # 维度1: 涨跌家数比 (0-30分)
    if total > 0:
        up_ratio = up_count / total
        if up_ratio >= 0.7: up_score = 30
        elif up_ratio >= 0.55: up_score = 24
        elif up_ratio >= 0.45: up_score = 18
        elif up_ratio >= 0.35: up_score = 12
        elif up_ratio >= 0.25: up_score = 6
        else: up_score = 0
    else:
        up_score = 15

    # 维度2: 涨跌停比 (0-30分)
    zt_dt_ratio = zt_count / max(dt_count, 1)
    if zt_dt_ratio >= 5: zt_score = 30
    elif zt_dt_ratio >= 3: zt_score = 24
    elif zt_dt_ratio >= 1.5: zt_score = 18
    elif zt_dt_ratio >= 0.8: zt_score = 12
    elif zt_dt_ratio >= 0.3: zt_score = 6
    else: zt_score = 0

    # 维度3: 大盘涨跌幅 (0-25分)
    avg_pct = (sh_pct + sz_pct) / 2 if sz_idx else sh_pct
    if avg_pct >= 1.5: idx_score = 25
    elif avg_pct >= 0.8: idx_score = 20
    elif avg_pct >= 0.3: idx_score = 16
    elif avg_pct >= -0.3: idx_score = 12
    elif avg_pct >= -0.8: idx_score = 8
    elif avg_pct >= -1.5: idx_score = 4
    else: idx_score = 0

    # 维度4: 恐慌/贪婪极端情况调整 (-15~+15分)
    extreme = 0
    if dt_count >= 10 and zt_count <= 2:
        extreme = -15  # 千股跌停式恐慌
    elif dt_count >= 5 and zt_count <= 1:
        extreme = -10
    elif zt_count >= 20 and dt_count == 0:
        extreme = 10   # 普涨涨停潮
    elif zt_count >= 50:
        extreme = 15   # 极端贪婪

    mood_score = max(0, min(100, up_score + zt_score + idx_score + extreme))

    if mood_score < 20: mood_label = 'panic'
    elif mood_score < 40: mood_label = 'caution'
    elif mood_score < 60: mood_label = 'neutral'
    elif mood_score < 80: mood_label = 'optimistic'
    else: mood_label = 'greedy'

    details = {
        'up_ratio': up_count / total if total else 0,
        'zt_count': zt_count,
        'dt_count': dt_count,
        'zt_dt_ratio': zt_dt_ratio,
        'avg_pct': avg_pct,
        'up_score': up_score,
        'zt_score': zt_score,
        'idx_score': idx_score,
        'extreme': extreme,
    }
    return mood_score, mood_label, details


def detect_sector_temperature(candidates, sector_name=None):
    """
    v10.4: 检测板块温度（基于候选池数据）
    返回: 'ice'(冰点) / 'cool'(偏冷) / 'warm'(温热) / 'hot'(高潮) / 'overheat'(过热)
    原理: 统计板块内候选股的平均得分、涨停基因、条件命中数
    """
    if not candidates:
        return 'neutral'

    # 全市场统计
    all_scores = [c.get('raw_score', 0) for c in candidates]
    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0
    high_score_count = sum(1 for s in all_scores if s >= 200)

    if sector_name:
        sector_cands = [c for c in candidates if sector_name in (c.get('news_sectors') or [])]
        if not sector_cands:
            return 'cool'
        sec_scores = [c.get('raw_score', 0) for c in sector_cands]
        sec_avg = sum(sec_scores) / len(sec_scores)
        sec_zt_gene = sum(c.get('surge_gene_score', 0) for c in sector_cands) / len(sector_cands)
        sec_cond_count = sum(len(c.get('conditions', [])) for c in sector_cands) / len(sector_cands)
    else:
        sec_avg = avg_score
        sec_zt_gene = sum(c.get('surge_gene_score', 0) for c in candidates) / len(candidates)
        sec_cond_count = sum(len(c.get('conditions', [])) for c in candidates) / len(candidates)

    # 温度判定
    if sec_avg >= avg_score * 1.5 and sec_cond_count >= 2.5 and sec_zt_gene >= 35:
        return 'overheat'
    elif sec_avg >= avg_score * 1.2 and sec_cond_count >= 2.0 and sec_zt_gene >= 25:
        return 'hot'
    elif sec_avg >= avg_score * 0.9 and sec_cond_count >= 1.5:
        return 'warm'
    elif sec_avg >= avg_score * 0.6:
        return 'cool'
    else:
        return 'ice'


def get_mainboard_stocks():
    """
    获取沪深主板全部股票列表（全量扫描）
    优先级: mootdx > akshare(SZ)+上交所API(SH) > 内置备用列表
    """
    stocks = []

    # === 方式1: mootdx（网络通畅时优先） ===
    if client is not None:
        try:
            for mid, df in [(1, client.stocks(market=1)), (0, client.stocks(market=0))]:
                if df is None: continue
                for _, row in df.iterrows():
                    code, name = str(row['code']), str(row['name'])
                    if 'ST' in name or 'st' in name.lower(): continue
                    if code.startswith('8') or code.startswith('4'): continue
                    if code.startswith('688'): continue
                    if code.startswith('300') or code.startswith('301'): continue
                    if not (code.startswith('60') or code.startswith('00')): continue
                    stocks.append({'code': code, 'name': name, 'market': mid})
            if stocks:
                print(f"mootdx获取: {len(stocks)}只")
                return stocks
        except Exception as e:
            print(f"mootdx获取失败: {e}")

    # === 方式2: 腾讯API批量获取（全量扫描，优先）===
    print("使用腾讯API批量获取全量股票列表...")
    try:
        import urllib.request
        tencent_codes = []
        # 沪市主板
        for i in range(600000, 605000):
            tencent_codes.append(f'sh{i}')
        # 科创板
        for i in range(688000, 689000):
            tencent_codes.append(f'sh{i}')
        # 深市主板+中小板
        for i in range(1, 10000):
            tencent_codes.append(f'sz{i:06d}')
        # 创业板
        for i in range(300000, 310000):
            tencent_codes.append(f'sz{i}')

        batch_size = 80
        total_batches = (len(tencent_codes) + batch_size - 1) // batch_size
        tencent_stocks = []
        for b in range(total_batches):
            batch = tencent_codes[b*batch_size:(b+1)*batch_size]
            url = f"https://qt.gtimg.cn/q={','.join(batch)}"
            try:
                with urllib.request.urlopen(url, timeout=15) as resp:
                    data = resp.read().decode('gbk')
                    for line in data.strip().split('\n'):
                        if '~' not in line: continue
                        parts = line.split('~')
                        if len(parts) < 45: continue
                        code = parts[2]
                        name = parts[1]
                        if 'ST' in name or 'st' in name: continue
                        if '退' in name: continue
                        # v10.2: 仅沪深主板(60/00)，剔除创业板(300/301)和科创板(688)
                        if code.startswith(('60','00')):
                            market = 1 if code.startswith('6') else 0
                            tencent_stocks.append({'code': code, 'name': name, 'market': market})
            except:
                pass
            if (b+1) % 50 == 0:
                print(f"  进度: {b+1}/{total_batches}, 已发现 {len(tencent_stocks)} 只")
        if len(tencent_stocks) > 100:
            stocks = tencent_stocks
            print(f"腾讯API获取: {len(stocks)}只")
            return stocks
    except Exception as e2:
        print(f"腾讯API获取失败: {e2}")

    # === 方式3: akshare(深交所) + 上交所HTTP API(沪市) ===
    print("使用akshare+上交所API获取全量股票列表...")
    try:
        import akshare as ak
        import json as _json

        # 深市主板 (00开头，排除ST) — 重试1次(超时30s)
        df_sz = None
        for retry in range(1):
            try:
                df_sz = ak.stock_info_sz_name_code(symbol='A股列表')
                break
            except Exception as e:
                print(f"  深交所API失败: {e}")
                time.sleep(1)
        if df_sz is not None:
            sz_main = df_sz[
                df_sz['A股代码'].str.startswith('00') &
                ~df_sz['A股简称'].str.contains('ST|st', case=False, na=False)
            ]
            for _, row in sz_main.iterrows():
                stocks.append({
                    'code': str(row['A股代码']),
                    'name': str(row['A股简称']),
                    'market': 0,
                })
            print(f"  深市主板: {len(sz_main)}只")
        else:
            # v9.8备用: 深交所API失败时用ak.stock_zh_a_spot()
            try:
                df_spot = ak.stock_zh_a_spot()
                sz_spot = df_spot[df_spot['代码'].str.startswith('sz00')]
                sz_spot = sz_spot[~sz_spot['名称'].str.contains('ST|st', case=False, na=False)]
                for _, row in sz_spot.iterrows():
                    code = str(row['代码']).replace('sz', '')
                    stocks.append({'code': code, 'name': str(row['名称']), 'market': 0})
                print(f"  深市主板(akshare spot备选): {len(sz_spot)}只")
            except Exception as e2:
                print(f"  深市备选也失败: {e2}")

        # 沪市主板 (60开头，排除ST/688) — 重试3次
        url = 'http://query.sse.com.cn/sseQuery/commonQuery.do?isPagination=true&pageHelp.pageSize=5000&pageHelp.pageNo=1&sqlId=COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L&STOCK_TYPE=1'
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'http://www.sse.com.cn/'}
        sh_count = 0
        for retry in range(3):
            try:
                r = requests.get(url, timeout=15, headers=headers)
                if r.status_code == 200:
                    data = _json.loads(r.text)
                    sh_list = data.get('pageHelp', {}).get('data', [])
                    for item in sh_list:
                        code = item.get('A_STOCK_CODE', '')
                        name = item.get('SEC_NAME_CN', item.get('COMPANY_ABBR', ''))
                        if not code.startswith('60'): continue
                        if not name or name == '-': continue  # v9.7: 过滤名称缺失
                        if 'ST' in name or 'st' in name: continue
                        if code.startswith('688'): continue
                        if any(kw in name for kw in ['退市', '终止', '摘牌', '退出']): continue
                        stocks.append({'code': code, 'name': name, 'market': 1})
                    sh_count = len([s for s in stocks if s['market']==1])
                    print(f"  沪市主板: {sh_count}只")
                    break
                else:
                    print(f"  沪市API重试{retry+1}: 返回{r.status_code}")
                    time.sleep(1)
            except Exception as e:
                print(f"  沪市API重试{retry+1}: {e}")
                time.sleep(1)
        # v9.8: 上交所API失败时，用akshare备选
        if sh_count == 0:
            try:
                df_sh = ak.stock_info_sh_name_code()
                if df_sh is not None and not df_sh.empty:
                    for _, row in df_sh.iterrows():
                        code = str(row.get('证券代码', ''))
                        name = str(row.get('证券简称', ''))
                        if not code.startswith('60'): continue
                        if not name or name == '-': continue
                        if 'ST' in name or 'st' in name: continue
                        if code.startswith('688'): continue
                        stocks.append({'code': code, 'name': name, 'market': 1})
                    sh_count = len([s for s in stocks if s['market']==1])
                    print(f"  沪市主板(akshare备选): {sh_count}只")
            except Exception as e:
                print(f"  沪市akshare备选也失败: {e}")

        if sh_count == 0:
            print("  ⚠️ 沪市数据获取失败，仅扫描深市")

        if len(stocks) > 100:
            print(f"全量扫描: 共{len(stocks)}只沪深主板股票")
            # v9.8: 取消500只采样限制，真正全量扫描
            print(f"  ⚡全量模式: 扫描全部{len(stocks)}只股票")
            return stocks
    except Exception as e:
        print(f"akshare+上交所API失败: {e}")

    # === 方式4: 内置备用列表 ===
    print("使用内置备用股票列表...")
    backup_stocks = [
        {'code':'600519','name':'贵州茅台','market':1},{'code':'601318','name':'中国平安','market':1},
        {'code':'600036','name':'招商银行','market':1},{'code':'601012','name':'隆基绿能','market':1},
        {'code':'600276','name':'恒瑞医药','market':1},{'code':'601888','name':'中国中免','market':1},
        {'code':'600309','name':'万华化学','market':1},{'code':'601899','name':'紫金矿业','market':1},
        {'code':'600887','name':'伊利股份','market':1},{'code':'601398','name':'工商银行','market':1},
        {'code':'600030','name':'中信证券','market':1},{'code':'601668','name':'中国建筑','market':1},
        {'code':'600900','name':'长江电力','market':1},{'code':'601857','name':'中国石油','market':1},
        {'code':'600028','name':'中国石化','market':1},{'code':'601728','name':'中国电信','market':1},
        {'code':'600438','name':'通威股份','market':1},{'code':'601669','name':'中国电建','market':1},
        {'code':'600089','name':'特变电工','market':1},{'code':'601600','name':'中国铝业','market':1},
        {'code':'600690','name':'海尔智家','market':1},{'code':'601766','name':'中国中车','market':1},
        {'code':'600893','name':'航发动力','market':1},{'code':'601390','name':'中国中铁','market':1},
        {'code':'600703','name':'三安光电','market':1},{'code':'601995','name':'中金公司','market':1},
        {'code':'600196','name':'复星医药','market':1},{'code':'601818','name':'光大银行','market':1},
        {'code':'600745','name':'闻泰科技','market':1},{'code':'601225','name':'陕西煤业','market':1},
        {'code':'600460','name':'士兰微','market':1},{'code':'601117','name':'中国化学','market':1},
        {'code':'600584','name':'长电科技','market':1},{'code':'601919','name':'中远海控','market':1},
        {'code':'600010','name':'包钢股份','market':1},{'code':'601186','name':'中国铁建','market':1},
        {'code':'600019','name':'宝钢股份','market':1},{'code':'601088','name':'中国神华','market':1},
        {'code':'600048','name':'保利发展','market':1},{'code':'601288','name':'农业银行','market':1},
        {'code':'600031','name':'三一重工','market':1},{'code':'601688','name':'华泰证券','market':1},
        {'code':'600111','name':'北方稀土','market':1},{'code':'601618','name':'中国中冶','market':1},
        {'code':'600104','name':'上汽集团','market':1},{'code':'601319','name':'中国人保','market':1},
        {'code':'600050','name':'中国联通','market':1},{'code':'601658','name':'邮储银行','market':1},
        {'code':'600837','name':'海通证券','market':1},{'code':'601939','name':'建设银行','market':1},
        {'code':'600016','name':'民生银行','market':1},{'code':'601988','name':'中国银行','market':1},
        {'code':'000858','name':'五粮液','market':0},{'code':'000001','name':'平安银行','market':0},
        {'code':'000002','name':'万科A','market':0},{'code':'000568','name':'泸州老窖','market':0},
        {'code':'000063','name':'中兴通讯','market':0},{'code':'000333','name':'美的集团','market':0},
        {'code':'000725','name':'京东方A','market':0},{'code':'000538','name':'云南白药','market':0},
        {'code':'000651','name':'格力电器','market':0},{'code':'000768','name':'中航西飞','market':0},
        {'code':'000776','name':'广发证券','market':0},{'code':'000895','name':'双汇发展','market':0},
        {'code':'000792','name':'盐湖股份','market':0},{'code':'000938','name':'中煤能源','market':0},
        {'code':'000983','name':'山西焦煤','market':0},{'code':'000963','name':'华东医药','market':0},
        {'code':'000408','name':'藏格矿业','market':0},{'code':'000807','name':'云铝股份','market':0},
        {'code':'000709','name':'河钢股份','market':0},{'code':'000825','name':'太钢不锈','market':0},
        {'code':'000878','name':'云南铜业','market':0},{'code':'000917','name':'电广传媒','market':0},
        {'code':'000060','name':'中金岭南','market':0},{'code':'000039','name':'中集集团','market':0},
        {'code':'000157','name':'中联重科','market':0},{'code':'000166','name':'申万宏源','market':0},
        {'code':'000425','name':'徐工机械','market':0},{'code':'000581','name':'威孚高科','market':0},
        {'code':'000617','name':'中油资本','market':0},{'code':'000629','name':'钒钛股份','market':0},
        {'code':'000630','name':'铜陵有色','market':0},{'code':'000661','name':'长春高新','market':0},
        {'code':'000708','name':'中信特钢','market':0},{'code':'000723','name':'美锦能源','market':0},
        {'code':'000786','name':'北新建材','market':0},{'code':'000800','name':'一汽解放','market':0},
        {'code':'000830','name':'鲁西化工','market':0},{'code':'000951','name':'中国重汽','market':0},
    ]
    stocks = backup_stocks
    print(f"备用模式: 加载{len(stocks)}只主流股票")
    return stocks

def get_tencent_batch(codes_list):
    results = {}
    for i in range(0, len(codes_list), 50):
        batch = codes_list[i:i+50]
        symbols = [f'sh{c}' if c.startswith('6') else f'sz{c}' for c in batch]
        url = f'https://qt.gtimg.cn/q={",".join(symbols)}'
        try:
            r = requests.get(url, timeout=15)
            lines = r.text.strip().split(';')
            for line in lines:
                if not line.strip(): continue
                try:
                    eq_idx = line.index('=')
                    content = line[eq_idx+2:].strip().rstrip('"').rstrip('";').strip('"')
                    fields = content.split('~')
                    if len(fields) < 50: continue
                    code = fields[2]
                    results[code] = {
                        'name': fields[1],
                        'price': float(fields[3]) if fields[3] else 0,
                        'last_close': float(fields[4]) if fields[4] else 0,
                        'open_price': float(fields[5]) if fields[5] else 0,
                        'vol': float(fields[6]) if fields[6] else 0,
                        'buy_vol': float(fields[7]) if fields[7] else 0,
                        'sell_vol': float(fields[8]) if fields[8] else 0,
                        'pct': float(fields[32]) if fields[32] and fields[32]!='-' else 0,
                        'amount': float(fields[37]) if fields[37] else 0,
                        'turnover': float(fields[38]) if fields[38] and fields[38]!='-' else 0,
                        'vol_ratio': float(fields[49]) if fields[49] and fields[49]!='-' else 0,
                        'pe': float(fields[39]) if fields[39] and fields[39]!='-' else 0,
                        'pb': float(fields[46]) if len(fields)>46 and fields[46] and fields[46]!='-' else 0,
                        'circ_mv': float(fields[44]) if len(fields)>44 and fields[44] and fields[44]!='-' else 0,
                        'total_mv': float(fields[45]) if len(fields)>45 and fields[45] and fields[45]!='-' else 0,
                        'circ_shares': float(fields[76]) if fields[76] else 0,
                        'buy_sell_ratio': float(fields[7])/float(fields[8]) if fields[8] and float(fields[8])>0 else 0,
                        'net_buy_vol': float(fields[7])-float(fields[8]) if fields[7] and fields[8] else 0,
                    }
                except: pass
        except: pass
    return results

def get_sina_kline(code, datalen=150):
    """v9.4->v10.9: 腾讯K线API, 失败自动回退到 /kline/kline 接口 (规避WAF拦截 fqkline)"""
    prefix = 'sh' if code.startswith('6') else 'sz'
    kline2dict = lambda kls: {
        'closes': [float(d[2]) for d in kls],
        'opens': [float(d[1]) for d in kls],
        'highs': [float(d[3]) for d in kls],
        'lows': [float(d[4]) for d in kls],
        'volumes': [float(d[5])/100 for d in kls],
        'dates': [d[0] for d in kls],
    }
    # 主源: 原 fqkline
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{datalen},qfq'
    try:
        r = requests.get(url, timeout=(5, 15))
        data = r.json()
        stock_data = data.get('data', {}).get(f'{prefix}{code}', {})
        klines = stock_data.get('day', []) or stock_data.get('qfqday', [])
        if len(klines) >= 5:
            return kline2dict(klines)
    except: pass
    # 回退: /kline/kline (WAF宽松, 返回同样格式)
    alt_url = f'https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={prefix}{code},day,,,{datalen}'
    try:
        r = requests.get(alt_url, timeout=(5, 15), headers={'User-Agent': 'Mozilla/5.0'})
        data = r.json()
        stock_data = data.get('data', {}).get(f'{prefix}{code}', {})
        klines = stock_data.get('day', []) or stock_data.get('qfqday', [])
        # 排除未来日期
        if len(klines) >= 5:
            import datetime as _dt
            today = _dt.date.today().isoformat()
            klines = [k for k in klines if k[0].replace('-', '')[:8] <= today.replace('-', '')]
        if len(klines) >= 5:
            return kline2dict(klines)
    except: pass
    return None


def get_sina_klines_batch(codes, datalen=150, max_workers=20, use_cache=True):
    """v10.6: 并发批量获取K线，支持全量扫描 + 盘前缓存加速"""
    import os as _os
    CACHE_FILE = '/workspace/stock-scan-package/kline_cache.json'

    # v10.6: 优先使用盘前预加载的K线缓存
    if use_cache and _os.path.exists(CACHE_FILE):
        try:
            import json as _json
            with open(CACHE_FILE, 'r') as f:
                cache = _json.load(f)
            cache_time = cache.get('cache_time', '')
            cache_klines = cache.get('klines', {})
            cached_count = 0
            results = {}
            missing_codes = []
            for code in codes:
                if code in cache_klines and len(cache_klines[code].get('closes', [])) >= 5:
                    results[code] = cache_klines[code]
                    cached_count += 1
                else:
                    missing_codes.append(code)
            print(f"  K线缓存命中: {cached_count}/{len(codes)}只 (缓存时间: {cache_time})")
            if missing_codes:
                print(f"  补充获取: {len(missing_codes)}只未缓存股票K线...")
                fetched = _fetch_klines_online(missing_codes, datalen, max_workers)
                results.update(fetched)
            print(f"  K线获取完成: {len(results)}/{len(codes)}只")
            return results
        except Exception as e:
            print(f"  K线缓存读取失败: {e}, 在线获取...")

    # 在线获取
    results = _fetch_klines_online(codes, datalen, max_workers)
    print(f"  K线获取完成: {len(results)}/{len(codes)}只")
    return results


def _fetch_klines_online(codes, datalen=150, max_workers=20):
    """在线并发获取K线（内部函数）"""
    results = {}
    total = len(codes)
    done = 0
    def fetch_one(code):
        return code, get_sina_kline(code, datalen)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_one, code): code for code in codes}
        for future in as_completed(futures):
            done += 1
            if done % 500 == 0:
                print(f"    K线进度: {done}/{total} 完成{len(results)}只")
            try:
                code, data = future.result(timeout=20)
                if data:
                    results[code] = data
            except Exception:
                pass
    print(f"    K线进度: {done}/{total} 完成{len(results)}只")
    return results

# ============ 基本面数据获取（东方财富API） ============
def get_sina_finance(code):
    """
    从东方财富获取基本面财务数据
    返回: {roe, debt_ratio, revenue_yoy, profit_yoy, contract_liab_yoy}
    数据来源: emweb.securities.eastmoney.com 财务摘要API
    """
    prefix = 'SH' if code.startswith('6') else 'SZ'
    result = {
        'roe': None, 'debt_ratio': None, 'revenue_yoy': None,
        'profit_yoy': None, 'contract_liab_yoy': None,
    }
    try:
        url = f'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZyzbAjaxNew?type=0&code={prefix}{code}'
        r = requests.get(url, timeout=8, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://emweb.securities.eastmoney.com/'
        })
        d = r.json()
        if isinstance(d, dict) and d.get('data') and len(d['data']) > 0:
            # 取最新一期财报
            fin = d['data'][0]
            # ROE（净资产收益率）
            if fin.get('ROEJQ') is not None:
                result['roe'] = float(fin['ROEJQ'])
            # 资产负债率
            if fin.get('ZCFZL') is not None:
                result['debt_ratio'] = float(fin['ZCFZL'])
            # 营业收入同比增长
            if fin.get('TOTALOPERATEREVETZ') is not None:
                result['revenue_yoy'] = float(fin['TOTALOPERATEREVETZ'])
            # 归母净利润同比增长
            if fin.get('PARENTNETPROFITTZ') is not None:
                result['profit_yoy'] = float(fin['PARENTNETPROFITTZ'])
            # 预收账款/合同负债比率（合同负债是业绩先行指标）
            if fin.get('PREPAID_ACCOUNTS_RATIO') is not None:
                result['contract_liab_yoy'] = float(fin['PREPAID_ACCOUNTS_RATIO'])
    except: pass
    return result


# ============ 技术指标 ============
def calc_pct(c): return [(c[i]-c[i-1])/c[i-1]*100 if c[i-1]>0 else 0 for i in range(1,len(c))]
def calc_rsi(c,p=6):
    if len(c)<p+1: return None
    g=[max(c[i]-c[i-1],0) for i in range(1,len(c))];l=[max(-(c[i]-c[i-1]),0) for i in range(1,len(c))]
    ag=sum(g[-p:])/p;al=sum(l[-p:])/p
    return 100-100/(1+ag/al) if al>0 else 100
def calc_rsi_series(c,p=6):
    """计算RSI序列，用于底背离判断"""
    if len(c)<p+1: return []
    rsi_list = []
    for i in range(p, len(c)):
        g = [max(c[j]-c[j-1],0) for j in range(i-p+1, i+1)]
        l = [max(-(c[j]-c[j-1]),0) for j in range(i-p+1, i+1)]
        ag = sum(g)/p; al = sum(l)/p
        rsi_list.append(100-100/(1+ag/al) if al>0 else 100)
    return rsi_list
def calc_sma(c,p): return sum(c[-p:])/p if len(c)>=p else None
def calc_sma_series(c,p): return [sum(c[i-p:i])/p for i in range(p,len(c)+1)]
def calc_ema_series(d,p):
    if len(d)<p: return []
    a=2/(p+1);e=[sum(d[:p])/p]
    for v in d[p:]: e.append((v-e[-1])*a+e[-1])
    return e
def calc_macd(c,f=12,s=26,sig=9):
    if len(c)<s+sig: return None,None,None
    ef=calc_ema_series(c,f);es=calc_ema_series(c,s);ml=min(len(ef),len(es))
    dif=[ef[-ml+i]-es[-ml+i] for i in range(ml)]
    if len(dif)<sig: return None,None,None
    dea=calc_ema_series(dif,sig)
    return dif[-1],dea[-1] if dea else None,dif[-1]-dea[-1] if dea else None
def calc_macd_series(c,f=12,s=26,sig=9):
    """计算MACD DIF/DEA序列，用于判断水下金叉"""
    if len(c)<s+sig: return [],[],[]
    ef=calc_ema_series(c,f);es=calc_ema_series(c,s);ml=min(len(ef),len(es))
    dif=[ef[-ml+i]-es[-ml+i] for i in range(ml)]
    if len(dif)<sig: return [],[],[]
    dea=calc_ema_series(dif,sig)
    hist=[dif[i]-dea[i] for i in range(len(dea))]
    return dif,dea,hist
def calc_angle(v,p=10):
    if len(v)<p+1: return None
    r=v[-(p+1):]; return math.atan((r[-1]-r[0])/p)*180/math.pi
def zt_count(c,d):
    p=calc_pct(c);n=min(len(p),d-1);cnt=0;idx=[]
    for i,x in enumerate(p[-n:]):
        if x>=9.8: cnt+=1;idx.append(len(p)-n+i)
    return cnt,idx
def big_pct_count(c,d,t):
    p=calc_pct(c);n=min(len(p),d-1)
    return sum(1 for x in p[-n:] if x>=t)
def consec_zt(c,d,m):
    p=calc_pct(c);n=min(len(p),d-1);mx=0;cur=0
    for x in p[-n:]:
        if x>=9.8: cur+=1;mx=max(mx,cur)
        else: cur=0
    return mx>=m


# ============ 原有6条选股条件（条件3/5优化） ============
def cond1(c,tc,tp,r6):
    """10日线强势：连续4天站上10日线+近30天有涨停"""
    if len(c)<14: return False,""
    s10=calc_sma_series(c,10)
    if len(s10)<4: return False,""
    ok=True
    for i in range(4):
        if c[len(c)-1-i]<=s10[len(s10)-1-i]: ok=False; break
    if not ok: return False,""
    z30,_=zt_count(c,30)
    if z30<=1: return False,""
    if tc>=70 or tp<-8 or tp>8: return False,""  # v10.0: tp上限从3放宽到8
    return True,f"连续4天站上10日线，近30天涨停{z30}次"

def cond2(c,v,tc,tp,r6,vr,circ,to):
    """v9.8缩量回调：60天涨停≥3+10天有涨停+缩量+量比>0.5（排除阴跌）"""
    if len(c)<60: return False,""
    z60,_=zt_count(c,60)
    if z60<=2: return False,""
    z10,_=zt_count(c,10)
    if z10==0: return False,""
    v7=v[-7:];tv=v7[-1];mx=max(v7[:-1])
    if mx==0 or tv>=mx*0.7: return False,""
    # v9.8: 排除极度缩量（阴跌信号）
    if mx>0 and tv/mx<0.3: return False,"极度缩量，疑为阴跌"
    if circ and circ>=1e9: return False,""
    if r6 and r6>=80: return False,""
    if vr and vr>=1: return False,""
    if tc>=70 or tp<-6 or tp>8: return False,""  # v10.0: tp上限从3放宽到8
    return True,f"60天涨停{z60}次，10天涨停{z10}次，缩量{tv/mx:.1%}"

# v9.8新增: 条件11 — 涨停基因（活跃度筛选）
def cond11_limit_up_gene(c,tc,tp):
    """涨停基因：60天内至少涨停过1次，证明股性活跃"""
    if len(c)<30: return False,""
    z60,_=zt_count(c,60)
    if z60<1: return False,""
    if tc>=70 or tp<-8 or tp>15: return False,""
    return True,f"60天涨停{z60}次，股性活跃"

# v9.8新增: 条件12 — 超跌反弹（低位启动型）
def cond12_oversold_bounce(c,tc,tp,r6):
    """超跌反弹：RSI<50+站上MA10+近5日跌幅-10%~0%（低位启动）"""
    if len(c)<15: return False,""
    if r6 is None or r6>=55: return False,""  # RSI需处于相对低位
    # 站上MA10
    ma10 = sum(c[-10:])/10
    if c[-1] <= ma10: return False,""
    # 近5日总跌幅在-10%到+2%之间（不高不低）
    if len(c)>=6:
        pct_5d = (c[-1]-c[-6])/c[-6]*100
        if pct_5d > 2 or pct_5d < -15: return False,""
    if tc>=70 or tp<-6 or tp>5: return False,""
    return True,f"RSI{r6:.1f}低位，站上MA10，5日{pct_5d:+.1f}%"

# v10.0: 条件13/14 包装函数（调用surge_predictor）
if SURGE_MODULE_LOADED:
    def cond13(c, v, lo, hi, tc, tp, r6, bsr, vr=None, to=None):
        return cond13_launch_pattern(c, v, hi, lo, to, tc, tp, r6, bsr)
    
    def cond14(c, v, lo, hi, tc, tp, r6, vr=None, to=None):
        return cond14_acceleration(c, v, hi, lo, tc, tp, r6, vr)

    # v10.3: 条件19包装函数
    def cond19(c, v, lo, hi, tc, tp, r6, bsr=None, vr=None, to=None):
        return cond19_strong_bounce(c, v, hi, lo, tc, tp, r6)
else:
    def cond13(c, v, lo, hi, tc, tp, r6, bsr, vr=None, to=None):
        return False, ""
    def cond14(c, v, lo, hi, tc, tp, r6, vr=None, to=None):
        return False, ""
    def cond19(c, v, lo, hi, tc, tp, r6, bsr=None, vr=None, to=None):
        return False, ""

def cond3_optimized(c,v,lo,to,tc,tp,r6,bsr):
    """
    条件3优化版（趋势上行）：放宽角度门槛+增加均线排列要求
    v8问题: 角度门槛过高(a120>15, aw5>15)导致几乎不触发
    v9优化: a120>-2(放宽) + 5/10/20日线多头排列 + 量能配合
    """
    if len(c)<60: return False,""
    # 需要120日线计算角度（如果有足够数据）
    if len(c)>=120:
        s120=calc_sma_series(c,120);a120=calc_angle(s120,10)
        if a120 is None or a120<=-2: return False,""
    # 5/10/20日线多头排列
    s5=calc_sma(c,5);s10=calc_sma(c,10);s20=calc_sma(c,20)
    if s5 is None or s10 is None or s20 is None: return False,""
    if not (s5>s10 and s10>s20): return False,""
    # 价格站上5日线
    if tc<=s5: return False,""
    # 25日线角度（放宽到>5，原来是>15）
    s25=calc_sma_series(c,25);aw5=calc_angle(s25,10)
    if aw5 is None or aw5<=5: return False,""
    # 近期有涨停或大阳
    z30,_=zt_count(c,30);b30=big_pct_count(c,30,7)
    if z30==0 and b30==0: return False,""
    # RSI不在超买区
    if r6 and r6>=80: return False,""
    # 买卖比≥1.0（从<1.0收紧）
    if bsr<1.0: return False,""
    # 近3日有换手>5%（活跃度）
    if not any(t>5 for t in (to[-3:] if len(to)>=3 else [])): return False,""
    if tc>=70 or tp<-8 or tp>3: return False,""
    return True,f"5/10/20多头排列+25日角度{aw5:.1f}°+涨停{z30}次+买卖比{bsr:.2f}"

def cond4(c,tc,tp,r6,up_ratio):
    """v9.8强势型：前天涨停+连续2天维持>95%，RSI<80，涨跌比>30%"""
    if up_ratio <= 0.30:
        return False,f"涨跌比{up_ratio:.0%}≤30%，环境不支持"
    if r6 is not None and r6 >= 80:
        return False,f"RSI{r6:.1f}≥80，超买排除"
    if len(c)<4: return False,""
    p=calc_pct(c)
    if len(p)<3 or p[-3]<9.8: return False,""
    ztc=c[-4]
    for i in range(1,3):  # v9.8: 3天→2天
        if c[len(c)-i]<ztc*0.95: return False,""  # v9.8: 93%→95%
    if tc>=70 or tp<-3 or tp>6: return False,""
    return True,f"前天涨停价{c[-4]:.2f}，连续2天维持>{ztc*0.95:.2f}"

def cond5_optimized(c,tc,tp,r6,bsr):
    """
    条件5优化版（大单净买）：v9.5进一步收紧门槛提升胜率
    v8问题: 60天涨停≥3+买卖比≥0.8，命中208次但RSI均值35.2、当日跌-2.41%，胜率低
    v9优化: 60天涨停≥4 + 买卖比≥1.0 + RSI≥40(排除超卖) + 近10天有涨停
    v9.5优化: 增加当日涨幅>0过滤，排除资金诱多后次日低开的情况
    """
    if len(c)<60: return False,""
    z60,_=zt_count(c,60)
    if z60<4: return False,""  # 从≥3收紧到≥4
    z10,_=zt_count(c,10);b10=big_pct_count(c,10,7)
    if z10==0 and b10==0: return False,""  # 增加10天活跃度要求
    if r6 is None or r6<40: return False,""  # 排除RSI<35超卖区（原v8无此限制）
    if r6>=80: return False,""
    if bsr<1.0: return False,""  # 从≥0.8收紧到≥1.0（资金必须净流入）
    # v9.5: 当日涨幅必须>0%，排除诱多低开股
    if tp <= 0: return False,""
    if tc>=70 or tp<-8 or tp>3: return False,""
    return True,f"60天涨停{z60}次，RSI{r6:.1f}，买卖比{bsr:.2f}，当日涨{tp:.1f}%"


# ============ v10.4: 动态条件权重体系（基于市场走势 + 情绪 + 板块温度 + 风控模式） ============
# 回测胜率参考: 条件4(71.2%), 条件9(81.8%), 消息面(73.1%), 条件1(37.3%), 条件2(36.8%), 条件5(44.7%)
def get_dynamic_weights(market_trend, risk_mode='normal', mood_score=50, sector_temp='warm'):
    """
    v10.4: 根据市场走势、情绪分数、板块温度、风控模式返回条件权重字典
    核心逻辑:
      - 高胜率条件(4/9/10)始终高权重
      - 下跌市: 条件1/2/7降权或清零，条件9/6加权（反转型）
      - 上升市: 条件7/8/10加权，条件9降权
      - 震荡市: 条件8/9/10均衡加权
      - v10.4新增: 情绪极端恐慌时全面降权，情绪贪婪时提升强势型权重
      - v10.4新增: 板块过热时降低该板块相关条件的权重，冰点时提升
    """
    base_weights = {
        1: 5,   # 10日线强势 — 低胜率，基础权重低
        2: 6,   # 缩量回调 — 低胜率，基础权重低
        3: 12,  # 趋势上行 — 待验证
        4: 24,  # v10.3: 强势型权重提升（昨日唯一上涨的股）
        5: 0,   # 大单净买 — 已取消
        6: 20,  # 涨停回踩 — 高胜率策略
        7: 12,  # 均线多头排列 — 牛市策略
        8: 18,  # v10.3: 突破回踩权重略升
        9: 18,  # v10.3: MACD金叉大幅降低（昨日涨停股0%有金叉）
        10: 22, # v10.3: 强势回踩金叉略降
        11: 18, # v10.3: 涨停基因权重提升（股性活跃是核心）
        12: 16, # v10.3: 超跌反弹略降（今日涨停股仅10%RSI<40）
        13: 20, # v10.3: 启动型权重提升（大阳线阈值已放宽到5%）
        14: 16, # 加速突破型
        15: 20, # v10.3: 缩量洗盘型权重提升
        16: 14, # 小阳蓄势型
        17: 24, # v10.3: 突破前高型最高权重（连板概率最高）
        18: 20, # v10.3: 高振幅洗盘型权重提升（今日涨停93%振幅>=5%）
        19: 26, # v10.3: 强势股反包型最高权重（博通集成模式）
    }

    # v9.7: 根据风控模式调整基础权重
    if risk_mode in ('caution', 'stop'):
        base_weights[2] = 0
        print(f"  v9.7风控: 条件2(缩量回调)权重清零(高风险模式)")
    if risk_mode == 'stop':
        base_weights[1] = 0
        base_weights[5] = 0
        base_weights[7] = 0
        base_weights[8] = 0
        print(f"  v9.7风控: 条件1/5/7/8权重清零(空仓模式)")

    # v10.4: 根据市场走势获取基础动态权重
    if market_trend == 'bull':
        weights = {
            **base_weights,
            1: 6,   2: 4,   4: 28,  7: 20,  8: 22,
            9: 10,  10: 24, 11: 22, 12: 8,  13: 24,
            14: 22, 15: 18, 17: 28, 18: 24, 19: 30,
        }
    elif market_trend == 'bear':
        weights = {
            **base_weights,
            1: 3,   2: 6,   4: 18,  7: 5,   8: 10,
            9: 22,  10: 12, 11: 20, 12: 28, 13: 8,
            14: 6,  15: 22, 17: 14, 18: 16, 19: 24,
        }
    elif market_trend == 'crash':
        weights = {
            **base_weights,
            1: 0,   2: 0,   3: 5,   4: 6,   6: 12,
            7: 0,   8: 0,   9: 20,  10: 6,  11: 18,
            12: 30, 13: 4,  14: 0,  15: 18, 17: 6,
            18: 12, 19: 22,
        }
    else:
        weights = {
            **base_weights,
            1: 4,   2: 5,   4: 26,  7: 10,  8: 24,
            9: 16,  10: 20, 11: 20, 12: 20, 13: 18,
            14: 16, 15: 20, 17: 26, 18: 22, 19: 28,
        }

    # v10.4: 根据市场情绪分数微调权重
    # mood_score: 0-100 (panic/caution/neutral/optimistic/greedy)
    if mood_score < 20:
        # 极端恐慌: 仅保留超跌反弹+强势反包，全面收紧
        weights[12] = min(weights.get(12, 16) + 8, 35)   # 超跌反弹最高权重
        weights[19] = min(weights.get(19, 26) + 4, 35)   # 强势反包加权
        weights[4] = max(weights.get(4, 24) - 10, 5)     # 强势型降权
        weights[17] = max(weights.get(17, 24) - 8, 5)    # 突破前高降权
        weights[14] = max(weights.get(14, 16) - 8, 0)    # 加速突破降权
    elif mood_score < 40:
        # 谨慎情绪: 提升防御型，降低激进型
        weights[12] = min(weights.get(12, 16) + 4, 30)
        weights[15] = min(weights.get(15, 20) + 3, 28)
        weights[4] = max(weights.get(4, 24) - 4, 8)
        weights[14] = max(weights.get(14, 16) - 4, 2)
    elif mood_score > 80:
        # 极端贪婪: 提升强势加速型，但降低MACD（追涨风险）
        weights[4] = min(weights.get(4, 24) + 4, 32)
        weights[17] = min(weights.get(17, 24) + 4, 32)
        weights[19] = min(weights.get(19, 26) + 2, 32)
        weights[9] = max(weights.get(9, 18) - 6, 2)      # MACD金叉在贪婪时降权
        weights[12] = max(weights.get(12, 16) - 6, 5)    # 超跌反弹在贪婪时降权
    elif mood_score > 60:
        # 乐观情绪: 略微提升强势型
        weights[4] = min(weights.get(4, 24) + 2, 30)
        weights[17] = min(weights.get(17, 24) + 2, 30)

    # v10.4: 根据板块温度微调权重
    # sector_temp: ice/cool/warm/hot/overheat
    if sector_temp == 'overheat':
        # 板块过热: 降低启动型/突破前高型（追涨风险），提升缩量洗盘/反包
        weights[13] = max(weights.get(13, 20) - 6, 4)    # 启动型降权
        weights[17] = max(weights.get(17, 24) - 8, 5)    # 突破前高降权
        weights[14] = max(weights.get(14, 16) - 6, 0)    # 加速突破降权
        weights[15] = min(weights.get(15, 20) + 4, 28)   # 缩量洗盘加权（等回调）
        weights[19] = min(weights.get(19, 26) + 2, 32)   # 强势反包加权
    elif sector_temp == 'hot':
        # 板块高潮: 略微降低突破型，提升反包型
        weights[17] = max(weights.get(17, 24) - 4, 8)
        weights[13] = max(weights.get(13, 20) - 3, 6)
        weights[19] = min(weights.get(19, 26) + 3, 32)
    elif sector_temp == 'ice':
        # 板块冰点: 提升启动型/突破型（冰点反转预期），降低反包型
        weights[13] = min(weights.get(13, 20) + 6, 30)   # 启动型加权
        weights[15] = min(weights.get(15, 20) + 4, 28)   # 缩量洗盘加权
        weights[12] = min(weights.get(12, 16) + 4, 28)   # 超跌反弹加权
        weights[4] = max(weights.get(4, 24) - 4, 8)      # 强势型降权（冰点无强势）
        weights[19] = max(weights.get(19, 26) - 6, 10)    # 强势反包降权

    return weights


# ============================================================
#  v10.5: 集合竞价强度量化（开盘价+竞价量能综合评分）
# ============================================================

def calc_premarket_intensity(open_price, last_close, current_price, vol_ratio, conditions, trend):
    """
    v10.5: 集合竞价强度量化
    基于开盘价、竞价量能、开盘形态综合评分

    返回: (intensity_score, intensity_label, gap_pct, open_pattern, desc)
      intensity_score: 0-100 (越高越强)
      intensity_label: 极强抢筹/强抢筹/温和偏强/中性/偏弱/弱势低开
      gap_pct: 开盘涨幅 (open vs last_close)
      open_pattern: 高开高走/高开平走/高开低走/平开高走/平开平走/平开低走/低开高走/低开平走/低开低走
    """
    if not open_price or not last_close or last_close <= 0:
        return 0, '无数据', 0, '未知', ''

    gap_pct = (open_price - last_close) / last_close * 100
    # 当前价格相对开盘价的走势
    if current_price and current_price > 0:
        move_from_open = (current_price - open_price) / open_price * 100
    else:
        move_from_open = 0

    # 1. 开盘涨幅评分 (0-40分)
    gap_score = 0
    if gap_pct >= 5:
        gap_score = 40  # 大幅高开，极强抢筹
    elif gap_pct >= 3:
        gap_score = 32  # 强势高开
    elif gap_pct >= 1.5:
        gap_score = 24  # 温和高开
    elif gap_pct >= 0.3:
        gap_score = 14  # 小幅高开
    elif gap_pct >= -0.3:
        gap_score = 8   # 平开
    elif gap_pct >= -1.5:
        gap_score = 4   # 小幅低开
    elif gap_pct >= -3:
        gap_score = 2   # 低开
    else:
        gap_score = 0   # 大幅低开

    # 2. 竞价量能评分 (0-30分)
    vol_score = 0
    if vol_ratio and vol_ratio > 0:
        if vol_ratio >= 3.0:
            vol_score = 30  # 巨量竞价
        elif vol_ratio >= 2.0:
            vol_score = 24  # 放量竞价
        elif vol_ratio >= 1.5:
            vol_score = 18  # 温和放量
        elif vol_ratio >= 1.0:
            vol_score = 12  # 正常量能
        elif vol_ratio >= 0.5:
            vol_score = 6   # 缩量
        else:
            vol_score = 2   # 极度缩量

    # 3. 开盘后走势评分 (0-30分) — 高开高走最强，低开高走次强(吸筹)
    pattern_score = 0
    open_pattern = ''
    if gap_pct >= 1.0:  # 高开类
        if move_from_open >= 1.0:
            pattern_score = 30
            open_pattern = '高开高走'
        elif move_from_open >= -0.5:
            pattern_score = 20
            open_pattern = '高开平走'
        else:
            pattern_score = 5  # 高开低走=出货嫌疑
            open_pattern = '高开低走'
    elif gap_pct >= -1.0:  # 平开类
        if move_from_open >= 1.0:
            pattern_score = 22
            open_pattern = '平开高走'
        elif move_from_open >= -0.5:
            pattern_score = 12
            open_pattern = '平开平走'
        else:
            pattern_score = 4
            open_pattern = '平开低走'
    else:  # 低开类
        if move_from_open >= 1.5:
            pattern_score = 25  # 低开高走=主力吸筹，强信号
            open_pattern = '低开高走'
        elif move_from_open >= 0:
            pattern_score = 10
            open_pattern = '低开平走'
        else:
            pattern_score = 2
            open_pattern = '低开低走'

    # 4. 条件加成：某些技术形态与强势开盘共振时额外加分
    condition_synergy = 0
    if gap_pct >= 1.5:  # 高开1.5%以上时
        if 19 in (conditions or []):  # 强势反包型+高开=反包确认
            condition_synergy += 15
        if 17 in (conditions or []):  # 突破前高+高开=突破确认
            condition_synergy += 12
        if 13 in (conditions or []):  # 启动型+高开=启动确认
            condition_synergy += 10
        if 4 in (conditions or []):   # 强势型+高开=加速
            condition_synergy += 8
    elif gap_pct <= -2:  # 低开2%以上时
        if 12 in (conditions or []):  # 超跌反弹+低开=更好的反弹位置
            condition_synergy += 8
        if 6 in (conditions or []):   # 涨停回踩+低开=回踩到位
            condition_synergy += 6

    total_score = min(gap_score + vol_score + pattern_score + condition_synergy, 100)

    # 5. 确定强度标签
    if total_score >= 75:
        label = '极强抢筹'
    elif total_score >= 55:
        label = '强抢筹'
    elif total_score >= 40:
        label = '温和偏强'
    elif total_score >= 25:
        label = '中性'
    elif total_score >= 15:
        label = '偏弱'
    else:
        label = '弱势低开'

    # 构建描述
    desc = f"开盘{gap_pct:+.1f}%({open_pattern}), 量比{vol_ratio:.1f}" if vol_ratio else f"开盘{gap_pct:+.1f}%({open_pattern})"

    return total_score, label, gap_pct, open_pattern, desc


# v9.5: 多条件组合奖励（高胜率条件叠加=信号增强）
COMBO_BONUSES = {
    # 双高胜率组合
    frozenset({4, 9}): 18,   # 强势型 + MACD金叉 = 极强信号
    frozenset({4, 10}): 16,  # 强势型 + 强势回踩金叉
    frozenset({9, 10}): 14,  # 双金叉共振
    frozenset({8, 9}): 12,   # 突破回踩 + MACD金叉
    frozenset({6, 9}): 12,   # 涨停回踩 + MACD金叉
    frozenset({4, 8}): 10,   # 强势型 + 突破回踩
    frozenset({7, 8}): 10,   # 多头排列 + 突破回踩
    frozenset({6, 10}): 10,  # 涨停回踩 + 强势回踩金叉
    # v10.0: 启动型/加速型组合奖励（涨停潜力核心）
    frozenset({9, 13}): 22,  # MACD金叉 + 启动型 = 涨停前夜最强信号
    frozenset({9, 14}): 20,  # MACD金叉 + 加速突破 = 主升浪
    frozenset({8, 13}): 18,  # 突破回踩 + 启动型 = 二次启动
    frozenset({13, 14}): 25,  # 启动型 + 加速突破 = 双重确认涨停
    frozenset({10, 13}): 20,  # 强势回踩 + 启动型
    frozenset({10, 14}): 18,  # 强势回踩 + 加速突破
    # 三高胜率组合
    frozenset({4, 9, 10}): 28,
    frozenset({6, 9, 10}): 25,
    frozenset({4, 8, 9}): 22,
    frozenset({7, 8, 10}): 18,
    # v10.0: 三重共振组合（涨停级别）
    frozenset({9, 13, 14}): 35,  # MACD金叉+启动型+加速突破 = 理想涨停形态
    frozenset({4, 9, 14}): 30,  # 强势型+金叉+加速突破
    # v10.2: 涨停板回溯高胜率形态组合
    frozenset({15, 9}): 20,   # 缩量洗盘 + MACD金叉 = 洗盘结束+反转确认
    frozenset({17, 10}): 18,  # 突破前高 + 强势回踩 = 主升浪加速
    frozenset({15, 17}): 22,  # 缩量洗盘 + 突破前高 = 爆发前夜最强信号
    frozenset({16, 9}): 16,   # 小阳蓄势 + MACD金叉 = 吸筹结束
    frozenset({17, 9}): 20,   # 突破前高 + MACD金叉 = 主升浪确认
    frozenset({15, 16}): 14,  # 缩量洗盘 + 小阳蓄势 = 主力控盘
    frozenset({15, 17, 9}): 30,  # 三重共振：洗盘+突破+金叉
    frozenset({16, 17, 10}): 26, # 三重共振：蓄势+突破+强势回踩
    # v10.3: 强势股反包组合
    frozenset({19, 4}): 24,   # 强势反包 + 强势型 = 主升浪加速
    frozenset({19, 17}): 26,  # 强势反包 + 突破前高 = 最强连板信号
    frozenset({19, 11}): 22,  # 强势反包 + 涨停基因 = 活跃股反包
    frozenset({19, 18}): 20,  # 强势反包 + 高振幅洗盘 = 股性活跃+反包
    frozenset({19, 4, 17}): 35, # 三重共振：反包+强势+突破前高
}

def calc_combo_bonus(conditions):
    """计算多条件组合奖励分"""
    if not conditions or len(conditions) < 2:
        return 0
    cond_set = frozenset(conditions)
    # 直接匹配
    if cond_set in COMBO_BONUSES:
        return COMBO_BONUSES[cond_set]
    # 子集匹配（取最大匹配）
    best_bonus = 0
    for combo_set, bonus in COMBO_BONUSES.items():
        if combo_set.issubset(cond_set) and bonus > best_bonus:
            best_bonus = bonus
    return best_bonus

def cond6(c,v,lo,hi,tc,tp):
    """
    条件6: 涨停后缩量回踩均线再放量（涨停回踩型）—— v9.8重构版
    路线: 近10天有涨停 → 昨天缩量回踩任一均线 → 今天放量收阳线
    取消RSI/涨幅/买卖比限制，增加确定性
    """
    if len(c)<12 or len(v)<12 or len(lo)<12: return False,""

    # 1. 近10天有涨停
    zt_idx = -1
    for i in range(-10, 0):
        pct = (c[i]-c[i-1])/c[i-1]*100 if i-1>=0 else 0
        if pct >= 9.9:
            zt_idx = i
            break
    if zt_idx == -1:
        return False,""

    # 2. 昨天缩量回踩任一均线(5/10/20/30/60)
    yesterday = c[-2]
    y_vol = v[-2]
    ma_touch = False
    ma_period = 0
    for p in [5, 10, 20, 30, 60]:
        if len(c) < p+1: continue
        ma_val = sum(c[-(p+1):-1]) / p
        if yesterday >= ma_val * 0.97 and yesterday <= ma_val * 1.03:
            ma_touch = True
            ma_period = p
            break
    if not ma_touch:
        return False,""

    # 3. 昨天缩量 < 涨停日量的70%
    zt_vol = v[zt_idx]
    if zt_vol > 0 and y_vol >= zt_vol * 0.70:
        return False,""

    # 4. 今天放量收阳线（量>昨日1.2倍，且收阳）
    if v[-1] < y_vol * 1.2:
        return False,""
    if c[-1] <= c[-2]:
        return False,""

    # 5. 基本过滤
    if tc>=100 or tp<-8 or tp>15: return False,""

    return True,f"近10天涨停后回踩MA{ma_period}，昨缩量{y_vol/zt_vol*100:.0f}%今放量收阳"


# ============ 新增3条高胜率选股条件 ============
def cond7_ma_multiline(c,v,tc,tp,r6,bsr):
    """
    条件7: 均线多头排列+量能阶梯（趋势延续型）—— 回测优化版
    路线: 5>10>20>60日线全部向上 + 量能阶梯 + 近5日不破10日线(防守) + 不追高
    回测反馈: v9初版在下跌市4只全负(均-8.39%)，因高位多头排列股补跌
    优化: 加入近5日最低价>10日线(趋势防守) + RSI上限65(排除高位) + 当日涨幅<2%(不追高)
    适配: 3-10日趋势延续，仅在bull/range走势下启用（主程序会按走势过滤）
    """
    if len(c)<70: return False,""
    s5=calc_sma(c,5);s10=calc_sma(c,10);s20=calc_sma(c,20);s60=calc_sma(c,60)
    if None in (s5,s10,s20,s60): return False,""
    # 多头排列: 5>10>20>60
    if not (s5>s10 and s10>s20 and s20>s60): return False,""
    # 全部向上（5日线角度>0，10日线角度>0）
    s5s=calc_sma_series(c,5);s10s=calc_sma_series(c,10)
    a5=calc_angle(s5s,5);a10=calc_angle(s10s,10)
    if a5 is None or a10 is None or a5<=0 or a10<=0: return False,""
    # 价格在5日线上方（不远离，3%以内）
    if tc<s5 or tc>s5*1.03: return False,""
    # 回测新增防守: 近5日最低价>10日线（趋势未破坏）
    if min(c[-5:]) < s10*0.98: return False,""
    # 量能阶梯: 近5日均量 > 近10日均量 > 近20日均量
    v5=sum(v[-5:])/5;v10=sum(v[-10:])/10;v20=sum(v[-20:])/20
    if not (v5>v10 and v10>v20): return False,""
    # 当日缩量或平量（不在放量冲高日买入）
    if v[-1]>v5*1.3: return False,""
    # RSI适中（45-65，排除高位超买）—— 回测后从70收紧到65
    if r6 is None or r6<45 or r6>65: return False,""
    # 买卖比≥1.0
    if bsr<1.0: return False,""
    # 当日涨幅<2%（不追高）—— 回测后新增
    if tp>2: return False,""
    # 近20天有涨停或大阳（有资金关注）
    z20,_=zt_count(c,20);b20=big_pct_count(c,20,7)
    if z20==0 and b20==0: return False,""
    if tc>=70 or tp<-5: return False,""
    return True,f"5/10/20/60多头排列+近5日守10日线+量能阶梯+RSI{r6:.1f}"

def cond8_breakout_pullback(c,v,lo,hi,tc,tp,r6,bsr,vr):
    """
    条件8: 放量异动后缩量回踩均线（量能异动型）—— v9.8重构版
    路线: 近5天出现2.5倍以上放量 → 今日回踩任一均线(5/10/20/30/60)或即将回踩 → 缩量<最大量70%
    """
    if len(c)<30 or len(v)<30: return False,""

    # 1. 近5天内出现2.5倍以上成交量（相对于前5日均量）
    surge_idx = -1
    max_vol = 0
    for i in range(-5, 0):
        v5_before = sum(v[max(0,i-5):i])/5 if i>0 else v[0]
        if v5_before > 0 and v[i] >= v5_before * 2.5:
            surge_idx = i
            max_vol = max(max_vol, v[i])
    if surge_idx == -1 or max_vol <= 0:
        return False,""

    # 2. 今日缩量 < 最大放量的70%
    if v[-1] >= max_vol * 0.70:
        return False,""

    # 3. 今日回踩任一均线(5/10/20/30/60)，或即将回踩(价在均线±2%内)
    ma_periods = [5, 10, 20, 30, 60]
    ma_touch = False
    ma_reason = ""
    for p in ma_periods:
        if len(c) < p: continue
        ma_val = sum(c[-p:]) / p
        if tc >= ma_val * 0.98 and tc <= ma_val * 1.03:
            ma_touch = True
            ma_reason = f"回踩MA{p}"
            break
        # 即将回踩: 昨日在均线上方，今日接近均线
        if len(c) >= p+1:
            ma_prev = sum(c[-(p+1):-1]) / p
            if c[-2] > ma_prev * 1.01 and tc >= ma_val * 0.95 and tc <= ma_val * 1.05:
                ma_touch = True
                ma_reason = f"即将回踩MA{p}"
                break
    if not ma_touch:
        return False,""

    # 4. 基本过滤
    if r6 is None or r6<35 or r6>75: return False,""
    if tc>=100 or tp<-8 or tp>15: return False,""

    return True,f"近5天放量{max_vol/sum(v[-10:-5])*5:.1f}倍后缩量{v[-1]/max_vol:.1%}，{ma_reason}"

def calc_kdj_series(closes, highs, lows, n=9):
    """计算KDJ序列（K=EMA(RSV,3), D=EMA(K,3), J=3K-2D）"""
    if len(closes) < n + 3: return [], [], []
    rsv_list = []
    for i in range(n - 1, len(closes)):
        h = max(highs[i-n+1:i+1])
        l = min(lows[i-n+1:i+1])
        if h == l:
            rsv = 50.0
        else:
            rsv = (closes[i] - l) / (h - l) * 100
        rsv_list.append(rsv)
    # K = EMA(RSV, 3), D = EMA(K, 3)
    k_list = []; d_list = []
    k_prev = 50.0; d_prev = 50.0
    alpha = 1.0 / 3.0  # EMA 3日平滑因子
    for rsv in rsv_list:
        k_prev = k_prev + alpha * (rsv - k_prev)
        d_prev = d_prev + alpha * (k_prev - d_prev)
        k_list.append(k_prev)
        d_list.append(d_prev)
    j_list = [3 * k_list[i] - 2 * d_list[i] for i in range(len(k_list))]
    return k_list, d_list, j_list

def check_ma_resonance(c, tc):
    """
    v9.3新增: 多级别均线共振检查
    检查5项均线共振条件，返回(共振项数, 详情列表)
    ① 5日线上穿10日线 或 5>10且5向上
    ② 5日线上穿20日线 或 5>20且5向上
    ③ 10日线上穿20日线 或 10>20且10向上
    ④ 5/10/20/30日线多头排列
    ⑤ 站上30日线
    """
    if len(c) < 35:
        return 0, []
    ma5 = calc_sma_series(c, 5)
    ma10 = calc_sma_series(c, 10)
    ma20 = calc_sma_series(c, 20)
    ma30 = calc_sma_series(c, 30)
    if len(ma5) < 2 or len(ma10) < 2 or len(ma20) < 2 or len(ma30) < 1:
        return 0, []

    m5_now, m5_prev = ma5[-1], ma5[-2]
    m10_now, m10_prev = ma10[-1], ma10[-2]
    m20_now, m20_prev = ma20[-1], ma20[-2]
    m30_now = ma30[-1]

    resonance = []

    # ① 5穿10 或 5>10且向上
    if (m5_prev <= m10_prev and m5_now > m10_now):
        resonance.append("5穿10")
    elif (m5_now > m10_now and m5_now > m5_prev):
        resonance.append("5>10↑")

    # ② 5穿20 或 5>20且向上
    if (m5_prev <= m20_prev and m5_now > m20_now):
        resonance.append("5穿20")
    elif (m5_now > m20_now and m5_now > m5_prev):
        resonance.append("5>20↑")

    # ③ 10穿20 或 10>20且向上
    if (m10_prev <= m20_prev and m10_now > m20_now):
        resonance.append("10穿20")
    elif (m10_now > m20_now and m10_now > m10_prev):
        resonance.append("10>20↑")

    # ④ 多头排列 5>10>20>30
    if m5_now > m10_now > m20_now > m30_now:
        resonance.append("多头排列")

    # ⑤ 站上30日线
    if tc > m30_now:
        resonance.append("站上30日线")

    return len(resonance), resonance


def cond9_macd_underwater_golden(c,v,tc,tp,r6,bsr,hi=None,lo=None):
    """
    条件9: MACD水上精准金叉 —— v9.4水上精筛版
    v9.4优化:
      1. 废弃9A(水下金叉DIF<0): 复盘证实水下金叉在单边跌时完全失效
      2. 仅保留水上金叉: 9B(零轴附近DIF:0~5) + 9C(水上首次DIF:0~15)
      3. 9B加均线共振≥1项(水上金叉必须均线配合)
      4. 9B DIF放宽到0~5(原0~3太窄仅22只)
    返回: (ok, reason, gap, tier, surge_info)
      tier: '9B'/'9C' (档位, 9A已废弃)
      surge_info: dict(涨停次数, 5日涨幅, 量比, 均线共振数, 均线全多头)
    """
    if len(c)<60: return False,"",None,None,None
    dif,dea,hist = calc_macd_series(c, f=6, s=13, sig=5)
    if len(dif)<25 or len(dea)<20: return False,"",None,None,None
    n = min(len(dif), len(dea))
    dif_a = dif[-n:]; dea_a = dea[-n:]

    # v9.4: 废弃水下金叉，DIF必须>0（水上）
    dif_now = dif_a[-1]
    if dif_now <= 0 or dif_now >= 15: return False,"",None,None,None

    # 均线共振检查（水上金叉必须均线配合）
    ma_count, ma_details = check_ma_resonance(c, tc)
    if ma_count < 1: return False,"",None,None,None

    # DIF底背离: DIF > 近22日最低点
    month_len = min(22, n)
    dif_min_month = min(dif_a[-month_len:])
    if dif_a[-1] <= dif_min_month: return False,"",None,None,None

    # KDJ提前金叉检测
    kdj_golden = False
    if hi is not None and lo is not None:
        k_list, d_list, j_list = calc_kdj_series(c, hi, lo, 9)
        if len(k_list) >= 3:
            k_now, d_now = k_list[-1], d_list[-1]
            k_prev, d_prev = k_list[-2], d_list[-2]
            if (k_prev < d_prev and k_now >= d_now) or (k_now > d_now and k_now > k_prev):
                kdj_golden = True

    # ===== 金叉检测与时效约束 =====
    cross_days_ago = None
    cross_count_20 = 0
    check_len = min(20, n-1)
    for i in range(n-1, n-check_len-1, -1):
        if i < 0: break
        if dif_a[i] < dea_a[i] and (i+1 < n and dif_a[i+1] >= dea_a[i+1]):
            cross_count_20 += 1
            if cross_days_ago is None:
                cross_days_ago = n - 1 - i

    gap = dea_a[-1] - dif_a[-1]
    is_golden = dif_a[-1] >= dea_a[-1]

    # ===== v9.4: 仅保留水上两档 =====
    tier = None
    status = 'none'

    if is_golden:
        if cross_days_ago is None:
            return False,"",None,None,None

        # 9B: 零轴附近金叉 (DIF: 0~12, 最近5天, RSI 30-65, 20天≤3次, 均线共振≥0)
        if 0 < dif_now < 12 and cross_days_ago <= 5 and 30 <= r6 <= 65 and cross_count_20 <= 3 and ma_count >= 0:
            tier = '9B'
            status = '零轴附近金叉'

        # 9C: 水上首次金叉 (DIF: 0~20, 最近7天, RSI 40-75, 20天≤2次, 均线共振≥1)
        elif 0 < dif_now < 20 and cross_days_ago <= 7 and 40 <= r6 <= 75 and cross_count_20 <= 2 and ma_count >= 1:
            tier = '9C'
            status = '水上首次金叉'
        else:
            return False,"",None,None,None
    else:
        # 未金叉——KDJ提前金叉抵消滞后（仅水上）
        if not kdj_golden:
            return False,"",None,None,None
        if gap > 0.05: return False,"",None,None,None
        if not (n>=2 and (dif_a[-1]-dea_a[-1]) <= (dif_a[-2]-dea_a[-2])):
            return False,"",None,None,None

        if 0 < dif_now < 12 and 30 <= r6 <= 65 and ma_count >= 0:
            tier = '9B'
            status = '零轴KDJ领先金叉'
        elif 0 < dif_now < 20 and 40 <= r6 <= 75 and ma_count >= 1:
            tier = '9C'
            status = '水上KDJ领先金叉'
        else:
            return False,"",None,None,None

    # 底背离检测
    price_low_20 = min(c[-20:]) if len(c)>=20 else min(c)
    price_low_40 = min(c[-40:]) if len(c)>=40 else price_low_20
    dif_low_20 = min(dif_a[-20:]) if n>=20 else min(dif_a)
    dif_low_40 = min(dif_a[-40:]) if n>=40 else dif_low_20
    has_divergence = (price_low_20 <= price_low_40 * 1.01) and (dif_low_20 > dif_low_40 * 0.9)

    # 量价配合（放宽：允许放量上涨）
    v3 = sum(v[-3:])/3; v10 = sum(v[-10:])/10
    if v3 > v10*2.0: return False,"",None,None,None

    # 买卖比（放宽）
    if bsr<0.8: return False,"",None,None,None

    # 价格区域（放宽）
    s20=calc_sma(c,20)
    if s20 and tc>s20*1.15: return False,"",None,None,None
    if tc>=100 or tp<-8 or tp>10: return False,"",None,None,None

    # ===== 拉升强度信息收集 =====
    limit_up_60 = 0
    if len(c) >= 60:
        for j in range(len(c)-60, len(c)):
            if j > 0 and (c[j]-c[j-1])/c[j-1]*100 > 9.5:
                limit_up_60 += 1

    pct_5d = 0
    if len(c) >= 6:
        pct_5d = (c[-1] - c[-6]) / c[-6] * 100

    # 均线全多头判断
    ma_all_bull = False
    ma5_s = calc_sma(c, 5); ma10_s = calc_sma(c, 10); ma20_s = calc_sma(c, 20); ma30_s = calc_sma(c, 30)
    if ma5_s and ma10_s and ma20_s and ma30_s:
        if ma5_s > ma10_s > ma20_s > ma30_s:
            if len(c) >= 31:
                ma5_p = calc_sma(c[:-1], 5); ma10_p = calc_sma(c[:-1], 10)
                if ma5_p and ma10_p:
                    if ma5_s > ma5_p and ma10_s > ma10_p:
                        ma_all_bull = True

    surge_info = {
        'limit_up_60': limit_up_60,
        'pct_5d': pct_5d,
        'ma_count': ma_count,
        'ma_all_bull': ma_all_bull,
        'kdj_golden': kdj_golden,
    }

    div_str = "+底背离" if has_divergence else ""
    kdj_str = "+KDJ金叉确认" if kdj_golden else ""
    ma_str = f"+均线共振{ma_count}项"
    reason = f"{tier} {status}{div_str}{kdj_str}{ma_str}+gap{gap:.4f}+DIF{dif[-1]:.3f}/DEA{dea[-1]:.3f}+RSI{r6:.1f}"

    return True, reason, gap, tier, surge_info

# 条件9的评分梯度（gap越小=越精准，加分越高）
def calc_sector_overheat(sector_name, all_candidates):
    """
    v9.4新增: 板块过热指数（高潮次日预警）
    返回: (overheat_level, warning_msg)
      overheat_level: 0=正常, 1=偏热, 2=过热(高潮次日)
    """
    # 判定是否为高潮板块（基于公开消息：涨停>5只 且 板块涨幅>3%）
    # 这里用HOT_SECTOR_NAMES中是否包含该板块作为粗略判定
    # 更精确的判定需要前日实时数据
    hot_sectors_with_boom = {
        "人形机器人": 44,  # 7/3 44股涨停，绝对高潮
        "商业航天": 5,     # 7/3 5股涨停
    }

    overheat = 0
    warning = ""

    if sector_name in hot_sectors_with_boom:
        count = hot_sectors_with_boom[sector_name]
        if count >= 20:
            overheat = 2
            warning = f"⚠️ {sector_name}前日{count}股涨停(高潮日)，次日分化风险极高"
        elif count >= 5:
            overheat = 1
            warning = f"⚠️ {sector_name}前日{count}股涨停，短期偏热需谨慎"

    return overheat, warning


def detect_rotation_direction(market_trend, market_style):
    """
    v9.4新增: 板块轮动方向探测器
    根据市场趋势和风格判断当前轮动方向
    返回: 'defense'(防御占优) / 'attack'(进攻占优) / 'rotation'(轮动中)
    """
    # 规则:
    # - 震荡市(大盘涨跌互现) + 价值风格 = 防御占优(资金从科技切向避险)
    # - 趋势上行 + 成长风格 = 进攻占优
    # - 暴跌市 = 防御占优(避险为主)
    # - 其他 = 轮动中

    if market_trend == 'crash':
        return 'defense'
    if market_trend == 'bear':
        return 'defense'
    if market_trend == 'range' and market_style.get('style') == 'value':
        return 'defense'
    if market_trend == 'bull' and market_style.get('style') == 'growth':
        return 'attack'

    return 'rotation'


# ============ v9.4高潮预警相关 ============
# 高潮板块名称（前日涨停>5只），这些板块在选股时自动降权
OVERHEAT_SECTORS = {
    "人形机器人": 2,   # 过热等级2(最高)
    "商业航天": 1,     # 过热等级1
}

def cond9_gap_bonus(gap):
    """根据DIF-DEA gap大小返回梯度加分"""
    if gap is None: return 0
    if gap <= 0: return 20  # 已金叉，满分
    if gap <= 0.005: return 18  # 即将金叉，gap<0.005
    if gap <= 0.01: return 15   # gap<0.01
    if gap <= 0.02: return 12   # gap<0.02
    return 8  # 其他已命中但gap较大


# ====== v9.2 新增: 条件10 — 强势回踩金叉（趋势追踪型入口） ======
# 针对人形机器人/商业航天/算力等主升浪板块，RSI允许55-80（强势区）
# 不追第一波拉升（可能是陷阱），等回踩时MACD/KDJ金叉确认再介入
# 与条件9互补: 条件9=底部反转(RSI30-65), 条件10=强势回踩(RSI55-80)
def cond10_strong_pullback_golden(c, v, tc, tp, r6, bsr, hi=None, lo=None, news_sectors=None):
    """
    条件10: 强势回踩金叉（趋势追踪型）—— v9.2新增
    路线: 主升浪板块回踩时MACD零轴上方金叉 + KDJ金叉确认 + 5d涨幅>5% + 量比>1.2
    核心逻辑:
      1. RSI 55-80（强势区，不排除主升浪个股）
      2. MACD参数6/13/5，DIF在零轴附近或上方（-2~30），刚金叉或即将金叉
      3. KDJ金叉确认（必须，强势股KDJ更敏感）
      4. 5d涨幅>5%（确认资金认可度，非冷门票）
      5. 量比>1.0（交投活跃）
      6. 站上20日线（趋势未破）
      7. 近10天有涨停或大阳>7%（有主力资金介入）
    入场时机: 等回踩——当日涨幅<3%（不追高），缩量或平量（量比<2.5）
    """
    if len(c) < 60: return False, "", None
    # RSI 55-80（强势区核心）
    if r6 is None or r6 < 55 or r6 > 80: return False, "", None
    # MACD 6/13/5
    dif, dea, hist = calc_macd_series(c, f=6, s=13, sig=5)
    if len(dif) < 25 or len(dea) < 20: return False, "", None
    n = min(len(dif), len(dea))
    dif_a = dif[-n:]; dea_a = dea[-n:]
    # DIF范围: -2~30（零轴附近或上方，区别于条件9的-10~25水下）
    if dif_a[-1] <= -2 or dif_a[-1] >= 30: return False, "", None
    # KDJ金叉检测（必须）
    kdj_golden = False
    if hi is not None and lo is not None:
        k_list, d_list, j_list = calc_kdj_series(c, hi, lo, 9)
        if len(k_list) >= 3:
            k_now, d_now = k_list[-1], d_list[-1]
            k_prev, d_prev = k_list[-2], d_list[-2]
            if (k_prev < d_prev and k_now >= d_now) or (k_now > d_now and k_now > k_prev):
                kdj_golden = True
    if not kdj_golden: return False, "", None
    # MACD状态判定: 刚金叉 或 即将金叉（gap<0.03，KDJ已确认所以放宽）
    status = 'none'
    gap = dea_a[-1] - dif_a[-1]  # 正数=DIF<DEA
    if dif_a[-1] >= dea_a[-1]:
        # 已金叉
        if n >= 3 and dif_a[-2] < dea_a[-2]:
            status = 'golden_fresh'  # 刚金叉（1-2天内）
        elif n >= 4 and dif_a[-3] < dea_a[-3] and dif_a[-2] >= dea_a[-2]:
            status = 'golden_fresh'
        else:
            # 已金叉多日，检查是否在零轴上方且柱状线缩短（回踩结束信号）
            if dif_a[-1] > 0 and n >= 3:
                # 柱状线缩短即可（不要求连续2天缩短，强势股1天缩短也可能是回踩结束）
                if hist[-1] < hist[-2]:
                    status = 'pullback_end'  # 柱状线缩短，回踩信号
                else:
                    return False, "", None
            else:
                return False, "", None
    else:
        # MACD尚未金叉，但KDJ已金叉（KDJ领先确认）
        if gap > 0.03: return False, "", None  # gap太大，金叉无望
        if n >= 2 and (dif_a[-1] - dea_a[-1]) <= (dif_a[-2] - dea_a[-2]):
            status = 'kdj_lead_cross'
        else:
            return False, "", None
    # 5d涨幅>5%（资金认可度）
    if len(c) >= 6:
        pct_5d = (c[-1] - c[-6]) / c[-6] * 100
        if pct_5d < 5: return False, "", None
    else:
        return False, "", None
    # 站上20日线（趋势未破）
    s20 = calc_sma(c, 20)
    if s20 is None or tc < s20: return False, "", None
    # 近10天有涨停或大阳>7%（主力资金介入证据）
    z10, _ = zt_count(c, 10)
    b10 = big_pct_count(c, 10, 7)
    if z10 == 0 and b10 == 0: return False, "", None
    # 量比>1.0（交投活跃）
    vr = None
    if news_sectors is None: news_sectors = []
    # 当日不追高: 涨幅<5%（强势股允许3-5%，但不超过5%）
    if tp > 5: return False, "", None
    # 量价配合: 近3日均量不暴增（非冲高放量日）
    v3 = sum(v[-3:]) / 3; v10 = sum(v[-10:]) / 10
    if v10 > 0 and v3 > v10 * 1.8: return False, "", None  # 放量冲高，等回踩
    # 买卖比≥0.9
    if bsr < 0.9: return False, "", None
    # 价格上限
    if tc >= 100: return False, "", None
    if tp < -5: return False, "", None

    div_str = ""
    # 回踩幅度检查（如果是从高位回落）
    if len(c) >= 10:
        high_10 = max(c[-10:])
        pullback_pct = (high_10 - tc) / high_10 * 100
        if pullback_pct > 3:
            div_str = f"+回踩{pullback_pct:.1f}%"

    kdj_str = "+KDJ金叉确认"
    return True, f"强势金叉{status}{div_str}{kdj_str}+gap{gap:.4f}+DIF{dif[-1]:.3f}/DEA{dea[-1]:.3f}+RSI{r6:.1f}", gap


def cond10_gap_bonus(gap):
    """条件10的gap梯度加分（略低于条件9，因为趋势股本身波动大）"""
    if gap is None: return 0
    if gap <= 0: return 15  # 已金叉
    if gap <= 0.01: return 12  # 即将金叉
    if gap <= 0.02: return 8
    return 5


# ====== v9.2 新增: 消息面热点专属通道 ======
# 当个股命中TOP热点板块时，放宽技术门槛
# 核心: 涨停≥1次（用户要求） 或 5d涨幅>8%，用资金认可度替代技术低位要求
# RSI上限放宽到80（允许主升浪个股），但仍需有技术配合
HOT_SECTOR_NAMES = {"人形机器人", "商业航天", "黄金/贵金属", "稀土永磁",
                    "半导体设备/材料", "算力基础设施", "创新药", "存储芯片",
                    "算力/AI服务器", "光模块/CPO"}

def hot_sector_channel(c, v, tc, tp, r6, bsr, hi, lo, news_sectors, limit_up_count, max_pct_5d):
    """
    消息面热点专属通道 —— v9.2新增
    当个股命中TOP热点板块时:
      1. 涨停≥1次 或 5d涨幅>8%（资金认可度确认，用户要求涨停≥1）
      2. RSI放宽到30-80（允许强势区，不追极端超买>80）
      3. MACD+KDJ至少有一个金叉信号
      4. 站上20日线（趋势未破）
      5. 量比≥0.8（有交投）
      6. 当日涨幅<5%（不追涨停板）
    """
    if not news_sectors: return False, "", None
    # 必须命中HOT板块
    hot_matched = [s for s in news_sectors if s in HOT_SECTOR_NAMES]
    if not hot_matched: return False, "", None
    # 资金认可度: 涨停≥1 或 5d涨幅>8%
    if limit_up_count < 1 and max_pct_5d < 8: return False, "", None
    # RSI 30-80（放宽上限）
    if r6 is None or r6 < 30 or r6 > 80: return False, "", None
    # MACD 6/13/5
    dif, dea, hist = calc_macd_series(c, f=6, s=13, sig=5)
    if len(dif) < 20 or len(dea) < 15: return False, "", None
    n = min(len(dif), len(dea))
    dif_a = dif[-n:]; dea_a = dea[-n:]
    gap = dea_a[-1] - dif_a[-1]
    # MACD状态: 刚金叉 或 即将金叉（gap<0.03） 或 DIF在零轴上方且柱状线缩短
    macd_signal = False
    macd_status = 'none'
    if dif_a[-1] >= dea_a[-1]:
        if n >= 3 and dif_a[-2] < dea_a[-2]:
            macd_signal = True; macd_status = 'golden_fresh'
        elif dif_a[-1] > 0 and n >= 3 and hist[-1] < hist[-2]:
            macd_signal = True; macd_status = 'above_zero'
    else:
        if gap < 0.03 and n >= 2 and (dif_a[-1] - dea_a[-1]) > (dif_a[-2] - dea_a[-2]):
            macd_signal = True; macd_status = 'about_to_cross'
    # KDJ检测
    kdj_golden = False
    if hi is not None and lo is not None:
        k_list, d_list, j_list = calc_kdj_series(c, hi, lo, 9)
        if len(k_list) >= 3:
            k_now, d_now = k_list[-1], d_list[-1]
            k_prev, d_prev = k_list[-2], d_list[-2]
            if (k_prev < d_prev and k_now >= d_now) or (k_now > d_now and k_now > k_prev):
                kdj_golden = True
    # MACD和KDJ至少一个有信号（热点通道放宽: 允许只有KDJ金叉）
    if not macd_signal and not kdj_golden: return False, "", None
    # 站上20日线
    s20 = calc_sma(c, 20)
    if s20 is None or tc < s20 * 0.97: return False, "", None  # 允许略低于20日线3%
    # 量比≥0.8
    # 买卖比≥0.85（热点股可以略低，有消息面驱动）
    if bsr < 0.85: return False, "", None
    # 当日涨幅<5%（不追涨停板）
    if tp > 5 or tp < -5: return False, "", None
    # 价格上限
    if tc >= 100: return False, "", None

    sig_parts = []
    if macd_signal: sig_parts.append(f"MACD{macd_status}")
    if kdj_golden: sig_parts.append("KDJ金叉")
    if limit_up_count >= 1: sig_parts.append(f"涨停{limit_up_count}次")
    if max_pct_5d >= 8: sig_parts.append(f"5d涨{max_pct_5d:.1f}%")
    hot_str = "/".join(hot_matched[:2])

    return True, f"热点通道[{hot_str}]({'+'.join(sig_parts)})+RSI{r6:.1f}+DIF{dif[-1]:.3f}", gap


def match_news_sector(name, sector_kw=None):
    """
    消息面板块匹配（v9.5优化：使用动态消息面数据）
    sector_kw: 传入的板块字典，默认使用全局NEWS_SECTOR_KW
    """
    matched = []
    name_lower = name.lower()
    sectors = sector_kw if sector_kw is not None else NEWS_SECTOR_KW
    for sector, info in sectors.items():
        for kw in info.get('keywords', []):
            kw_lower = kw.lower()
            if kw in name or kw_lower in name_lower:
                matched.append((sector, info['bonus'], info['style']))
                break
    return matched

# ============ 基本面加分计算 ============
def calc_fundamental_bonus(c, td, fin_data, name):
    """
    计算5维度基本面加分 — v9.5优化版
    返回: (total_bonus, detail_dict, veto_reason)
      veto_reason: 如果触发了硬性排除条件，返回排除原因字符串；否则返回None
    """
    bonus = 0
    detail = {
        'basic': 0,      # 个股基本面（PE+资产负债率）
        'recognition': 0, # 辨识度
        'heat': 0,        # 热度
        'contract': 0,    # 合同负债率
        'growth': 0,      # 业绩增长
    }

    # ============ v9.5: 硬性排除条件（一票否决） ============
    # 1. 业绩暴雷排除：净利润同比大幅下滑
    profit_yoy = fin_data.get('profit_yoy')
    if profit_yoy is not None and profit_yoy < -50:
        return -999, detail, f"净利润同比暴跌{profit_yoy:.1f}%"

    # 2. 高负债排除：资产负债率>85%（财务风险极高）
    debt = fin_data.get('debt_ratio')
    if debt is not None and debt > 85:
        return -999, detail, f"资产负债率过高{debt:.1f}%"

    # 3. 亏损股处理：PE<0 — v10.1优化
    # 3-15天短线交易，PE<0不是涨跌的关键决定因素。
    # 题材驱动、资金控盘、技术形态才是短线核心。
    # 改为扣分制(-10分)，不再一票否决，避免错杀高弹性题材票。
    pe = td.get('pe', 0)
    if pe is not None and pe < 0:
        bonus -= 10
        detail['basic'] -= 10
        # 保留标记但不否决，让后续涨停基因/技术信号有机会覆盖

    # 4. 营收营收双降排除
    rev_yoy = fin_data.get('revenue_yoy')
    if rev_yoy is not None and profit_yoy is not None:
        if rev_yoy < -30 and profit_yoy < -30:
            return -999, detail, f"营收同比{rev_yoy:.1f}%且净利润同比{profit_yoy:.1f}%双降"

    # ============ v9.6: 正常加分逻辑（权重翻倍） ============
    # 1. 个股基本面（PE合理性 + 资产负债率）
    if pe and pe > 0:
        if 5 < pe <= 15: bonus += 16; detail['basic'] += 16     # 低PE优质 (8→16)
        elif 15 < pe <= 25: bonus += 12; detail['basic'] += 12   # 合理PE (6→12)
        elif 25 < pe <= 40: bonus += 6; detail['basic'] += 6    # 偏高但可接受 (3→6)
        elif 40 < pe <= 80: bonus += 2; detail['basic'] += 2    # 高PE (1→2)
        elif pe > 200: bonus -= 6; detail['basic'] -= 6         # 极高PE扣分 (3→6)
    if debt is not None:
        if debt < 30: bonus += 8; detail['basic'] += 8          # 低负债 (4→8)
        elif 30 <= debt < 50: bonus += 4; detail['basic'] += 4   # (2→4)
        elif debt > 80: bonus -= 6; detail['basic'] -= 6       # 高负债扣分 (3→6)
    roe = fin_data.get('roe')
    if roe is not None:
        if roe > 20: bonus += 10; detail['basic'] += 10         # 高ROE (5→10)
        elif roe > 10: bonus += 6; detail['basic'] += 6          # (3→6)
        elif roe > 5: bonus += 2; detail['basic'] += 2           # (1→2)

    # 2. 辨识度（行业龙头/细分龙头/名称含核心关键词）
    is_leader = False
    for cat, leaders in LEADER_KEYWORDS.items():
        for ldr in leaders:
            if ldr in name or name in ldr:
                bonus += 15; detail['recognition'] += 15; is_leader = True  # (10→15)
                break
        if is_leader: break
    if not is_leader:
        for kw in NICHE_KEYWORDS:
            if kw in name:
                bonus += 8; detail['recognition'] += 8          # (5→8)
                break

    # 3. 热度（换手率合理区间 + 量比 + 资金关注度）
    to_val = td.get('turnover', 0)
    if 3 <= to_val <= 8: bonus += 8; detail['heat'] += 8         # 适中换手 (5→8)
    elif 8 < to_val <= 15: bonus += 5; detail['heat'] += 5      # 活跃 (3→5)
    elif 1 < to_val < 3: bonus += 4; detail['heat'] += 4        # 偏低但可接受 (2→4)
    elif to_val > 20: bonus -= 4; detail['heat'] -= 4          # 过度活跃扣分 (2→4)
    vr = td.get('vol_ratio', 0)
    if 0.8 <= vr <= 2.0: bonus += 5; detail['heat'] += 5        # 量比适中 (3→5)
    elif 2.0 < vr <= 3.0: bonus += 2; detail['heat'] += 2        # (1→2)
    nbv = td.get('net_buy_vol', 0)
    if nbv > 50000: bonus += 6; detail['heat'] += 6             # 净买入大 (4→6)
    elif nbv > 10000: bonus += 3; detail['heat'] += 3            # (2→3)
    elif nbv > 0: bonus += 1; detail['heat'] += 1

    # 4. 合同负债率（预收账款/合同负债同比增长）
    contract_yoy = fin_data.get('contract_liab_yoy')
    if contract_yoy is not None:
        if contract_yoy > 50: bonus += 12; detail['contract'] += 12   # 高增长 (8→12)
        elif contract_yoy > 20: bonus += 8; detail['contract'] += 8    # (6→8)
        elif contract_yoy > 0: bonus += 5; detail['contract'] += 5     # (3→5)
        elif contract_yoy < -20: bonus -= 4; detail['contract'] -= 4   # 大幅下降扣分 (2→4)

    # 5. 业绩增长（营收+净利润同比）
    if rev_yoy is not None and profit_yoy is not None:
        if rev_yoy > 30 and profit_yoy > 50: bonus += 16; detail['growth'] += 16  # 高增长 (10→16)
        elif rev_yoy > 15 and profit_yoy > 25: bonus += 10; detail['growth'] += 10
        elif rev_yoy > 0 and profit_yoy > 15: bonus += 6; detail['growth'] += 6
        elif profit_yoy < -20: bonus -= 5; detail['growth'] -= 5   # 业绩下滑扣分 (3→5)
    elif profit_yoy is not None:
        if profit_yoy > 50: bonus += 8; detail['growth'] += 8      # (5→8)
        elif profit_yoy > 20: bonus += 5; detail['growth'] += 5    # (3→5)
        elif profit_yoy < -20: bonus -= 4; detail['growth'] -= 4   # (2→4)

    return round(bonus, 1), detail, None


# ============ 主流程 ============
def main():
    import os as _os
    FAST_MODE = _os.environ.get('FAST_MODE', '0') == '1'
    t0=time.time()
    mode_label = "⚡极速模式(跳过基本面)" if FAST_MODE else "完整模式"
    print(f"选股扫描 v10.6 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [{mode_label}]")
    print("v10.6优化: K线缓存加速 + 极速模式 + v10.5竞价强度 + v10.4动态权重")
    print("="*70)

    stocks=get_mainboard_stocks()
    print(f"主板非ST: {len(stocks)}只")

    tencent=get_tencent_batch([s['code'] for s in stocks])
    print(f"腾讯行情: {len(tencent)}只")

    # 盘前/周末检测：腾讯API无数据时使用K线收盘价
    use_kline_price = False
    if len(tencent) == 0 or all(v.get('price', 0) <= 0 for v in tencent.values()):
        print("⚠️ 检测到盘前/周末，腾讯行情无数据，将使用K线最后收盘价作为当前价")
        use_kline_price = True

    # ====== 风格判断 ======
    if not use_kline_price:
        style, v_avg, g_avg = detect_market_style(tencent)
        style_cn = "价值/周期/低位" if style == 'value' else "成长/科技/高位"
        print(f"市场风格判断: {style_cn} (价值均涨{v_avg:+.2f}% vs 成长均涨{g_avg:+.2f}%)")
    else:
        style = 'growth'  # 盘前无法判断风格，默认成长
        print(f"市场风格判断: 盘前无法判断，默认成长风格")

    # ====== 大盘趋势检测 + v9.7风控评估 ======
    if not use_kline_price:
        idx_tencent = get_tencent_batch(['000001'])
        trend, risk_level, risk_reason, up_ratio = detect_market_trend({**tencent, **idx_tencent})
    else:
        trend, risk_level, risk_reason, up_ratio = 'range', 0, '盘前无法判断', 0.5
    trend_cn = {'bull':'🟢上涨日','bear':'🟡下跌日','crash':'🔴暴跌日','range':'🔵震荡日'}[trend]
    risk_labels = {0: '✅正常', 1: '⚠️谨慎', 2: '🔴高风险', 3: '🚨建议空仓'}
    print(f"大盘趋势判断: {trend_cn}")
    print(f"v9.7风险评估: {risk_labels.get(risk_level, '未知')} | {risk_reason}")
    
    # v9.7: 风控参数（影响选股策略）
    if risk_level >= 3:
        print("\n🚨 v9.7风控触发: 大盘暴跌/普跌，建议空仓观望！")
        print("  仅保留防御性标的供参考，不推荐买入。")
        risk_mode = 'stop'  # 空仓模式
    elif risk_level >= 2:
        print("\n🔴 v9.7风控提醒: 高风险环境，降低仓位至30%以下")
        risk_mode = 'caution'  # 谨慎模式
    elif risk_level >= 1:
        risk_mode = 'watch'  # 观望模式
    else:
        risk_mode = 'normal'  # 正常模式

    # v10.4: 市场情绪量化检测
    if not use_kline_price:
        mood_score, mood_label, mood_details = detect_market_mood({**tencent, **idx_tencent})
        mood_labels_cn = {'panic':'😱极度恐慌', 'caution':'😰谨慎', 'neutral':'😐中性', 'optimistic':'😊乐观', 'greedy':'🤑贪婪'}
        print(f"v10.4市场情绪: {mood_labels_cn.get(mood_label, mood_label)} (分数:{mood_score})")
        print(f"  涨跌比:{mood_details.get('up_ratio',0):.1%} | 涨停:{mood_details.get('zt_count',0)} | 跌停:{mood_details.get('dt_count',0)} | 大盘:{mood_details.get('avg_pct',0):+.2f}%")
    else:
        mood_score, mood_label = 50, 'neutral'

    # ====== v9.5: 实时消息面采集（替代硬编码关键词） ======
    try:
        from news_fetcher import fetch_realtime_news, analyze_news_to_sectors, generate_sector_report, export_to_scan_format
        realtime_news = fetch_realtime_news()
        realtime_sectors = analyze_news_to_sectors(realtime_news)
        # 导出为选股系统格式
        realtime_kw = export_to_scan_format(realtime_sectors)
        if realtime_kw and len(realtime_kw) >= 5:
            # 合并: 实时数据为主，硬编码兜底
            current_sector_kw = {}
            for sector, info in realtime_kw.items():
                current_sector_kw[sector] = info
            # 保留硬编码中没有被实时覆盖的板块（兜底）
            for sector, info in NEWS_SECTOR_KW.items():
                if sector not in current_sector_kw:
                    current_sector_kw[sector] = {k: v for k, v in info.items() if k in ('bonus', 'style')}
                    current_sector_kw[sector]['keywords'] = info.get('keywords', [])
            print(f"\n📡 实时消息面: 采集{len(realtime_news)}条新闻, 命中{len(realtime_kw)}个板块, 合并后{len(current_sector_kw)}个板块")
            generate_sector_report(realtime_sectors, min_count=2)
        else:
            print("\n📡 实时消息面采集量不足，使用硬编码板块（兜底）")
            current_sector_kw = dict(NEWS_SECTOR_KW)
    except ImportError:
        print("\n📡 未找到news_fetcher模块，使用硬编码板块")
        current_sector_kw = dict(NEWS_SECTOR_KW)
    except Exception as e:
        print(f"\n📡 消息面采集异常: {e}，使用硬编码板块")
        current_sector_kw = dict(NEWS_SECTOR_KW)

    print("\n📰 消息面板块（含风格标注）:")
    for sector, info in sorted(current_sector_kw.items(), key=lambda x:-x[1]['bonus']):
        style_tag = "🟡价值" if info['style']=='value' else "🔵成长"
        adj = ""
        if info['style'] == style: adj = " ×1.5(顺风格)"
        else: adj = " ×0.5(逆风格)"
        # v9.4: 高潮预警标注
        overheat_tag = ""
        if sector in OVERHEAT_SECTORS:
            lv = OVERHEAT_SECTORS[sector]
            overheat_tag = f" 🔥高潮Lv{lv}(降权)"
        print(f"  [{info['bonus']}] {style_tag} {sector}{adj}{overheat_tag}")

    # v9.4: 轮动方向探测
    rotation_dir = detect_rotation_direction(trend, {'style': style})
    rotation_label = {'defense': '🛡️防御占优', 'attack': '⚔️进攻占优', 'rotation': '🔄轮动中'}.get(rotation_dir, '轮动中')
    print(f"\n🔄 轮动方向: {rotation_label} (趋势:{trend}, 风格:{style})")

    pf=[]
    for s in stocks:
        td=tencent.get(s['code'])
        tc = tp = 0
        if td:
            tc=td['price'];tp=td['pct']
        if use_kline_price or not td or tc<=0:
            # 盘前模式：获取K线数据，用最后收盘价作为当前价
            raw=get_sina_kline(s['code'], 5)
            if not raw or len(raw['closes']) < 2: continue
            c = raw['closes']
            tc = c[-1]  # 最后收盘价作为当前价
            tp = (c[-1] - c[-2]) / c[-2] * 100  # 计算涨跌幅
            if not td: td = {}
            td['price'] = tc; td['pct'] = tp
            # 盘前无法获取的指标设为默认值
            td.setdefault('buy_sell_ratio', 0)
            td.setdefault('vol_ratio', 0)
            td.setdefault('turnover', 0)
            td.setdefault('pe', 0)
            td.setdefault('pb', 0)
            td.setdefault('circ_mv', 0)
            td.setdefault('circ_shares', 0)
            td.setdefault('net_buy_vol', 0)
            td.setdefault('buy_vol', 0)
            td.setdefault('sell_vol', 0)
            td.setdefault('amount', 0)
            td.setdefault('last_close', c[-2] if len(c) >= 2 else tc)
            td.setdefault('open_price', 0)  # v10.5: 盘前无开盘价
        # v9.7: 腾讯API可能返回空name，始终用akshare的name兜底
        if not td: td = {}
        td['name'] = s.get('name', '') or td.get('name', '')
        if tc<=0 or tc>=70 or tp<-8 or tp>20: continue  # v10.0: tp上限从15放宽到20（允许20cm涨停股进入候选）
        s['tc']=tc;s['tp']=tp;s['td']=td
        pf.append(s)
    print(f"\n预筛选: {len(pf)}只 (盘前模式: {use_kline_price})")

    # v9.8: 并发批量获取K线（全量扫描加速）
    print(f"  并发获取{len(pf)}只股票K线数据...")
    klines_batch = get_sina_klines_batch([s['code'] for s in pf], datalen=150, max_workers=20)
    print(f"  K线获取完成: {len(klines_batch)}/{len(pf)}只")

    candidates=[];proc=0
    for s in pf:
        proc+=1
        if proc%200==0: print(f"  分析:{proc}/{len(pf)} 候选:{len(candidates)}")
        raw=klines_batch.get(s['code'])
        if not raw: continue
        kd=raw
        c=kd['closes'];v=kd['volumes'];lo=kd['lows'];hi=kd['highs']
        tc=s['tc'];tp=s['tp']
        r6=calc_rsi(c,6)
        td=s['td']
        bsr=td.get('buy_sell_ratio',0);vr=td.get('vol_ratio',0);to=td.get('turnover',0);pe=td.get('pe',0);circ=td.get('circ_shares',0)
        turnovers=[]
        if circ and circ>0:
            for x in v: turnovers.append(x*100/circ*100)

        mcs=[];mrs=[]; cond9_gap_val = None
        ok,r=cond1(c,tc,tp,r6)
        if ok: mcs.append(1); mrs.append(r)
        ok,r=cond2(c,v,tc,tp,r6,vr,circ,to)
        if ok: mcs.append(2); mrs.append(r)
        ok,r=cond3_optimized(c,v,lo,turnovers,tc,tp,r6,bsr)
        if ok: mcs.append(3); mrs.append(r)
        ok,r=cond4(c,tc,tp,r6,up_ratio)
        if ok: mcs.append(4); mrs.append(r)
        # 条件5(大单净买)已取消
        ok,r=cond6(c,v,lo,hi,tc,tp)
        if ok: mcs.append(6); mrs.append(r)
        # 新增3条条件
        ok,r=cond7_ma_multiline(c,v,tc,tp,r6,bsr)
        if ok: mcs.append(7); mrs.append(r)
        ok,r=cond8_breakout_pullback(c,v,lo,hi,tc,tp,r6,bsr,vr)
        if ok: mcs.append(8); mrs.append(r)
        cond9_gap_val = None
        cond9_tier = None
        cond9_surge = None
        ok,r,gap,tier,surge_info=cond9_macd_underwater_golden(c,v,tc,tp,r6,bsr,hi,lo)
        if ok: mcs.append(9); mrs.append(r); cond9_gap_val = gap; cond9_tier = tier; cond9_surge = surge_info

        # v9.2新增: 条件10 — 强势回踩金叉（趋势追踪型入口）
        # 预先获取消息面匹配（条件10需要知道是否是热点股）
        news_matches = match_news_sector(s.get('name',td.get('name','')), sector_kw=current_sector_kw)
        news_sector_names = [m[0] for m in news_matches]
        cond10_gap_val = None
        ok,r,gap=cond10_strong_pullback_golden(c,v,tc,tp,r6,bsr,hi,lo,news_sector_names)
        if ok: mcs.append(10); mrs.append(r); cond10_gap_val = gap

        # 条件11(涨停基因)已取消

        # v9.8新增: 条件12 — 超跌反弹
        ok,r=cond12_oversold_bounce(c,tc,tp,r6)
        if ok: mcs.append(12); mrs.append(r)

        # v10.0: 条件13/14 评估
        if SURGE_MODULE_LOADED:
            c13_hit, c13_desc = cond13(c, v, lo, hi, tc, tp, r6, bsr, vr, to)
            if c13_hit:
                mcs.append(13)
                mrs.append(c13_desc)
            
            c14_hit, c14_desc = cond14(c, v, lo, hi, tc, tp, r6, vr)
            if c14_hit:
                mcs.append(14)
                mrs.append(c14_desc)

        # v10.2: 条件15-18 评估（基于近60天涨停板回溯分析的高胜率形态）
        if SURGE_MODULE_LOADED:
            c15_hit, c15_desc = cond15_shrink_wash(c, v, hi, lo, tc, tp, r6)
            if c15_hit:
                mcs.append(15)
                mrs.append(c15_desc)
            
            c16_hit, c16_desc = cond16_small_yang_charge(c, v, tc, tp, r6)
            if c16_hit:
                mcs.append(16)
                mrs.append(c16_desc)
            
            c17_hit, c17_desc = cond17_breakout_high(c, v, hi, tc, tp, r6)
            if c17_hit:
                mcs.append(17)
                mrs.append(c17_desc)
            
            c18_hit, c18_desc = cond18_high_amp_wash(c, v, hi, lo, tc, tp, r6)
            if c18_hit:
                mcs.append(18)
                mrs.append(c18_desc)

            # v10.3: 条件19 — 强势股反包型（博通集成模式）
            c19_hit, c19_desc = cond19(c, v, lo, hi, tc, tp, r6)
            if c19_hit:
                mcs.append(19)
                mrs.append(c19_desc)

        # 计算涨停基因（60天涨停次数）和拉升幅度（最近5天最大涨幅）
        limit_up_count = 0
        max_pct_5d = 0
        if len(c) >= 5:
            for j in range(max(0, len(c)-60), len(c)):
                if j > 0:
                    pct = (c[j] - c[j-1]) / c[j-1] * 100
                    if pct > 9.5: limit_up_count += 1
            # 最近5天最大涨幅
            for j in range(max(0, len(c)-5), len(c)):
                if j > 0:
                    pct = (c[j] - c[j-1]) / c[j-1] * 100
                    max_pct_5d = max(max_pct_5d, pct)

        # v9.2新增: 消息面热点专属通道
        hot_channel_hit = False
        hot_channel_reason = ""
        hot_channel_gap = None
        ok,r,gap = hot_sector_channel(c,v,tc,tp,r6,bsr,hi,lo,news_sector_names,limit_up_count,max_pct_5d)
        if ok:
            hot_channel_hit = True
            hot_channel_reason = r
            hot_channel_gap = gap

        # v10.1: 硬性市值/股本过滤 — 大蓝筹弹性不足，不适合次日>5%目标
        circ_mv = td.get('circ_mv', 0)  # 流通市值(亿元)
        circ_shares_val = circ / 1e8 if circ else 0  # 流通股本(亿股)，腾讯API返回的是股数
        if circ_mv > 500 or circ_shares_val > 15:
            continue  # 直接跳过：市值>500亿或流通股本>15亿，弹性不足

        # 有选股条件 或 有消息面匹配的 或 命中热点通道的，都进入候选池
        if mcs or news_matches or hot_channel_hit:
            candidates.append({
                'code':s['code'],'name':s.get('name',td.get('name','')),
                'close':tc,'pct':tp,'last_close':td.get('last_close',0),
                'open_price':td.get('open_price',0),
                'rsi6':r6,'vol_ratio':vr,'turnover':to,'pe':pe,
                'pb':td.get('pb',0),'circ_mv':circ_mv,
                'buy_sell_ratio':bsr,'net_buy_vol':td.get('net_buy_vol',0),
                'buy_vol':td.get('buy_vol',0),'sell_vol':td.get('sell_vol',0),
                'circ_shares':circ_shares_val,'amount':td.get('amount',0),
                'conditions':mcs,'reasons':mrs,
                'news_matches':news_matches,
                'cond9_gap': cond9_gap_val,
                'cond9_tier': cond9_tier,
                'cond9_surge': cond9_surge,
                'cond10_gap': cond10_gap_val,
                'hot_channel_hit': hot_channel_hit,
                'hot_channel_reason': hot_channel_reason,
                'hot_channel_gap': hot_channel_gap,
                'limit_up_count': limit_up_count,
                'max_pct_5d': max_pct_5d,
                # v10.0: 传入K线数据给涨停基因模块
                'c': list(c), 'v': list(v), 'hi': list(hi), 'lo': list(lo),
            })

    print(f"候选池: {len(candidates)}只, 耗时{time.time()-t0:.0f}秒")

    # ====== 基本面数据获取（有条件或有消息面的候选股都获取） ======
    # v10.6: 极速模式跳过基本面分析（节省40秒）
    if FAST_MODE:
        print(f"\n⚡ 极速模式: 跳过基本面数据获取")
        for c in candidates:
            c['fin_data'] = None
        fin_count = 0
    else:
        print(f"\n📊 获取基本面数据（{len([c for c in candidates if c['conditions'] or c.get('news_matches')])}只候选）...")
        fin_count = 0
        for c in candidates:
            if not c['conditions'] and not c.get('news_matches'): continue  # 无条件且无消息面的跳过
            fin_data = get_sina_finance(c['code'])
            c['fin_data'] = fin_data
            fin_count += 1
            if fin_count % 50 == 0: print(f"  基本面: {fin_count}")
            time.sleep(0.02)  # 控制请求频率(加速)
        print(f"  基本面数据获取完成: {fin_count}只")

    # ====== v9.5: 前瞻性事件日历采集 ======
    upcoming_events = None
    try:
        from event_calendar import fetch_upcoming_events, score_event_impact, generate_event_report
        upcoming_events = fetch_upcoming_events(days_ahead=7)
        if upcoming_events:
            generate_event_report(upcoming_events, min_strength=2)
    except ImportError:
        print("\n  未找到event_calendar模块，跳过事件日历")
    except Exception as e:
        print(f"\n  事件日历采集异常: {e}")

    # ====== v10.4评分体系：动态权重(走势+情绪) + 组合奖励 + 走势适配 ======
    # v10.4: 先获取基础动态权重（基于走势+情绪，默认板块温度warm）
    cw = get_dynamic_weights(trend, risk_mode=risk_mode, mood_score=mood_score, sector_temp='warm')
    print(f"\n📊 v10.4动态权重 (趋势:{trend} | 情绪:{mood_label}):")
    cond_names = {1:'10日线',2:'缩量回调',3:'趋势上行',4:'强势型',5:'大单净买',
                  6:'涨停回踩',7:'均线多头',8:'突破回踩',9:'MACD金叉',10:'强势回踩',
                  11:'涨停基因',12:'超跌反弹',13:'启动型',14:'加速突破',
                  15:'缩量洗盘',16:'小阳蓄势',17:'突破前高',18:'高振幅洗盘',
                  19:'强势反包'}  # v10.3: 新增强势股反包型
    for cid, w in sorted(cw.items(), key=lambda x:-x[1]):
        if w > 0:
            print(f"  条件{cid}({cond_names[cid]}): 权重{w}")
        else:
            print(f"  条件{cid}({cond_names[cid]}): ❌已禁用")

    for c in candidates:
        sc=0
        bsr=c.get('buy_sell_ratio',0)
        news_sectors=[];news_bonus_raw=0;news_styles=[]

        # v10.4: 检测该股票消息板块的温度
        sector_temps = []
        for (sector, bonus, s_style) in c.get('news_matches',[]):
            stemp = detect_sector_temperature(candidates, sector_name=sector)
            sector_temps.append(stemp)
        primary_sector_temp = sector_temps[0] if sector_temps else 'warm'
        c['sector_temp'] = primary_sector_temp  # 保存用于输出展示

        # v10.4: 消息面加分动态调整（根据市场走势+风格+情绪+板块温度）
        # 基础: 走势风格倍数
        if trend == 'bull':
            style_mult = 2.5; anti_mult = 0.3
        elif trend == 'bear':
            style_mult = 1.8; anti_mult = 0.4
        elif trend == 'crash':
            style_mult = 1.5; anti_mult = 0.2
        else:
            style_mult = 2.0; anti_mult = 0.5

        # v10.4: 情绪倍数（恐慌时消息失效，贪婪时消息放大）
        mood_mult = {'panic':0.5, 'caution':0.8, 'neutral':1.0, 'optimistic':1.2, 'greedy':1.3}.get(mood_label, 1.0)

        # v10.4: 板块温度倍数（冰点反转预期加分，过热追涨降权）
        temp_mult = {'ice':1.8, 'cool':1.3, 'warm':1.0, 'hot':0.7, 'overheat':0.3, 'neutral':1.0}.get(primary_sector_temp, 1.0)

        for idx, (sector, bonus, s_style) in enumerate(c.get('news_matches',[])):
            news_sectors.append(sector)
            news_styles.append(s_style)
            # v10.4: 综合倍数 = 风格倍数 × 情绪倍数 × 板块温度倍数
            if s_style == style:
                combined_mult = style_mult * mood_mult * temp_mult
            else:
                combined_mult = anti_mult * mood_mult * temp_mult
            news_bonus_raw += bonus * combined_mult
            # v10.4: 记录每个消息的动态倍数（用于调试）
            c.setdefault('news_mults', []).append({
                'sector': sector, 'style_mult': style_mult if s_style==style else anti_mult,
                'mood_mult': mood_mult, 'temp_mult': temp_mult, 'combined': round(combined_mult,2)
            })

        news_bonus = news_bonus_raw * 4  # v9.8: 提高消息面权重

        # v10.4: 冰点板块有强消息催化时，额外加分（冰点反转预期）
        if primary_sector_temp == 'ice' and news_bonus_raw >= 15:
            news_bonus += 20
            c['ice_reversal_bonus'] = 20
        # v10.4: 过热板块即使有好消息，也要封顶
        if primary_sector_temp == 'overheat' and news_bonus > 60:
            news_bonus = 60
            c['overheat_cap'] = True

        # v9.7: 防御板块优先机制（高风险环境下）
        defense_sectors = {'养殖/农业', '创新药', '大金融'}
        is_defense = any(ns in defense_sectors for ns in news_sectors)
        if risk_mode in ('caution', 'stop') and is_defense:
            news_bonus += 15  # 高风险环境防御板块额外+15分
            c['defense_bonus'] = 15
        elif risk_mode == 'stop' and not is_defense:
            # 空仓模式非防御板块大幅降权
            news_bonus = max(news_bonus * 0.3, 0)
            c['defense_bonus'] = -10

        # v9.5: 初始化各分项（用于后面统一输出）
        rsi_penalty = 0
        turnover_penalty = 0
        net_buy_bonus = 0
        fund_bonus = 0
        combo_bonus = 0
        veto_reason = None

        if c['conditions']:
            # v10.4: 根据该股票的板块温度微调条件权重
            # 板块温度会影响某些条件的有效性，需要个股级别动态调整
            cw_stock = dict(cw)  # 复制基础权重
            if primary_sector_temp == 'overheat':
                # 过热板块: 降低突破型权重，提升缩量洗盘/反包权重
                cw_stock[13] = max(cw_stock.get(13, 20) - 6, 4)
                cw_stock[17] = max(cw_stock.get(17, 24) - 8, 5)
                cw_stock[14] = max(cw_stock.get(14, 16) - 6, 0)
                cw_stock[15] = min(cw_stock.get(15, 20) + 4, 28)
                cw_stock[19] = min(cw_stock.get(19, 26) + 2, 32)
            elif primary_sector_temp == 'hot':
                cw_stock[17] = max(cw_stock.get(17, 24) - 4, 8)
                cw_stock[13] = max(cw_stock.get(13, 20) - 3, 6)
                cw_stock[19] = min(cw_stock.get(19, 26) + 3, 32)
            elif primary_sector_temp == 'ice':
                # 冰点板块: 提升启动型/突破型权重，降低强势型/反包权重
                cw_stock[13] = min(cw_stock.get(13, 20) + 6, 30)
                cw_stock[15] = min(cw_stock.get(15, 20) + 4, 28)
                cw_stock[12] = min(cw_stock.get(12, 16) + 4, 28)
                cw_stock[4] = max(cw_stock.get(4, 24) - 4, 8)
                cw_stock[19] = max(cw_stock.get(19, 26) - 6, 10)

            # v10.4: 动态条件权重评分（使用个股级别微调后的权重）
            sig = sum(cw_stock.get(x, 0) for x in c['conditions'])
            # 权重为0的条件不计分也不扣分
            active_conds = [x for x in c['conditions'] if cw_stock.get(x, 0) > 0]
            if len(active_conds) >= 2: sig += 8
            if len(active_conds) >= 3: sig += 14
            if len(active_conds) >= 4: sig += 18
            sc += sig * 3

            # v9.5: 多条件组合奖励（高胜率条件叠加=信号增强）
            combo_bonus = calc_combo_bonus(active_conds)
            if combo_bonus > 0:
                sc += combo_bonus * 2  # 组合奖励乘2放大

            # v9.5: 高胜率条件独立额外加分（确保高胜率条件股票排名靠前）
            if 9 in active_conds:  # MACD金叉 胜率81.8%
                sc += 35
            if 4 in active_conds:  # 强势型 胜率71.2%
                sc += 25
            if 10 in active_conds: # 强势回踩金叉
                sc += 22
            if 6 in active_conds:  # 涨停回踩
                sc += 18

            # 量价评分
            vs=0;vr=c.get('vol_ratio',0)
            if vr<0.5: vs=9
            elif vr<0.7: vs=7
            elif vr<1: vs=5
            else: vs=3
            sc+=vs*2

            # 资金态度
            ms=0
            if bsr>=1.3: ms=12
            elif bsr>=1.1: ms=9
            elif bsr>=1.0: ms=7
            elif bsr>=0.9: ms=4
            else: ms=1
            if c.get('net_buy_vol',0)>0:ms+=3
            sc+=ms*2

            # v10.3: RSI评分动态调整（根据市场走势）
            ts=0; r6=c.get('rsi6'); rsi_penalty=0
            if r6:
                if trend in ('bull', 'range'):
                    # v10.3: 牛市/震荡市优先RSI 45-75（强势加速区）
                    if 50<=r6<=65: ts=12      # 最强区间
                    elif 45<=r6<50: ts=10
                    elif 65<r6<75: ts=8
                    elif 40<=r6<45: ts=6
                    elif 75<=r6<80: ts=4; rsi_penalty=8
                    elif r6>=80: ts=2; rsi_penalty=15
                    elif 30<=r6<40: ts=4      # 低位在牛市不是最优
                    else: ts=2
                else:
                    # v10.3: 熊市/暴跌市优先RSI 30-50（低位反弹区）
                    if 35<=r6<=50: ts=12      # 最强区间
                    elif 30<=r6<35: ts=10
                    elif 50<r6<60: ts=8
                    elif 25<=r6<30: ts=6
                    elif 60<=r6<70: ts=4; rsi_penalty=5
                    elif r6>=70: ts=2; rsi_penalty=12
                    else: ts=2
            sc+=ts*1.5
            sc-=rsi_penalty

            # 换手率异常过滤
            to_val = c.get('turnover',0)
            if to_val > 20: turnover_penalty = 15
            elif to_val > 15: turnover_penalty = 8
            elif to_val > 10 and trend != 'bull': turnover_penalty = 4
            sc -= turnover_penalty

            # 净买入加分
            nbv = c.get('net_buy_vol',0)
            if nbv > 50000: net_buy_bonus = 15
            elif nbv > 20000: net_buy_bonus = 10
            elif nbv > 5000: net_buy_bonus = 5
            elif nbv > 0: net_buy_bonus = 2
            sc += net_buy_bonus

            # ====== v9.1 新增: 涨停基因加分（短线3-10天核心指标）======
            luc = c.get('limit_up_count', 0)
            if luc >= 5: sc += 25
            elif luc >= 3: sc += 18
            elif luc >= 1: sc += 10
            
            # 拉升幅度加分
            max_pct = c.get('max_pct_5d', 0)
            if max_pct >= 15: sc += 15
            elif max_pct >= 10: sc += 10
            elif max_pct >= 5: sc += 5
            
            # 量比加分
            vr = c.get('vol_ratio', 0) or 0
            if vr >= 2.0: sc += 12
            elif vr >= 1.5: sc += 8
            elif vr >= 1.0: sc += 4

            # ====== v9.3优化: 条件9三档分级加分 + 拉升强度评分 ======
            c9_gap = c.get('cond9_gap')
            c9_tier = c.get('cond9_tier')
            c9_surge = c.get('cond9_surge')
            if c9_gap is not None and c9_tier:
                gap_bonus = cond9_gap_bonus(c9_gap)
                sc += gap_bonus
                if c9_tier == '9B':
                    sc += 30
                elif c9_tier == '9C':
                    sc += 20
                if c9_gap <= 0:
                    sc += 10
                elif c9_gap <= 0.005:
                    sc += 7
                if c9_surge:
                    lu = c9_surge.get('limit_up_60', 0)
                    if lu >= 5: sc += 25
                    elif lu >= 3: sc += 15
                    elif lu >= 1: sc += 5
                    pct5 = c9_surge.get('pct_5d', 0)
                    if pct5 > 15: sc += 20
                    elif pct5 > 8: sc += 12
                    elif pct5 > 3: sc += 5
                    if c9_surge.get('ma_all_bull', False):
                        sc += 20
                    ma_c = c9_surge.get('ma_count', 0)
                    sc += min(ma_c * 5, 15)

            # ====== v10.0: 涨停基因六因子评分（高弹性核心指标）======
            if SURGE_MODULE_LOADED and c.get('c') and c.get('v'):
                gene_score, gene_factors, gene_details = calc_limit_gene_score(
                    c['c'], c['v'], c.get('hi', c['c']), c.get('lo', c['c']), 
                    c.get('to', [])
                )
                c['surge_gene_score'] = gene_score
                c['surge_gene_factors'] = gene_factors
                c['surge_gene_details'] = gene_details
                # 涨停基因直接加到技术分（不计入封顶，单独计算）
                c['surge_gene_raw'] = gene_score  # 原始分，不封顶
                if gene_score >= 60:
                    sc += 30  # 超强涨停基因
                elif gene_score >= 40:
                    sc += 20  # 强涨停基因
                elif gene_score >= 25:
                    sc += 12  # 中等涨停基因
                elif gene_score >= 10:
                    sc += 5   # 弱涨停基因
            else:
                gene_score = 0
                c['surge_gene_score'] = 0
                c['surge_gene_raw'] = 0

            # ====== v9.2新增: 条件10梯度加分 ======
            c10_gap = c.get('cond10_gap')
            if c10_gap is not None:
                gap10_bonus = cond10_gap_bonus(c10_gap)
                sc += gap10_bonus
                sc += 25
                if c10_gap <= 0:
                    sc += 15
                elif c10_gap <= 0.01:
                    sc += 10

            # ====== v9.2新增: 热点通道加分 ======
            if c.get('hot_channel_hit'):
                sc += 35
                if 10 in c.get('conditions', []) or 9 in c.get('conditions', []):
                    sc += 20
                luc = c.get('limit_up_count', 0)
                if luc >= 5: sc += 15
                elif luc >= 3: sc += 10
                elif luc >= 1: sc += 5

            # ====== v9.6: 技术面封顶200分（防止单维度碾压） ======
            TECH_CAP = 200
            if sc > TECH_CAP:
                sc = TECH_CAP
                c['tech_capped'] = True

            # ====== v10.0: 次日涨幅预测 ======
            if SURGE_MODULE_LOADED and c.get('c') and c.get('v'):
                pred_gain, pred_grade, pred_reasons = predict_next_day_gain(
                    c['c'], c['v'], 
                    c.get('hi', c['c']), c.get('lo', c['c']),
                    c.get('to', []),
                    c.get('rsi6'), 
                    c.get('vol_ratio', 0),
                    c.get('buy_sell_ratio', 0),
                    c.get('pct', 0),
                    c.get('conditions', []),
                    c.get('news_matches', []),
                    c.get('combo_bonus', 0),
                    c.get('limit_up_count', 0),
                    c.get('max_pct_5d', 0),
                )
                c['pred_gain'] = pred_gain
                c['pred_grade'] = pred_grade
                c['pred_reasons'] = pred_reasons
            else:
                c['pred_gain'] = 0
                c['pred_grade'] = 'E'
                c['pred_reasons'] = []

            # ====== v9.4→v9.6: 高潮预警折价（幅度翻倍） ======
            overheat_penalty = 0
            news_sectors_list = news_sectors or []
            for ns in news_sectors_list:
                if ns in OVERHEAT_SECTORS:
                    lv = OVERHEAT_SECTORS[ns]
                    if lv >= 2:
                        overheat_penalty = max(overheat_penalty, 40)  # v9.6: 20→40
                    elif lv >= 1:
                        overheat_penalty = max(overheat_penalty, 20)  # v9.6: 10→20
            if overheat_penalty > 0:
                sc -= overheat_penalty
                c['overheat_penalty'] = overheat_penalty

            # ====== v9.6: 轮动方向调整（幅度提高到±15） ======
            if rotation_dir == 'defense':
                ns_info = [info for kw, info in current_sector_kw.items() if kw in (news_sectors_list or [])]
                has_value = any(i['style'] == 'value' for i in ns_info)
                has_growth = any(i['style'] == 'growth' for i in ns_info)
                if has_value: sc += 15  # v9.6: 5→15
                if has_growth: sc -= 15  # v9.6: 5→15
            elif rotation_dir == 'attack':
                ns_info = [info for kw, info in current_sector_kw.items() if kw in (news_sectors_list or [])]
                has_value = any(i['style'] == 'value' for i in ns_info)
                has_growth = any(i['style'] == 'growth' for i in ns_info)
                if has_growth: sc += 15  # v9.6: 5→15
                if has_value: sc -= 15  # v9.6: 5→15

            # 风控评分
            rs=0;pct=c.get('pct',0)
            if -3<=pct<=2: rs=7
            elif -5<=pct<=3: rs=5
            else: rs=3
            if c['close']<20: rs+=5
            elif c['close']<30: rs+=4
            elif c['close']<50: rs+=3
            else: rs+=1
            sc+=rs*1.5

        # ====== v9.5: 基本面5维加分（含一票否决）—— v9.8: 移出条件块，纯消息股也可受益 ======
        fin_data = c.get('fin_data', {})
        fund_bonus, fund_detail, veto_reason = calc_fundamental_bonus(c, c, fin_data, c['name'])
        if veto_reason:
            # 一票否决：评分归零并标记
            sc = -500
            c['veto_reason'] = veto_reason
        sc += fund_bonus
        c['fund_bonus'] = fund_bonus
        c['fund_detail'] = fund_detail

        # ====== v9.7: 高PE股在下跌市/高风险环境额外扣分 ======
        pe_val = c.get('pe', 0)
        if risk_mode in ('caution', 'stop') and pe_val and pe_val > 50:
            pe_penalty = min((pe_val - 50) * 0.5, 40)  # PE 50→0分, PE 130→40分
            sc -= pe_penalty
            c['pe_penalty'] = pe_penalty
            if pe_penalty > 20:
                c['veto_reason'] = c.get('veto_reason', '') + f' | v9.7高PE风险(PE{pe_val:.0f}, 扣{pe_penalty:.0f}分)'
        elif risk_mode == 'stop' and pe_val and pe_val > 80:
            sc -= 50
            c['veto_reason'] = c.get('veto_reason', '') + f' | v9.7空仓模式高PE(PE{pe_val:.0f})一票否决'
            sc = -500

        # 加入消息面加分
        if not c['conditions'] and news_bonus > 0:
            sc += news_bonus * 3  # 纯消息面驱动股票加权
        else:
            sc += news_bonus

        # ====== v9.5: 前瞻性事件埋伏加分 ======
        event_bonus = 0
        event_details = []
        if upcoming_events:
            try:
                from event_calendar import score_event_impact
                event_bonus, event_details = score_event_impact(
                    c['code'], c['name'], upcoming_events,
                    news_sectors=c.get('news_sectors', [])
                )
                sc += event_bonus
            except Exception:
                pass

        # ====== v10.5: 集合竞价强度评分（开盘价+竞价量能综合） ======
        open_px = c.get('open_price', 0)
        last_cl = c.get('last_close', 0)
        curr_px = c.get('close', 0)
        pm_vol_ratio = c.get('vol_ratio', 0) or 0
        pm_score, pm_label, pm_gap, pm_pattern, pm_desc = calc_premarket_intensity(
            open_px, last_cl, curr_px, pm_vol_ratio, c.get('conditions', []), trend
        )
        c['pm_score'] = pm_score
        c['pm_label'] = pm_label
        c['pm_gap'] = pm_gap
        c['pm_pattern'] = pm_pattern
        c['pm_desc'] = pm_desc
        c['open_price'] = open_px

        # v10.5: 竞价强度加分（按强度等级加权）
        pm_bonus = 0
        if pm_score >= 75:      # 极强抢筹
            pm_bonus = 35
        elif pm_score >= 55:    # 强抢筹
            pm_bonus = 22
        elif pm_score >= 40:    # 温和偏强
            pm_bonus = 12
        elif pm_score >= 25:    # 中性
            pm_bonus = 3
        elif pm_score >= 15:    # 偏弱
            pm_bonus = -5       # 竞价偏弱轻微扣分
        else:                   # 弱势低开
            pm_bonus = -15      # 弱势低开扣分（但超跌反弹型除外）
            # 超跌反弹型低开不扣分（低位低开是买入机会）
            if 12 in c.get('conditions', []):
                pm_bonus = 0
        sc += pm_bonus
        c['pm_bonus'] = pm_bonus

        # v10.5: 高开低走出货预警 — 高开>3%但当前已跌破开盘价2%以上
        if pm_gap >= 3 and curr_px > 0 and open_px > 0:
            drop_from_open = (curr_px - open_px) / open_px * 100
            if drop_from_open <= -2:
                sc -= 20  # 高开低走出货嫌疑
                c['pm_warning'] = f'⚠️高开{pm_gap:.1f}%后回落{abs(drop_from_open):.1f}%，出货嫌疑'

        c['score']=round(sc,1)
        c['event_bonus']=event_bonus
        c['event_details']=event_details
        c['news_sectors']=news_sectors
        c['news_bonus']=news_bonus
        c['rsi_penalty']=rsi_penalty
        c['turnover_penalty']=turnover_penalty
        c['net_buy_bonus']=net_buy_bonus
        c['combo_bonus']=combo_bonus
        c['style_match']=any(s==style for s in news_styles)

        # v9.5: 次日概率计算（基于动态权重和实际命中条件）
        prob=50
        active_conds = [x for x in c.get('conditions', []) if cw.get(x, 0) > 0]
        if active_conds:
            # 按条件最高概率 + 组合加成
            cond_probs = {
                9: 82,  # MACD金叉
                4: 71,  # 强势型
                10: 70, # 强势回踩金叉
                6: 68,  # 涨停回踩
                8: 65,  # 突破回踩
                17: 65, # v10.2: 突破前高型（次日连板概率22.1%）
                7: 62,  # 均线多头
                15: 58, # v10.2: 缩量洗盘型
                16: 55, # v10.2: 小阳蓄势型
                3: 55,  # 趋势上行
                18: 52, # v10.2: 高振幅洗盘型
                2: 38,  # 缩量回调
                1: 37,  # 10日线强势
            }
            max_prob = max(cond_probs.get(x, 50) for x in active_conds)
            # 多条件加成
            if len(active_conds) >= 3: max_prob += 8
            elif len(active_conds) >= 2: max_prob += 4
            # 组合加成
            if combo_bonus >= 15: max_prob += 6
            elif combo_bonus >= 10: max_prob += 3
            prob = max_prob
        elif c.get('hot_channel_hit'):
            prob=58
        else:
            prob=45

        if bsr>=1.2: prob+=5
        elif bsr>=1.0: prob+=3
        if news_bonus>=20: prob+=8
        elif news_bonus>=12: prob+=5
        elif news_bonus>=6: prob+=3
        if c.get('style_match'): prob+=3
        if fund_bonus >= 20: prob+=5
        elif fund_bonus >= 10: prob+=3
        # v10.5: 集合竞价强度影响概率
        if pm_score >= 75: prob+=8    # 极强抢筹
        elif pm_score >= 55: prob+=5  # 强抢筹
        elif pm_score >= 40: prob+=3  # 温和偏强
        elif pm_score < 15: prob-=5   # 弱势低开降低概率
        c['prob']=min(prob,90)

    # 排序
    candidates.sort(key=lambda x:x['score'],reverse=True)

    # ====== v10.1: 弹性优先排序（涨停基因 + 预测涨幅作为主排序键）======
    # v10.0问题：综合评分主排导致大蓝筹靠前（它们技术分高但弹性差）
    # v10.1修复：elasticity_score（pred_gain*2+gene_score）作为主排序键
    #            同时增加尾盘涨幅惩罚（今日已涨太多的票排除或降权）
    for c in candidates:
        pred = c.get('pred_gain', 0)
        gene = c.get('surge_gene_score', 0)
        pct_today = c.get('pct', 0)  # 当日涨跌幅
        pm_sc = c.get('pm_score', 0)  # v10.5: 集合竞价强度
        # v10.1: 尾盘涨幅惩罚 — 今日已涨>4%则降权（追高风险大）
        if pct_today > 4:
            elasticity_penalty = min((pct_today - 4) * 3, 25)  # 最多扣25分
        else:
            elasticity_penalty = 0
        # v10.5: 竞价强度纳入弹性评分（权重0.3，避免过度依赖开盘数据）
        c['elasticity_score'] = pred * 2 + gene + pm_sc * 0.3 - elasticity_penalty
    # 最终排序: 弹性评分主排 + 综合评分辅排
    candidates.sort(key=lambda x: (x.get('elasticity_score', 0), x['score']), reverse=True)

    # ====== v9.5: 板块分散式TOP10选取 + 走势适配 + 高胜率优先 ======
    MIN_SECTORS = 3
    if trend in ('crash','bear'):
        MAX_NO_NEWS = 2 if trend == 'crash' else 3
    else:
        MAX_NO_NEWS = 10

    # v9.5: 走势适配权重调整（基于回测胜率动态调整优先级）
    # bull: 趋势延续型优先（条件7/8/10），条件9降权
    # range: 突破回踩+反转均衡（条件8/9/10）
    # bear/crash: 底部反转型优先（条件9/6），趋势型清零
    # v10.3: 趋势优先级大调整 — 强势反包(19)和强势型(4)优先，MACD(9)降级
    trend_priority = {
        'bull': [19, 4, 17, 10, 18, 11, 8, 13, 6, 15, 7, 9, 16, 3, 1, 2, 5],   # v10.3: 牛市优先强势反包+强势型
        'range': [19, 4, 17, 8, 10, 18, 11, 15, 6, 13, 7, 9, 16, 3, 1, 2, 5],  # v10.3: 震荡市均衡
        'bear': [19, 12, 15, 4, 6, 11, 18, 10, 8, 9, 16, 3, 5, 2, 1, 7, 17],   # v10.3: 下跌市优先反包+超跌
        'crash': [12, 19, 15, 6, 11, 4, 18, 10, 8, 9, 16, 3, 5, 2, 1, 7, 17],  # v10.3: 暴跌市优先超跌+反包
    }

    def get_sector_key(c):
        if c.get('news_sectors'): return c['news_sectors'][0]
        name = c['name']
        if any(kw in name for kw in ['科技','电子','光电','芯片','半导体']): return '科技'
        if any(kw in name for kw in ['能源','电力','煤','电']): return '能源'
        if any(kw in name for kw in ['化工','化学','材料','药','生物']): return '化工/医药'
        if any(kw in name for kw in ['股份','控股','集团','发展','实业']): return '综合'
        return '其他'

    def get_cond_priority_score(c):
        """
        v9.5: 计算候选股的走势适配优先分数
        命中走势优先列表中排名前3的条件 → 高分
        命中高胜率条件(4/9) → 额外加分
        被一票否决 → 直接排除
        """
        if c.get('veto_reason'):
            return -999  # 一票否决的股票不参与TOP10
        if not c['conditions']:
            return 0
        priority = trend_priority.get(trend, [])
        score = 0
        # 命中前3优先条件
        for i, p in enumerate(priority[:3]):
            if p in c['conditions']:
                score += (3 - i) * 10  # 第1优先+30, 第2优先+20, 第3优先+10
        # v10.3: 高胜率条件额外加分 — MACD降级，强势型/反包/突破前高升級
        if 19 in c['conditions']: score += 30 # v10.3: 强势反包型（博通集成模式）
        if 4 in c['conditions']: score += 24  # v10.3: 强势型提升
        if 17 in c['conditions']: score += 22 # v10.3: 突破前高型提升
        if 9 in c['conditions']: score += 12  # v10.3: MACD金叉大幅降低
        if 10 in c['conditions']: score += 15 # 强势回踩
        if 18 in c['conditions']: score += 14 # v10.3: 高振幅洗盘型提升
        if 15 in c['conditions']: score += 14 # 缩量洗盘型
        if 11 in c['conditions']: score += 12 # v10.3: 涨停基因加分
        if 16 in c['conditions']: score += 10 # 小阳蓄势型
        # 多条件组合加分
        if c.get('combo_bonus', 0) >= 15: score += 15
        elif c.get('combo_bonus', 0) >= 10: score += 8
        # v10.0: 涨停基因加分
        gene = c.get('surge_gene_score', 0)
        if gene >= 60: score += 35
        elif gene >= 40: score += 25
        elif gene >= 25: score += 15
        elif gene >= 10: score += 8
        # v10.0: 预测涨幅加分
        pred = c.get('pred_gain', 0)
        if pred >= 7: score += 30
        elif pred >= 5: score += 20
        elif pred >= 3: score += 10
        # v10.0: 启动型/加速型条件加分
        if 13 in c.get('conditions', []): score += 20
        if 14 in c.get('conditions', []): score += 18

        # v10.1: 尾盘涨幅惩罚 — 当日已涨太多不宜追
        pct_today = c.get('pct', 0)
        if pct_today > 5:
            score -= 20  # 已涨>5%，追高风险极大
        elif pct_today > 3:
            score -= 10  # 已涨>3%，谨慎追高

        # v10.1: 涨停基因硬性要求 — 没有涨停基因的票不适合次日>5%目标
        gene = c.get('surge_gene_score', 0)
        if gene < 10:
            score -= 15  # 完全没有涨停基因
        elif gene < 20:
            score -= 5   # 涨停基因较弱

        return score

    # v9.5: 先按走势适配优先分数排序，再按综合评分排序
    candidates_with_priority = [(c, get_cond_priority_score(c)) for c in candidates]
    # 排序键：优先分数降序 → 综合评分降序
    candidates_with_priority.sort(key=lambda x: (x[1], x[0]['score']), reverse=True)

    diversified_top = []
    used_sectors = set()
    no_news_count = 0
    existing_codes = set()

    # 第一轮: 优先选取走势适配且高胜率的股票（优先分数>0）
    for c, p_score in candidates_with_priority:
        if len(diversified_top) >= 10: break
        if p_score <= 0: continue  # 只选走势适配的股票
        sector = get_sector_key(c)
        has_news = bool(c.get('news_sectors'))
        if not has_news and no_news_count >= MAX_NO_NEWS: continue
        if not has_news: no_news_count += 1
        diversified_top.append(c)
        used_sectors.add(sector)
        existing_codes.add(c['code'])

    # 第二轮: 从剩余候选中按综合评分补满10只
    for c, _ in candidates_with_priority:
        if len(diversified_top) >= 10: break
        if c['code'] in existing_codes: continue
        sector = get_sector_key(c)
        has_news = bool(c.get('news_sectors'))
        if not has_news and no_news_count >= MAX_NO_NEWS: continue
        if not has_news: no_news_count += 1
        diversified_top.append(c)
        used_sectors.add(sector)
        existing_codes.add(c['code'])

    # 第三轮: 检查板块覆盖
    sector_count_in_top = len({get_sector_key(c) for c in diversified_top})
    if sector_count_in_top < MIN_SECTORS:
        for c, _ in candidates_with_priority:
            if len(diversified_top) >= 10: break
            if c['code'] in existing_codes: continue
            sector = get_sector_key(c)
            if sector not in used_sectors:
                diversified_top.append(c)
                used_sectors.add(sector)
                existing_codes.add(c['code'])

    # v10.1: 第四轮 — 市值分散（强制保留2只中盘高弹性，100-500亿）
    # 避免TOP10全是一堆20-50亿小票，增加组合稳健性
    mid_cap_in_top = sum(1 for c in diversified_top if 100 <= c.get('circ_mv', 0) <= 500)
    if mid_cap_in_top < 2:
        # 从全部候选中（已按elasticity_score排序）找100-500亿的中盘
        mid_cap_candidates = [
            c for c in candidates
            if c['code'] not in existing_codes
            and 100 <= c.get('circ_mv', 0) <= 500
            and c.get('elasticity_score', 0) > 0
        ]
        # 按弹性分排序，取最好的补足到2只
        mid_cap_candidates.sort(key=lambda x: x.get('elasticity_score', 0), reverse=True)
        for c in mid_cap_candidates:
            if len(diversified_top) >= 10: break
            if mid_cap_in_top >= 2: break
            diversified_top.append(c)
            existing_codes.add(c['code'])
            mid_cap_in_top += 1

    # 保存
    result={'scan_time':datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'version':'v9.5',
            'total':len(stocks),'pre_filtered':len(pf),
            'market_style':{'style':style,'value_avg':v_avg,'growth_avg':g_avg},
            'market_trend':trend,
            'new_conditions':{'7':'均线多头排列+量能阶梯','8':'放量突破+缩量回踩','9':'MACD水下二次金叉',
                              '10':'强势回踩金叉(趋势追踪)','hot_channel':'消息面热点专属通道'},
            'fundamental_bonus_dims':['个股基本面(PE/ROE/负债率)','辨识度(龙头/细分)','热度(换手/量比/净买入)','合同负债率','业绩增长(营收/净利润)'],
            'diversified_top10':[{'code':c['code'],'name':c['name'],'score':c['score'],'sector':get_sector_key(c),'has_news':bool(c.get('news_sectors')),'conditions':c.get('conditions',[])} for c in diversified_top],
            'candidates':candidates}
    import os
    os.makedirs('/root/.codebuddy/artifact/stock-scan', exist_ok=True)
    with open('/root/.codebuddy/artifact/stock-scan/candidates_v9.json','w') as f:
        json.dump(result,f,ensure_ascii=False,indent=2)

    # 输出
    cond_labels={1:'10日线强势',2:'缩量回调',3:'趋势上行(优化)',4:'强势型',5:'大单净买(优化)',
                 6:'涨停回踩',7:'均线多头排列',8:'突破回踩',9:'MACD水上金叉',
                 10:'强势回踩金叉',11:'涨停基因',12:'超跌反弹',
                 13:'启动型涨停',14:'加速突破型',
                 15:'缩量洗盘型',16:'小阳蓄势型',17:'突破前高型',18:'高振幅洗盘型'}

    # v9.7: 止盈止损提示计算
    def calc_trade_plan(close, conditions, risk_mode):
        """根据命中条件和风险模式，给出交易计划建议"""
        cond_set = set(conditions)
        # 基础止损/目标/持仓周期
        if 6 in cond_set:               # 涨停回踩 → 高弹性
            stop_pct, target_pct, hold = 0.06, 0.15, '3-5天'
        elif 4 in cond_set or 8 in cond_set:  # 强势型/突破回踩
            stop_pct, target_pct, hold = 0.05, 0.12, '3-5天'
        elif 9 in cond_set:             # MACD水上金叉
            stop_pct, target_pct, hold = 0.05, 0.10, '3-5天'
        elif 12 in cond_set:            # v9.8: 超跌反弹 → 弹性大
            stop_pct, target_pct, hold = 0.06, 0.12, '3-5天'
        elif 11 in cond_set:            # v9.8: 涨停基因 → 活跃度高
            stop_pct, target_pct, hold = 0.06, 0.10, '3-5天'
        elif 13 in cond_set:            # v10.0: 启动型涨停 → 高弹性
            stop_pct, target_pct, hold = 0.05, 0.15, '2-3天'
        elif 14 in cond_set:            # v10.0: 加速突破 → 主升浪
            stop_pct, target_pct, hold = 0.05, 0.18, '3-5天'
        elif 2 in cond_set:             # 缩量回调 → 时间换空间
            stop_pct, target_pct, hold = 0.07, 0.10, '5-7天'
        elif 7 in cond_set:             # 均线多头排列
            stop_pct, target_pct, hold = 0.06, 0.10, '5-7天'
        else:
            stop_pct, target_pct, hold = 0.06, 0.08, '3-5天'

        # 风险模式收紧
        if risk_mode == 'stop':
            stop_pct = min(stop_pct, 0.05)
            target_pct = min(target_pct, 0.08)
        elif risk_mode == 'caution':
            stop_pct = min(stop_pct, 0.06)

        entry = close
        stop_loss = entry * (1 - stop_pct)
        target = entry * (1 + target_pct)
        return entry, stop_loss, target, hold

    sector_display = len({get_sector_key(c) for c in diversified_top})
    news_count = sum(1 for c in diversified_top if c.get('news_sectors'))
    veto_count = sum(1 for c in candidates if c.get('veto_reason'))
    combo_count = sum(1 for c in candidates if c.get('combo_bonus', 0) > 0)
    mood_label_short = {'panic':'恐慌','caution':'谨慎','neutral':'中性','optimistic':'乐观','greedy':'贪婪'}.get(mood_label, mood_label)
    print(f"\n🏆 v10.5智能动态TOP10（情绪:{mood_label_short} | 覆盖{sector_display}个板块 | {news_count}只有消息面 | 趋势:{trend}）")
    print(f"   一票否决:{veto_count}只 | 多条件组合:{combo_count}只 | 集合竞价强度已纳入评分")
    print("="*70)
    for i,c in enumerate(diversified_top):
        cs=','.join(str(x) for x in c['conditions'])
        r6s=f'{c["rsi6"]:.1f}' if c['rsi6'] else 'N/A'
        bsr=f'{c["buy_sell_ratio"]:.2f}'
        pct_s=f'+{c["pct"]:.2f}' if c['pct']>=0 else f'{c["pct"]:.2f}'
        news_str=' | '.join(c.get('news_sectors',[])) if c.get('news_sectors') else '无'
        nb=c.get('news_bonus',0)
        fb=c.get('fund_bonus',0)
        rp=c.get('rsi_penalty',0)
        cb=c.get('combo_bonus',0)
        sm='✅顺风格' if c.get('style_match') else ''
        veto=c.get('veto_reason','')
        # v10.4: 板块温度标记
        stemp = c.get('sector_temp', 'warm')
        temp_mark = {'ice':'❄️冰点','cool':'🍃偏冷','warm':'☀️温热','hot':'🔥高潮','overheat':'⚠️过热'}.get(stemp, '')

        print(f'\n#{i+1} 【{c["code"]}】 {c["name"]} {temp_mark}')
        eb = c.get('event_bonus', 0)
        score_parts = f"技术{c['score']-nb-fb-eb:.0f}+消息{nb:.0f}+基本{fb:.0f}"
        if rp: score_parts += f"-RSI扣{rp:.0f}"
        if cb: score_parts += f"+组合{cb:.0f}"
        if eb: score_parts += f"+事件{eb:.0f}"
        # v10.4: 显示动态调整标记
        dyn_marks = []
        if c.get('ice_reversal_bonus'): dyn_marks.append('冰点反转+20')
        if c.get('overheat_cap'): dyn_marks.append('过热封顶')
        if c.get('defense_bonus'): dyn_marks.append(f'防御{c["defense_bonus"]:+d}')
        dyn_str = f" [{' | '.join(dyn_marks)}]" if dyn_marks else ''
        print(f'  💰 {c["close"]:.2f}元 {pct_s}% | 评分:{c["score"]}({score_parts}) | 概率:{c["prob"]}% {sm}{dyn_str}')
        pe_str = f'{c["pe"]:.1f}' if c.get('pe') and c['pe'] != 0 else 'N/A'
        # v10.5: 开盘价和竞价强度信息
        open_px = c.get('open_price', 0)
        pm_label = c.get('pm_label', '')
        pm_gap = c.get('pm_gap', 0)
        pm_pattern = c.get('pm_pattern', '')
        pm_score_val = c.get('pm_score', 0)
        pm_bonus_val = c.get('pm_bonus', 0)
        pm_emoji = {'极强抢筹':'🔥','强抢筹':'🟠','温和偏强':'🟡','中性':'⚪','偏弱':'🔵','弱势低开':'🟤','无数据':'❓'}.get(pm_label, '⚪')
        open_str = f'{open_px:.2f}({pm_gap:+.1f}%)' if open_px > 0 else 'N/A'
        print(f'  开盘:{open_str} | {pm_emoji}竞价:{pm_label}({pm_score_val}分,{pm_pattern}) | RSI:{r6s} | 量比:{c["vol_ratio"]:.2f} | PE:{pe_str}')
        if c.get('pm_warning'):
            print(f'  {c["pm_warning"]}')
        print(f'  条件:{cs} | 消息面:{news_str}')
        if c.get('fund_detail'):
            fd = c['fund_detail']
            print(f'  基本面: 基础{fd["basic"]}/辨识{fd["recognition"]}/热度{fd["heat"]}/合同{fd["contract"]}/增长{fd["growth"]}')
        if c.get('veto_reason'):
            print(f'  ⚠️ 一票否决:{c["veto_reason"]}')
        # v9.5: 前瞻性事件埋伏信息
        if c.get('event_details'):
            for ed in c['event_details'][:2]:
                print(f'  📅 {ed}')
        if c['reasons']:
            for r in c['reasons'][:2]: print(f'    · {r}')
        # v10.0: 涨停基因和预测涨幅
        if c.get('pred_gain') is not None and c.get('pred_gain', 0) != 0:
            grade = c.get('pred_grade', 'E')
            gene_s = c.get('surge_gene_score', 0)
            gene_d = c.get('surge_gene_details', [])
            pred_r = c.get('pred_reasons', [])
            print(f"  🚀 预测涨幅: {c['pred_gain']:+.1f}% (等级{grade}) | 涨停基因: {gene_s}/100")
            if gene_d:
                print(f"     涨停因子: {', '.join(gene_d)}")
            if pred_r:
                print(f"     涨幅依据: {'; '.join(pred_r[:3])}")
        # v9.7: 止盈止损提示
        entry, stop_loss, target, hold = calc_trade_plan(c['close'], c['conditions'], risk_mode)
        stop_pct = (entry - stop_loss) / entry * 100
        target_pct = (target - entry) / entry * 100
        print(f'  🎯 交易计划: 参考{entry:.2f} | 止损{stop_loss:.2f}(-{stop_pct:.0f}%) | 目标{target:.2f}(+{target_pct:.0f}%) | 持仓{hold}')

    # v9.5统计
    hit=[c for c in candidates if c['conditions']]
    news_hit=[c for c in candidates if c.get('news_sectors')]
    both=[c for c in candidates if c['conditions'] and c.get('news_sectors')]
    style_match=[c for c in candidates if c.get('style_match')]
    veto_list=[c for c in candidates if c.get('veto_reason')]
    combo_list=[c for c in candidates if c.get('combo_bonus', 0) > 0]

    # v9.5: 事件埋伏统计
    event_hit = [c for c in candidates if c.get('event_bonus', 0) > 0]
    event_total_bonus = sum(c.get('event_bonus', 0) for c in candidates)

    print(f'\n📊 v9.5统计:')
    print(f'  条件命中:{len(hit)} | 消息面匹配:{len(news_hit)} | 双重共振:{len(both)} | 顺风格:{len(style_match)}')
    print(f'  一票否决:{len(veto_list)}只(业绩暴雷/高负债/亏损股) | 多条件组合奖励:{len(combo_list)}只')
    print(f'  事件埋伏:{len(event_hit)}只(+{event_total_bonus:.0f}分) | 未来事件提前布局' if event_hit else '  事件埋伏: 无(东方财富API本地可获取)')
    cd=Counter()
    for c in hit: cd.update([x for x in c['conditions'] if cw.get(x, 0) > 0])
    print(f'  有效条件分布(权重>0): {dict(sorted(cd.items()))}')
    # 被禁用条件统计
    disabled_conds = [x for c in hit for x in c['conditions'] if cw.get(x, 0) == 0]
    if disabled_conds:
        dcd = Counter(disabled_conds)
        print(f'  ⚠️ 被禁用条件(权重=0): {dict(sorted(dcd.items()))}')
    mc=Counter(len([x for x in c['conditions'] if cw.get(x,0)>0]) for c in hit)
    print(f'  有效多条件: {dict(sorted(mc.items()))}')

    # 各条件胜率预测（基于动态权重）
    print(f'\n📈 v9.5条件胜率预测(基于回测数据+走势适配):')
    for cond in [9, 4, 10, 17, 6, 8, 15, 16, 7, 18, 3, 5, 2, 1]:
        if cw.get(cond, 0) == 0: continue
        ch = [c for c in hit if cond in c['conditions']]
        if not ch: continue
        avg_prob = sum(c['prob'] for c in ch) / len(ch)
        avg_score = sum(c['score'] for c in ch) / len(ch)
        print(f'  条件{cond}({cond_labels[cond]}): {len(ch)}只, 均预测胜率{avg_prob:.1f}%, 均分{avg_score:.0f}')

    # v9.3: 条件9分档统计
    tier_count = {'9B': 0, '9C': 0}
    for c in hit:
        if 9 in c['conditions'] and c.get('cond9_tier'):
            tier_count[c['cond9_tier']] = tier_count.get(c['cond9_tier'], 0) + 1
    print(f'\n  v9.5条件9分档: 9B(零轴附近):{tier_count.get("9B",0)} | 9C(水上首次):{tier_count.get("9C",0)}')
    # 高潮预警统计
    overheat_hit = [c for c in hit if c.get('overheat_penalty', 0) > 0]
    print(f'  高潮预警: {len(overheat_hit)}只个股受高潮预警折价')
    print(f'  轮动方向: {rotation_label}')
    print(f'\n✅ v9.5优化完成: 动态权重+组合奖励+走势适配+一票否决')

    return candidates

if __name__=='__main__': main()
