#!/usr/bin/env python3
"""
大盘风险预警模块 (Risk Guard)
在选股前判定"今天能不能买" — 独立于任何选股策略

预警维度:
1. 阻力位/支撑位量化
2. 连续缩量+振幅收窄 = 变盘预警
3. 开盘信号: 低开幅度+量能 = 日内风险
4. 技术指标背离检测
5. 综合风险评分 (0-100, 越高越危险)
"""
import math


def compute_support_resistance(idx_c, idx_h, idx_l, idx_v, ti):
    """
    计算关键支撑/阻力位
    返回: {'support': [价位], 'resistance': [价位], 'current': float}
    """
    c = idx_c[ti]; h = idx_h; l = idx_l

    # 20日高/低
    h20 = max(h[max(0,ti-19):ti+1])
    l20 = min(l[max(0,ti-19):ti+1])

    # 均线
    ma5 = sum(idx_c[max(0,ti-4):ti+1]) / min(5, ti+1)
    ma10 = sum(idx_c[max(0,ti-9):ti+1]) / min(10, ti+1) if ti >= 9 else ma5
    ma20 = sum(idx_c[max(0,ti-19):ti+1]) / min(20, ti+1) if ti >= 19 else ma10
    ma60 = sum(idx_c[max(0,ti-59):ti+1]) / min(60, ti+1) if ti >= 59 else ma20

    # 整数关口
    round_levels = []
    base = int(c / 100) * 100
    for offset in [-200, -100, 0, 100, 200]:
        lvl = base + offset
        if lvl > 0 and abs(lvl - c) / c < 0.05:
            round_levels.append(lvl)

    support = sorted([l for l in [l20, ma20, ma60] + round_levels if l < c], reverse=True)
    resistance = sorted([r for r in [h20, ma5, ma10] + round_levels if r > c])

    return {
        'support': [round(s, 1) for s in support[:3]],
        'resistance': [round(r, 1) for r in resistance[:3]],
        'current': round(c, 1),
        'ma5': round(ma5, 1), 'ma10': round(ma10, 1),
        'ma20': round(ma20, 1), 'ma60': round(ma60, 1),
        'h20': round(h20, 1), 'l20': round(l20, 1),
    }


def detect_volatility_squeeze(idx_c, idx_h, idx_l, idx_v, ti, lookback=5):
    """
    检测连续缩量+振幅收窄 → 变盘前兆
    返回: {'squeeze': bool, 'days': int, 'vol_shrink_pct': float, 'amp_shrink_pct': float}
    """
    if ti < lookback + 5: return {'squeeze': False, 'days': 0}

    # 近5日振幅
    recent_amps = []
    for i in range(ti-lookback+1, ti+1):
        if i > 0 and idx_c[i-1] > 0:
            amp = (idx_h[i] - idx_l[i]) / idx_c[i-1] * 100
            recent_amps.append(amp)

    # 近5日均量
    recent_vols = [idx_v[i] for i in range(ti-lookback+1, ti+1)]

    # 前5日
    prev_amps = []
    prev_vols = []
    for i in range(ti-lookback*2+1, ti-lookback+1):
        if i > 0 and idx_c[i-1] > 0:
            amp = (idx_h[i] - idx_l[i]) / idx_c[i-1] * 100
            prev_amps.append(amp)
        prev_vols.append(idx_v[i])

    if not recent_amps or not prev_amps: return {'squeeze': False, 'days': 0}

    avg_amp_now = sum(recent_amps) / len(recent_amps)
    avg_amp_prev = sum(prev_amps) / len(prev_amps)
    avg_vol_now = sum(recent_vols) / len(recent_vols)
    avg_vol_prev = sum(prev_vols) / len(prev_vols)

    amp_shrink = (avg_amp_prev - avg_amp_now) / avg_amp_prev * 100 if avg_amp_prev > 0 else 0
    vol_shrink = (avg_vol_prev - avg_vol_now) / avg_vol_prev * 100 if avg_vol_prev > 0 else 0

    # 连续缩量天数
    shrink_days = 0
    for i in range(ti, max(0, ti-lookback*2), -1):
        if i >= 5 and idx_v[i] < sum(idx_v[i-4:i]) / 4:
            shrink_days += 1
        else:
            break

    # 连续缩量+振幅收窄 = 变盘前兆
    # 降低阈值: 振幅收窄>10% 且 量缩>10% (原来是20%/15%)
    squeeze = (amp_shrink > 10 and vol_shrink > 10) or shrink_days >= 3

    return {
        'squeeze': squeeze,
        'days': shrink_days,
        'vol_shrink_pct': round(vol_shrink, 1),
        'amp_shrink_pct': round(amp_shrink, 1),
    }


