#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段2: 交易日早上 9:27 集合竞价结束后运行
=========================================
# 读取昨晚阶段1选出的 TOP20 (/workspace/stock-v1224/system/night20_latest.json),
用当日集合竞价开盘价(open_pct)重排, 取前5, 输出买入建议与持股1天冲高卖计划.

筛选规则(对20只):
  1. 剔除当日竞价 open_pct >= 9.5 (一字涨停买不进)
  2. 综合分 = 基础v1224分 + 竞价信号分(低开/平开高配, 大幅高开降权)
  3. 取前5名, 买入价=当日开盘, 持股1天 -> 次日冲高卖
"""
import json, sys, os, glob, time
from datetime import datetime, timezone, timedelta

_SYSTEM = '/workspace/stock-v1224/system'
NIGHT_POOL = os.path.join(_SYSTEM, 'night20_latest.json')
FINAL5 = 5
OUT = '/workspace/edge/auction_top5.json'

beijing = datetime.now(timezone(timedelta(hours=8)))
TODAY = beijing.strftime('%Y-%m-%d')

sys.path.insert(0, _SYSTEM)
import scan_v1224 as S

def auction_bonus(open_pct):
    if open_pct <= 0: return 15
    if open_pct <= 1: return 10
    if open_pct <= 2.5: return 5
    if open_pct <= 4: return 0
    if open_pct <= 7: return -8
    return -15

# ===== 止盈止损规则 (来自最近15个交易日回测优化) =====
# 回测要点: 低止盈(<=3%)封顶涨赢点反而压低收益; 紧止损(<=2%)只增波动;
# 推荐 止盈>=8(冲高落袋) + 止损5%, 且两种触发顺序口径一致、稳健略优.
SELL_RULE = {
    'target_pct': 10.0,   # 止盈: 冲高至+10%(或更高)落袋, 不在+5%以内过早截断盈利
    'min_hold_pct': 5.0,  # +5%前不手动止盈, 让盈利奔跑
    'stop_pct': 5.0,      # 止损: 盘中/盘尾跌破-5%即走, 不死扛
    'trail_note': '冲高到+5%后把止损上移至成本价, 再涨再上移, 吃"区间最高止盈"那部分超额; 用价格触发而非时间触发离场'
}

def sell_plan(c):
    tp = c['open_px'] * (1 + SELL_RULE['target_pct'] / 100)
    sl = c['open_px'] * (1 - SELL_RULE['stop_pct'] / 100)
    return {'buy_price': round(c['open_px'], 3),
            'take_profit_pct': SELL_RULE['target_pct'],
            'take_profit': round(tp, 3),
            'stop_loss_pct': SELL_RULE['stop_pct'],
            'stop_loss': round(sl, 3)}

def is_trading_day(today):
    """校验今天是否A股交易日: 沪指最新一根K线日期是否=今天"""
    try:
        ik = S.get_index_kl(30)
        if not ik or not ik['d']:
            return False
        # 沪指最新一根若含今天 或 今天在交易日序列中 即视为交易日(盘中已开盘)
        return any(d.startswith(today) for d in ik['d'][-6:])
    except Exception:
        return False

def main():
    print(f"[阶段2] 竞价筛股  {beijing.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    sys.path.insert(0, _SYSTEM)
    import scan_v1224 as S
    # 交易日校验: 非交易日(周末/节假日)不运行
    if not is_trading_day(TODAY):
        print(f"今天{TODAY}非A股交易日, 跳过. (沪指无今日K线)")
        with open('/workspace/edge/auction_top5_skip.txt', 'a') as f:
            f.write(f"{beijing.strftime('%Y-%m-%d %H:%M:%S')} 非交易日跳过\n")
        return 0
    print("今日为交易日, 继续执行")
    if not os.path.exists(NIGHT_POOL):
        print(f"!! 找不到昨晚选股池 {NIGHT_POOL}, 终止")
        return 1
    pool = json.load(open(NIGHT_POOL, encoding='utf-8'))
    codes = [t['code'] for t in pool['top']]
    print(f"昨晚TOP20: {len(codes)}只")

    S.load_sector_mapping()
    # 拉取这20只当日实时行情(竞价开盘)
    rt = S.get_realtime_quote(codes)
    cands = []
    have = {}
    for t in pool['top']:
        code = t['code']
        q = rt.get(code, {})
        have[code] = (q.get('open_pct'), q.get('open_price'))
        op = q.get('open_pct')
        if op is None:
            continue
        if op >= 9.5:
            continue
        base = t.get('v1224_score', 0)
        cands.append({'code': code, 'name': t['name'], 'base_score': base,
                      'open_pct': round(op, 2), 'open_px': q.get('open_price') or 0,
                      'comp': base + auction_bonus(op),
                      'sel_mode': t.get('sel_mode', '')})
    cands.sort(key=lambda x: -x['comp'])
    top5 = cands[:FINAL5]

    print(f"\n===== {TODAY} 竞价筛出的 TOP{FINAL5} =====")
    for i, c in enumerate(top5, 1):
        pl = sell_plan(c)
        print(f"{i}. {c['name']} {c['code']} 开盘{c['open_px']:.3f} "
              f"竞价{c['open_pct']:+.2f}% 基础分{c['base_score']:.1f} 综合分{c['comp']:.1f} | "
              f"止盈+{pl['take_profit_pct']:.0f}%={pl['take_profit']:.3f} 止损-{pl['stop_loss_pct']:.0f}%={pl['stop_loss']:.3f}", flush=True)
    print(f"\n卖出纪律: {SELL_RULE['trail_note']}", flush=True)

    name_of = {t['code']: t['name'] for t in pool['top']}
    for c in top5:
        c['sell'] = sell_plan(c)
    result = {'run_time': beijing.strftime('%Y-%m-%d %H:%M:%S'), 'date': TODAY,
              'method': '昨晚TOP20 + 当日竞价重排取前5, 当日开盘买入, 持股1天冲高卖',
              'sell_rule': SELL_RULE,
              'top5': top5,
              'all_20_auction': [{'code': c, 'name': name_of.get(c, ''), 'open_pct': v}
                                 for c, v in have.items()]}
    os.makedirs('/workspace/edge', exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n已保存 {OUT}")

if __name__ == '__main__':
    main()