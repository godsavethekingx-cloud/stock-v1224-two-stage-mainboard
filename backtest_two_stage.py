#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
两阶段选股回测: 当天晚选20 -> 次日竞价后筛5 -> 持股1天冲高卖
================================================================
阶段1(晚11点): 用 T-1 收盘技术面 + T-1 当日 open_pct 全盘选 TOP20 (晚间模式)
阶段2(次日9:27竞价后): 对阶段1的20只, 用 T 日真实竞价开盘价重算 open_pct,
   在20只内按 [v1224基础分 + 竞价强弱修正] 重排序, 取前5名.
买入 = T 日开盘价;  持股1天 -> T+1 最高价卖出.

阶段2筛选规则(对20只):
  1. 剔除 T 日竞价 open_pct >= 9.5 (一字涨停买不进)
  2. 录用竞级分 = v1224_score + 竞价信号分(高开适中奖励/大幅高开惩罚, 与原策略 us_buy_signal 一致口径)
     - open_pct<=0: +15 (低开低吸优先)
     - 0<open_pct<=1: +10
     - 1<open_pct<=2.5: +5
     - 2.5<open_pct<=4: 0
     - 4<open_pct<=7: -8
     - open_pct>7: -15
  3. 按竞级分降序取前5
全部只用 <=T 的数据(买入日开盘价即时可得), 无前视.
"""
import json, sys, os, time
from concurrent.futures import ThreadPoolExecutor, as_completed

_SYSTEM = '/workspace/.uploads/stock_system/stock-v1224/system'
sys.path.insert(0, _SYSTEM)
import scan_v1224 as S

TOP20 = 20
FINAL5 = 5
BUY_DAYS = ['2026-09-14', '2026-09-15', '2026-09-16', '2026-09-17', '2026-09-18']

def find_idx(dates, target):
    for i, d in enumerate(dates):
        if d.startswith(target):
            return i
    return -1

def truncate_to(kl, upto_date):
    idx = find_idx(kl['d'], upto_date)
    if idx < 0:
        return None
    return {'d': kl['d'][:idx+1], 'c': kl['c'][:idx+1], 'o': kl['o'][:idx+1],
            'h': kl['h'][:idx+1], 'l': kl['l'][:idx+1], 'v': kl['v'][:idx+1]}

def build_history(all_full, upto_date, relevant_dates):
    rel = [d for d in relevant_dates if d <= upto_date]
    truncated = {}
    for code, kl in all_full.items():
        t = truncate_to(kl, upto_date)
        if t:
            truncated[code] = t
    return S.build_sector_zt_history(truncated, rel) if truncated else {}

def auction_bonus(open_pct):
    """竞价强弱信号分(与策略低频逻辑一致)"""
    if open_pct <= 0: return 15
    if open_pct <= 1: return 10
    if open_pct <= 2.5: return 5
    if open_pct <= 4: return 0
    if open_pct <= 7: return -8
    return -15

def stage1_night_top(base_date, hot_date, sector_zth, open_date, all_full, N=20):
    """晚间全盘选 N 只. base_date=T-1收盘, open_date=T-1竞价."""
    all_tech, trunc_kls = {}, {}
    for code, kl in all_full.items():
        t = truncate_to(kl, base_date)
        if not t or len(t['d']) < 7:
            continue
        tech = S.analyze_tech(t)
        if not tech:
            continue
        oi = find_idx(kl['d'], open_date)
        if oi <= 0:
            continue
        lc = kl['c'][oi-1]; op = kl['o'][oi]
        open_pct = (op/lc - 1)*100 if lc > 0 else 0
        if open_pct >= 9.5:
            continue
        all_tech[code] = {'tech': tech, 'name': kl['name'], 'open_pct': open_pct}
        trunc_kls[code] = t

    hot_sectors = S.detect_hot_sectors(all_tech)
    sector_accel = S.detect_sector_acceleration(hot_date, sector_zth)
    daily_scores = []
    for code, data in all_tech.items():
        sc = S.score_v1212(data['tech'], hot_sectors, data['name'], code, data['open_pct'])
        sc['v1212_score'] = sc['score']; daily_scores.append(sc)
    daily_scores.sort(key=lambda x: -x['score'])
    pool = {r['code']: r for r in daily_scores[:20]}
    local_bull = S.detect_local_bull(pool)

    scored_all = []
    for code, data in all_tech.items():
        tech, name, open_pct = data['tech'], data['name'], data['open_pct']
        scored = S.score_v1212(tech, hot_sectors, name, code, open_pct)
        scored['v1212_score'] = scored['score']
        trunc = trunc_kls[code]
        us, usg, c1, c2, buyable = S.score_user_mode(scored, trunc, local_bull, sector_accel, 'BULL')
        scored['v1224_score'] = us; scored['signals'] = usg
        scored['cond1_met'], scored['cond2_met'], scored['is_buyable'] = c1, c2, buyable
        scored['is_defensive'] = S.is_defensive(name, code)
        scored_all.append(scored)

    buyable = [x for x in scored_all if x.get('is_buyable', True)]
    buyable.sort(key=lambda x: -x['v1224_score'])
    both = [x for x in buyable if x.get('cond1_met') and x.get('cond2_met')]
    oc1 = [x for x in buyable if x.get('cond1_met') and not x.get('cond2_met')]
    oc2 = [x for x in buyable if x.get('cond2_met') and not x.get('cond1_met')]
    topn, sel = [], set()
    for x in both:
        if len(topn) >= N: break
        if x['code'] not in sel: x['sel_mode']='DUAL_COND'; topn.append(x); sel.add(x['code'])
    for x in oc1:
        if len(topn) >= N: break
        if x['code'] not in sel: x['sel_mode']='COND1'; topn.append(x); sel.add(x['code'])
    for x in oc2:
        if len(topn) >= N: break
        if x['code'] not in sel: x['sel_mode']='COND2'; topn.append(x); sel.add(x['code'])
    for x in buyable:
        if len(topn) >= N: break
        if x['code'] not in sel: x['sel_mode']='BULL'; topn.append(x); sel.add(x['code'])
    topn.sort(key=lambda x: -x['v1224_score'])
    return topn

def stage2_auction_top5(night_top, buy_date, all_full):
    """对夜间TOP20, 用T日竞价open_pct重排取5只"""
    cands = []
    for r in night_top:
        kl = all_full[r['code']]
        di = find_idx(kl['d'], buy_date)
        if di <= 0:
            continue
        lc = kl['c'][di-1]; op = kl['o'][di]
        open_pct = (op/lc - 1)*100 if lc > 0 else 0
        if open_pct >= 9.5:
            continue   # T日一字涨停买不进
        # 基础分(阶段1的v1224_score基于T-1)；这里叠加T日竞价信号
        base = r.get('v1224_score', 0)
        comp = base + auction_bonus(open_pct)
        cands.append({'code': r['code'], 'name': r['name'], 'base': base,
                      'open_pct': round(open_pct,2), 'comp': comp,
                      'sel_mode': r['sel_mode']})
    cands.sort(key=lambda x: -x['comp'])
    return cands[:FINAL5]

def main():
    bj = S.datetime.now(S.BEIJING_TZ)
    print(f"两阶段回测(晚选20->竞价筛5->持股1天) {bj.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    S.load_sector_mapping()
    stocks = S.get_all_stocks()
    filtered = [s for s in stocks if 2 <= s['price'] <= 80 and s['circ_mv'] <= 500]
    print(f"全量{len(stocks)} -> 过滤后{len(filtered)}", flush=True)

    all_full = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=S._MAX_WORKERS) as ex:
        futs = {ex.submit(S.get_kl, s['code'], 120): s for s in filtered}
        for i, fut in enumerate(as_completed(futs), 1):
            s = futs[fut]
            try:
                kl = fut.result()
                if kl:
                    kl['name'] = s['name']; all_full[s['code']] = kl
            except: pass
            if i % 500 == 0 or i == len(filtered):
                print(f"  K线 {i}/{len(filtered)} 成功{len(all_full)} 用时{time.time()-t0:.0f}s", flush=True)
    print(f"K线成功{len(all_full)}只 用时{time.time()-t0:.0f}s", flush=True)

    index_kl = S.get_index_kl(80)
    trade_dates = index_kl['d'] if index_kl else []
    relevant_dates = [d for d in trade_dates if d >= '2026-08-01']
    def prev_trading(d):
        i = find_idx(trade_dates, d)
        return trade_dates[i-1] if i > 0 else None

    agg = []
    detail = {}
    day_summ = []

    for D in BUY_DAYS:
        PD = prev_trading(D)
        if not PD:
            print(f"跳过{D}"); continue
        print(f"\n=== 买入日 {D} (夜间基准 {PD}) ===", flush=True)
        hist = build_history(all_full, PD, relevant_dates)
        night_top = stage1_night_top(PD, PD, hist, PD, all_full, TOP20)
        fin5 = stage2_auction_top5(night_top, D, all_full)

        res = []
        for it in fin5:
            kl = all_full[it['code']]; di = find_idx(kl['d'], D)
            if di < 0 or di+1 >= len(kl['h']):
                continue
            buy = kl['o'][di]; ret = (kl['h'][di+1]/buy - 1)*100
            res.append({'code':it['code'],'name':it['name'],'base':it['base'],
                        'open_pct':it['open_pct'],'comp':it['comp'],'mode':it['sel_mode'],
                        'buy':round(buy,2),'ret':round(ret,2)})
            agg.append(ret)
        detail[D] = res
        rs = [x['ret'] for x in res]
        av = sum(rs)/len(rs) if rs else 0; wn = sum(1 for x in rs if x>0)/len(rs)*100 if rs else 0
        day_summ.append({'date':D,'n':len(res),'avg':round(av,2),'win':round(wn,1)})
        print(f"  选5: {', '.join(x['name'] for x in res)}", flush=True)
        print(f"  均{av:+.2f}% 胜{wn:.0f}%", flush=True)

    print("\n" + "="*100)
    print(f"{'买入日':<12}{'只数':>5}{'均涨':>9}{'胜率':>9}")
    for d in day_summ:
        print(f"{d['date']:<12}{d['n']:>5}{d['avg']:>+8.2f}%{d['win']:>8.1f}%")
    n=len(agg)
    avg=sum(agg)/n if n else 0; med=sorted(agg)[n//2] if n else 0
    win=sum(1 for x in agg if x>0)/n*100 if n else 0
    print("-"*100)
    print(f"合计: n={n} 均{avg:+.2f}% 中位{med:+.2f}% 胜率{win:.1f}% 最佳{max(agg):.2f}% 最差{min(agg):.2f}%")
    out={'buy_days':BUY_DAYS,'stat':{'n':n,'avg':round(avg,2),'med':round(med,2),'win':round(win,1),
                                     'max':round(max(agg),2) if agg else 0,'min':round(min(agg),2) if agg else 0},
         'daily':day_summ,'details':detail}
    with open('/workspace/twostage_backtest.json','w',encoding='utf-8') as f:
        json.dump(out,f,ensure_ascii=False,indent=2,default=str)
    print("已保存 /workspace/twostage_backtest.json")

if __name__ == '__main__':
    main()