def detect_divergence(idx_c, idx_h, idx_v, ti):
    """
    检测MACD顶背离/底背离 (简化版: 价格 vs 量能)
    返回: {'bearish_div': bool, 'bullish_div': bool, 'detail': str}
    """
    if ti < 20: return {'bearish_div': False, 'bullish_div': False, 'detail': ''}

    # 近5日高点趋势
    h5 = max(idx_h[ti-4:ti+1])
    h10 = max(idx_h[ti-9:ti-4])
    h20 = max(idx_h[ti-19:ti-9])

    # 近5日量能趋势
    v5 = sum(idx_v[ti-4:ti+1]) / 5
    v10 = sum(idx_v[ti-9:ti-4]) / 5
    v20 = sum(idx_v[ti-19:ti-9]) / 10

    # 价格新高但量能萎缩 → 顶背离
    bearish = h5 >= h10 and v5 < v10 * 0.85

    # 价格新低但量能放大 → 底背离
    l5 = min(idx_h[ti-4:ti+1])
    bullish = l5 <= min(idx_h[ti-9:ti-4]) and v5 > v10 * 1.15

    detail = ''
    if bearish: detail = '价格新高但量能萎缩'
    elif bullish: detail = '价格新低但量能放大'

    return {'bearish_div': bearish, 'bullish_div': bullish, 'detail': detail}


def assess_opening_risk(open_pct, vol_ratio, prev_close_pct):
    """
    开盘风险评估
    返回: {'risk_level': str, 'score': int, 'detail': str}
    """
    risk = 0
    detail = []

    # 低开幅度
    if open_pct < -2:
        risk += 35
        detail.append(f'大幅低开{open_pct:+.1f}%')
    elif open_pct < -1:
        risk += 20
        detail.append(f'低开{open_pct:+.1f}%')
    elif open_pct < -0.5:
        risk += 10
        detail.append(f'小幅低开{open_pct:+.1f}%')

    # 放量下跌
    if open_pct < 0 and vol_ratio > 1.3:
        risk += 25
        detail.append(f'放量下跌(量比{vol_ratio:.1f})')

    # 昨日已弱+今日低开 = 加速下跌
    if prev_close_pct < 0 and open_pct < -0.5:
        risk += 15
        detail.append('连续下跌加速')

    # 高开+放量 = 追高风险
    if open_pct > 1 and vol_ratio > 1.5:
        risk += 10
        detail.append('高开放量追涨风险')

    # 高开太多
    if open_pct > 2:
        risk += 15
        detail.append(f'大幅高开{open_pct:+.1f}%追高风险')

    if risk >= 60:
        level = 'DANGER'
    elif risk >= 35:
        level = 'WARNING'
    elif risk >= 15:
        level = 'CAUTION'
    else:
        level = 'SAFE'

    return {'risk_level': level, 'score': min(risk, 100), 'detail': '; '.join(detail)}


