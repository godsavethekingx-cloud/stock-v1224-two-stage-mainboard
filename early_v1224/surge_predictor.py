#!/usr/bin/env python3
"""
涨停基因增强模块 + 次日涨幅预测 — v10.0
================================================================
核心目标: 从候选池中筛选出次日涨幅>5%的高弹性标的

【涨停基因六因子模型】
1. zt_freq: 涨停频率（60天涨停次数，越高越活跃）
2. zt_recency: 涨停新鲜度（最近一次涨停距今天数，越近越好）
3. big_yang_count: 大阳线频率（60天内涨幅>7%的天数）
4. consec_power: 连板能力（60天内最大连板数）
5. vol_surge: 量能爆发（最近3天是否有放量>2倍均量）
6. amplitude: 振幅活力（近5日平均振幅，振幅大的股票弹性好）

【次日涨幅预测模型】
综合6因子涨停基因 + 技术信号 + 消息面 + 资金面，预测次日涨幅区间
"""
import math

# ============================================================
#  工具函数（从 scan_stocks_v9.py 复制核心计算）
# ============================================================

def calc_pct(c):
    """计算每日涨跌幅列表"""
    if len(c) < 2: return []
    return [(c[i]-c[i-1])/c[i-1]*100 for i in range(1, len(c))]

def zt_count(c, d):
    """统计d天内涨停次数(>=9.8%)，返回(次数, 索引列表)"""
    p = calc_pct(c)
    n = min(len(p), d-1)
    cnt = 0
    idx = []
    for i, x in enumerate(p[-n:]):
        if x >= 9.8:
            cnt += 1
            idx.append(len(p) - n + i)
    return cnt, idx

def big_pct_count(c, d, t):
    """统计d天内涨幅超过t%的天数"""
    p = calc_pct(c)
    n = min(len(p), d-1)
    return sum(1 for x in p[-n:] if x >= t)

def consec_zt_max(c, d):
    """d天内最大连板数"""
    p = calc_pct(c)
    n = min(len(p), d-1)
    mx = 0
    cur = 0
    for x in p[-n:]:
        if x >= 9.8:
            cur += 1
            mx = max(mx, cur)
        else:
            cur = 0
    return mx

def calc_sma(c, p):
    """简单移动平均"""
    if len(c) < p: return None
    return sum(c[-p:]) / p

def calc_amplitude(c, hi, lo, d=5):
    """计算近d天平均振幅(%)"""
    if len(c) < d or len(hi) < d or len(lo) < d:
        return 0
    amps = []
    for i in range(-d, 0):
        if lo[i] > 0:
            amp = (hi[i] - lo[i]) / lo[i] * 100
            amps.append(amp)
    return sum(amps) / len(amps) if amps else 0


# ============================================================
#  涨停基因六因子评分
# ============================================================

