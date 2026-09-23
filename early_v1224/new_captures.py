#!/usr/bin/env python3
"""
V10 🆕 复盘优化增强模块 (基于 2026-08-25 复盘建议)

根据复盘结论落地三条优化:
1. oversold_rebound_capture: 超跌反弹捕捉 —— 补"昨日大跌但缩量、未破位"的漏选区
   (复盘: 未选中涨停54只中34只来自"昨日收跌被一刀切排除", 故新增此捕捉)
2. relay_pool: 连板接力池 —— 昨日涨停股单独入池做连板承接候选
   (复盘: 5只涨停漏选来自"昨日涨停连板接力未入池")
3. 通道B高位过滤已在 run_v9_final 内联优化(见 optimize_channel_b)，此处提供评分辅助

用法:
  from new_captures import capture_oversold_rebound, build_relay_pool
"""
import math
from collections import defaultdict


def _safe_round(x, n=2):
    try:
        return round(float(x), n)
    except (TypeError, ValueError):
        return 0.0


def compute_ma(closes, idx, window):
    s = closes[max(0, idx - window + 1):idx + 1]
    return sum(s) / len(s) if s else 0


def rsi(closes, idx, n=6):
    gains = losses = 0.0
    for j in range(max(1, idx - n + 1), idx + 1):
        d = closes[j] - closes[j - 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + (gains / n) / (losses / n))


def capture_oversold_rebound(all_kls, match_sector, min_score=60, top_n=15):
    """
    捕捉"昨日大跌但缩量、今日未放量破位"的超跌反弹候选。

    原则(源自复盘 34/54 漏选教训): 昨日明确下跌的票不是一律放弃,
    而是看是否满足"跌深+缩量+未破关键支撑+今日止跌"组合。

    评分项:
      - 昨日跌幅>3% (越深越加分, 超跌弹性)
      - 昨日缩量(量比<0.9加分, 缩量下跌=抛压衰竭)
      - 未破MA20深位 (dev_ma20 > -8, 非趋势性破位)
      - 今日止跌(今日跌幅>-1 或 收在昨收上方/长下影)
      - 靠近MA10/MA20 支撑 (候选介入位)
    """
    results = []
    for code, kl in all_kls.items():
        c = kl['c']; v = kl['v']; o = kl['o']; h = kl['h']; l = kl['l']; n = len(c)
        ti = n - 1
        if ti < 25:
            continue
        if v[ti] * c[ti] < 3000:
            continue
        yi = ti - 1  # 昨日收盘索引
        if yi < 20:
            continue
        yesterday = c[yi]
        if yi < 1 or c[yi - 1] <= 0:
            continue
        yest_pct = (yesterday / c[yi - 1] - 1) * 100
        # 必须昨日收跌
        if yest_pct >= -3:
            continue
        # 昨日量比
        vol20 = sum(v[max(0, yi - 20):yi]) / min(20, yi)
        vol_ratio = v[yi] / vol20 if vol20 > 0 else 1.0
        # 均线
        ma5 = compute_ma(c, ti, 5)
        ma10 = compute_ma(c, ti, 10)
        ma20 = compute_ma(c, ti, 20)
        dev_ma20 = (c[ti] / ma20 - 1) * 100 if ma20 > 0 else 0
        # 今日表现
        today_pct = (c[ti] / yesterday - 1) * 100
        today_ratio = v[ti] / vol20 if vol20 > 0 else 1.0
        # 长下影
        body_lo = min(o[ti], c[ti])
        lower_shadow = body_lo - l[ti]
        amp = (h[ti] - l[ti]) / yesterday * 100 if yesterday > 0 else 0
        has_lower = amp > 0 and lower_shadow / (h[ti] - l[ti]) > 0.3 if (h[ti] - l[ti]) > 0 else False
        stabilize = (today_pct >= -1.0) and has_lower

        # 评分
        score = 0
        # 跌深加分
        if yest_pct <= -6: score += 30
        elif yest_pct <= -4: score += 24
        elif yest_pct <= -3: score += 18
        # 昨日缩量(抛压衰竭)
        if vol_ratio < 0.7: score += 20
        elif vol_ratio < 0.9: score += 14
        elif vol_ratio < 1.0: score += 8
        else: score -= 10  # 放量下跌=下跌中继
        # 未深破位
        if -6 <= dev_ma20 <= 3: score += 15
        elif -10 <= dev_ma20 < -6: score += 8
        elif dev_ma20 < -10: score -= 15  # 趋势性破位, 不接
        # 今日止跌
        if today_pct > 0 and has_lower: score += 20
        elif today_pct >= -0.5 and has_lower: score += 14
        elif today_pct > 0: score += 12
        elif today_pct >= -1: score += 6
        else: score -= 12  # 今日继续大跌, 淘汰
        # 今日缩量企稳
        if today_ratio < 0.9: score += 10
        # 未放量破位硬性淘汰
        if today_ratio >= 1.3 and today_pct < -2:
            continue

        if score < min_score:
            continue

        # 支撑位
        if ma10 > 0 and c[ti] <= ma10 * 1.02:
            support = 'MA10'
            support_price = ma10
        elif ma20 > 0 and c[ti] <= ma20 * 1.03:
            support = 'MA20'
            support_price = ma20
        else:
            support = 'MA20'
            support_price = ma20

        name = kl.get('name', code)
        results.append({
            'code': code, 'name': name, 'score': round(score),
            'yest_pct': _safe_round(yest_pct), 'today_pct': _safe_round(today_pct),
            'vol_ratio_yest': _safe_round(vol_ratio), 'vol_ratio_today': _safe_round(today_ratio),
            'dev_ma20': _safe_round(dev_ma20), 'ma10': _safe_round(ma10), 'ma20': _safe_round(ma20),
            'support': support, 'support_price': _safe_round(support_price),
            'stop_loss': _safe_round(support_price * 0.94),
            'rs6': _safe_round(rsi(c, ti), 1),
            'sectors': match_sector(name, code)[:4],
            'signals': [
                f'昨日大跌{yest_pct:+.1f}%', f'昨日量比{vol_ratio:.2f}x',
                '未破MA20' if dev_ma20 > -8 else '深跌破位',
                '今日止跌' if stabilize else '今日续弱',
                f'回踩{support}',
            ],
        })
    results.sort(key=lambda x: -x['score'])
    return results[:top_n]


