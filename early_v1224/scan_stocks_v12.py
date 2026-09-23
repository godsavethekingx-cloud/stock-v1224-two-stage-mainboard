#!/usr/bin/env python3
"""
v12.0涨停板复盘优化版 — 基于7/22-7/29深度复盘
============================================
【复盘核心结论】
1. 47%涨停股前一天下跌(27.5%跌超3%) → 回调是买点不是风险
2. 56.4%涨停股RSI≥70 → 高RSI是强势股标志不是超买
3. 仅8.1%涨停股MACD金叉 → MACD完全不重要
4. 85.2%涨停股站上MA10 → 均线多头排列是必要条件
5. 48.3%近5日已涨停2次+ → 连板基因是核心中的核心
6. 近5日平均已涨14% → 要追强不是抄底

【v12优化】
- MACD权重从18→5（几乎废弃）
- 高RSI从扣分→加分（RSI>70加15分）
- 连板基因权重从20→35（最高权重）
- 新增"强势回调型"：昨日跌但站上MA10，权重25
- 放宽涨幅限制：允许近5日涨幅10-30%的股票
- 突破前高权重从24→30
- 均线多头排列权重从10→20
"""

import json, math, re, time, os, sys, requests
from datetime import datetime
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, '/workspace/stock-scan-package')
from scan_stocks_v9 import (
    get_mainboard_stocks, get_tencent_batch, get_sina_klines_batch,
    get_sina_kline, get_sina_finance,
    calc_rsi, calc_sma, calc_ema_series, calc_macd_series, calc_kdj_series,
    detect_market_trend, detect_market_style, detect_market_mood,
    SURGE_MODULE_LOADED,
)
if SURGE_MODULE_LOADED:
    from surge_predictor import (
        calc_limit_gene_score, predict_next_day_gain,
        cond13_launch_pattern, cond14_acceleration,
        cond15_shrink_wash, cond16_small_yang_charge,
        cond17_breakout_high, cond18_high_amp_wash, cond19_strong_bounce,
    )

# ============ v12.0: 基于复盘优化的动态权重 ============
def get_v12_weights(market_trend='range'):
    """v12: 基于7/22-7/29涨停板复盘优化"""
    weights = {
        1: 5,   # 10日线强势
        2: 5,   # 缩量回调
        3: 10,  # 趋势上行
        4: 30,  # 强势型（连续强势）
        6: 25,  # 涨停回踩（高胜率）
        7: 20,  # 均线多头排列（85%涨停股具备）
        8: 20,  # 突破回踩
        9: 5,   # MACD金叉（仅8.1%涨停股有，权重降低）
        10: 25, # 强势回踩金叉
        11: 35, # 涨停基因（48%近5日已涨停2次+，最高权重）
        12: 5,  # 超跌反弹（极少涨停股是超跌）
        13: 25, # 启动型
        14: 20, # 加速突破型
        15: 20, # 缩量洗盘型
        16: 10, # 小阳蓄势型
        17: 30, # 突破前高型（47.7%涨停股突破5日高）
        18: 20, # 高振幅洗盘型
        19: 30, # 强势股反包型
        20: 25, # v12新增: 强势回调型（昨日跌但站上MA10）
    }
    
    if market_trend == 'bull':
        weights[4] = 35; weights[11] = 40; weights[17] = 35; weights[19] = 35; weights[20] = 30
        weights[9] = 3
    elif market_trend == 'bear':
        weights[12] = 15; weights[9] = 8; weights[6] = 30; weights[20] = 20
    
    return weights


