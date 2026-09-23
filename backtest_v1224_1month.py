#!/usr/bin/env python3
"""
v12.24 一个月回测 (21个交易日) - BULL激进主线龙头版
================================================================
核心改进 (BULL模式重写):
  1. BULL模式: 主线龙头优先 + 激进追强
     - 移除"无板块热度涨停-12"重罚 (v12.23的过度保守问题)
     - 主线板块+T-1涨停 = 龙头信号 (+12)
     - 热点板块+T-1涨停 = 板块龙头 (+8)
     - 连板续涨奖励: 3连板+8, 2连板+5, 5日2涨停+3
     - 追涨降分大幅放宽: 仅高开>7%且无板块热度才轻罚-8
     - 板块热度权重提升至40% (主导因子)
  2. 继承v12.22: 用户手动判断大盘态势 (完美前瞻)
  3. 继承v12.23: CORRECTION板块热度加分, RANGE_BULL 5攻1防
  4. 继承v12.20: 起爆放宽, 样本1500只
回测周期: 2026-07-09 ~ 2026-08-06
"""
import json, requests, time, random, os, sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter

# 复用会话+连接池, 提升批量并发拉取的稳定性与速度
_SESSION = None
_WORKERS = 24
def _session():
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.headers.update(HEADERS)
        a = HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=1)
        s.mount('http://', a); s.mount('https://', a)
        _SESSION = s
    return _SESSION

BEIJING_TZ = timezone(timedelta(hours=8))
def now_bj(): return datetime.now(BEIJING_TZ)
HEADERS = {'User-Agent': 'Mozilla/5.0'}

BACKTEST_DATES = [
    '2026-07-09', '2026-07-10',
    '2026-07-13', '2026-07-14', '2026-07-15', '2026-07-16', '2026-07-17',
    '2026-07-20', '2026-07-21', '2026-07-22', '2026-07-23', '2026-07-24',
    '2026-07-27', '2026-07-28', '2026-07-29', '2026-07-30', '2026-07-31',
    '2026-08-03', '2026-08-04', '2026-08-05', '2026-08-06',
]

# ==================== 板块定义 ====================
DEFENSIVE_SECTORS = {'贵金属', '煤炭', '电力电网', '消费医药', '军工'}

SECTOR_KW = {
    'PCB': ['电路','覆铜','奥士康','生益','华正','博敏','方正科技','景旺','沪电','兴森','胜宏','明阳','骏亚','中京','普林','依顿','金安','超声','超华','崇达','科翔','逸豪','金百泽'],
    '6G通信': ['通宇','凡谷','大富','盛路','硕贝德','灿勤','信维','麦捷','春兴','通光','中富通','中光','超讯','纵横','华脉','瑞斯','广和通','移为','移远','高新兴','三环','中贝通信','通光线缆','东方通信','波导','长江通信','烽火','特发信息','永鼎','国脉','邦讯'],
    'CPO光通信': ['光迅','华工','铭普','中贝','三安','亨通','烽火','特发','永鼎','中际','新易盛','天孚','光库','太辰','博创','腾景','剑桥','联特','德科立','源杰'],
    '先进封装': ['封测','封装','长电','通富','华天','晶方','甬矽','环旭','安靠','气派','同兴达'],
    '电子特气': ['特气','气体','金宏','华特','和远','凯美','南大','雅克','正帆','昊华','广钢'],
    '半导体': ['半导体','芯片','中芯','华虹','北方华创','中微','沪硅','立昂微','中晶','卓胜','圣邦','拓荆','江丰','有研','阿石创','安集','鼎龙'],
    '煤炭': ['煤','山西焦','兖州','陕西煤','中煤','潞安','昊华能源','兰花','阳泉','盘江','恒源','靖远','大同'],
    '贵金属': ['黄金','白银','山东黄金','紫金','中金黄金','赤峰','银泰','湖南黄金','恒邦','荣华','四川黄金','招金'],
    'AI算力': ['算力','浪潮','中科','紫光','拓维','神州','直真','润建','英维克','高澜','曙光','富信'],
    '数字货币': ['数字','飞天','恒宝','翠微','广电','数字认证','格尔','信安','御银','创识','雄帝','朗科'],
    '商业航天': ['航天','火箭','卫星','天银','盟升','星网','海格','北斗','航天电子','航天动力','航天晨光','中国卫星','中国卫通','星网宇达','神剑','中天火箭'],
    '电力电网': ['电力','电网','特高压','许继','国电','华银','涪陵','桂东','岷江','西昌','乐山','闽东','粤电力','晋控','明星'],
    '消费医药': ['医药','消费','白酒','茅台','五粮液','汾酒','泸州','古井','迎驾','今世缘','口子窖','老白干','水井坊','舍得','酒鬼','福元','浙江医药','南京医药','冀衡','中国医药','亿帆'],
    '军工': ['军工','航发','中航','沈飞','西飞','洪都','成飞','贵航','航天科技','航天通信','北方导航','光电股份','中光学'],
    '新能源': ['新能源','锂电','宁德','比亚迪','光伏','隆基','通威','阳光','风电','金风','明阳智能','天合','晶澳','东方日升'],
    '房地产': ['地产','万科','保利','招商','绿地','华夏幸福','碧桂园','融创','龙湖','金地','华发','城建'],
}

def match_sector(name):
    r = []
    for sec, kws in SECTOR_KW.items():
        for kw in kws:
            if kw in name:
                r.append(sec)
                break
    return r

def is_defensive(name):
    matched = match_sector(name)
    return any(s in DEFENSIVE_SECTORS for s in matched)

# ==================== 1. 获取全量股票 ====================
def _parse_stock_batch(batch):
    """并发解析单批次行情(线程安全)"""
    syms = [f'sh{c}' if c.startswith('6') else f'sz{c}' for c in batch]
    url = f'https://qt.gtimg.cn/q={",".join(syms)}'
    out = []
    try:
        r = _session().get(url, timeout=8)
        for line in r.text.strip().split(';'):
            if not line.strip():
                continue
            try:
                eq = line.index('=')
                content = line[eq+2:].strip().rstrip('"').rstrip('";').strip('"')
                f = content.split('~')
                if len(f) < 50:
                    continue
                code, name = f[2], f[1]
                price = float(f[3]) if f[3] else 0
                lc = float(f[4]) if f[4] else 0
                if price <= 0 or lc <= 0:
                    continue
                if 'ST' in name or '退' in name:
                    continue
                out.append({
                    'code': code, 'name': name, 'price': price,
                    'circ_mv': float(f[44]) if len(f) > 44 and f[44] and f[44] != '-' else 0,
                })
            except:
                continue
    except:
        pass
    return out


def get_all_stocks():
    stocks = []
    sh = [f'60{i:04d}' for i in range(0, 4001)]
    sz = [f'{i:06d}' for i in range(1, 3000)]
    codes = sh + sz
    codes = [c for c in codes if not c.startswith('688') and not c.startswith('300') and not c.startswith('301')]

    bs = 100
    nb = (len(codes) + bs - 1) // bs
    batches = [codes[bi*bs:(bi+1)*bs] for bi in range(nb)]
    print(f"  总代码:{len(codes)} 批次:{nb} 并发:{min(_WORKERS, nb)}", flush=True)

    done = 0
    with ThreadPoolExecutor(max_workers=min(_WORKERS, nb)) as ex:
        for res in as_completed([ex.submit(_parse_stock_batch, b) for b in batches]):
            stocks.extend(res.result())
            done += 1
            if done % 15 == 0 or done == nb:
                print(f"  进度:{done}/{nb} 已获取:{len(stocks)}", flush=True)
    return stocks

# ==================== 2. 获取K线 ====================
def get_kl(code, datalen=80):
    p = 'sh' if code.startswith('6') else 'sz'
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={p}{code},day,,,{datalen},qfq'
    try:
        r = requests.get(url, timeout=8, headers=HEADERS)
        d = r.json().get('data', {}).get(f'{p}{code}', {})
        kl = d.get('day', []) or d.get('qfqday', [])
        if kl and len(kl) >= 10:
            return {
                'd': [k[0] for k in kl],
                'c': [float(k[2]) for k in kl],
                'o': [float(k[1]) for k in kl],
                'h': [float(k[3]) for k in kl],
                'l': [float(k[4]) for k in kl],
                'v': [float(k[5])/100 for k in kl],
            }
    except:
        pass
    return None