def calc_limit_gene_score(c, v, hi, lo, to):
    """
    涨停基因六因子评分（0-100分）
    返回: (总分, 因子明细字典, 因子描述列表)
    """
    factors = {}
    details = []
    total = 0

    # 因子1: 涨停频率（0-25分）
    zt60, zt_idx = zt_count(c, 60)
    zt30, _ = zt_count(c, 30)
    zt10, _ = zt_count(c, 10)

    f1 = 0
    if zt60 >= 10: f1 = 25
    elif zt60 >= 6: f1 = 20
    elif zt60 >= 3: f1 = 15
    elif zt60 >= 2: f1 = 10
    elif zt60 >= 1: f1 = 5
    factors['zt_freq'] = f1
    total += f1
    if f1 >= 15:
        details.append(f"涨停{zt60}次/60天({zt10}次/10天)")

    # 因子2: 涨停新鲜度（0-20分）—— 最近涨停距今越近越好
    f2 = 0
    if zt_idx:
        days_since = len(c) - 1 - zt_idx[-1]  # 距今天数
        if days_since <= 3: f2 = 20
        elif days_since <= 5: f2 = 16
        elif days_since <= 10: f2 = 12
        elif days_since <= 20: f2 = 6
        elif days_since <= 30: f2 = 3
        factors['zt_recency'] = f2
        total += f2
        if f2 >= 12:
            details.append(f"最近涨停距今{days_since}天")

    # 因子3: 大阳线频率（0-15分）
    f3 = 0
    big_yang_60 = big_pct_count(c, 60, 7)
    big_yang_30 = big_pct_count(c, 30, 7)
    if big_yang_60 >= 10: f3 = 15
    elif big_yang_60 >= 5: f3 = 12
    elif big_yang_60 >= 3: f3 = 9
    elif big_yang_30 >= 2: f3 = 6
    elif big_yang_30 >= 1: f3 = 3
    factors['big_yang'] = f3
    total += f3
    if f3 >= 9:
        details.append(f"大阳线{big_yang_60}次/60天")

    # 因子4: 连板能力（0-15分）
    f4 = 0
    max_consec = consec_zt_max(c, 60)
    if max_consec >= 5: f4 = 15
    elif max_consec >= 4: f4 = 13
    elif max_consec >= 3: f4 = 10
    elif max_consec >= 2: f4 = 6
    elif max_consec >= 1: f4 = 2
    factors['consec_power'] = f4
    total += f4
    if f4 >= 6:
        details.append(f"最大连板{max_consec}板")

    # 因子5: 量能爆发（0-15分）
    f5 = 0
    if len(v) >= 20:
        avg_vol = sum(v[-20:-3]) / 17 if v[-20:-3] else 1
        recent_max_ratio = max([x / avg_vol for x in v[-3:]]) if avg_vol > 0 else 1
        if recent_max_ratio >= 4.0: f5 = 15
        elif recent_max_ratio >= 3.0: f5 = 12
        elif recent_max_ratio >= 2.0: f5 = 9
        elif recent_max_ratio >= 1.5: f5 = 5
        elif recent_max_ratio >= 1.2: f5 = 2
    factors['vol_surge'] = f5
    total += f5
    if f5 >= 9:
        details.append(f"近期放量{recent_max_ratio:.1f}倍")

    # 因子6: 振幅活力（0-10分）
    f6 = 0
    avg_amp = calc_amplitude(c, hi, lo, 5)
    if avg_amp >= 8: f6 = 10
    elif avg_amp >= 6: f6 = 8
    elif avg_amp >= 4: f6 = 5
    elif avg_amp >= 3: f6 = 3
    elif avg_amp >= 2: f6 = 1
    factors['amplitude'] = f6
    total += f6
    if f6 >= 5:
        details.append(f"5日均振幅{avg_amp:.1f}%")

    return total, factors, details


# ============================================================
#  次日涨幅预测模型
# ============================================================

