#!/usr/bin/env python3
"""
通道C: 首板埋伏评分模块
不依赖涨停基因(zt_30d), 专注T-2日识别首板前夜形态

核心信号 (基于日出东方/中毅达/罗普斯金等首板股T-2日特征):
1. MA10黄金区间(偏离0-3%): 所有首板股T-2日都满足
2. 缩量控盘(振幅<3%+量比<1.0): 主力暗中吸筹
3. 均线聚合(MA5/MA10/MA20靠拢): 变盘前兆
4. 近期量价异动(5日有放量>1.5x或振幅>5%): 主力试探
5. RSI黄金区间(45-65): 不高不低, 有上涨空间
6. 阴线买入: 首板前夜收阴占多数
7. 连续缩量小K线: 蓄力待发
"""

C_COND_NAMES = {
    1: 'MA10黄金区间',
    2: '缩量控盘',
    3: '均线聚合',
    4: '量价异动',
    5: 'RSI黄金区间',
    6: '阴线买入',
    7: 'MA20上方',
    8: '连续小K线',
}


def score_channel_c(kl, tech, ti):
    """
    通道C首板埋伏评分
    
    返回: (total_score, hit_conditions)
    """
    c = kl['c']; v = kl['v']; o = kl['o']; h = kl['h']; l = kl['l']
    tc = c[ti]; tp = tech['yest_pct']; vr = tech['vol_ratio']
    dev10 = tech['dev_ma10']; r6 = tech['rsi6']

    # ===== 基础过滤 (比V6宽松, 但保留核心保护) =====
    # 昨日涨停排除 (已经涨停的留给通道B)
    if tech['is_yest_zt']: return 0, []
    # 涨幅过大排除
    if tp > 5: return 0, []
    # 量比过大排除
    if vr > 3.0: return 0, []
    # 振幅不能太小 (僵尸股)
    if tech['yest_amp'] < 1.0: return 0, []
    # MA10以上 (通道C的核心要求, 但不强制, 允许MA10下方1%以内)
    if dev10 < -1.0: return 0, []

    # ===== 通道C专属条件 =====
    hit = []; total = 0

    # C1: MA10黄金区间 (0-3%) — 核心信号
    if 0 <= dev10 <= 3:
        total += 30; hit.append(1)
    elif 3 < dev10 <= 5:
        total += 20; hit.append(1)
    elif -1 <= dev10 < 0:
        total += 15; hit.append(1)

    # C2: 缩量控盘 (振幅<3% + 量比<1.0)
    if tech['yest_amp'] < 3 and vr < 1.0:
        total += 25; hit.append(2)
    elif tech['yest_amp'] < 3 and vr < 1.2:
        total += 15; hit.append(2)

    # C3: 均线聚合 (MA5/MA10/MA20靠拢在5%以内)
    if ti >= 20:
        ma5 = tech['ma5']; ma10 = tech['ma10']; ma20 = tech['ma20']
        ma_range = (max(ma5, ma10, ma20) - min(ma5, ma10, ma20)) / ma20 * 100
        if ma_range < 5:
            total += 20; hit.append(3)
        elif ma_range < 8:
            total += 10; hit.append(3)

    # C4: 近期量价异动 (5日内有某天放量>1.5x或振幅>5%)
    has_anomaly = False
    for j in range(max(0, ti-4), ti+1):
        if ti >= 5:
            v_avg = sum(v[max(0,j-4):j]) / 5 if j >= 5 else v[j]
            if v_avg > 0 and v[j] / v_avg > 1.5:
                has_anomaly = True; break
        if j > 0 and c[j-1] > 0:
            amp = (h[j] - l[j]) / c[j-1] * 100
            if amp > 5:
                has_anomaly = True; break
    if has_anomaly:
        total += 20; hit.append(4)

    # C5: RSI黄金区间 (45-65)
    if 45 <= r6 <= 65:
        total += 15; hit.append(5)
    elif 40 <= r6 < 45:
        total += 8; hit.append(5)

    # C6: 阴线买入
    if not tech['is_yang']:
        total += 15; hit.append(6)
    elif tp < 0.5:
        total += 8; hit.append(6)  # 假阳线也算

    # C7: MA20上方
    if ti >= 20:
        dev20 = (tc - tech['ma20']) / tech['ma20'] * 100
        if dev20 > 0:
            total += 10; hit.append(7)

    # C8: 连续缩量小K线 (3日振幅<3%)
    small_k_count = 0
    for j in range(max(0, ti-2), ti+1):
        if j > 0 and c[j-1] > 0:
            amp = (h[j] - l[j]) / c[j-1] * 100
            if amp < 3: small_k_count += 1
    if small_k_count >= 3:
        total += 15; hit.append(8)
    elif small_k_count >= 2:
        total += 8; hit.append(8)

    # ===== 基础技术分 =====
    base = 0
    # MA10偏离微调
    if 0 <= dev10 <= 1.5:
        base += 12  # 紧贴MA10
    elif 1.5 < dev10 <= 3:
        base += 8

    # 缩量程度
    if vr < 0.7:
        base += 10  # 深度缩量
    elif vr < 0.85:
        base += 6

    # 振幅
    if 1.5 <= tech['yest_amp'] <= 2.5:
        base += 8  # 黄金振幅

    # 均线多头排列
    if ti >= 20 and tech['ma5'] > tech['ma10'] > tech['ma20']:
        base += 10
    elif ti >= 10 and tech['ma5'] > tech['ma10']:
        base += 5

    # MA5即将上穿MA10
    if ti >= 10:
        ma5_yest = sum(c[ti-5:ti]) / 5
        ma10_yest = sum(c[ti-10:ti]) / 10
        ma5_today = tech['ma5']
        ma10_today = tech['ma10']
        if ma5_yest <= ma10_yest and ma5_today > ma10_today:
            base += 12  # 金叉!

    total += base
    return total, hit