# ============ v12.0: 新增条件20 — 强势回调型 ============
def cond20_strong_pullback(c, v, lo, hi, tc, tp, r6, bsr):
    """
    v12新增: 强势回调型 — 基于复盘核心发现
    特征:
      - 昨日下跌（跌幅0-5%，不能暴跌）
      - 但股价仍站上MA10（趋势未破坏）
      - RSI在55-80之间（强势区间，不是弱股）
      - 近5日有过涨停或大涨（有股性）
      - 成交量萎缩（洗盘特征）
    复盘数据: 47%涨停股前一天下跌，85%站上MA10
    """
    if len(c) < 10 or tc <= 0:
        return False, ""
    
    # 昨日下跌
    if tp >= 0:
        return False, ""
    
    # 跌幅不能太大（-0.5%到-5%）
    if tp < -5 or tp > -0.5:
        return False, ""
    
    # 必须站上MA10
    ma10 = calc_sma(c, 10)
    if not ma10 or tc < ma10 * 0.98:
        return False, ""
    
    # RSI在强势区间（不是弱势股）
    if not r6 or r6 < 55 or r6 > 85:
        return False, ""
    
    # 近5日至少有一天涨幅>5%（有股性/活性）
    has_surge = False
    for i in range(-5, 0):
        if len(c) >= abs(i) + 1:
            daily_pct = (c[i] - c[i-1]) / c[i-1] * 100
            if daily_pct >= 5:
                has_surge = True
                break
    if not has_surge:
        return False, ""
    
    # 近3日有涨停更佳（连板基因）
    zt_in_3 = False
    for i in range(-3, 0):
        if len(c) >= abs(i) + 1:
            if (c[i] - c[i-1]) / c[i-1] * 100 >= 9.5:
                zt_in_3 = True
                break
    
    label = "强势回调"
    if zt_in_3:
        label += "(连板基因)"
    
    return True, f"{label}: 昨日跌{tp:.1f}%但站MA10，RSI{r6:.0f}强势，近5日有大阳"


# ============ v12.0: RSI评分重构 ============
def calc_rsi_score_v12(r6):
    """
    v12: RSI评分重构 — 基于复盘56.4%涨停股RSI≥70
    旧逻辑: RSI>70扣分 → 新逻辑: RSI>70加分
    """
    if r6 is None:
        return 0
    if r6 >= 75:
        return 15  # 超强动量，加分
    elif r6 >= 65:
        return 10  # 强势，加分
    elif r6 >= 55:
        return 5   # 偏强
    elif r6 >= 45:
        return 0   # 中性
    elif r6 >= 35:
        return -5  # 偏弱
    else:
        return -10  # 超弱


# ============ v12.0: 涨幅限制重构 ============
def check_v12_pre_filter(tp, c, circ_mv):
    """
    v12: 放宽涨幅限制 — 近5日涨幅不再一票否决
    复盘: 近5日平均已涨14%，很多股票已经涨了20%+
    """
    # 当日涨幅不过大即可（不超过7%，留给盘中空间）
    if tp > 7:
        return False, "当日涨幅>7%"
    
    # 不再严格限制近5日涨幅（复盘显示涨停前已涨14%）
    # 但近5日不能涨超40%（避免顶部）
    if len(c) >= 6:
        pct_5d = (c[-1] - c[-6]) / c[-6] * 100
        if pct_5d > 40:
            return False, "近5日涨幅>40%，风险过高"
        if pct_5d < -15:
            return False, "近5日跌幅>15%，太弱"
    
    # 市值限制保留
    if circ_mv > 500:
        return False, "市值>500亿"
    
    return True, ""