def comprehensive_risk_score(idx_kl, ti, open_pct=None, vol_ratio=None):
    """
    综合风险评分 (0=安全, 100=极度危险)
    返回完整风险评估dict
    """
    c = idx_kl['c']; h = idx_kl['h']; l = idx_kl['l']; v = idx_kl['v']

    risk_score = 0
    risk_items = []

    # 1. 支撑/阻力位
    sr = compute_support_resistance(c, h, l, v, ti)
    current = sr['current']
    nearest_support = sr['support'][0] if sr['support'] else 0
    nearest_resistance = sr['resistance'][0] if sr['resistance'] else 0

    # 2. 变盘预警 (先计算, 后面支撑/阻力位检查需要用到)
    squeeze = detect_volatility_squeeze(c, h, l, v, ti)

    # 距支撑位距离
    if nearest_support > 0:
        dist_to_support = (current - nearest_support) / current * 100
        if dist_to_support > 5:
            risk_score += 15
            risk_items.append(f'距支撑位{nearest_support:.0f}还有{dist_to_support:.1f}%')
        elif dist_to_support > 3:
            risk_score += 8

    # 阻力位受阻: 连续3天在阻力位下方窄幅震荡 → 重大风险
    if nearest_resistance > 0:
        dist_to_resistance = (nearest_resistance - current) / current * 100
        if dist_to_resistance < 0.5:  # 距阻力位<0.5%
            risk_score += 15
            risk_items.append(f'紧贴阻力位{nearest_resistance:.0f}(距{current-nearest_resistance:+.1f})')
        elif dist_to_resistance < 1.0:
            risk_score += 8
            risk_items.append(f'接近阻力位{nearest_resistance:.0f}(距{dist_to_resistance:.1f}%)')

    # 连续缩量+冲击阻力位受阻 → 强卖出信号
    if squeeze['squeeze'] and nearest_resistance > 0 and (nearest_resistance - current) / current < 0.01:
        risk_score += 15
        risk_items.append(f'缩量受阻阻力位{nearest_resistance:.0f}')

    # 均线下方
    if current < sr['ma5']:
        risk_score += 10
        risk_items.append(f'跌破MA5({sr["ma5"]:.0f})')
    if current < sr['ma20']:
        risk_score += 8
        risk_items.append(f'跌破MA20({sr["ma20"]:.0f})')

    # 变盘预警 (已在上面计算)
    if squeeze['squeeze']:
        risk_score += 20
        risk_items.append(f'变盘预警: 连续{squeeze["days"]}日缩量+振幅收窄')

    # 3. 背离检测
    div = detect_divergence(c, h, v, ti)
    if div['bearish_div']:
        risk_score += 25
        risk_items.append(f'顶背离: {div["detail"]}')
    if div['bullish_div']:
        risk_score -= 15
        risk_items.append(f'底背离: {div["detail"]}')

    # 4. 近期趋势
    if ti >= 5:
        pct_5d = (c[ti] - c[ti-5]) / c[ti-5] * 100
        if pct_5d > 5:
            risk_score += 12
            risk_items.append(f'5日涨{pct_5d:.1f}%超买风险')
        elif pct_5d < -3:
            risk_score += 10
            risk_items.append(f'5日跌{pct_5d:.1f}%弱势')

    # 5. 整数关口
    for lvl in [3800, 3900, 4000, 4100, 4200]:
        if abs(current - lvl) / lvl < 0.005:
            risk_score += 5
            risk_items.append(f'整数关口{lvl}附近')

    # 6. 开盘风险（如果有实时数据）
    open_risk = None
    if open_pct is not None and vol_ratio is not None:
        prev_pct = (c[ti] - c[ti-1]) / c[ti-1] * 100 if ti > 0 else 0
        open_risk = assess_opening_risk(open_pct, vol_ratio, prev_pct)
        risk_score += open_risk['score']

    # 限制范围
    risk_score = max(0, min(100, risk_score))

    # 判定
    if risk_score >= 60:
        recommendation = 'DANGER — 建议空仓观望，不操作'
        action = '空仓'
    elif risk_score >= 40:
        recommendation = 'WARNING — 风险较高，控制仓位≤30%，仅选最强信号'
        action = '轻仓(≤30%)'
    elif risk_score >= 20:
        recommendation = 'CAUTION — 存在风险，仓位≤50%，注意止损'
        action = '半仓(≤50%)'
    else:
        recommendation = 'SAFE — 风险可控，可正常操作'
        action = '正常'

    return {
        'risk_score': risk_score,
        'risk_level': 'DANGER' if risk_score >= 60 else ('WARNING' if risk_score >= 40 else ('CAUTION' if risk_score >= 20 else 'SAFE')),
        'recommendation': recommendation,
        'action': action,
        'risk_items': risk_items,
        'support_resistance': sr,
        'squeeze': squeeze,
        'divergence': div,
        'open_risk': open_risk,
    }