def build_relay_pool(all_kls, match_sector, top_n=15):
    """
    连板接力池: 昨日涨停(含一字/自然板)个股单独入池。

    复盘: 5只涨停(登海种业/上海能源/汉森制药/深中华A/新华百货)漏选自
    "昨日涨停连板接力未入池"。本池为这些高人气股提供系统化承接候选,
    供今日竞价/打板参考, 并标注溢价与风险, 不与通道B的稳健逻辑混淆。

    筛选:
      - 昨日涨停 (yest_pct >= 9.5)
      - 今日未直接大面(今日跌幅>-6, 一字板除外)
      - 给出今日量比、是否回封提示
    """
    results = []
    for code, kl in all_kls.items():
        c = kl['c']; v = kl['v']; o = kl['o']; h = kl['h']; l = kl['l']; n = len(c)
        ti = n - 1
        if ti < 25:
            continue
        yi = ti - 1
        if yi < 1 or c[yi - 1] <= 0:
            continue
        yest_pct = (c[yi] / c[yi - 1] - 1) * 100
        if yest_pct < 9.5:
            continue
        if v[ti] * c[ti] < 3000:
            continue
        today_pct = (c[ti] / c[yi] - 1) * 100
        # 今日大跌大面(>-6)淘汰(除非涨停回封)
        if today_pct < -6 and today_pct < 9:
            continue
        vol20 = sum(v[max(0, ti - 20):ti + 1]) / min(21, ti + 1)
        today_ratio = v[ti] / vol20 if vol20 > 0 else 1.0
        is_zt_today = today_pct >= 9.5
        # 连板数 (粗略: 连续昨日涨停数)
        consec = 0
        j = yi
        while j >= 1 and c[j] / c[j - 1] - 1 >= 0.095:
            consec += 1
            j -= 1
        ma5 = compute_ma(c, ti, 5)
        ma10 = compute_ma(c, ti, 10)
        dev_ma5 = (c[ti] / ma5 - 1) * 100 if ma5 > 0 else 0
        name = kl.get('name', code)
        board = '科创/创业' if (code.startswith('30') or code.startswith('688')) else '主板'
        # 风险提示
        risk = []
        if today_ratio >= 3: risk.append('今日巨量')
        if dev_ma5 > 15: risk.append('严重偏离5日线')
        if consec >= 5: risk.append(f'{consec}连板高标')
        if today_pct > 9: risk.append('今日续板')
        results.append({
            'code': code, 'name': name, 'board': board,
            'yest_pct': _safe_round(yest_pct), 'today_pct': _safe_round(today_pct),
            'consec': consec, 'today_ratio': _safe_round(today_ratio),
            'dev_ma5': _safe_round(dev_ma5), 'ma5': _safe_round(ma5), 'ma10': _safe_round(ma10),
            'is_zt_today': is_zt_today, 'risk': risk,
            'sectors': match_sector(name, code)[:4],
            'advice': f"竞价关注({'高开' if c[ti]>c[yi] else '分歧'})",
        })
    results.sort(key=lambda x: (-x['is_zt_today'], -x['consec'], -x['today_pct']))
    return results[:top_n]