def predict_next_day_gain(c, v, hi, lo, to, rsi6, vol_ratio, buy_sell_ratio,
                           pct, conditions, news_matches, combo_bonus,
                           limit_up_count, max_pct_5d):
    """
    次日涨幅预测（综合模型）
    返回: (预测涨幅%, 涨幅等级, 预测理由列表)

    涨幅等级:
      'A': 预测>=7%（涨停级别）
      'B': 预测5-7%（大阳级别）
      'C': 预测3-5%（中阳级别）
      'D': 预测1-3%（小阳级别）
      'E': 预测<1%（平盘/微跌）
    """
    reasons = []
    base_gain = 0.5  # 基础预期（大盘平均）

    # 1. 涨停基因加分
    gene_score, factors, gene_details = calc_limit_gene_score(c, v, hi, lo, to)
    if gene_score >= 60:
        base_gain += 3.0
        reasons.append(f"涨停基因极强({gene_score}分): {', '.join(gene_details[:3])}")
    elif gene_score >= 40:
        base_gain += 2.0
        reasons.append(f"涨停基因较强({gene_score}分)")
    elif gene_score >= 20:
        base_gain += 1.0
        reasons.append(f"涨停基因一般({gene_score}分)")
    elif gene_score >= 10:
        base_gain += 0.5

    # 2. 技术信号加分
    # MACD金叉当天或次日
    if 9 in conditions:
        base_gain += 1.5
        reasons.append("MACD金叉信号")
    # 强势回踩金叉
    if 10 in conditions:
        base_gain += 2.0
        reasons.append("强势回踩金叉")
    # 涨停回踩型
    if 6 in conditions:
        base_gain += 2.5
        reasons.append("涨停回踩放量启动")
    # 突破回踩
    if 8 in conditions:
        base_gain += 1.0
        reasons.append("突破回踩确认")
    # 超跌反弹
    if 12 in conditions:
        base_gain += 1.5
        reasons.append("超跌反弹低位启动")
    # v10.2: 涨停板回溯高胜率形态
    if 15 in conditions:
        base_gain += 2.0
        reasons.append("缩量洗盘型（涨停回溯高胜率）")
    if 16 in conditions:
        base_gain += 1.5
        reasons.append("小阳蓄势型（主力偷偷吸筹）")
    if 17 in conditions:
        base_gain += 2.5
        reasons.append("突破前高型（次日连板概率最高）")
    if 18 in conditions:
        base_gain += 1.2
        reasons.append("高振幅洗盘型（股性活跃）")
    # 多条件组合
    if combo_bonus >= 20:
        base_gain += 2.0
        reasons.append(f"多条件共振(组合+{combo_bonus})")
    elif combo_bonus >= 10:
        base_gain += 1.0
        reasons.append(f"条件组合(组合+{combo_bonus})")

    # 3. 量能配合加分
    if vol_ratio and vol_ratio >= 2.5:
        base_gain += 2.0
        reasons.append(f"量能爆发(量比{vol_ratio:.1f})")
    elif vol_ratio and vol_ratio >= 1.8:
        base_gain += 1.2
        reasons.append(f"放量配合(量比{vol_ratio:.1f})")
    elif vol_ratio and vol_ratio >= 1.3:
        base_gain += 0.5

    # 4. 资金面加分
    if buy_sell_ratio and buy_sell_ratio >= 1.3:
        base_gain += 1.5
        reasons.append(f"大单强力净买入(买卖比{buy_sell_ratio:.2f})")
    elif buy_sell_ratio and buy_sell_ratio >= 1.1:
        base_gain += 0.8

    # 5. 消息面催化加分
    news_boost = 0
    if news_matches:
        total_news_bonus = sum(b for _, b, _ in news_matches)
        if total_news_bonus >= 30:
            news_boost = 2.0
            reasons.append(f"强消息面催化({total_news_bonus}分)")
        elif total_news_bonus >= 18:
            news_boost = 1.2
            reasons.append(f"消息面利好({total_news_bonus}分)")
        elif total_news_bonus >= 12:
            news_boost = 0.6
    base_gain += news_boost

    # 6. RSI位置调整
    if rsi6:
        if rsi6 < 30:
            base_gain += 1.0  # 超卖反弹空间大
            reasons.append("RSI超卖区，反弹空间大")
        elif rsi6 > 80:
            base_gain -= 1.5  # 超买区有回调压力
        elif rsi6 > 75:
            base_gain -= 0.5

    # 7. 当日涨幅调整（低开高走潜力大）
    if pct is not None:
        if -2 <= pct <= 0:
            base_gain += 0.8  # 平盘或微跌，次日反包概率高
        elif 0 < pct <= 2:
            base_gain += 0.3
        elif pct > 5:
            base_gain -= 0.5  # 已涨太多，追高风险
        elif pct > 7:
            base_gain -= 1.0

    # 8. 近期5天最大涨幅调整（弹性确认）
    if max_pct_5d and max_pct_5d >= 9.8:
        base_gain += 1.0  # 近期有涨停，弹性确认
        reasons.append("5日内有涨停，弹性确认")
    elif max_pct_5d and max_pct_5d >= 7:
        base_gain += 0.5

    # 确定涨幅等级
    pred_gain = max(base_gain, -3.0)  # 下限-3%
    if pred_gain >= 7:
        grade = 'A'
    elif pred_gain >= 5:
        grade = 'B'
    elif pred_gain >= 3:
        grade = 'C'
    elif pred_gain >= 1:
        grade = 'D'
    else:
        grade = 'E'

    return round(pred_gain, 1), grade, reasons