# ==================== 3. 获取指数K线 ====================
def get_index_kl(datalen=60):
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,{datalen},'
    try:
        r = requests.get(url, timeout=8, headers=HEADERS)
        d = r.json().get('data', {}).get('sh000001', {})
        kl = d.get('day', []) or d.get('qfqday', [])
        if kl and len(kl) >= 10:
            return {
                'd': [k[0] for k in kl],
                'c': [float(k[2]) for k in kl],
                'o': [float(k[1]) for k in kl],
                'h': [float(k[3]) for k in kl],
                'l': [float(k[4]) for k in kl],
                'v': [float(k[5]) for k in kl],
            }
    except:
        pass
    return None

# ==================== 4. 技术分析(T-1日, 扩展版) ====================
def analyze_for_date(kl, target_date_str):
    dates = kl['d']
    target_idx = -1
    for i, d in enumerate(dates):
        if target_date_str in d:
            target_idx = i
            break

    if target_idx < 5:
        return None

    yi = target_idx - 1
    if yi < 5:
        return None

    c = kl['c']; o = kl['o']; h = kl['h']; l = kl['l']; v = kl['v']

    yc = c[yi]; yo = o[yi]; yh = h[yi]; yl = l[yi]; yv = v[yi]
    pc = c[yi-1]; pv = v[yi-1]

    yp = (yc/pc - 1) * 100 if pc > 0 else 0
    ya = (yh - yl) / pc * 100 if pc > 0 else 0
    yang = yc >= yo
    vr = yv / pv if pv > 0 else 1

    t2_pct = (c[yi-1] / c[yi-2] - 1) * 100 if yi >= 2 and c[yi-2] > 0 else 0

    ma5 = sum(c[max(0, yi-4):yi+1]) / min(5, yi+1)
    above_ma5 = yc > ma5 if ma5 > 0 else False
    ma10a = c[max(0, yi-9):yi+1]
    ma10 = sum(ma10a) / len(ma10a) if len(ma10a) >= 5 else 0
    ma20a = c[max(0, yi-19):yi+1]
    ma20 = sum(ma20a) / len(ma20a) if len(ma20a) >= 10 else ma10
    ma_bull = ma5 > ma10 if ma5 > 0 and ma10 > 0 else False

    dev_ma5 = (yc / ma5 - 1) * 100 if ma5 > 0 else 0
    dev_ma10 = (yc / ma10 - 1) * 100 if ma10 > 0 else 0
    dev_ma20 = (yc / ma20 - 1) * 100 if ma20 > 0 else 0

    p5 = (yc / c[yi-5] - 1) * 100 if yi >= 5 and c[yi-5] > 0 else 0

    gains = []
    losses = []
    for j in range(max(1, yi-5), yi+1):
        chg = c[j] - c[j-1]
        if chg > 0:
            gains.append(chg)
        else:
            losses.append(abs(chg))
    avg_gain = sum(gains) / 6 if gains else 0
    avg_loss = sum(losses) / 6 if losses else 0.001
    rsi6 = 100 - 100 / (1 + avg_gain / avg_loss) if avg_loss > 0 else 100

    z5 = z30 = mxc = cur = 0
    for j in range(1, yi+1):
        if c[j-1] > 0:
            dp = (c[j] / c[j-1] - 1) * 100
            if dp >= 9.5:
                z30 += 1
                if j >= yi - 4:
                    z5 += 1
                cur += 1
                mxc = max(mxc, cur)
            else:
                cur = 0

    yzt = yp >= 9.5
    ls = min(yo, yc) - yl
    bd = abs(yc - yo)
    hll = ls > bd * 1.5 if bd > 0 else ls > 0

    h20 = max(h[max(0, yi-19):yi+1]) if yi >= 1 else 0
    bk20 = yc >= h20 * 0.98 if h20 > 0 else False
    bks = yc >= h20 if h20 > 0 else False
    dist_high20 = (yc / h20 - 1) * 100 if h20 > 0 else -10

    vw = v[max(0, yi-4):yi+1]
    av5 = sum(vw) / len(vw) if vw else yv
    vexp = yv > av5 * 1.2 if av5 > 0 else False

    t_open = o[target_idx]
    t_close = c[target_idx]
    t_high = h[target_idx]
    t_pct = (t_close / yc - 1) * 100 if yc > 0 else 0
    t_max_pct = (t_high / yc - 1) * 100 if yc > 0 else 0
    t_open_pct = (t_open / yc - 1) * 100 if yc > 0 else 0

    return {
        'yest_close': round(yc, 2), 'yest_pct': round(yp, 2),
        'yest_amp': round(ya, 2), 'is_yang': yang,
        'vol_ratio': round(vr, 2), 'above_ma5': above_ma5,
        'ma_bull': ma_bull, 'ma5': round(ma5, 2), 'ma10': round(ma10, 2),
        'ma20': round(ma20, 2),
        'dev_ma5': round(dev_ma5, 2), 'dev_ma10': round(dev_ma10, 2),
        'dev_ma20': round(dev_ma20, 2),
        'pct_5d': round(p5, 2), 'zt_5d': z5, 'zt_30d': z30,
        'max_consec': mxc, 'is_yest_zt': yzt, 'has_long_lower': hll,
        'breakout_20d': bk20, 'breakout_strong': bks,
        'vol_expanding': vexp, 'high_20d': round(h20, 2),
        'dist_high20': round(dist_high20, 2),
        'rsi6': round(rsi6, 1), 't2_pct': round(t2_pct, 2),
        'today_pct': round(t_pct, 2), 'today_max_pct': round(t_max_pct, 2),
        'today_open_pct': round(t_open_pct, 2),
    }

# ==================== 5. 大盘走势分析 ====================
def analyze_market(index_kl, target_date_str):
    dates = index_kl['d']
    c = index_kl['c']; o = index_kl['o']; h = index_kl['h']; l = index_kl['l']; v = index_kl['v']

    target_idx = -1
    for i, d in enumerate(dates):
        if target_date_str in d:
            target_idx = i
            break
    if target_idx < 6:
        return None

    yi = target_idx - 1

    consec_up = 0
    for j in range(yi, 0, -1):
        dp = (c[j] / c[j-1] - 1) * 100
        if dp > 0:
            consec_up += 1
        else:
            break

    pct_1d = (c[yi] / c[yi-1] - 1) * 100 if yi >= 1 else 0
    pct_3d = (c[yi] / c[yi-3] - 1) * 100 if yi >= 3 else 0
    pct_5d = (c[yi] / c[yi-5] - 1) * 100 if yi >= 5 else 0

    vol_recent = sum(v[max(0, yi-4):yi+1]) / min(5, yi+1)
    vol_before = sum(v[max(0, yi-9):max(0, yi-4)]) / min(5, max(1, yi-4))
    vol_ratio = vol_recent / vol_before if vol_before > 0 else 1
    vol_expanding = vol_ratio > 1.2

    ma5 = sum(c[max(0, yi-4):yi+1]) / min(5, yi+1)
    ma10 = sum(c[max(0, yi-9):yi+1]) / min(10, yi+1) if yi >= 9 else ma5
    ma20 = sum(c[max(0, yi-19):yi+1]) / min(20, yi+1) if yi >= 19 else ma10
    above_ma5 = c[yi] > ma5
    above_ma20 = c[yi] > ma20
    ma_bull = ma5 > ma10 > ma20
    dev_ma5 = (c[yi] / ma5 - 1) * 100
    dev_ma20 = (c[yi] / ma20 - 1) * 100

    t1_yang = c[yi] > o[yi]
    t1_big_yang = (c[yi] / o[yi] - 1) * 100 > 1.5

    has_recent_pullback = False
    pullback_depth = 0
    for j in range(yi, max(yi-3, 0), -1):
        dp = (c[j] / c[j-1] - 1) * 100
        if dp < -0.5:
            has_recent_pullback = True
            pullback_depth = min(pullback_depth, dp)

    # T日实际数据 (用于用户判断)
    t_close = c[target_idx]
    t_open = o[target_idx]
    t_pct = (t_close / c[yi] - 1) * 100 if c[yi] > 0 else 0
    t_open_pct = (t_open / c[yi] - 1) * 100 if c[yi] > 0 else 0
    t_yang = t_close > t_open

    return {
        'consec_up': consec_up,
        'pct_1d': round(pct_1d, 2),
        'pct_3d': round(pct_3d, 2),
        'pct_5d': round(pct_5d, 2),
        'vol_ratio': round(vol_ratio, 2),
        'vol_expanding': vol_expanding,
        'ma5': round(ma5, 2), 'ma10': round(ma10, 2), 'ma20': round(ma20, 2),
        'above_ma5': above_ma5, 'above_ma20': above_ma20,
        'ma_bull': ma_bull,
        'dev_ma5': round(dev_ma5, 2), 'dev_ma20': round(dev_ma20, 2),
        't1_yang': t1_yang, 't1_big_yang': t1_big_yang,
        'has_recent_pullback': has_recent_pullback,
        'pullback_depth': round(pullback_depth, 2),
        't_pct': round(t_pct, 2),
        't_open_pct': round(t_open_pct, 2),
        't_yang': t_yang,
        't1_close': round(c[yi], 2),
        't_close': round(t_close, 2),
    }