def capture_microcap_rebound(all_kls, circ_mv_map, match_sector, top_n=12):
    """
    微盘低位反弹补池 V17.1 (2026-08-31 复盘二次建模: 弹性/情绪/启动三维)。
    复盘发现 '昨<3%次日涨停' 的 39 只微盘相对未涨停对照组, 关键判别特征:
      弹性  -> 市值更小(中位55亿)、低价
      情绪  -> 近20日有涨停基因(中位2次)、换手活跃(act20约1.3x)
      启动  -> 20日区间位置偏高(74%)、5日转强(+8%)、20日涨幅高(+18%), 且前一日小幅收阴回踩守MA5
    据此重建打分: 保留 微盘+昨小幅收阴 门槛, 新增 基因/位置/5日加速 等加分,
    目标是让 '低位+情绪+启动' 的票挤进 Top6/Top10, 抬高次日涨停捕获率。
    """
    results = []
    for code, kl in all_kls.items():
        mv = circ_mv_map.get(code, 0) or 0
        if not (0 < mv < 300):           # 仅 微盘<300亿
            continue
        c = kl['c']; v = kl['v']; o = kl['o']; h = kl['h']; l = kl['l']; n = len(c)
        ti = n - 1
        if ti < 25:
            continue
        if v[ti] * c[ti] < 5000:
            continue
        yi = ti - 1
        if yi < 20 or c[yi - 1] <= 0:
            continue
        yest_pct = (c[yi] / c[yi - 1] - 1) * 100
        # 昨涨<3%(含小幅收阴回踩、小阳启动): 复盘上界约+3%
        if not (-5 <= yest_pct < 3.0):
            continue
        # ---- 均线 ----
        ma5 = compute_ma(c, ti, 5)
        ma10 = compute_ma(c, ti, 10)
        ma20 = compute_ma(c, ti, 20)
        dev_ma10 = (c[ti] / ma10 - 1) * 100 if ma10 > 0 else 0
        dev_ma20 = (c[ti] / ma20 - 1) * 100 if ma20 > 0 else 0
        if dev_ma20 < -10:               # 趋势性深破位不接
            continue
        cur = c[ti]
        # ---- 弹性/情绪/启动 特征 ----
        hi20 = max(h[max(0, ti - 19):ti + 1]); lo20 = min(l[max(0, ti - 19):ti + 1])
        rng = hi20 - lo20
        pos_hi20 = (cur - lo20) / rng * 100 if rng > 0 else 50.0   # 20日区间位置
        g5 = (cur / c[max(0, ti - 5)] - 1) * 100
        g20 = (cur / c[max(0, ti - 20)] - 1) * 100
        gene = sum(1 for j in range(max(0, yi - 20), yi)
                   if c[j] / c[j - 1] - 1 >= 0.095)                 # 20日涨停基因(不含基日)
        # 量能/活跃度
        vol20 = sum(v[max(0, yi - 20):yi]) / min(20, yi) or 1.0
        today_ratio = v[ti] / vol20 if vol20 > 0 else 0.0
        today_pct = (cur / c[yi] - 1) * 100
        back5 = cur >= ma5
        vol_ok = today_ratio >= 1.05
        rebound = today_pct > 0
        # 信号闸: 止跌/放量/站回至少要有一个
        if not (vol_ok or back5 or rebound):
            continue
        # ---- 打分(弹性/情绪/启动主导 + 今日信号) ----
        score = 0
        # 弹性
        score += 10 if mv <= 60 else (6 if mv <= 120 else 0)
        score += 3 if cur <= 15 else 0
        # 情绪/股性
        score += min(18, gene * 6)                                  # 涨停基因
        score += 4 if today_ratio >= 1.0 else 0                     # 相对活跃
        score += 3 if mv <= 80 and gene >= 1 else 0                 # 小盘+有涨停记忆
        # 启动结构
        score += 10 if pos_hi20 >= 60 else (4 if (gene >= 3 and pos_hi20 >= 30) else 0)
        score += 6 if g5 >= 3 else 0                                # 5日刚转强
        score += 5 if g20 >= 8 else 0                               # 处于上升趋势
        # 回踩位置: 前一日小幅收阴且守在MA5上方
        score += 6 if (yest_pct <= 0.5 and back5) else 0
        # 今日量能/信号
        score += min(12, today_ratio * 6)
        if rebound: score += 6
        if dev_ma10 <= 0: score += 4
        score += 4 if dev_ma20 >= -3 else -5
        name = kl.get('name', code)
        results.append({
            'code': code, 'name': name, 'mv': round(mv),
            'score': round(score),
            'yest_pct': _safe_round(yest_pct), 'today_pct': _safe_round(today_pct),
            'vol_ratio_today': _safe_round(today_ratio),
            'pos_hi20': _safe_round(pos_hi20), 'g5': _safe_round(g5), 'g20': _safe_round(g20),
            'gene': gene, 'dev_ma10': _safe_round(dev_ma10), 'dev_ma20': _safe_round(dev_ma20),
            'support': 'MA10' if dev_ma10 <= 1 else ('MA20' if dev_ma20 <= 1 else 'MA5'),
            'support_price': _safe_round(ma10 if dev_ma10 <= 1 else ma20),
            'stop_loss': _safe_round((ma10 if dev_ma10 <= 1 else ma20) * 0.94),
            'sectors': match_sector(name, code)[:4],
            'signals': [
                f'市值{mv:.0f}亿·弹性', f'位置{pos_hi20:.0f}%',
                f'20日基因{gene}次', '启动' if (pos_hi20 >= 60 and g5 >= 3) else '回踩',
            ],
        })
    results.sort(key=lambda x: -x['score'])
    return results[:top_n]