# ============================================================
#  条件13: 启动型涨停捕捉（专门捕捉涨停前一天的形态）
# ============================================================

def cond13_launch_pattern(c, v, hi, lo, to, tc, tp, rsi6, buy_sell_ratio):
    """
    条件13: 启动型涨停捕捉
    特征: 近10天有涨停/大阳 -> 缩量回踩 -> 今日放量突破（次日涨停概率高）

    关键形态:
    1. 10天内有过涨停或>7%大阳（证明有资金关注）
    2. 回踩3-8天，量缩（洗盘结束）
    3. 今日放量收阳，量>5日均量1.5倍
    4. RSI从低位回升（30-70区间）
    5. 买卖比>=0.95（资金开始回流）
    """
    if len(c) < 15 or len(v) < 15 or len(lo) < 15:
        return False, ""

    # 1. 近10天有涨停或大阳(v10.3: 阈值从7%降到5%，博通集成6.63%就能命中)
    zt10, _ = zt_count(c, 10)
    big10 = big_pct_count(c, 10, 5)
    if zt10 == 0 and big10 == 0:
        return False, ""

    # 2. 缩量回踩确认（近3天量<5天前量均量的70%）
    if len(v) >= 8:
        recent_vols = v[-3:]
        old_vols = v[-8:-3]
        if old_vols:
            avg_old = sum(old_vols) / len(old_vols)
            avg_recent = sum(recent_vols) / len(recent_vols)
            if avg_old > 0 and avg_recent >= avg_old * 0.7:
                return False, ""  # 没有缩量，不是洗盘

    # 3. 今日放量（量>5日均量1.5倍）且收阳
    if len(v) >= 6:
        avg5_vol = sum(v[-6:-1]) / 5
        if avg5_vol <= 0 or v[-1] < avg5_vol * 1.5:
            return False, ""  # 没有放量
        if c[-1] <= c[-2]:
            return False, ""  # 没有收阳

    # 4. RSI在合理区间
    if rsi6 is not None and (rsi6 < 25 or rsi6 >= 80):
        return False, ""

    # 5. 涨幅过滤（不高不低，3%以内最好）
    if tp > 8 or tp < -3:
        return False, ""

    # 6. 基本面过滤
    if tc >= 80:
        return False, ""

    # 构建描述
    pct_today = tp
    vol_ratio_5d = v[-1] / (sum(v[-6:-1]) / 5) if len(v) >= 6 and sum(v[-6:-1]) > 0 else 0

    return True, (f"启动型: 10天涨停{zt10}次/大阳{big10}次，"
                  f"缩量洗盘后放量{vol_ratio_5d:.1f}倍收阳涨{pct_today:.1f}%")


# ============================================================
#  条件14: 加速突破型（二波启动，主升浪前夜）
# ============================================================