# ==================== 6. 用户判断态势 (v12.22核心) ====================
def determine_regime_user(market):
    """
    用T日实际收盘数据模拟用户手动判断大盘态势
    用户在9:20时告知系统当天走势判断, 回测中使用T日实际收盘模拟完美判断
    """
    if not market:
        return 'BULL', '震荡偏多(默认追强)', 0.5, {}

    t_pct = market.get('t_pct', 0)
    t_open_pct = market.get('t_open_pct', 0)
    t_yang = market.get('t_yang', False)

    user_judgment = {
        't_pct': t_pct,
        't_open_pct': t_open_pct,
        't_yang': t_yang,
        't1_close': market.get('t1_close', 0),
        't_close': market.get('t_close', 0),
    }

    # BULL: T日涨幅 >= 1%
    if t_pct >= 1.0:
        if t_open_pct > 0:
            return 'BULL', '高开高走(用户判断:强势上行)', 0.95, user_judgment
        else:
            return 'BULL', '低开高走(用户判断:强势上行)', 0.95, user_judgment

    # RANGE_BULL偏强: T日涨幅 0.3% ~ 1%
    if t_pct >= 0.3:
        return 'RANGE_BULL', '震荡偏多(用户判断:小幅上行)', 0.75, user_judgment

    # RANGE_BULL中性: T日涨幅 -0.5% ~ 0.3%
    if t_pct >= -0.5:
        return 'RANGE_BULL', '震荡(用户判断:横盘整理)', 0.75, user_judgment

    # CORRECTION: T日涨幅 -1.5% ~ -0.5%
    if t_pct >= -1.5:
        return 'CORRECTION', '回调(用户判断:防守优先)', 0.85, user_judgment

    # 大跌回调: T日涨幅 < -1.5%
    return 'CORRECTION', '大跌回调(用户判断:防守优先)', 0.90, user_judgment

# ==================== 7. v12.12 基础评分 ====================
def score_v1212(tech, hot_sectors, name, code):
    matched = match_sector(name)
    bs = ''
    sm = 1.0
    for s in matched:
        if s in hot_sectors:
            bs = s
            stats = hot_sectors[s]
            sm = 1.5 if stats.get('zt', 0) >= 3 else (1.4 if stats.get('surging', 0) >= 3 else (1.2 if stats.get('surging', 0) >= 2 else 1.0))
            break
    if not bs and matched:
        bs = matched[0]

    g = 0
    if tech['zt_30d'] >= 10: g = 15
    elif tech['zt_30d'] >= 5: g = 12
    elif tech['zt_30d'] >= 3: g = 8
    elif tech['zt_30d'] >= 1: g = 4
    if tech['max_consec'] >= 3: g = min(g + 3, 15)

    p = 0
    if tech['yest_amp'] >= 3: p += 5
    if tech['is_yang']: p += 5
    if tech['above_ma5']: p += 5
    if tech['has_long_lower']: p = min(p + 3, 15)
    p = min(p, 15)

    vp = 0
    vr = tech['vol_ratio']
    if vr >= 3: vp = 15
    elif vr >= 2: vp = 12
    elif vr >= 1.5: vp = 8
    elif vr >= 1: vp = 5
    if tech['vol_expanding']: vp = min(vp + 3, 15)

    m = 0
    p5 = tech['pct_5d']
    if p5 >= 20: m = 15
    elif p5 >= 15: m = 12
    elif p5 >= 10: m = 10
    elif p5 >= 5: m = 7
    elif p5 >= 0: m = 3
    if tech['breakout_20d']: m = min(m + 3, 15)

    ss = 0
    if bs in hot_sectors:
        stats = hot_sectors[bs]
        if stats.get('zt', 0) >= 3: ss = 20
        elif stats.get('surging', 0) >= 3: ss = 16
        elif stats.get('surging', 0) >= 2: ss = 12

    cc = 0
    if tech['is_yest_zt']:
        if tech['max_consec'] >= 3: cc = 10
        elif tech['zt_5d'] >= 2: cc = 8
        else: cc = 6

    rsi_s = 0
    rsi = tech['rsi6']
    if 40 <= rsi <= 60: rsi_s = 10
    elif 60 < rsi <= 70: rsi_s = 8
    elif 30 <= rsi < 40: rsi_s = 7
    elif 70 < rsi <= 80: rsi_s = 5
    elif rsi > 80: rsi_s = 2
    else: rsi_s = 3

    base = g + p + vp + m + ss + cc + rsi_s
    score = base * sm

    result = {
        'code': code, 'name': name, 'score': round(score, 1),
        'g': g, 'p': p, 'vp': vp, 'm': m, 'ss': ss, 'cc': cc, 'rsi_s': rsi_s,
        'sector': bs, 'sector_mult': sm,
    }
    result.update(tech)
    return result

# ==================== 8. 热点板块检测 ====================
def detect_hot_for_date(all_kls, target_date):
    sector_stats = defaultdict(lambda: {'total': 0, 'surging': 0, 'zt': 0})
    for code, kl in all_kls.items():
        tech = analyze_for_date(kl, target_date)
        if not tech:
            continue
        name = kl.get('name', '')
        matched = match_sector(name)
        for sec in matched:
            sector_stats[sec]['total'] += 1
            if tech['today_pct'] >= 5:
                sector_stats[sec]['surging'] += 1
            if tech['today_pct'] >= 9.5:
                sector_stats[sec]['zt'] += 1
    return {sec: stats for sec, stats in sector_stats.items() if stats['surging'] >= 2}

# ==================== 9. 板块轮动检测 ====================
def build_sector_zt_history(all_kls, relevant_dates):
    history = defaultdict(lambda: defaultdict(int))
    for code, kl in all_kls.items():
        name = kl.get('name', '')
        matched = match_sector(name)
        if not matched:
            continue
        dates = kl['d']
        closes = kl['c']
        for i in range(1, len(dates)):
            if dates[i] not in relevant_dates and dates[i-1] not in relevant_dates:
                continue
            if closes[i-1] > 0:
                pct = (closes[i] / closes[i-1] - 1) * 100
                if pct >= 9.5:
                    for sec in matched:
                        history[dates[i]][sec] += 1
    return history

def detect_sector_acceleration(target_date, sector_zt_history):
    all_dates = sorted(sector_zt_history.keys())
    target_idx = -1
    for i, d in enumerate(all_dates):
        if target_date in d:
            target_idx = i
            break
    if target_idx < 0:
        return {}

    today_zt = sector_zt_history.get(all_dates[target_idx], {})
    yest_zt = sector_zt_history.get(all_dates[target_idx - 1], {}) if target_idx >= 1 else {}
    d2_zt = sector_zt_history.get(all_dates[target_idx - 2], {}) if target_idx >= 2 else {}
    d3_zt = sector_zt_history.get(all_dates[target_idx - 3], {}) if target_idx >= 3 else {}

    result = {}
    all_sectors = set(today_zt.keys()) | set(yest_zt.keys()) | set(d2_zt.keys()) | set(d3_zt.keys())

    for sec in all_sectors:
        t0 = today_zt.get(sec, 0)
        t1 = yest_zt.get(sec, 0)
        t2 = d2_zt.get(sec, 0)
        t3 = d3_zt.get(sec, 0)
        total_4d = t0 + t1 + t2 + t3

        if t1 >= 2 and t2 >= 2:
            result[sec] = '主线确认'
        elif t1 >= 2 and t0 >= 1:
            result[sec] = '加速'
        elif t1 >= 2 and t2 < 2:
            result[sec] = '新起'
        elif t1 >= 1 and t2 >= 1 and t3 >= 1:
            result[sec] = '主线确认'
        elif total_4d >= 4 and t1 == 0:
            result[sec] = '衰减'
        elif t1 >= 1 and t2 == 0:
            result[sec] = '新起'

    return result

