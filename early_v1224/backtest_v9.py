#!/usr/bin/env python3
"""
v9.5选股系统回测验证脚本
======================
基于候选股扫描数据，回测多周期持有收益率 + 止损机制验证
- 分条件统计胜率（条件1-10各自胜率）
- 多周期回测（1日/2日/3日/5日/10日持有）
- 止损机制验证（硬止损-3%、移动止盈）
- TOP10组合胜率验证
- 分走势统计
数据来源: 腾讯K线（收盘买入，N日收盘卖出或触发止损）
"""

import json, re, time, requests
from collections import Counter, defaultdict
import sys
sys.path.insert(0, '.')

# 加载候选数据（优先v9.5，否则v9/v8）
candidates = []
scan_data = None
for fname in ['candidates_v9.json', 'candidates_v8.json']:
    try:
        with open(fname) as f:
            scan_data = json.load(f)
            candidates = scan_data['candidates']
            print(f"加载回测数据: {fname}")
            break
    except: pass

if not candidates:
    print("错误: 未找到候选数据文件(candidates_v9.json或candidates_v8.json)")
    sys.exit(1)

market_trend = scan_data.get('market_trend', 'range')
print(f"回测样本: {len(candidates)}只候选股")
print(f"扫描日期: {scan_data.get('scan_time', 'unknown')}")
print(f"市场趋势: {market_trend}")
print("="*80)

# 获取K线数据（使用腾讯API，支持前复权）
def get_kline_for_backtest(code, datalen=60):
    prefix = 'sh' if code.startswith('6') else 'sz'
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{datalen},qfq'
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
        stock_data = data.get('data', {}).get(f'{prefix}{code}', {})
        klines = stock_data.get('day', []) or stock_data.get('qfqday', [])
        result = []
        for d in klines:
            result.append({
                'day': d[0],
                'open': float(d[1]),
                'close': float(d[2]),
                'high': float(d[3]),
                'low': float(d[4]),
                'volume': float(d[5]),
            })
        return result
    except: pass
    return None

# 查找买入日后的第N个交易日
def find_nth_trading_day(kline, buy_day, n):
    """找到买入日后第N个交易日的索引"""
    buy_idx = None
    for i, k in enumerate(kline):
        if k['day'] == buy_day:
            buy_idx = i
            break
    if buy_idx is None:
        return None
    target_idx = buy_idx + n
    if target_idx < len(kline):
        return target_idx
    return None

# ====== 回测配置 ======
HOLD_PERIODS = [1, 2, 3, 5, 10]  # 多周期回测
STOP_LOSS_PCT = -3.0              # 硬止损 -3%
TRAILING_PROFIT_PCT = 5.0         # 移动止盈触发线 +5%
TRAILING_DRAWBACK_PCT = -2.0      # 移动止盈回撤 -2%

results = []
success = 0
fail = 0

print(f"\n📊 回测配置: 止损{STOP_LOSS_PCT}% | 移动止盈+{TRAILING_PROFIT_PCT}%后回撤{TRAILING_DRAWBACK_PCT}%")
print(f"📊 回测周期: {HOLD_PERIODS}个交易日")
print("="*80)

