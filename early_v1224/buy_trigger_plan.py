#!/usr/bin/env python3
"""
V10.5 🆕 买入触发计划 (Buy Trigger Plan) —— "只在盘后选股 + 盘中捕捉买入信号" 的解耦方案

核心思想: 盘中启动信号不是靠盘中盯着, 而是:
  1. 盘后选股时, 为每一只候选算出 "次日盘中买入触发条件" (触发价位 + 量能 + 逻辑),
     你次日挂条件单即可自动执行, 无需盘中盯盘。
  2. 盘中通过低频定时任务只对"昨日选池"扫描是否触发, 有信号才推送。

本文件提供两部分:
  A. build_trigger_plan(code, name, kl, source)  -> 单只票的次日触发线
  B. scan_trigger(plan_items, rt_data)           -> 盘中扫描, 判定是否已触发/取消
"""


def _ma(closes, idx, n):
    s = closes[max(0, idx - n + 1): idx + 1]
    return sum(s) / len(s) if s else 0.0


def _r6(closes, idx):
    gains = losses = 0.0
    for j in range(max(1, idx - 5), idx + 1):
        d = closes[j] - closes[j - 1]
        if d > 0: gains += d
        else: losses -= d
    if losses == 0: return 100.0
    return 100 - 100 / (1 + (gains / 6) / (losses / 6))


def build_trigger_plan(code, name, kl, source, next_low=None):
    """
    为一只候选股生成 "次日盘中买入触发计划"。

    参数:
      source: 来源标签(超跌反弹/主力建仓/回调到位/通道A/连板接力等)
      next_low: 次日最低价(可选)。若提供, 用于判定"次日是否真的出现过触发/未触发",
                便于盘后复盘校验触发线命中率。

    返回触发计划 dict:
      trigger_price: 触发价(现价上方某关键位, 突破=启动)
      stop_price:    止损价
      vol_confirm:   需配合的量能确认(量比阈值)
      logic:         触发逻辑描述
    """
    c = kl['c']; v = kl['v']; o = kl['o']; h = kl['h']; l = kl['l']
    n = len(c); ti = n - 1
    if ti < 30:
        return None
    last = c[ti]
    ma5 = _ma(c, ti, 5)
    ma10 = _ma(c, ti, 10)
    ma20 = _ma(c, ti, 20)
    # 昨日量能基准
    vol20 = sum(v[max(0, ti - 20): ti]) / min(20, ti)
    vol_ratio = v[ti] / vol20 if vol20 > 0 else 1.0
    rsi6 = _r6(c, ti)

    # 触发价: 现价上方最近关键位 —— 突破涨停价/前高/MA等
    # 简化: 用"前一日高点"作为启动确认位(突破昨日高点=资金启动), 或现价+3%保护
    prev_high = h[ti - 1] if ti >= 1 else last
    # 若现价已贴近昨日高点, 则用 MA5/MA10 上方作为触发
    trigger_price = max(prev_high, last * 1.02)
    trigger_price = round(trigger_price, 2)

    # 止损: 触发价下方一定幅度, 或关键支撑
    stop_price = round(min(ma10, last * 0.95), 2) if ma10 > 0 else round(last * 0.95, 2)
    if stop_price >= trigger_price:
        stop_price = round(trigger_price * 0.94, 2)

    # 量能确认: 触发当日量比需达阈值
    vol_confirm = round(max(1.2, vol_ratio * 1.3), 2)

    # 逻辑描述
    logic = f"放量站上 {trigger_price} 且量比>={vol_confirm} 视为启动; 失效则回踩 {stop_price} 不破可低吸"

    plan = {
        'code': code, 'name': name, 'source': source,
        'last_close': round(last, 2),
        'trigger_price': trigger_price,
        'stop_price': stop_price,
        'vol_confirm': vol_confirm,
        'rsi6': round(rsi6, 1),
        'dev_ma20': round((last / ma20 - 1) * 100, 1) if ma20 > 0 else 0,
        'logic': logic,
    }
    if next_low is not None:
        # 次日触发校验: 若次日最低价 <= 现有触发价(可成交)且当日确实放量, 标记可触发
        plan['next_low'] = round(next_low, 2)
        plan['triggerable_next'] = next_low <= trigger_price * 1.001
    return plan


def build_plan_for_pool(codes_meta, all_kls, source):
    """
    为一批候选(如超跌反弹池/主力建仓池)批量生成触发计划。
    codes_meta: [{code, name}] 或 [{code, name, ...}]
    """
    plans = []
    for meta in codes_meta:
        code = meta['code']; name = meta.get('name', code)
        kl = all_kls.get(code)
        if not kl:
            continue
        p = build_trigger_plan(code, name, kl, source)
        if p:
            plans.append(p)
    return plans


def scan_trigger(plans, rt_data):
    """
    盘中扫描: 给定触发计划和当前实时行情, 判定每只是否已触发买点 / 仍在区间 / 已跌破止损。

    返回: [{plan, status, signal, now_price, vol_now}]
      status: 'TRIGGERED'(已触发可买) / 'ACTIVE'(接近触发,关注) / 'BROKEN'(跌破止损,放弃) / 'PENDING'(未到)
    """
    from scan_v1224 import get_realtime_quote
    if not plans:
        return []
    codes = [p['code'] for p in plans]
    need = {c: p for c, p in zip(codes, plans) if c not in rt_data}
    if need:
        rt_data.update(get_realtime_quote(list(need.keys())))
    out = []
    for p in plans:
        q = rt_data.get(p['code'])
        if not q or q['last_close'] <= 0:
            out.append({'plan': p, 'status': 'PENDING', 'signal': '无实时数据', 'now_price': 0})
            continue
        now = q['current']
        change = (now / q['last_close'] - 1) * 100
        trig = p['trigger_price']; stop = p['stop_price']
        # 量能近实时较难直接得, 用价先行
        if stop > 0 and now < stop * 1.001:
            status = 'BROKEN'
            signal = f'跌破止损 {stop},放弃'
        elif now >= trig * 0.995:
            status = 'TRIGGERED'
            signal = f'放量站上 {trig},可买入(止损 {stop})'
        elif now >= trig * 0.97:
            status = 'ACTIVE'
            signal = f'逼近触发区(现价 {now},触发 {trig})'
        else:
            status = 'PENDING'
            signal = f'未到触发位(现价 {now},触发 {trig})'
        out.append({
            'plan': p, 'status': status, 'signal': signal,
            'now_price': round(now, 2), 'change_pct': round(change, 2),
        })
    return out