def capture_microcap_rebound_v172(all_kls, circ_mv_map, match_sector, top_n=12):
    """
    微盘低位反弹补池 V17.2 (2026-08-31 基于30天/1107个次日涨停大样本重建模)。
    大样本推翻单日结论: 次日涨停微盘 的稳健特征是「涨停基因」, 且单调——
      基因≥2 → ~5.2%、≥3 → 7.5%、≥4 → 9%、≥5 → 12.3% (vs 随机1.7%)。
    形态上多在 MA5/MA10 下方回落(低位回踩), 而非高位加速。
    硬门槛: 基因≥2 + 基日收盘在MA10下方(低位回踩) + 基日<3%。
    打分: 基因为主(封顶40), 加弹性/收阴/温和放量 加权。
    """
    results = []
    for code, kl in all_kls.items():
        mv = circ_mv_map.get(code, 0) or 0
        if not (0 < mv < 300):
            continue
        c = kl['c']; v = kl['v']; o = kl['o']; h = kl['h']; l = kl['l']; n = len(c)
        ti = n - 1
        if ti < 25 or v[ti] * c[ti] < 5000:
            continue
        yi = ti - 1
        if yi < 20 or c[yi - 1] <= 0:
            continue
        yest_pct = (c[yi] / c[yi - 1] - 1) * 100
        # 基日<3%(含小幅收阴回踩、小阳)
        if not (-6 <= yest_pct < 3.0):
            continue
        cur = c[ti]
        ma5 = compute_ma(c, ti, 5); ma10 = compute_ma(c, ti, 10); ma20 = compute_ma(c, ti, 20)
        dev_ma10 = (cur / ma10 - 1) * 100 if ma10 > 0 else 0
        dev_ma20 = (cur / ma20 - 1) * 100 if ma20 > 0 else 0
        if dev_ma20 < -12:
            continue
        # 涨停基因(近20日, 不含基日) —— ★ 决定性
        gene = sum(1 for j in range(max(0, yi - 20), yi) if c[j] / c[j - 1] - 1 >= 0.095)
        if gene < 2:                                    # 硬门槛
            continue
        # 低位回踩: 收盘在MA10下方(近30日大样本验证)
        if not (dev_ma10 < 0):
            continue
        # 量能/信号
        vol20 = sum(v[max(0, yi - 20):yi]) / min(20, yi) or 1.0
        today_ratio = v[ti] / vol20 if vol20 > 0 else 0.0
        today_pct = (cur / c[yi] - 1) * 100
        rebound = today_pct > 0
        if not (today_ratio >= 1.0 or rebound):         # 需有放量或止跌
            continue
        # ---- 打分 ----
        score = 0
        score += min(40, gene * 10)                     # 基因主导(封顶40)
        if yest_pct < 0: score += 8                     # 基日收阴(回踩蓄势)
        score += 8 if mv <= 60 else (4 if mv <= 120 else 0)
        score += 3 if cur <= 12 else 0
        score += 6 if -12 <= dev_ma10 < 0 else 0
        if today_ratio < 2.5:                           # 温和放量; 巨量降权
            score += min(10, today_ratio * 5)
        else:
            score -= 6
        if rebound: score += 4
        score += 3 if (yest_pct > -4 and yest_pct < 0) else 0   # 跌幅恰到-4~0最优
        name = kl.get('name', code)
        results.append({
            'code': code, 'name': name, 'mv': round(mv),
            'score': round(score),
            'yest_pct': _safe_round(yest_pct), 'today_pct': _safe_round(today_pct),
            'vol_ratio_today': _safe_round(today_ratio),
            'gene': gene, 'dev_ma10': _safe_round(dev_ma10), 'dev_ma20': _safe_round(dev_ma20),
            'ma5': _safe_round(ma5), 'ma10': _safe_round(ma10), 'ma20': _safe_round(ma20),
            'support': 'MA10',
            'support_price': _safe_round(ma10),
            'stop_loss': _safe_round(ma10 * 0.94),
            'sectors': match_sector(name, code)[:4],
            'signals': [f'市值{mv:.0f}亿·弹性', f'基因{gene}次', '低位回踩(破MA10)',
                        '收阴蓄势' if yest_pct < 0 else '小阳'],
        })
    results.sort(key=lambda x: -x['score'])
    return results[:top_n]