for i, cand in enumerate(candidates):
    code = cand['code']
    buy_price = cand.get('close', 0)
    buy_day = cand.get('scan_day', scan_data.get('scan_time', '')[:10])
    # 如果没有明确扫描日，用数据中的日期推断
    if not buy_day or len(buy_day) < 10:
        buy_day = '2026-06-30'  # 默认回测基准日

    kline = get_kline_for_backtest(code, 60)
    if not kline or len(kline) < 5:
        fail += 1
        continue

    # 找到买入日
    buy_idx = None
    for idx, k in enumerate(kline):
        if k['day'] == buy_day:
            buy_idx = idx
            break

    # 如果找不到精确日期，找最近的交易日
    if buy_idx is None:
        # 尝试找前后5天内最接近的
        for offset in range(1, 6):
            for idx, k in enumerate(kline):
                if k['day'] == buy_day.replace(buy_day[-2:], str(int(buy_day[-2:]) + offset).zfill(2)):
                    buy_idx = idx
                    break
            if buy_idx is not None: break

    if buy_idx is None:
        fail += 1
        continue

    # 更新买入价为K线数据（更准确）
    buy_price = kline[buy_idx]['close']
    buy_high = kline[buy_idx]['high']
    buy_low = kline[buy_idx]['low']

    # 计算各周期收益 + 止损
    period_results = {}
    for period in HOLD_PERIODS:
        sell_idx = buy_idx + period
        if sell_idx >= len(kline):
            continue

        # 获取持有期间K线
        hold_klines = kline[buy_idx+1:sell_idx+1]  # 买入后第1天到卖出日
        if not hold_klines:
            continue

        # 止损检查
        stopped = False
        stop_day = None
        stop_price = None
        stop_reason = None
        max_price_during_hold = buy_price

        for hk in hold_klines:
            current_low = hk['low']
            current_high = hk['high']
            current_close = hk['close']

            # 更新持有期最高价
            if current_high > max_price_during_hold:
                max_price_during_hold = current_high

            # 硬止损检查
            current_dd = (current_low - buy_price) / buy_price * 100
            if current_dd <= STOP_LOSS_PCT and not stopped:
                stopped = True
                stop_day = hk['day']
                # 假设止损在触发价位成交
                stop_price = buy_price * (1 + STOP_LOSS_PCT / 100)
                stop_reason = f"硬止损{STOP_LOSS_PCT}%"
                break

            # 移动止盈检查（仅在达到止盈线后）
            max_gain = (max_price_during_hold - buy_price) / buy_price * 100
            if max_gain >= TRAILING_PROFIT_PCT:
                # 从最高点回撤超过阈值
                current_drawback = (current_low - max_price_during_hold) / max_price_during_hold * 100
                if current_drawback <= TRAILING_DRAWBACK_PCT and not stopped:
                    stopped = True
                    stop_day = hk['day']
                    stop_price = current_close  # 用收盘价近似
                    stop_reason = f"移动止盈(最高+{max_gain:.1f}%后回撤{current_drawback:.1f}%)"
                    break

        if stopped:
            ret = (stop_price - buy_price) / buy_price * 100
            sell_price = stop_price
            sell_day = stop_day
        else:
            sell_price = hold_klines[-1]['close']
            sell_day = hold_klines[-1]['day']
            ret = (sell_price - buy_price) / buy_price * 100

        # 持有期最大收益和最大回撤
        max_high = max(hk['high'] for hk in hold_klines)
        min_low = min(hk['low'] for hk in hold_klines)
        max_gain = (max_high - buy_price) / buy_price * 100
        max_dd = (min_low - buy_price) / buy_price * 100

        period_results[period] = {
            'ret': ret,
            'win': ret > 0,
            'sell_price': sell_price,
            'sell_day': sell_day,
            'stopped': stopped,
            'stop_reason': stop_reason,
            'max_gain': max_gain,
            'max_dd': max_dd,
            'hold_days': (sell_idx - buy_idx) if not stopped else len(hold_klines[:[hk['day'] for hk in hold_klines].index(sell_day)+1]) if stop_day else period,
        }

    if not period_results:
        fail += 1
        continue

    success += 1
    results.append({
        'code': code,
        'name': cand.get('name', ''),
        'conditions': cand.get('conditions', []),
        'news_sectors': cand.get('news_sectors', []),
        'score': cand.get('score', 0),
        'prob': cand.get('prob', 0),
        'buy_price': buy_price,
        'buy_day': buy_day,
        'period_results': period_results,
    })

    if (i+1) % 100 == 0:
        print(f"  进度: {i+1}/{len(candidates)} 成功:{success} 失败:{fail}")

print(f"\n回测数据获取完成: 成功{success}只, 失败{fail}只")

# ====== 分周期统计胜率 ======
print("\n" + "="*80)
print("📊 各持有周期胜率统计（含止损机制）")
print("="*80)