# ==================== 10. 起爆初期检测 (放宽条件) ====================
def detect_early_surge(r, sector_accel, local_bull_sectors):
    bonus = 0
    signals = []

    name = r.get('name', '')
    p5 = r.get('pct_5d', 0)
    rsi = r.get('rsi6', 50)
    vr = r.get('vol_ratio', 1.0)
    above_ma5 = r.get('above_ma5', False)
    has_ll = r.get('has_long_lower', False)
    is_yang = r.get('yest_pct', 0) >= 0
    t2_pct = r.get('t2_pct', 0)
    yest_pct = r.get('yest_pct', 0)
    dist_h20 = r.get('dist_high20', -10)
    zt30 = r.get('zt_30d', 0)
    mc = r.get('max_consec', 0)

    matched = match_sector(name)

    if 0 <= p5 <= 5 and 35 <= rsi <= 65 and vr < 1.5 and above_ma5:
        bonus += 5
        signals.append('蓄势待发+5')

    sector_hot = False
    for sec in matched:
        if sec in sector_accel and sector_accel[sec] in ('加速', '主线确认', '新起'):
            sector_hot = True
            break
        if sec in local_bull_sectors:
            sector_hot = True
            break

    if has_ll and vr < 1.5 and sector_hot:
        bonus += 8
        signals.append('起爆信号+8')
    elif has_ll and vr < 1.5:
        bonus += 3
        signals.append('长下影缩量+3')

    if t2_pct > yest_pct and is_yang and above_ma5 and t2_pct > 0:
        bonus += 6
        signals.append('加速趋势+6')

    if sector_hot and above_ma5 and zt30 >= 1:
        bonus += 7
        signals.append('板块共振+7')
    elif sector_hot and above_ma5:
        bonus += 4
        signals.append('板块跟随+4')

    if dist_h20 < -10 and above_ma5 and is_yang:
        bonus += 5
        signals.append('低位起爆+5')

    if mc >= 1 and sector_hot and above_ma5:
        bonus += 3
        signals.append('连板+板块+3')

    bonus = min(bonus, 20)
    return bonus, signals

# ==================== 11. 局部牛市检测 ====================
def detect_local_bull(pool):
    sector_zt = defaultdict(int)
    for code, r in pool.items():
        if r.get('is_yest_zt', False) or r.get('yest_pct', 0) >= 9.5:
            matched = match_sector(r.get('name', ''))
            for sec in matched:
                sector_zt[sec] += 1
    return {sec: cnt for sec, cnt in sector_zt.items() if cnt >= 2}

# ==================== 12. 竞价5级评分 ====================
def auction_score_v1219(r):
    open_pct = r.get('today_open_pct', 0)
    vr = r.get('vol_ratio', 1.0)

    if -1 <= open_pct <= 0 and vr >= 2:
        return 95, '极强(低开放量)'
    if 0 < open_pct <= 1 and vr >= 1.5:
        return 88, '强(平开放量)'
    if -1 <= open_pct <= 0:
        return 85, '强(低开)'
    if 0 < open_pct <= 1:
        return 82, '强(小幅高开)'
    if -3 <= open_pct < -1:
        return 78, '中(低开-1~-3%)'
    if 1 < open_pct <= 2:
        return 75, '中(小高开)'
    if 2 < open_pct <= 4:
        return 60, '弱(高开2-4%)'
    if open_pct > 4:
        return 40, '极弱(高开>4%)'
    return 50, '极弱(深低开<-3%)'

# ==================== 13. 追涨连板风险控制 ====================
def apply_zt_chase_penalty(r, mode='BULL'):
    is_zt = r.get('is_yest_zt', False)
    open_pct = r.get('today_open_pct', 0)
    mc = r.get('max_consec', 0)
    yest_pct = r.get('yest_pct', 0)
    return 0, ''

# ==================== ★ v12.24 BULL模式: 主线龙头优先 + 激进追强 ====================
def score_bull_v1224(r, local_bull_sectors, sector_accel, hot_sectors):
    """
    v12.24 BULL模式重写: 激进追强, 聚焦主线龙头
    核心改进:
    1. 移除"无板块热度涨停-12"重罚 (v12.23的过度保守问题)
    2. 主线板块+T-1涨停 = 龙头信号 (+12)
    3. 连板续涨奖励: 3连板+8, 2连板+5
    4. 追涨风险大幅放宽: 仅高开>7%且无板块热度才轻罚
    5. 板块热度权重40% (主导因子)
    """
    base = r.get('v1212_score', r.get('score', 0))

    matched = match_sector(r.get('name', ''))
    is_zt = r.get('is_yest_zt', False)
    open_pct = r.get('today_open_pct', 0)
    mc = r.get('max_consec', 0)
    vr = r.get('vol_ratio', 1.0)
    above_ma5 = r.get('above_ma5', False)
    p5 = r.get('pct_5d', 0)
    rsi = r.get('rsi6', 50)
    zt5 = r.get('zt_5d', 0)
    zt30 = r.get('zt_30d', 0)

    # 1. 板块热度 (权重40%, 最高+20) - 主导因子
    sector_heat = 0
    local_bonus = 0
    accel_bonus = 0
    sector_hot = False
    mainline = False

    for sec in matched:
        if sec in local_bull_sectors:
            local_bonus += 10
            sector_hot = True
        if sec in sector_accel:
            status = sector_accel[sec]
            if status == '主线确认':
                accel_bonus += 8
                sector_hot = True
                mainline = True
            elif status == '加速':
                accel_bonus += 6
                sector_hot = True
            elif status == '新起':
                accel_bonus += 5
                sector_hot = True
            elif status == '衰减':
                accel_bonus -= 3

        # 热点板块涨停数加分
        if sec in hot_sectors:
            stats = hot_sectors[sec]
            if stats.get('zt', 0) >= 3:
                accel_bonus += 5
                sector_hot = True
            elif stats.get('zt', 0) >= 2:
                accel_bonus += 3

    sector_heat = local_bonus + accel_bonus

    # 2. 主线龙头识别 (权重20%, 最高+12)
    leader_bonus = 0
    if is_zt and mainline:
        leader_bonus = 12
    elif is_zt and sector_hot:
        leader_bonus = 8
    elif is_zt and mc >= 2:
        leader_bonus = 5

    # 3. 连板续涨奖励 (权重15%, 最高+8) - BULL模式独有
    continuation_bonus = 0
    if is_zt:
        if mc >= 3:
            continuation_bonus = 8
        elif mc >= 2:
            continuation_bonus = 5
        elif zt5 >= 2:
            continuation_bonus = 3

    # 4. 起爆初期信号 (权重10%)
    surge_bonus, surge_signals = detect_early_surge(r, sector_accel, local_bull_sectors)

    # 5. 追涨风险控制 (BULL模式大幅放宽)
    chase_penalty = 0
    chase_reason = ''
    if is_zt and open_pct > 7 and not sector_hot:
        chase_penalty = -8
        chase_reason = f'高开{open_pct:.1f}%无板块-8'
    elif is_zt and open_pct > 7 and sector_hot:
        chase_penalty = -3
        chase_reason = f'高开{open_pct:.1f}%轻罚-3'
    elif mc >= 5 and open_pct > 5 and not sector_hot:
        chase_penalty = -5
        chase_reason = f'{mc}连板高开{open_pct:.1f}%无板块-5'

    # 综合评分
    final = base + sector_heat * 0.40 + leader_bonus * 0.20 + continuation_bonus * 0.15 + surge_bonus * 0.10 + chase_penalty

    total_bonus = sector_heat + leader_bonus + continuation_bonus + surge_bonus + chase_penalty

    all_signals = surge_signals[:]
    if local_bonus:
        all_signals.append(f"局部牛+{local_bonus}")
    if accel_bonus:
        all_signals.append(f"轮动{accel_bonus:+d}")
    if leader_bonus:
        all_signals.append(f"龙头+{leader_bonus}")
    if continuation_bonus:
        all_signals.append(f"连板续涨+{continuation_bonus}")
    if chase_penalty:
        all_signals.append(chase_reason)

    return round(final, 1), all_signals, total_bonus