def optimize_channel_b(b_list, mode='normal'):
    """
    通道B高位过滤优化: 对已排序的通道B列表, 根据高位风险重新打分与降权。
    复盘结论: 通道B 6只仅1涨停、胜率50%, 高位连板(RSI/偏离)应降权并给明确风险。
    """
    out = []
    for s in b_list:
        risk_tag = s.get('risk_tag', '')
        rsi_val = s.get('rsi6', 50)
        consec = s.get('max_consec', 1)
        dev5 = s.get('dev_ma5', 0)
        score = s.get('score', 0)
        penalties = []
        # RSI过热
        if rsi_val >= 85:
            score -= 12; penalties.append('RSI过热≥85')
        elif rsi_val >= 78:
            score -= 6; penalties.append('RSI偏高')
        # 连板过高
        if consec >= 5:
            score -= 10; penalties.append(f'{consec}连板高标风险')
        elif consec >= 4:
            score -= 5; penalties.append(f'{consec}连板')
        if score != s.get('score', 0):
            s['score'] = max(0, score)
            if not risk_tag:
                s['risk_tag'] = ' / '.join(penalties)
            else:
                s['risk_tag'] = risk_tag + ' / ' + '/'.join(penalties)
        out.append(s)
    out.sort(key=lambda x: -x.get('score', 0))
    return out