def cond14_acceleration(c, v, hi, lo, tc, tp, rsi6, vol_ratio):
    """
    条件14: 加速突破型（主升浪前夜）
    特征: 前期有一波上涨（20天涨10-30%）-> 回调不破关键位 ->
          今日放量突破前高或平台，RSI从50-65区间启动

    这是涨停最密集的形态之一，通常启动后2-3天内有涨停
    """
    if len(c) < 30 or len(v) < 30 or len(hi) < 30:
        return False, ""

    # 1. 20天前有一波上涨（20天涨10-30%）
    if len(c) >= 25:
        low_20 = min(c[-25:-10])
        high_20 = max(c[-25:-5])
        if low_20 <= 0:
            return False, ""
        wave_pct = (high_20 - low_20) / low_20 * 100
        if wave_pct < 8 or wave_pct > 40:
            return False, ""  # 波幅不合理
    else:
        return False, ""

    # 2. 回调不破 wave 起点的1.05倍
    recent_low = min(c[-5:])
    if recent_low < low_20 * 1.05:
        return False, ""  # 跌破了

    # 3. 今日放量突破近5日高点
    high_5d = max(hi[-6:-1]) if len(hi) >= 6 else hi[-2]
    if tc <= high_5d:
        return False, ""  # 没有突破
    if vol_ratio is None or vol_ratio < 1.3:
        return False, ""  # 没有放量

    # 4. RSI从适中区间启动
    if rsi6 is not None:
        if rsi6 < 35 or rsi6 > 75:
            return False, ""

    # 5. 涨幅过滤
    if tp > 9 or tp < -2:
        return False, ""

    vol_desc = f"量比{vol_ratio:.1f}" if vol_ratio else ""
    return True, (f"加速突破: 前波涨{wave_pct:.0f}%后回调不破，"
                  f"今日放量突破5日高，{vol_desc}")


# ============================================================
#  条件19: 强势股反包型（v10.3新增 — 博通集成模式）
# ============================================================

def cond19_strong_bounce(c, v, hi, lo, tc, tp, rsi6):
    """
    条件19: 强势股反包型（大阳启动→缩量/平量回踩→次日反包涨停）
    v10.3核心新增: 专门捕捉博通集成7/22(+6.63%)→7/23(-4.16%)→7/24(+10%)模式

    关键形态:
    1. 3日内有大阳(>=5%)或涨停（启动信号）
    2. 次日缩量或平量回踩(-2%~-7%)（洗盘，不要求缩到70%以下）
    3. 今日收阳且站上昨日高点（反包确认）
    4. 股价在MA5之上（强势特征）
    5. 5日平均振幅>=5%（股性活跃）
    """
    if len(c) < 8 or len(v) < 8 or len(hi) < 8 or len(lo) < 8:
        return False, ""

    # 1. 3日内有大阳(>=5%)或涨停（启动信号）
    has_big_yang = False
    launch_idx = -1
    for i in range(2, 5):  # 往前看2-4天
        if len(c) > i:
            pct_i = (c[-i] - c[-i-1]) / c[-i-1] * 100
            if pct_i >= 5.0:
                has_big_yang = True
                launch_idx = i
                break
    if not has_big_yang:
        return False, ""

    # 2. 次日缩量或平量回踩(-2%~-7%)
    # 回踩日 = 启动日后一天
    if launch_idx >= 2:
        wash_pct = (c[-launch_idx+1] - c[-launch_idx]) / c[-launch_idx] * 100
        if not (-7 <= wash_pct <= -1.5):
            return False, ""
        # 量比不要求缩到0.7，平量也可以（0.5-1.5倍）
        if len(v) > launch_idx:
            wash_vol_ratio = v[-launch_idx+1] / (sum(v[-launch_idx-4:-launch_idx+1]) / 4) if sum(v[-launch_idx-4:-launch_idx+1]) > 0 else 1
            if wash_vol_ratio > 1.8:  # 放量下跌不是洗盘
                return False, ""

    # 3. 今日收阳且站上昨日高点（反包确认）
    if tp < 0.5:  # 今日必须涨
        return False, ""
    if len(hi) >= 2 and tc <= hi[-2] * 0.98:  # 必须站上昨日高点
        return False, ""

    # 4. 股价在MA5之上
    ma5 = calc_sma(c, 5)
    if ma5 and tc < ma5 * 0.98:
        return False, ""

    # 5. 5日平均振幅>=5%（股性活跃）
    amps = []
    for i in range(-5, 0):
        if lo[i] > 0:
            amps.append((hi[i] - lo[i]) / lo[i] * 100)
    avg_amp = sum(amps) / len(amps) if amps else 0
    if avg_amp < 4:
        return False, ""

    return True, f"强势反包: {launch_idx}天前大阳启动，回踩{abs(wash_pct):.1f}%后今日反包+站上昨日高"