for period in HOLD_PERIODS:
    valid = [r for r in results if period in r['period_results']]
    if not valid: continue

    wins = [r for r in valid if r['period_results'][period]['win']]
    stopped_count = sum(1 for r in valid if r['period_results'][period]['stopped'])
    avg_ret = sum(r['period_results'][period]['ret'] for r in valid) / len(valid)
    avg_max_gain = sum(r['period_results'][period]['max_gain'] for r in valid) / len(valid)
    avg_max_dd = sum(r['period_results'][period]['max_dd'] for r in valid) / len(valid)

    print(f"\n{'='*60}")
    print(f"持有{period}日: {len(valid)}只 | 胜{len(wins)} | 胜率{len(wins)/len(valid)*100:.1f}% | 均收益{avg_ret:+.2f}%")
    print(f"  止损触发: {stopped_count}只({stopped_count/len(valid)*100:.1f}%) | 均最大涨{avg_max_gain:+.2f}% | 均最大跌{avg_max_dd:+.2f}%")

    # 分条件统计
    cond_labels = {
        1:'10日线强势', 2:'缩量回调', 3:'趋势上行', 4:'强势型',
        5:'大单净买', 6:'涨停回踩', 7:'均线多头排列', 8:'突破回踩',
        9:'MACD金叉', 10:'强势回踩金叉'
    }

    print(f"\n  {'条件':<18} {'命中':>5} {'胜':>4} {'负':>4} {'胜率':>7} {'均收益':>8} {'止损数':>5}")
    print(f"  {'-'*55}")
    for cond in range(1, 11):
        cond_results = [r for r in valid if cond in r['conditions']]
        if not cond_results: continue
        cw = sum(1 for r in cond_results if r['period_results'][period]['win'])
        cstopped = sum(1 for r in cond_results if r['period_results'][period]['stopped'])
        cret = sum(r['period_results'][period]['ret'] for r in cond_results) / len(cond_results)
        label = cond_labels.get(cond, f'条件{cond}')
        print(f"  {label:<16} {len(cond_results):>5} {cw:>4} {len(cond_results)-cw:>4} {cw/len(cond_results)*100:>6.1f}% {cret:>+7.2f}% {cstopped:>5}")

    # 多条件组合
    print(f"\n  多条件组合统计:")
    combo_stats = defaultdict(list)
    for r in valid:
        n = len(r['conditions'])
        if n == 0:
            combo_stats['纯消息面'].append(r)
        elif n == 1:
            combo_stats['单条件'].append(r)
        elif n == 2:
            combo_stats['双条件'].append(r)
        elif n == 3:
            combo_stats['三条件'].append(r)
        else:
            combo_stats['四条件+'].append(r)

    for combo_name, crs in combo_stats.items():
        if not crs: continue
        cw = sum(1 for r in crs if r['period_results'][period]['win'])
        cret = sum(r['period_results'][period]['ret'] for r in crs) / len(crs)
        cstopped = sum(1 for r in crs if r['period_results'][period]['stopped'])
        print(f"    {combo_name}: {len(crs)}只, 胜{cw}, 胜率{cw/len(crs)*100:.1f}%, 均收益{cret:+.2f}%, 止损{cstopped}")

    # TOP10胜率验证
    print(f"\n  TOP10组合验证(按评分排序前10):")
    top10 = sorted(valid, key=lambda x: -x['score'])[:10]
    if top10:
        tw = sum(1 for r in top10 if r['period_results'][period]['win'])
        tret = sum(r['period_results'][period]['ret'] for r in top10) / len(top10)
        tstopped = sum(1 for r in top10 if r['period_results'][period]['stopped'])
        print(f"    TOP10: 胜{tw}/10, 胜率{tw/len(top10)*100:.1f}%, 均收益{tret:+.2f}%, 止损{tstopped}")
        for r in sorted(top10, key=lambda x: -x['period_results'][period]['ret']):
            pr = r['period_results'][period]
            ws = "✅" if pr['win'] else "❌"
            sr = f"[止损:{pr['stop_reason']}]" if pr['stopped'] else ""
            print(f"      {ws} {r['code']} {r['name']:<8} {pr['ret']:+.2f}% {sr}")

# ====== 最优持有周期分析 ======
print("\n" + "="*80)
print("📊 最优持有周期分析（按条件）")
print("="*80)