def compute_c_tech(kl, ti):
    """预计算通道C所需的技术特征"""
    c = kl['c']; o = kl['o']; h = kl['h']; l = kl['l']; v = kl['v']
    tc = c[ti]

    tech = {}
    tech['yest_pct'] = (c[ti]-c[ti-1])/c[ti-1]*100 if ti > 0 else 0
    tech['is_yest_zt'] = tech['yest_pct'] >= 9.5
    tech['yest_amp'] = (h[ti]-l[ti])/c[ti-1]*100 if ti > 0 and c[ti-1] > 0 else 0
    tech['is_yang'] = tc >= o[ti]

    ma5 = sum(c[max(0,ti-4):ti+1])/min(5,ti+1)
    ma10 = sum(c[max(0,ti-9):ti+1])/min(10,ti+1) if ti >= 9 else ma5
    ma20 = sum(c[max(0,ti-19):ti+1])/min(20,ti+1) if ti >= 19 else ma10
    tech['ma5'] = ma5; tech['ma10'] = ma10; tech['ma20'] = ma20
    tech['dev_ma10'] = (tc-ma10)/ma10*100

    if ti >= 6:
        gains = [max(0, c[j]-c[j-1]) for j in range(ti-5, ti+1)]
        losses = [max(0, c[j-1]-c[j]) for j in range(ti-5, ti+1)]
        avg_gain = sum(gains)/6; avg_loss = sum(losses)/6
        tech['rsi6'] = 100-100/(1+avg_gain/avg_loss) if avg_loss > 0 else 100
    else:
        tech['rsi6'] = 50

    tech['vol_ratio'] = v[ti]/sum(v[max(0,ti-4):ti])*5 if ti >= 5 and sum(v[max(0,ti-4):ti])>0 else 1.0
    tech['zt_30d'] = sum(1 for j in range(max(0,ti-19), ti+1) if j>0 and (c[j]-c[j-1])/c[j-1]>=0.095)

    return tech