# ============================================================
#  辅助函数: 判断是否为涨停潜力股
# ============================================================

def is_surge_candidate(c, v, hi, lo, to, rsi6, vol_ratio, pct,
                       conditions, news_matches, combo_bonus,
                       limit_up_count, max_pct_5d):
    """
    判断是否为涨停潜力股（次日涨幅>5%概率>40%）
    返回: (bool, predicted_gain, grade, reasons)
    """
    pred_gain, grade, reasons = predict_next_day_gain(
        c, v, hi, lo, to, rsi6, vol_ratio, 0,  # buy_sell_ratio placeholder
        pct, conditions, news_matches, combo_bonus,
        limit_up_count, max_pct_5d
    )

    # 涨停基因阈值
    gene_score, _, _ = calc_limit_gene_score(c, v, hi, lo, to)

    # 综合判断
    is_candidate = False
    if pred_gain >= 5.0 and gene_score >= 25:
        is_candidate = True
    elif pred_gain >= 4.0 and gene_score >= 35:
        is_candidate = True
    elif grade in ('A', 'B') and len(reasons) >= 3:
        is_candidate = True

    return is_candidate, pred_gain, grade, reasons


# ============================================================
#  条件15-18: 基于近60天涨停板回溯分析的高胜率形态
# ============================================================

def cond15_shrink_wash(c, v, hi, lo, tc, tp, rsi6):
    """
    条件15: 缩量洗盘型（涨停回溯胜率最高的洗盘结束信号）
    特征: 缩量(<0.8倍均量) + 阴线/十字星 + RSI<55 + 股性活跃
    回测: 近60天5415次涨停中，268次(4.9%)涨停前一天出现此形态
    """
    if len(c) < 10 or len(v) < 10 or len(hi) < 10:
        return False, ""
    # 1. 缩量 < 0.8倍5日均量
    avg5_vol = sum(v[-6:-1]) / 5 if len(v) >= 6 else v[-1]
    if avg5_vol <= 0 or v[-1] >= avg5_vol * 0.8:
        return False, ""
    # 2. 阴线或十字星
    body_pct = abs(c[-1] - c[-2]) / c[-2] * 100 if c[-2] > 0 else 0
    if tp > 0.5:
        return False, ""
    # 3. RSI<55
    if rsi6 is None or rsi6 >= 55:
        return False, ""
    # 4. 股性活跃证明（近10日有涨停或大阳）
    zt10, _ = zt_count(c, 10)
    big10 = big_pct_count(c, 10, 7)
    if zt10 == 0 and big10 == 0:
        return False, ""
    # 5. 基本面过滤
    if tc >= 80 or tp < -5:
        return False, ""
    return True, f"缩量洗盘: 量缩至{v[-1]/avg5_vol:.2f}倍均量, 跌{abs(tp):.1f}%, RSI{rsi6:.0f}"