for cond in [9, 4, 10, 6, 8, 7, 3, 5, 2, 1]:
    cond_results = [r for r in results if cond in r['conditions']]
    if len(cond_results) < 5: continue
    label = cond_labels.get(cond, f'条件{cond}')

    best_period = None
    best_winrate = 0
    best_ret = -999

    print(f"\n{label} ({len(cond_results)}只):")
    for period in HOLD_PERIODS:
        valid = [r for r in cond_results if period in r['period_results']]
        if not valid: continue
        wins = [r for r in valid if r['period_results'][period]['win']]
        wr = len(wins) / len(valid) * 100
        ar = sum(r['period_results'][period]['ret'] for r in valid) / len(valid)
        print(f"  持有{period:2d}日: 胜率{wr:5.1f}% | 均收益{ar:+.2f}% | {len(valid)}只")
        if wr > best_winrate or (wr == best_winrate and ar > best_ret):
            best_winrate = wr
            best_ret = ar
            best_period = period

    if best_period:
        print(f"  ⭐ 最优周期: 持有{best_period}日 (胜率{best_winrate:.1f}%, 均收益{best_ret:+.2f}%)")

# ====== 止损机制效果分析 ======
print("\n" + "="*80)
print("📊 止损机制效果分析")
print("="*80)

for period in [3, 5, 10]:
    valid = [r for r in results if period in r['period_results']]
    if not valid: continue

    # 无止损情况（持有到期）
    no_stop_rets = []
    for r in valid:
        # 模拟无止损：用最后一天的收盘价
        buy_idx = None
        for i, k in enumerate(get_kline_for_backtest(r['code'], 60) or []):
            if k['day'] == r['buy_day']:
                buy_idx = i
                break
        if buy_idx is not None:
            kline = get_kline_for_backtest(r['code'], 60)
            if kline and buy_idx + period < len(kline):
                ret_no_stop = (kline[buy_idx + period]['close'] - r['buy_price']) / r['buy_price'] * 100
                no_stop_rets.append(ret_no_stop)

    # 有止损情况
    stop_rets = [r['period_results'][period]['ret'] for r in valid]

    if no_stop_rets and stop_rets:
        avg_no_stop = sum(no_stop_rets) / len(no_stop_rets)
        avg_stop = sum(stop_rets) / len(stop_rets)
        wins_no_stop = sum(1 for x in no_stop_rets if x > 0)
        wins_stop = sum(1 for x in stop_rets if x > 0)
        print(f"\n持有{period}日:")
        print(f"  无止损: 胜率{wins_no_stop/len(no_stop_rets)*100:.1f}%, 均收益{avg_no_stop:+.2f}%")
        print(f"  有止损: 胜率{wins_stop/len(stop_rets)*100:.1f}%, 均收益{avg_stop:+.2f}%")
        print(f"  止损改善: 均收益{avg_stop - avg_no_stop:+.2f}%")

# 保存回测结果
backtest_output = {
    'backtest_version': 'v9.5',
    'backtest_date': '2026-07-06',
    'market_trend': market_trend,
    'hold_periods': HOLD_PERIODS,
    'stop_loss': STOP_LOSS_PCT,
    'trailing_profit': TRAILING_PROFIT_PCT,
    'trailing_drawback': TRAILING_DRAWBACK_PCT,
    'total_samples': len(results),
    'period_stats': {},
}

for period in HOLD_PERIODS:
    valid = [r for r in results if period in r['period_results']]
    if not valid: continue
    wins = sum(1 for r in valid if r['period_results'][period]['win'])
    stopped = sum(1 for r in valid if r['period_results'][period]['stopped'])
    backtest_output['period_stats'][period] = {
        'count': len(valid),
        'win': wins,
        'lose': len(valid) - wins,
        'win_rate': wins / len(valid) * 100,
        'avg_ret': sum(r['period_results'][period]['ret'] for r in valid) / len(valid),
        'stopped': stopped,
    }

import os
os.makedirs('/root/.codebuddy/artifact/stock-scan', exist_ok=True)
with open('/root/.codebuddy/artifact/stock-scan/backtest_v95.json', 'w') as f:
    json.dump(backtest_output, f, ensure_ascii=False, indent=2)

print(f"\n回测结果已保存: /root/.codebuddy/artifact/stock-scan/backtest_v95.json")