# ==================== 14. CORRECTION模式评分 (含板块热度加分) ====================
def score_correction_v1223(r, local_bull_sectors, sector_accel):
    g = 0
    zt30 = r.get('zt_30d', 0)
    if zt30 >= 10: g = 10
    elif zt30 >= 5: g = 8
    elif zt30 >= 3: g = 5
    elif zt30 >= 1: g = 3
    mc = r.get('max_consec', 0)
    if mc >= 3: g = min(g + 2, 10)

    p = 0
    has_ll = r.get('has_long_lower', False)
    yest_amp = r.get('yest_amp', abs(r.get('yest_pct', 0)) + 2)
    is_yang = r.get('yest_pct', 0) >= 0
    above_ma5 = r.get('above_ma5', False)
    if has_ll: p += 6
    if yest_amp >= 3: p += 3
    if is_yang: p += 3
    if above_ma5: p += 3
    p = min(p, 15)

    vp = 0
    vr = r.get('vol_ratio', 1.0)
    if vr < 1: vp = 8
    elif vr < 1.5: vp = 6
    elif vr >= 3: vp = 10
    elif vr >= 2: vp = 7
    else: vp = 5

    pq = 0
    dev10 = abs(r.get('dev_ma10', 0))
    dev20 = abs(r.get('dev_ma20', 0))
    if dev10 <= 2: pq = 8
    elif dev10 <= 4: pq = 6
    elif dev10 <= 7: pq = 3
    if dev20 <= 3: pq += 6
    elif dev20 <= 5: pq += 4
    elif dev20 <= 8: pq += 2
    if r.get('dev_ma20', 0) >= 0: pq += 3
    dist_h20 = r.get('dist_high20', -10)
    if -20 <= dist_h20 <= -5: pq += 3
    pq = min(pq, 20)

    ss = 0
    name = r.get('name', '')
    if is_defensive(name): ss = 10
    zt5 = r.get('zt_5d', 0)
    is_zt = r.get('is_yest_zt', False)
    if zt5 >= 1 and not is_zt: ss = min(ss + 3, 15)

    m = 0
    p5 = r.get('pct_5d', 0)
    if -3 <= p5 <= 5: m = 10
    elif 5 < p5 <= 12: m = 7
    elif -8 <= p5 < -3: m = 6
    elif 12 < p5 <= 20: m = 4
    elif p5 > 20: m = 2

    rsi_s = 0
    rsi = r.get('rsi6', 50)
    if 30 <= rsi <= 50: rsi_s = 10
    elif 50 < rsi <= 65: rsi_s = 8
    elif 25 <= rsi < 30: rsi_s = 6
    elif 65 < rsi <= 75: rsi_s = 4
    elif rsi > 75: rsi_s = 1
    else: rsi_s = 3

    cc = 0
    if is_zt:
        if mc >= 3: cc = 5
        elif zt5 >= 2: cc = 3
        else: cc = 2

    sg = 0
    if 0 <= p5 <= 8 and above_ma5 and is_yang: sg += 3
    if has_ll and vr < 1.5: sg += 2
    sg = min(sg, 5)

    rk = 0
    if mc >= 6: rk = 15
    elif mc >= 5: rk = 10
    elif mc >= 4: rk = 5
    if p5 > 25: rk += 8
    elif p5 > 20: rk += 5
    if rsi > 80: rk += 5
    if abs(r.get('dev_ma5', 0)) > 8: rk += 3
    rk = min(rk, 25)

    t1_score = (g + p + vp + pq + ss + m + rsi_s + cc + sg - rk)
    auction, auction_label = auction_score_v1219(r)
    base_score = t1_score * 0.6 + auction * 0.4

    # v12.23: 板块热度加分 (回调日局部牛更有价值)
    sector_heat_boost = 0
    matched = match_sector(r.get('name', ''))
    for sec in matched:
        if sec in local_bull_sectors:
            sector_heat_boost += 6
        if sec in sector_accel:
            status = sector_accel[sec]
            if status == '主线确认': sector_heat_boost += 4
            elif status == '加速': sector_heat_boost += 3
            elif status == '新起': sector_heat_boost += 2

    surge_bonus, surge_signals = detect_early_surge(r, sector_accel, local_bull_sectors)
    final_score = base_score + surge_bonus * 0.15 + sector_heat_boost * 0.15

    penalty, pen_reason = apply_zt_chase_penalty(r, 'CORRECTION')
    final_score += penalty

    all_signals = surge_signals[:]
    if sector_heat_boost:
        all_signals.append(f"板块热度+{sector_heat_boost}")
    if penalty:
        all_signals.append(pen_reason)

    return round(final_score, 1), all_signals, auction, auction_label

# ==================== ★ v12.23 RANGE_BULL模式: 5攻1防 ====================
def score_range_bull_v1223(r, local_bull_sectors, sector_accel):
    bull_base = r.get('v1212_score', r.get('score', 0))

    low_buy_bonus = 0
    vr = r.get('vol_ratio', 1.0)
    above_ma5 = r.get('above_ma5', False)
    is_yang = r.get('yest_pct', 0) >= 0
    has_ll = r.get('has_long_lower', False)
    dev_ma5 = r.get('dev_ma5', 0)
    rsi = r.get('rsi6', 50)
    p5 = r.get('pct_5d', 0)

    if vr < 1.2 and above_ma5 and -3 <= dev_ma5 <= 2:
        low_buy_bonus += 8
    if vr < 1.3 and is_yang and above_ma5:
        low_buy_bonus += 4
    if has_ll and vr < 1.5:
        low_buy_bonus += 4
    if 35 <= rsi <= 55:
        low_buy_bonus += 3
    if 0 <= p5 <= 8:
        low_buy_bonus += 3
    low_buy_bonus = min(low_buy_bonus, 18)

    local_bonus = 0
    matched = match_sector(r.get('name', ''))
    for sec in matched:
        if sec in local_bull_sectors:
            local_bonus += 8

    surge_bonus, surge_signals = detect_early_surge(r, sector_accel, local_bull_sectors)

    accel_bonus = 0
    for sec in matched:
        if sec in sector_accel:
            status = sector_accel[sec]
            if status == '主线确认': accel_bonus += 6
            elif status == '加速': accel_bonus += 5
            elif status == '新起': accel_bonus += 4
            elif status == '衰减': accel_bonus -= 3

    def_bonus = 0
    if is_defensive(r.get('name', '')):
        def_bonus += 5
    dist_h20 = r.get('dist_high20', -10)
    if -20 <= dist_h20 <= -5:
        def_bonus += 3

    weighted = low_buy_bonus * 0.20 + local_bonus * 0.15 + surge_bonus * 0.15 + accel_bonus * 0.15 + def_bonus * 0.10
    weighted = min(weighted, 14)

    final = bull_base + weighted
    penalty, pen_reason = apply_zt_chase_penalty(r, 'RANGE_BULL')
    final += penalty

    all_signals = surge_signals[:]
    if low_buy_bonus:
        all_signals.append(f"低吸+{low_buy_bonus}")
    if local_bonus:
        all_signals.append(f"局部牛+{local_bonus}")
    if accel_bonus:
        all_signals.append(f"轮动{accel_bonus:+d}")
    if def_bonus:
        all_signals.append(f"防守+{def_bonus}")
    if penalty:
        all_signals.append(pen_reason)

    total_bonus = low_buy_bonus + local_bonus + surge_bonus + accel_bonus + def_bonus
    return round(final, 1), all_signals, total_bonus

# ==================== 16. 买卖点建议 ====================
def generate_trade_advice(r, regime, sector_accel, local_bull):
    yest_close = r.get('yest_close', 0)
    ma5 = r.get('ma5', yest_close)
    ma10 = r.get('ma10', yest_close)
    open_pct = r.get('today_open_pct', 0)
    above_ma5 = r.get('above_ma5', False)
    is_zt = r.get('is_yest_zt', False)
    mc = r.get('max_consec', 0)
    rsi = r.get('rsi6', 50)

    name = r.get('name', '')
    matched = match_sector(name)
    sector_hot = any(s in sector_accel and sector_accel[s] in ('加速', '主线确认', '新起') for s in matched)
    sector_lb = any(s in local_bull for s in matched)

    if above_ma5 and open_pct <= 0:
        buy_point = f"低开/平开买入, 参考价{yest_close * (1 + open_pct/100):.2f}({open_pct:+.1f}%)"
    elif above_ma5 and 0 < open_pct <= 2:
        buy_point = f"小幅高开可追, 回踩MA5({ma5:.2f})加仓"
    elif not above_ma5:
        buy_point = f"回踩MA5({ma5:.2f})附近低吸, 不追高"
    elif open_pct > 2:
        buy_point = f"高开观望, 回调到{yest_close:.2f}附近再入"
    else:
        buy_point = f"MA5({ma5:.2f})上方持有, 跌破观望"

    if ma10 > 0:
        stop_loss = f"止损: MA10({ma10:.2f})或-3%, 先到先出"
    else:
        stop_loss = "止损: -3%"

    if regime == 'BULL' and (sector_hot or sector_lb):
        take_profit = "止盈: 涨停板封不住再卖, 目标+10%以上"
    elif regime == 'BULL':
        take_profit = "止盈: +8%减半仓, 涨停持有"
    elif regime == 'RANGE_BULL':
        take_profit = "止盈: +5~8%分批止盈"
    elif regime == 'CORRECTION':
        take_profit = "止盈: +5%快速止盈, 不贪"
    else:
        take_profit = "止盈: +3%即走, 保守操作"

    if is_zt and mc >= 3:
        hold = "持仓不动, 连板强者恒强"
    elif sector_hot and above_ma5:
        hold = "板块加速中, 持股待涨"
    elif regime == 'BULL' and above_ma5:
        hold = "多头持仓, 跌破MA5减仓"
    elif regime == 'RANGE_BULL' and above_ma5:
        hold = "均衡持仓, +5%分批止盈, 跌破MA10减仓"
    elif regime == 'CORRECTION' and is_defensive(name):
        hold = "防守仓位, 大盘企稳后加仓"
    elif rsi > 75:
        hold = "RSI偏高, 注意回调风险, 半仓操作"
    else:
        hold = "正常持仓, 破MA10离场"

    return {'buy_point': buy_point, 'stop_loss': stop_loss,
            'take_profit': take_profit, 'hold_advice': hold}