def quick_check(idx_kl, ti):
    """快速检查: 返回是否适合开仓"""
    result = comprehensive_risk_score(idx_kl, ti)
    return {
        'safe': result['risk_score'] < 40,
        'risk_score': result['risk_score'],
        'risk_level': result['risk_level'],
        'reason': '; '.join(result['risk_items'][:3]) if result['risk_items'] else '无风险信号',
    }


# ============================================================
# 诊断: 对8月18日收盘→8月19日暴跌进行回溯分析
# ============================================================
if __name__ == '__main__':
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from scan_v1224 import get_index_kl

    idx_kl = get_index_kl(80)
    if idx_kl:
        dates = idx_kl['d']
        c = idx_kl['c']

        print("="*80)
        print("  风险预警模块 — 回溯诊断 (8月18日收盘)")
        print("="*80)

        # 8月18日收盘
        yest_date = '2026-08-18'
        if yest_date in dates:
            ti = dates.index(yest_date)
            result = comprehensive_risk_score(idx_kl, ti)

            print(f"\n日期: {yest_date} 收盘: {c[ti]:.1f}")
            print(f"\n风险评分: {result['risk_score']}/100 [{result['risk_level']}]")
            print(f"建议: {result['recommendation']}")
            print(f"操作: {result['action']}")

            sr = result['support_resistance']
            print(f"\n-- 支撑/阻力 --")
            print(f"  当前: {sr['current']}")
            print(f"  支撑: {sr['support']}")
            print(f"  阻力: {sr['resistance']}")
            print(f"  均线: MA5={sr['ma5']} MA10={sr['ma10']} MA20={sr['ma20']}")

            print(f"\n-- 风险信号 --")
            for item in result['risk_items']:
                print(f"  ⚠ {item}")

            print(f"\n-- 变盘预警 --")
            squeeze = result['squeeze']
            print(f"  连续缩量: {squeeze['days']}天")
            print(f"  量能收缩: {squeeze['vol_shrink_pct']}%")
            print(f"  振幅收窄: {squeeze['amp_shrink_pct']}%")
            print(f"  变盘信号: {'是' if squeeze['squeeze'] else '否'}")

            print(f"\n-- 背离检测 --")
            div = result['divergence']
            print(f"  顶背离: {div['bearish_div']} ({div['detail']})")
            print(f"  底背离: {div['bullish_div']}")

            # 模拟8月19日开盘: -0.96%
            print(f"\n-- 开盘风险评估 (模拟8月19日低开-0.96%) --")
            open_risk = assess_opening_risk(-0.96, 1.38, 0.19)
            print(f"  风险等级: {open_risk['risk_level']}")
            print(f"  风险评分: {open_risk['score']}")
            print(f"  详情: {open_risk['detail']}")

            # 综合(含开盘)
            print(f"\n-- 综合风险(含开盘) --")
            result2 = comprehensive_risk_score(idx_kl, ti, open_pct=-0.96, vol_ratio=1.38)
            print(f"  总评分: {result2['risk_score']}/100 [{result2['risk_level']}]")
            print(f"  建议: {result2['recommendation']}")

        print(f"\n{'='*80}")