def cond16_small_yang_charge(c, v, tc, tp, rsi6):
    """
    条件16: 小阳蓄势型（主力偷偷吸筹，不引起市场注意）
    特征: 小阳线(0~3%) + 温和放量(量比1~2) + RSI 30-55
    回测: 近60天5415次涨停中，551次(10.2%)涨停前一天出现此形态
    """
    if len(c) < 10 or len(v) < 10:
        return False, ""
    # 1. 小阳线 0~3%
    if not (0 < tp <= 3):
        return False, ""
    # 2. 温和放量 1.0~2.0倍5日均量
    avg5_vol = sum(v[-6:-1]) / 5 if len(v) >= 6 else v[-1]
    if avg5_vol <= 0:
        return False, ""
    vol_ratio = v[-1] / avg5_vol
    if not (1.0 <= vol_ratio < 2.0):
        return False, ""
    # 3. RSI 30-55
    if rsi6 is None or not (30 <= rsi6 <= 55):
        return False, ""
    # 4. 股性活跃证明
    zt10, _ = zt_count(c, 10)
    big10 = big_pct_count(c, 10, 7)
    if zt10 == 0 and big10 == 0:
        return False, ""
    # 5. 基本面过滤
    if tc >= 80:
        return False, ""
    return True, f"小阳蓄势: 涨{tp:.1f}%, 量比{vol_ratio:.1f}, RSI{rsi6:.0f}"


def cond17_breakout_high(c, v, hi, tc, tp, rsi6):
    """
    条件17: 突破前高型（主升浪启动信号，次日连板概率最高）
    特征: 突破/接近20日高点 + 放量(量比>1.3) + RSI<70 + 收阳
    回测: 近60天5415次涨停中，198次(3.7%)涨停前一天出现此形态
          次日连板概率高达22.1%（所有形态中最高）
    """
    if len(c) < 25 or len(v) < 10 or len(hi) < 25:
        return False, ""
    # 1. 突破/接近20日高点
    high_20 = max(hi[-21:-1]) if len(hi) >= 21 else max(hi[:-1])
    if hi[-1] < high_20 * 0.98:
        return False, ""
    # 2. 放量 > 1.3倍5日均量
    avg5_vol = sum(v[-6:-1]) / 5 if len(v) >= 6 else v[-1]
    if avg5_vol <= 0 or v[-1] < avg5_vol * 1.3:
        return False, ""
    # 3. RSI < 70（避免超买区）
    if rsi6 is None or rsi6 >= 70:
        return False, ""
    # 4. 收阳
    if tp <= 0:
        return False, ""
    # 5. 涨幅不过大（避免追高风险）
    if tp > 8:
        return False, ""
    # 6. 基本面过滤
    if tc >= 80:
        return False, ""
    vol_ratio = v[-1] / avg5_vol
    return True, f"突破前高: 接近20日高, 量比{vol_ratio:.1f}, 涨{tp:.1f}%, RSI{rsi6:.0f}"


def cond18_high_amp_wash(c, v, hi, lo, tc, tp, rsi6):
    """
    条件18: 高振幅洗盘型（股性活跃，洗盘充分，爆发力极强）
    特征: 近5日振幅>5% + 当日缩量回踩(<0.9倍均量) + RSI 30-60
    回测: 近60天5415次涨停中，1536次(28.4%)涨停前一天出现此形态
    """
    if len(c) < 10 or len(v) < 10 or len(hi) < 10 or len(lo) < 10:
        return False, ""
    # 1. 近5日平均振幅 > 5%
    avg_amp = calc_amplitude(c, hi, lo, 5)
    if avg_amp < 5:
        return False, ""
    # 2. 当日缩量 < 0.9倍5日均量
    avg5_vol = sum(v[-6:-1]) / 5 if len(v) >= 6 else v[-1]
    if avg5_vol <= 0 or v[-1] >= avg5_vol * 0.9:
        return False, ""
    # 3. RSI 30-60
    if rsi6 is None or not (30 <= rsi6 <= 60):
        return False, ""
    # 4. 当日涨幅不过大
    if tp > 3 or tp < -3:
        return False, ""
    # 5. 基本面过滤
    if tc >= 80:
        return False, ""
    return True, f"高振幅洗盘: 5日振幅{avg_amp:.1f}%, 量缩至{v[-1]/avg5_vol:.2f}倍, RSI{rsi6:.0f}"