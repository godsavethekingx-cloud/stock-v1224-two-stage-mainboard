#!/usr/bin/env python3
"""
通道A V6 评分模块
基于V6回测最优权重(涨停率16.7%), 19条件+基础技术分
可直接被 scan_v1224.py 或独立脚本导入使用
"""
import sys, os

# V6最佳权重 (回测验证: 涨停率16.7%, 上涨率63.2%)
V6_WEIGHTS = {
    2: 22, 3: 16, 6: 24, 8: 32, 9: 10, 10: 14, 11: 22, 12: 20,
    1: 18, 4: 22, 7: 8,
    13: 14, 14: 8, 15: 30, 16: 10, 17: 5, 18: 28, 19: 10,
}

COND_NAMES = {
    1: '技术形态', 2: '缩量回调', 3: '资金流入', 4: '趋势确认',
    6: '量价配合', 7: '均线多头', 8: '突破回调', 9: 'MACD水下金叉',
    10: '强势回调金叉', 11: '涨停基因', 12: '超跌反弹',
    13: '量能爆发', 14: '量价突破', 15: '缩量洗盘', 16: '加速',
    17: '突破前高', 18: '高振幅洗盘', 19: '强势反转',
}


def score_channel_a_v6(kl, tech, ti, cond_fns=None, market_context=None, scene_mode='normal'):
    """
    V8通道A核心评分函数
    
    V8优化 (基于8/21复盘):
    - 场景化MA10约束: 
      normal: 标准V6 (MA10上方, 暴跌次日放宽)
      event_driven: 消息驱动模式 (MA10允许-5%以内, 激活超跌反弹)
      oversold_bounce: 超跌反弹模式 (MA10允许-8%, C12高权重)
    - 涨停基因+MA10黄金区间组合加分 (V7)
    - 暴跌次日放宽MA10约束 (V7)
    
    参数:
        kl: dict K线数据
        tech: dict T-1技术特征
        ti: int T-1日索引
        cond_fns: dict 条件函数映射
        market_context: dict 大盘上下文
        scene_mode: str 'normal' | 'event_driven' | 'oversold_bounce'
    """
    c = kl['c']; v = kl['v']; o = kl['o']; h = kl['h']; l = kl['l']
    tc = c[ti]; tp = tech['yest_pct']; r6 = tech['rsi6']; vr = tech['vol_ratio']
    dev10 = tech['dev_ma10']
    
    if market_context is None:
        market_context = {}
    crash_next_day = market_context.get('crash_next_day', False)
    index_pct_1d = market_context.get('index_pct_1d', 0)

    # ===== V8: 场景化MA10约束 =====
    # 根据场景模式动态调整MA10允许范围
    ma10_allow_below = 0  # 默认MA10下方不允许
    
    if scene_mode == 'event_driven':
        # 消息驱动模式: 允许MA10下方最多5% (消息面催化可能暴力拉升超跌股)
        ma10_allow_below = 5.0
    elif scene_mode == 'oversold_bounce':
        # 超跌反弹模式: 允许MA10下方最多8% (大盘弱势+个股超跌)
        ma10_allow_below = 8.0
    elif crash_next_day:
        # 暴跌次日: 允许MA10下方最多3%
        ma10_allow_below = 3.0
    
    ma10_cross = False
    if ti >= 10:
        ma10_yest = sum(c[ti-10:ti]) / 10
        if c[ti-1] < ma10_yest and tc > tech['ma10']:
            ma10_cross = True

    above_ma10 = dev10 > 0
    near_ma10 = dev10 >= -ma10_allow_below
    
    # V8: 场景化的MA10豁免逻辑
    if (not above_ma10 and not ma10_cross) and not near_ma10:
        # C12超跌反弹可以豁免MA10约束 (在oversold_bounce模式下C12权重更高)
        has_c12 = False
        if cond_fns and 12 in cond_fns:
            try:
                r12 = cond_fns[12](c, tc, tp, r6)
                if r12[0]: has_c12 = True
            except: pass
        if not has_c12:
            return 0, []

    if tech['is_yest_zt']: return 0, []
    if tp > 7: return 0, []
    if vr > 3.0: return 0, []
    if tech['zt_30d'] == 0 and tech['yest_amp'] < 2: return 0, []
    if tech['yest_amp'] < 1.5: return 0, []

    # ===== 买卖力量比 =====
    bsr = 1.0
    if ti >= 2:
        bv = sum(v[j] for j in range(max(0,ti-4),ti+1) if c[j]>=o[j])
        sv = sum(v[j] for j in range(max(0,ti-4),ti+1) if c[j]<o[j])
        if sv > 0: bsr = bv / sv

    w = V6_WEIGHTS.copy()  # V8: copy weights so we can modify for scene
    hit = []; total = 0

    # ===== V8: 场景化权重调整 =====
    if scene_mode == 'event_driven':
        # 消息驱动模式: 提升C12(超跌反弹)权重, C11(涨停基因)权重
        if 12 in w: w[12] = int(w[12] * 1.5)  # 30→45
        if 11 in w: w[11] = int(w[11] * 1.3)  # 22→28
        if 18 in w: w[18] = int(w[18] * 1.2)  # 高振幅洗盘
    elif scene_mode == 'oversold_bounce':
        # 超跌反弹模式: 大幅提升C12权重
        if 12 in w: w[12] = int(w[12] * 2.0)  # 20→40
        if 8 in w: w[8] = int(w[8] * 1.3)    # 突破回调
        if 15 in w: w[15] = int(w[15] * 1.2)  # 缩量洗盘

    # ===== 19个条件评分 =====
    if cond_fns:
        for cid in [1, 2, 3, 4, 6, 7, 8, 11, 12]:
            if w.get(cid, 0) > 0 and cid in cond_fns:
                try:
                    fn = cond_fns[cid]
                    if cid == 2:
                        r = fn(c, v, tc, tp, r6, vr, None, None)
                    elif cid == 3:
                        r = fn(c, v, l, None, tc, tp, r6, bsr)
                    elif cid == 4:
                        r = fn(c, tc, tp, r6, 0.35)
                    elif cid == 6:
                        r = fn(c, v, l, h, tc, tp)
                    elif cid == 7:
                        r = fn(c, v, tc, tp, r6, bsr)
                    elif cid == 8:
                        r = fn(c, v, l, h, tc, tp, r6, bsr, vr)
                    else:
                        r = fn(c, tc, tp, r6)
                    if r[0]: hit.append(cid); total += w[cid]
                except: pass

        for cid in [9, 10]:
            if w.get(cid, 0) > 0 and cid in cond_fns:
                try:
                    r = cond_fns[cid](c, v, tc, tp, r6, bsr, h, l)
                    if r[0]: hit.append(cid); total += w[cid]
                except: pass

        for cid in [13, 14, 15, 16, 17, 18, 19]:
            if w.get(cid, 0) > 0 and cid in cond_fns:
                try:
                    fn = cond_fns[cid]
                    if cid == 13:
                        r = fn(c, v, h, l, v, tc, tp, r6, bsr)
                    elif cid == 14:
                        r = fn(c, v, l, h, tc, tp, r6, vr)
                    elif cid in (15, 18, 19):
                        r = fn(c, v, h, l, tc, tp, r6)
                    elif cid == 16:
                        r = fn(c, v, tc, tp, r6)
                    elif cid == 17:
                        r = fn(c, v, h, tc, tp, r6)
                    if r[0]: hit.append(cid); total += w[cid]
                except: pass

    # ===== 组合加分 =====
    if 11 in hit and 8 in hit: total += 12
    if 11 in hit and 15 in hit: total += 10
    if 11 in hit and 1 in hit: total += 8
    
    # V8: 场景化组合加分
    if scene_mode in ('event_driven', 'oversold_bounce'):
        # 超跌反弹+缩量洗盘 组合 → 超跌反转信号
        if 12 in hit and 15 in hit: total += 15
        # 涨停基因+超跌反弹 → 弹性大
        if 11 in hit and 12 in hit: total += 12

    # ===== V7: 涨停基因+MA10黄金区间组合加分 =====
    zt_gene = tech['zt_30d']
    in_golden_zone = -3 <= dev10 <= 5
    is_shrink = 0.5 <= vr <= 0.85
    
    if zt_gene >= 1 and in_golden_zone:
        total += 10
        if is_shrink:
            total += 8
        if zt_gene >= 3:
            total += 6
    
    # V8: 事件驱动/超跌反弹模式下的额外加分
    if scene_mode in ('event_driven', 'oversold_bounce'):
        if dev10 < 0 and zt_gene >= 1:
            total += 12  # MA10下方+有涨停基因 → 反弹时弹性大
        if r6 < 40 and dev10 < -2:
            total += 8   # 超卖+MA10下方 → 超跌反弹潜力
    
    if crash_next_day and dev10 < 0 and zt_gene >= 1:
        total += 8

    # ===== 基础技术分 =====
    base = 0
    if above_ma10:
        if 0 <= dev10 <= 3: base += 24
        elif 3 < dev10 <= 5: base += 16
        elif 5 < dev10 <= 10: base += 8
        else: base += 3
    elif ma10_cross:
        base += 24
    elif near_ma10:
        # V8: 场景化MA10下方基础分
        if scene_mode == 'event_driven':
            if -3 <= dev10 <= 0: base += 20  # 消息驱动: MA10下方0~3%也给高分
            elif -5 <= dev10 < -3: base += 12
        elif scene_mode == 'oversold_bounce':
            if -3 <= dev10 <= 0: base += 18
            elif -5 <= dev10 < -3: base += 14
            elif -8 <= dev10 < -5: base += 8
        elif crash_next_day:
            if -3 <= dev10 <= -1: base += 16
            elif -1 <= dev10 < 0: base += 20

    if ti >= 10:
        ma5_today = sum(c[ti-4:ti+1]) / 5
        ma5_yest = sum(c[ti-5:ti]) / 5
        ma10_today = sum(c[ti-9:ti+1]) / 10
        ma10_yest = sum(c[ti-10:ti]) / 10
        if ma5_yest <= ma10_yest and ma5_today > ma10_today:
            base += 15

    if 50 <= r6 <= 70: base += 14
    elif 40 <= r6 < 50: base += 8
    # V8: 超卖也给分(超跌反弹)
    elif 25 <= r6 < 40 and scene_mode in ('event_driven', 'oversold_bounce'):
        base += 6
    
    if 0.8 <= vr <= 1.2: base += 12
    elif 1.2 < vr <= 1.8: base += 6
    if tech['zt_30d'] >= 3: base += 16
    elif tech['zt_30d'] >= 1: base += 12
    if 2.5 <= tech['yest_amp'] <= 7: base += 6
    if not tech['is_yang']: base += 12
    if -3 <= tp <= 0: base += 8
    elif tp < -3: base += 4

    # ===== V9: 回测形态因子反哺（与B通道一致，基于非一字涨停次日胜率） =====
    # 通道A是"回调/缩量洗盘"选股，已排除昨日涨停与暴量；这里追加稳健性微调:
    # - 暴量滞涨(vr 2~3 但收阴/滞涨) 扣分 → 放巨量分歧次日胜率低(仅43%)
    # - 缩量蓄势+涨停基因 加小分 → 缩量惜售次日胜率最高(88%)
    # - RSI>88 高位过热 扣分 → 接近一字/追高风险
    v9_morph = 0
    if 2.5 < vr <= 3.0 and not tech['is_yang']:
        v9_morph -= 8  # 明显放量+收阴 → 滞涨分歧
    elif vr > 3.0:
        v9_morph -= 10  # 极端放量(防御, 多数已被前置过滤)
    if 0.5 <= vr <= 0.9 and tech['zt_30d'] >= 2:
        v9_morph += 6  # 缩量+资金铺垫 → 健康洗盘
    if tech['rsi6'] > 88:
        v9_morph -= 6  # 高位过热
    total += v9_morph
    return total, hit