# ============ v12.0: 主扫描逻辑 ============
def main_v12():
    t0 = time.time()
    print(f"选股扫描 v12.0 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("v12优化: 基于7/22-7/29涨停板深度复盘")
    print("="*70)
    print("核心调整:")
    print("  1. MACD权重 18→5 (仅8.1%涨停股有金叉)")
    print("  2. 高RSI从扣分→加分 (56.4%涨停股RSI≥70)")
    print("  3. 涨停基因权重 20→35 (48%近5日已涨停2次+)")
    print("  4. 新增条件20: 强势回调型 (47%涨停前一天下跌)")
    print("  5. 放宽涨幅限制 (近5日平均已涨14%)")
    print("="*70)
    
    # 获取股票列表
    stocks = get_mainboard_stocks()
    print(f"\n主板非ST: {len(stocks)}只")
    
    # 获取实时行情
    codes = [s['code'] for s in stocks]
    quotes = {}
    batch_size = 300
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        q = get_tencent_batch(batch)
        quotes.update(q)
        if (i//batch_size + 1) % 50 == 0:
            print(f"  行情进度: {i}/{len(codes)}")
    print(f"腾讯行情: {len(quotes)}只")
    
    # 大盘判断
    trend = detect_market_trend(quotes)
    print(f"\n大盘趋势: {trend}")
    
    # 预筛选
    pf = []
    for s in stocks:
        td = quotes.get(s['code'], {})
        tc = td.get('price', 0)
        tp = td.get('pct', -999)
        circ_mv = td.get('circ_mv', 0)
        if tc <= 0 or tp < -999:
            continue
        ok, reason = check_v12_pre_filter(tp, [], circ_mv)
        if not ok:
            continue
        s['tc'] = tc; s['tp'] = tp; s['td'] = td
        pf.append(s)
    
    print(f"预筛选: {len(pf)}只")
    
    # K线获取
    print(f"  获取{len(pf)}只股票K线...")
    klines = get_sina_klines_batch([s['code'] for s in pf], datalen=150, max_workers=20)
    print(f"  K线完成: {len(klines)}/{len(pf)}只")
    
    # 条件扫描
    candidates = []
    weights = get_v12_weights(trend)
    
    for s in pf:
        kd = klines.get(s['code'])
        if not kd:
            continue
        c = kd['closes']; v = kd['volumes']; lo = kd['lows']; hi = kd['highs']
        tc = s['tc']; tp = s['tp']
        r6 = calc_rsi(c, 6)
        td = s['td']
        bsr = td.get('buy_sell_ratio', 0)
        vr = td.get('vol_ratio', 0)
        to = td.get('turnover', 0)
        circ = td.get('circ_shares', 0)
        
        mcs = []; mrs = []
        
        # 基础条件（从scan_stocks_v9导入）
        from scan_stocks_v9 import (
            cond1, cond2, cond3_optimized, cond4, cond6, cond7_ma_multiline,
            cond8_breakout_pullback, cond9_macd_underwater_golden, cond10_strong_pullback_golden,
            cond11_limit_up_gene, cond12_oversold_bounce,
        )
        
        ok, r = cond1(c, tc, tp, r6)
        if ok: mcs.append(1); mrs.append(r)
        ok, r = cond2(c, v, tc, tp, r6, vr, circ, to)
        if ok: mcs.append(2); mrs.append(r)
        ok, r = cond3_optimized(c, v, lo, [], tc, tp, r6, bsr)
        if ok: mcs.append(3); mrs.append(r)
        ok, r = cond4(c, tc, tp, r6, tp)
        if ok: mcs.append(4); mrs.append(r)
        ok, r = cond6(c, v, lo, hi, tc, tp)
        if ok: mcs.append(6); mrs.append(r)
        ok, r = cond7_ma_multiline(c, v, tc, tp, r6, bsr)
        if ok: mcs.append(7); mrs.append(r)
        ok, r = cond8_breakout_pullback(c, v, lo, hi, tc, tp, r6, bsr, vr)
        if ok: mcs.append(8); mrs.append(r)
        ok, r, _, _, _ = cond9_macd_underwater_golden(c, v, tc, tp, r6, bsr, hi, lo)
        if ok: mcs.append(9); mrs.append(r)
        ok, r, _ = cond10_strong_pullback_golden(c, v, tc, tp, r6, bsr, hi, lo)
        if ok: mcs.append(10); mrs.append(r)
        ok, r = cond11_limit_up_gene(c, tc, tp)
        if ok: mcs.append(11); mrs.append(r)
        ok, r = cond12_oversold_bounce(c, tc, tp, r6)
        if ok: mcs.append(12); mrs.append(r)
        
        # 涨停基因条件
        if SURGE_MODULE_LOADED:
            ok, r = cond13_launch_pattern(c, v, hi, lo, to, tc, tp, r6, bsr)
            if ok: mcs.append(13); mrs.append(r)
            ok, r = cond14_acceleration(c, v, hi, lo, tc, tp, r6, vr)
            if ok: mcs.append(14); mrs.append(r)
            ok, r = cond15_shrink_wash(c, v, hi, lo, tc, tp, r6)
            if ok: mcs.append(15); mrs.append(r)
            ok, r = cond16_small_yang_charge(c, v, tc, tp, r6)
            if ok: mcs.append(16); mrs.append(r)
            ok, r = cond17_breakout_high(c, v, hi, tc, tp, r6)
            if ok: mcs.append(17); mrs.append(r)
            ok, r = cond18_high_amp_wash(c, v, hi, lo, tc, tp, r6)
            if ok: mcs.append(18); mrs.append(r)
            ok, r = cond19_strong_bounce(c, v, hi, lo, tc, tp, r6)
            if ok: mcs.append(19); mrs.append(r)
        
        # v12新增: 条件20 — 强势回调型
        ok, r = cond20_strong_pullback(c, v, lo, hi, tc, tp, r6, bsr)
        if ok: mcs.append(20); mrs.append(r)
        
        if not mcs:
            continue
        
        # 评分计算
        sc = sum(weights.get(mc, 0) for mc in mcs)
        
        # v12: RSI重构评分
        rsi_score = calc_rsi_score_v12(r6)
        sc += rsi_score
        
        # 涨停基因评分
        gene_score = 0
        if SURGE_MODULE_LOADED and len(c) > 10:
            gs, _, _ = calc_limit_gene_score(c, v, hi, lo, [])
            gene_score = gs
            sc += gs * 0.3  # 涨停基因直接加分
        
        # 组合加分
        if len(mcs) >= 3:
            sc += len(mcs) * 5
        
        candidates.append({
            'code': s['code'], 'name': s.get('name', td.get('name', '')),
            'close': tc, 'pct': tp, 'rsi6': r6,
            'conditions': mcs, 'cond_reasons': mrs,
            'score': round(sc, 1), 'gene_score': gene_score,
        })
    
    print(f"候选池: {len(candidates)}只, 耗时{time.time()-t0:.0f}秒")
    
    # 排序输出
    candidates.sort(key=lambda x: x['score'], reverse=True)
    
    print(f"\n{'='*70}")
    print(f"🏆 v12.0 TOP15（趋势:{trend}）")
    print(f"{'='*70}")
    
    for i, c in enumerate(candidates[:15], 1):
        cond_str = ','.join(str(x) for x in c['conditions'])
        rsi_tag = "🔥" if c['rsi6'] and c['rsi6'] >= 70 else ""
        zt_gene_tag = f"基因{c['gene_score']:.0f}" if c['gene_score'] > 0 else ""
        print(f"\n#{i:2d} 【{c['code']}】 {c['name']} {rsi_tag}")
        print(f"   💰 {c['close']:.2f}元 {c['pct']:+.2f}% | 评分:{c['score']:.1f} | RSI:{c['rsi6']:.1f}")
        print(f"   条件: {cond_str}")
        for r in c['cond_reasons']:
            print(f"      · {r}")
        if zt_gene_tag:
            print(f"   🧬 {zt_gene_tag}")
    
    # 保存结果
    with open('/workspace/v12_picks.json', 'w') as f:
        json.dump({
            'scan_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'version': 'v12.0',
            'market_trend': trend,
            'candidates': candidates[:30],
        }, f, ensure_ascii=False, default=str)
    
    print(f"\n结果已保存: /workspace/v12_picks.json")
    return candidates

if __name__ == '__main__':
    main_v12()