# ==================== 主函数 ====================
def main():
    bj = now_bj()
    print(f"v12.24 一个月回测(BULL激进主线龙头版)  {bj.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)
    print(f"回测周期: {BACKTEST_DATES[0]} ~ {BACKTEST_DATES[-1]} ({len(BACKTEST_DATES)}个交易日)")
    print(f"v12.24核心: BULL激进主线龙头 | 继承v12.22: 用户判断态势 | 继承v12.23: RANGE_BULL 5攻1防+CORRECTION板块热度")

    # Step 1: 获取全量股票
    print("\n[1] 获取全量股票列表...")
    stocks = get_all_stocks()
    print(f"  获取: {len(stocks)}只")

    # Step 2: 基础筛选
    print("\n[2] 基础筛选(价格2-55, 市值<280亿)...")
    filtered = [s for s in stocks if 2 <= s['price'] <= 55 and s['circ_mv'] <= 280]
    print(f"  过滤后: {len(filtered)}只")

    # Step 3: 抽样获取K线
    SAMPLE_SIZE = 1500
    print(f"\n[3] 抽样获取K线(目标{SAMPLE_SIZE}只)...")
    hot_stocks = [s for s in filtered if match_sector(s['name'])]
    other_stocks = [s for s in filtered if s not in hot_stocks]
    random.seed(42)
    if len(hot_stocks) >= SAMPLE_SIZE:
        sampled = hot_stocks[:SAMPLE_SIZE]
    else:
        need = SAMPLE_SIZE - len(hot_stocks)
        sampled = hot_stocks + random.sample(other_stocks, min(need, len(other_stocks)))
    print(f"  热点板块:{len(hot_stocks)}只, 随机抽样:{len(sampled)-len(hot_stocks)}只, 总计:{len(sampled)}只")

    all_kls = {}
    for i, s in enumerate(sampled):
        if i % 100 == 0:
            print(f"  K线进度:{i}/{len(sampled)}", flush=True)
        kl = get_kl(s['code'], 80)
        if kl:
            kl['name'] = s['name']
            all_kls[s['code']] = kl
        time.sleep(0.03)
    print(f"  K线获取成功: {len(all_kls)}只")

    # Step 4: 获取指数K线
    print(f"\n[4] 获取沪指K线...")
    index_kl = get_index_kl(60)
    if index_kl:
        print(f"  沪指K线: {len(index_kl['d'])}天")
    else:
        print("  ✗ 沪指K线获取失败!")

    # Step 5: 构建板块涨停历史
    print(f"\n[5] 构建板块涨停历史...")
    all_dates_set = set()
    for code, kl in all_kls.items():
        for d in kl['d']:
            all_dates_set.add(d)
    all_dates_sorted = sorted(all_dates_set)
    min_date = '2026-07-01'
    max_date = '2026-08-06'
    relevant_dates = [d for d in all_dates_sorted if min_date <= d <= max_date]
    print(f"  相关日期: {len(relevant_dates)}天")

    sector_zt_history = build_sector_zt_history(all_kls, relevant_dates)
    print(f"  板块涨停历史构建完成")

    # Step 6: 逐日回测
    print(f"\n[6] 回测{len(BACKTEST_DATES)}个交易日...")
    print("=" * 100)

    all_daily_results = {}
    all_picks = []
    regime_counts = defaultdict(int)

    for target_date in BACKTEST_DATES:
        print(f"\n--- {target_date} ---")

        hot_sectors = detect_hot_for_date(all_kls, target_date)

        market = analyze_market(index_kl, target_date) if index_kl else None
        # ★ v12.22核心: 用户判断态势
        regime, regime_name, confidence, user_judgment = determine_regime_user(market)
        regime_counts[regime] += 1

        if market:
            print(f"  大盘: T-1:{market.get('pct_1d',0):+.2f}% 3日:{market.get('pct_3d',0):+.2f}% 5日:{market.get('pct_5d',0):+.2f}%")
            print(f"  用户判断: T日实际{market.get('t_pct',0):+.2f}% 开盘{market.get('t_open_pct',0):+.2f}%")
        print(f"  态势: [{regime}] {regime_name} (置信度:{confidence:.0%})")

        if hot_sectors:
            for sec, st in sorted(hot_sectors.items(), key=lambda x: -x[1]['surging'])[:3]:
                print(f"  热点: {sec}(涨>5%={st['surging']},涨停={st['zt']})")

        sector_accel = detect_sector_acceleration(target_date, sector_zt_history)
        if sector_accel:
            print(f"  轮动: {sector_accel}")

        # 对所有股票分析+评分
        daily_scores = []
        for code, kl in all_kls.items():
            tech = analyze_for_date(kl, target_date)
            if not tech:
                continue
            if tech['today_open_pct'] >= 9.5:
                continue

            scored = score_v1212(tech, hot_sectors, kl['name'], code)
            scored['v1212_score'] = scored['score']
            daily_scores.append(scored)

        daily_scores.sort(key=lambda x: -x['score'])
        top20 = daily_scores[:20]
        top6_12 = daily_scores[:6]

        pool = {}
        for r in top20:
            pool[r['code']] = r

        local_bull = detect_local_bull(pool)
        if local_bull:
            print(f"  局部牛: {local_bull}")

        # ==================== v12.24 评分逻辑 ====================
        if regime == 'BULL':
            # ★ v12.24 BULL模式: 全进攻, 主线龙头优先, 激进追强
            scored_t20 = []
            for r in top20:
                r = dict(r)
                bull_score, signals, bonus = score_bull_v1224(r, local_bull, sector_accel, hot_sectors)
                r['v1219_score'] = bull_score
                r['v1224_score'] = bull_score
                r['v1219_mode'] = 'BULL'
                r['surge_signals'] = signals
                r['total_bonus'] = bonus
                r['is_defensive'] = is_defensive(r.get('name', ''))
                auc, auc_label = auction_score_v1219(r)
                r['auction_v1219'] = auc
                r['auction_label'] = auc_label
                scored_t20.append(r)

            scored_t20.sort(key=lambda x: -x['v1224_score'])

            t6_12_codes = set(r['code'] for r in top6_12)
            top6 = []
            new_count = 0
            max_new = 2
            for r in scored_t20:
                if len(top6) >= 6:
                    break
                is_new = r['code'] not in t6_12_codes
                if is_new and new_count >= max_new:
                    continue
                top6.append(r)
                if is_new:
                    new_count += 1
            if len(top6) < 6:
                for r in scored_t20:
                    if len(top6) >= 6:
                        break
                    if r['code'] not in set(t['code'] for t in top6):
                        top6.append(r)

        elif regime == 'RANGE_BULL':
            # ★ v12.23 RANGE_BULL模式: 5进攻 + 1防守
            scored_all = []
            for r in top20:
                r = dict(r)
                range_score, range_signals, range_bonus = score_range_bull_v1223(r, local_bull, sector_accel)
                corr_score, corr_signals, corr_auction, corr_auc_label = score_correction_v1223(r, local_bull, sector_accel)

                matched = match_sector(r.get('name', ''))
                is_lb = any(s in local_bull for s in matched) if local_bull else False

                all_signals = range_signals if range_signals else corr_signals
                scored_all.append({
                    **r,
                    'v1224_range_score': range_score,
                    'v1224_corr_score': corr_score,
                    'surge_signals': all_signals,
                    'total_bonus': range_bonus,
                    'is_local_bull': is_lb,
                    'is_defensive': is_defensive(r.get('name', '')),
                    'auction_v1219': corr_auction,
                    'auction_label': corr_auc_label,
                })

            by_range = sorted(scored_all, key=lambda x: -x['v1224_range_score'])
            by_corr = sorted(scored_all, key=lambda x: -x['v1224_corr_score'])

            selected = set()
            top6 = []

            # 局部牛市优先 (最多1只进攻)
            lb_count = 0
            for s in by_range:
                if lb_count >= 1:
                    break
                if s.get('is_local_bull') and s['code'] not in selected:
                    s['v1219_score'] = s['v1224_range_score']
                    s['v1224_score'] = s['v1224_range_score']
                    s['v1219_mode'] = 'LOCAL_BULL'
                    top6.append(s)
                    selected.add(s['code'])
                    lb_count += 1

            # 进攻席位 (补到5个)
            for s in by_range:
                if len([t for t in top6 if t.get('v1219_mode') in ('BULL', 'LOCAL_BULL', 'SURGE')]) >= 5:
                    break
                if s['code'] not in selected:
                    s['v1219_score'] = s['v1224_range_score']
                    s['v1224_score'] = s['v1224_range_score']
                    s['v1219_mode'] = 'BULL'
                    top6.append(s)
                    selected.add(s['code'])

            # 防守席位 (1个)
            for s in by_corr:
                if len([t for t in top6 if t.get('v1219_mode') == 'CORRECTION']) >= 1:
                    break
                if s['code'] not in selected:
                    s['v1219_score'] = s['v1224_corr_score']
                    s['v1224_score'] = s['v1224_corr_score']
                    s['v1219_mode'] = 'CORRECTION'
                    top6.append(s)
                    selected.add(s['code'])

            # 补齐
            if len(top6) < 6:
                for s in by_corr:
                    if len(top6) >= 6:
                        break
                    if s['code'] not in selected:
                        s['v1219_score'] = s['v1224_corr_score']
                        s['v1224_score'] = s['v1224_corr_score']
                        s['v1219_mode'] = 'CORRECTION'
                        top6.append(s)
                        selected.add(s['code'])

            top6.sort(key=lambda x: -x['v1224_score'])

        else:  # CORRECTION
            scored = []
            for code, r in pool.items():
                r = dict(r)
                bull_score, bull_signals, bull_bonus = score_bull_v1224(r, local_bull, sector_accel, hot_sectors)
                corr_score, corr_signals, corr_auction, corr_auc_label = score_correction_v1223(r, local_bull, sector_accel)

                matched = match_sector(r.get('name', ''))
                is_lb = any(s in local_bull for s in matched) if local_bull else False

                all_signals = bull_signals if bull_signals else corr_signals
                scored.append({
                    **r,
                    'v1224_bull_score': bull_score,
                    'v1224_corr_score': corr_score,
                    'surge_signals': all_signals,
                    'total_bonus': bull_bonus,
                    'is_local_bull': is_lb,
                    'is_defensive': is_defensive(r.get('name', '')),
                    'auction_v1219': corr_auction,
                    'auction_label': corr_auc_label,
                })

            local_bull_stocks = sorted([s for s in scored if s['is_local_bull']], key=lambda x: -x['v1224_bull_score'])
            surge_stocks = sorted([s for s in scored if not s['is_local_bull'] and s.get('total_bonus', 0) >= 10], key=lambda x: -x['v1224_bull_score'])
            by_v1212 = sorted(scored, key=lambda x: -x.get('v1212_score', 0))
            by_corr = sorted(scored, key=lambda x: -x['v1224_corr_score'])

            selected = set()
            top6 = []
            n_agg = 3

            lb_count = 0
            for s in local_bull_stocks:
                if lb_count >= 1:
                    break
                if s['code'] not in selected:
                    s['v1219_score'] = s['v1224_bull_score']
                    s['v1224_score'] = s['v1224_bull_score']
                    s['v1219_mode'] = 'LOCAL_BULL'
                    top6.append(s)
                    selected.add(s['code'])
                    lb_count += 1

            for s in by_v1212:
                if len([t for t in top6 if t.get('v1219_mode') in ('BULL', 'LOCAL_BULL', 'SURGE')]) >= n_agg:
                    break
                if s['code'] not in selected:
                    s['v1219_score'] = s.get('v1212_score', 0)
                    s['v1224_score'] = s.get('v1212_score', 0)
                    s['v1219_mode'] = 'BULL'
                    top6.append(s)
                    selected.add(s['code'])

            for s in surge_stocks:
                if len([t for t in top6 if t.get('v1219_mode') in ('BULL', 'LOCAL_BULL', 'SURGE')]) >= n_agg:
                    break
                if s['code'] not in selected:
                    s['v1219_score'] = s['v1224_bull_score']
                    s['v1224_score'] = s['v1224_bull_score']
                    s['v1219_mode'] = 'SURGE'
                    top6.append(s)
                    selected.add(s['code'])

            for s in by_corr:
                if len(top6) >= 6:
                    break
                if s['code'] not in selected:
                    s['v1219_score'] = s['v1224_corr_score']
                    s['v1224_score'] = s['v1224_corr_score']
                    s['v1219_mode'] = 'CORRECTION'
                    top6.append(s)
                    selected.add(s['code'])

            if len(top6) < 6:
                for s in by_corr:
                    if len(top6) >= 6:
                        break
                    if s['code'] not in selected:
                        s['v1219_score'] = s['v1224_corr_score']
                        s['v1224_score'] = s['v1224_corr_score']
                        s['v1219_mode'] = 'CORRECTION'
                        top6.append(s)
                        selected.add(s['code'])

            top6.sort(key=lambda x: -x['v1224_score'])

        # 输出TOP6
        print(f"  TOP6:")
        for i, r in enumerate(top6):
            zt = "涨停" if r.get('today_pct', 0) >= 9.5 else ""
            ge8 = "★>8%" if r.get('today_pct', 0) >= 8 else ""
            mode_tag = r.get('v1219_mode', '')
            df = "[防守]" if r.get('is_defensive') else ""
            lb = "[局部牛]" if r.get('is_local_bull') else ""
            sg = f" {r.get('surge_signals', [])}" if r.get('surge_signals') else ""
            print(f"    {i+1}. {r.get('name','')[:8]:<10}({r['code']}) v24:{r.get('v1224_score',0):>5.1f} v12:{r.get('v1212_score',0):.1f} T-1:{r.get('yest_pct',0):+.1f}% 开:{r.get('today_open_pct',0):+.1f}% 收:{r.get('today_pct',0):+.2f}% {df}{lb}{sg} {zt} {ge8}")

        # 统计
        t6_up8 = sum(1 for r in top6 if r.get('today_pct', 0) >= 8)
        t6_zt = sum(1 for r in top6 if r.get('today_pct', 0) >= 9.5)
        t6_up = sum(1 for r in top6 if r.get('today_pct', 0) > 0)
        t6_avg = sum(r.get('today_pct', 0) for r in top6) / len(top6) if top6 else 0
        t6_max_avg = sum(r.get('today_max_pct', 0) for r in top6) / len(top6) if top6 else 0
        t6_loss = sum(1 for r in top6 if r.get('today_pct', 0) < 0)
        t6_def = sum(1 for r in top6 if r.get('is_defensive', False))
        t6_agg = sum(1 for r in top6 if r.get('v1219_mode') in ('BULL', 'LOCAL_BULL', 'SURGE'))
        t6_surge = sum(1 for r in top6 if r.get('v1219_mode') == 'SURGE')
        t6_lb = sum(1 for r in top6 if r.get('is_local_bull', False))
        t6_corr_mode = sum(1 for r in top6 if r.get('v1219_mode') == 'CORRECTION')

        t6_12_up8 = sum(1 for r in top6_12 if r.get('today_pct', 0) >= 8)
        t6_12_zt = sum(1 for r in top6_12 if r.get('today_pct', 0) >= 9.5)
        t6_12_avg = sum(r.get('today_pct', 0) for r in top6_12) / len(top6_12) if top6_12 else 0
        t6_12_loss = sum(1 for r in top6_12 if r.get('today_pct', 0) < 0)

        print(f"  v12.24[{regime[:4]}]: >8%={t6_up8}/6 涨停={t6_zt}/6 上涨={t6_up}/6 均涨:{t6_avg:+.2f}% 亏损:{t6_loss} 进攻:{t6_agg}(起爆:{t6_surge}) 防守:{t6_corr_mode} 局部牛:{t6_lb}")
        print(f"  v12.12[原版]:   >8%={t6_12_up8}/6 涨停={t6_12_zt}/6 均涨:{t6_12_avg:+.2f}% 亏损:{t6_12_loss}")

        for r in top6:
            advice = generate_trade_advice(r, regime, sector_accel, local_bull)
            r['trade_advice'] = advice

        for r in top6:
            r['date'] = target_date
            r['regime'] = regime
            r['regime_name'] = regime_name
            r['confidence'] = confidence
            r['local_bull'] = local_bull
            r['sector_accel'] = sector_accel
            all_picks.append(r)

        all_daily_results[target_date] = {
            'top6': top6,
            'regime': regime,
            'regime_name': regime_name,
            'confidence': confidence,
            'market': market,
            'user_judgment': user_judgment,
            'local_bull': local_bull,
            'sector_accel': sector_accel,
            'hot_sectors': {k: v for k, v in hot_sectors.items()},
            't6_up8': t6_up8, 't6_zt': t6_zt, 't6_up': t6_up, 't6_avg': t6_avg,
            't6_max_avg': t6_max_avg, 't6_loss': t6_loss, 't6_def': t6_def, 't6_agg': t6_agg,
            't6_lb': t6_lb, 't6_surge': t6_surge, 't6_corr_mode': t6_corr_mode,
            't6_12_up8': t6_12_up8, 't6_12_zt': t6_12_zt, 't6_12_avg': t6_12_avg, 't6_12_loss': t6_12_loss,
        }

    # ==================== 汇总 ====================
    n_days = len(BACKTEST_DATES)
    n_picks = n_days * 6

    total_24_up8 = sum(d['t6_up8'] for d in all_daily_results.values())
    total_24_zt = sum(d['t6_zt'] for d in all_daily_results.values())
    total_24_up = sum(d['t6_up'] for d in all_daily_results.values())
    total_24_pct = sum(d['t6_avg'] * 6 for d in all_daily_results.values())
    total_24_max = sum(d['t6_max_avg'] * 6 for d in all_daily_results.values())
    total_24_loss = sum(d['t6_loss'] for d in all_daily_results.values())
    total_24_def = sum(d['t6_def'] for d in all_daily_results.values())
    total_24_agg = sum(d['t6_agg'] for d in all_daily_results.values())
    total_24_surge = sum(d['t6_surge'] for d in all_daily_results.values())
    total_24_lb = sum(d['t6_lb'] for d in all_daily_results.values())
    total_24_corr = sum(d.get('t6_corr_mode', 0) for d in all_daily_results.values())

    total_12_up8 = sum(d['t6_12_up8'] for d in all_daily_results.values())
    total_12_zt = sum(d['t6_12_zt'] for d in all_daily_results.values())
    total_12_pct = sum(d['t6_12_avg'] * 6 for d in all_daily_results.values())
    total_12_loss = sum(d['t6_12_loss'] for d in all_daily_results.values())

    print(f"\n{'='*100}")
    print(f"  v12.24 一个月回测汇总 ({n_days}天 × TOP6 = {n_picks}只)")
    print(f"{'='*100}")
    print(f"  v12.24 核心改进: BULL激进主线龙头 + 连板续涨奖励 + 追涨放宽")
    print(f"    继承v12.22: 用户手动判断大盘态势(完美前瞻)")
    print(f"    继承v12.23: RANGE_BULL 5攻1防 + CORRECTION板块热度")
    print(f"  {'版本':<36} {'>8%率':>12} {'涨停率':>12} {'上涨率':>12} {'均涨':>8} {'亏损':>8}")
    print(f"  {'-'*100}")
    print(f"  {'v12.12(原版)':<36} {total_12_up8}/{n_picks}({total_12_up8/n_picks*100:.1f}%) {total_12_zt}/{n_picks}({total_12_zt/n_picks*100:.1f}%) {'':<12} {total_12_pct/n_picks:+.2f}% {total_12_loss}只")
    print(f"  {'v12.24(BULL激进主线龙头)':<36} {total_24_up8}/{n_picks}({total_24_up8/n_picks*100:.1f}%) {total_24_zt}/{n_picks}({total_24_zt/n_picks*100:.1f}%) {total_24_up}/{n_picks}({total_24_up/n_picks*100:.1f}%) {total_24_pct/n_picks:+.2f}% {total_24_loss}只")

    print(f"\n  v12.24新增统计:")
    print(f"    起爆初期入选: {total_24_surge}/{n_picks}只")
    print(f"    局部牛入选: {total_24_lb}/{n_picks}只")
    print(f"    进攻席位: {total_24_agg}/{n_picks}只")
    print(f"    防守席位: {total_24_corr}/{n_picks}只")

    print(f"\n  大盘态势分布:")
    for regime, cnt in sorted(regime_counts.items()):
        regime_days = [d for d in all_daily_results.values() if d['regime'] == regime]
        r_up8 = sum(d['t6_up8'] for d in regime_days)
        r_zt = sum(d['t6_zt'] for d in regime_days)
        r_avg = sum(d['t6_avg'] * 6 for d in regime_days) / (cnt * 6) if cnt > 0 else 0
        r_loss = sum(d['t6_loss'] for d in regime_days)
        print(f"    {regime}: {cnt}/{n_days}天 ({cnt/n_days*100:.0f}%) >8%={r_up8}/{cnt*6}({r_up8/(cnt*6)*100:.1f}%) 涨停={r_zt}/{cnt*6} 均涨:{r_avg:+.2f}% 亏损:{r_loss}")

    print(f"\n  逐日明细:")
    print(f"  {'日期':<12} {'态势':<12} {'v24>8%':>8} {'v24涨停':>8} {'v24均涨':>8} {'v24亏损':>6} {'v12>8%':>8} {'v12均涨':>8}")
    for date, d in all_daily_results.items():
        print(f"  {date:<12} {d['regime']:<12} {d['t6_up8']}/6 {d['t6_zt']}/6 {d['t6_avg']:+.2f}% {d['t6_loss']} {d['t6_12_up8']}/6 {d['t6_12_avg']:+.2f}%")

    # 涨幅分布
    print(f"\n  TOP6 涨幅分布:")
    buckets = {'涨停≥9.5%': 0, '8-9.5%': 0, '5-8%': 0, '0-5%': 0, '-3-0%': 0, '<-3%': 0}
    for p in all_picks:
        pct = p.get('today_pct', 0)
        if pct >= 9.5: buckets['涨停≥9.5%'] += 1
        elif pct >= 8: buckets['8-9.5%'] += 1
        elif pct >= 5: buckets['5-8%'] += 1
        elif pct >= 0: buckets['0-5%'] += 1
        elif pct >= -3: buckets['-3-0%'] += 1
        else: buckets['<-3%'] += 1
    total = len(all_picks)
    for label, count in buckets.items():
        bar = '█' * int(count / total * 50) if total > 0 else ''
        print(f"    {label:<12} {count:>3}只 ({count/total*100:.1f}%) {bar}")

    # 保存
    output = {
        'version': 'v12.24',
        'description': 'v12.24一个月回测 (BULL激进主线龙头 + 连板续涨奖励 + 追涨放宽)',
        'backtest_time': bj.strftime('%Y-%m-%d %H:%M:%S'),
        'backtest_dates': BACKTEST_DATES,
        'sample_size': len(all_kls),
        'summary': {
            'days': n_days,
            'total_picks': n_picks,
            'v1224': {
                'pct_ge8': round(total_24_up8 / n_picks * 100, 1),
                'pct_zt': round(total_24_zt / n_picks * 100, 1),
                'pct_up': round(total_24_up / n_picks * 100, 1),
                'avg_pct': round(total_24_pct / n_picks, 2),
                'avg_max': round(total_24_max / n_picks, 2),
                'loss': total_24_loss,
                'loss_rate': round(total_24_loss / n_picks * 100, 1),
                'surge_count': total_24_surge,
                'local_bull_count': total_24_lb,
                'defensive_count': total_24_corr,
                'aggressive_count': total_24_agg,
            },
            'v1212': {
                'pct_ge8': round(total_12_up8 / n_picks * 100, 1),
                'pct_zt': round(total_12_zt / n_picks * 100, 1),
                'avg_pct': round(total_12_pct / n_picks, 2),
                'loss': total_12_loss,
                'loss_rate': round(total_12_loss / n_picks * 100, 1),
            },
            'regime_distribution': dict(regime_counts),
        },
        'daily_results': all_daily_results,
        'all_picks': all_picks,
    }
    output_path = '/workspace/v1224_1month_backtest.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n已保存: {output_path}")


if __name__ == '__main__':
    main()