def compute_v6_tech(kl, ti):
    """
    预计算V6所需的技术特征
    返回 tech dict, 与 score_channel_a_v6 配合使用
    """
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


# ============================================================
# 便捷函数: 从scan_stocks_v9自动导入条件函数
# ============================================================
def get_cond_fns():
    """获取所有条件函数映射 {cid: fn}"""
    from scan_stocks_v9 import (
        cond1, cond2, cond3_optimized, cond4,
        cond6, cond7_ma_multiline, cond8_breakout_pullback,
        cond9_macd_underwater_golden, cond10_strong_pullback_golden,
        cond11_limit_up_gene, cond12_oversold_bounce,
        SURGE_MODULE_LOADED,
    )
    fns = {
        1: cond1, 2: cond2, 3: cond3_optimized, 4: cond4,
        6: cond6, 7: cond7_ma_multiline, 8: cond8_breakout_pullback,
        9: cond9_macd_underwater_golden, 10: cond10_strong_pullback_golden,
        11: cond11_limit_up_gene, 12: cond12_oversold_bounce,
    }
    if SURGE_MODULE_LOADED:
        try:
            from surge_predictor import (
                cond13_launch_pattern, cond14_acceleration,
                cond15_shrink_wash, cond16_small_yang_charge,
                cond17_breakout_high, cond18_high_amp_wash,
                cond19_strong_bounce,
            )
            fns.update({
                13: cond13_launch_pattern, 14: cond14_acceleration,
                15: cond15_shrink_wash, 16: cond16_small_yang_charge,
                17: cond17_breakout_high, 18: cond18_high_amp_wash,
                19: cond19_strong_bounce,
            })
        except: pass
    return fns