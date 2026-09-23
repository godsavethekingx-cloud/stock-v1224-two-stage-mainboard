#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""两阶段口径对比回测: 口径A(BULL用户模式) vs 口径B(RANGE_BULL原全盘扫描口径).
适用: 晚间全量选TOP20 -> 次日竞价筛TOP5 -> 持股1天冲高卖. 仅stage1选股口径不同, stage2/持仓同化.
BUY_DAYS 动态取最近N个可回测交易日(需次日最高价存在).
用法: python3 backtest_compare_mode.py [N=5|15]
"""
import json, sys, os, time
from concurrent.futures import ThreadPoolExecutor, as_completed

_SYSTEM = '/workspace/stock-v1224/system'
sys.path.insert(0, _SYSTEM)
import scan_v1224 as S

TOP20 = 20
FINAL5 = 5
BUY_DAYS = None
N_WIN = int(sys.argv[1]) if len(sys.argv) > 1 else 15

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
    truncated = {c: truncate_to(kl, upto_date) for c, kl in all_full.items()}
    truncated = {c: t for c, t in truncated.items() if t}
    return S.build_sector_zt_history(truncated, rel) if truncated else {}

def commons(base_date, sector_zth, all_full):
    all_tech, trunc_kls = {}, {}
    for code, kl in all_full.items():
        t = truncate_to(kl, base_date)
        if not t or len(t['d']) < 7:
            continue
        tech = S.analyze_tech(t)
        if not tech:
            continue
        oi = find_idx(kl['d'], base_date)
        if oi <= 0:
            continue
        lc = kl['c'][oi-1]; op = kl['o'][oi]
        open_pct = (op/lc - 1)*100 if lc > 0 else 0
        if open_pct >= 9.5:
            continue
        all_tech[code] = {'tech': tech, 'name': kl['name'], 'open_pct': open_pct}
        trunc_kls[code] = t
    hot_sectors = S.detect_hot_sectors(all_tech)
    sector_accel = S.detect_sector_acceleration(base_date, sector_zth)
    daily = []
    for code, data in all_tech.items():
        sc = S.score_v1212(data['tech'], hot_sectors, data['name'], code, data['open_pct'])
        daily.append(sc)
    daily.sort(key=lambda x: -x['score'])
    pool = {r['code']: r for r in daily[:20]}
    local_bull = S.detect_local_bull(pool)
    return all_tech, trunc_kls, hot_sectors, sector_accel, local_bull

def stage1_mode_A(all_tech, trunc_kls, hot_sectors, sector_accel, local_bull, N=20):
    scored_all = []
    for code, data in all_tech.items():
        tech, name, open_pct = data['tech'], data['name'], data['open_pct']
        scored = S.score_v1212(tech, hot_sectors, name, code, open_pct)
        scored['v1212_score'] = scored['score']
        us, usg, c1, c2, buyable = S.score_user_mode(scored, trunc_kls[code], local_bull, sector_accel, 'BULL')
        scored['v1224_score'] = us; scored['signals'] = usg
        scored['cond1_met'], scored['cond2_met'], scored['is_buyable'] = c1, c2, buyable
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

def stage1_mode_B(all_tech, trunc_kls, hot_sectors, sector_accel, local_bull, N=20):
    scored_all = []
    for code, data in all_tech.items():
        tech, name, open_pct = data['tech'], data['name'], data['open_pct']
        r = S.score_v1212(tech, hot_sectors, name, code, open_pct)
        r['v1212_score'] = r['score']
        m = S.match_sector(name, code)
        r['is_local_bull'] = any(s in local_bull for s in m) if local_bull else False
        rs, sig, _bonus = S.score_range_bull_v1223(r, local_bull, sector_accel)
        cs, csig, _, _ = S.score_correction_v1223(r, local_bull, sector_accel)
        r['range_score'] = rs; r['corr_score'] = cs
        r['signals'] = sig if sig else csig
        scored_all.append(r)
    by_range = sorted(scored_all, key=lambda x: -x['range_score'])
    by_corr = sorted(scored_all, key=lambda x: -x['corr_score'])
    selected, top = set(), []
    for s in by_range:
        if s.get('is_local_bull') and s['code'] not in selected:
            s['v1224_score'] = s['range_score']; s['sel_mode']='LOCAL_BULL'
            top.append(s); selected.add(s['code']); break
    for s in by_range:
        if len([t for t in top if t['sel_mode'] in ('BULL','LOCAL_BULL','SURGE')]) >= 5: break
        if s['code'] not in selected:
            s['v1224_score'] = s['range_score']; s['sel_mode']='BULL'
            top.append(s); selected.add(s['code'])
    for s in by_corr:
        if s['code'] not in selected:
            s['v1224_score'] = s['corr_score']; s['sel_mode']='CORRECTION'
            top.append(s); selected.add(s['code']); break
    while len(top) < N:
        for s in by_corr:
            if len(top) >= N: break
            if s['code'] not in selected:
                s['v1224_score'] = s['corr_score']; s['sel_mode']='CORRECTION'
                top.append(s); selected.add(s['code'])
    top.sort(key=lambda x: -x['v1224_score'])
    return top

def stage2_auction_top5(night_top, buy_date, all_full):
    cands = []
    for r in night_top:
        kl = all_full[r['code']]
        di = find_idx(kl['d'], buy_date)
        if di <= 0:
            continue
        lc = kl['c'][di-1]; op = kl['o'][di]
        open_pct = (op/lc - 1)*100 if lc > 0 else 0
        if open_pct >= 9.5:
            continue
        base = r.get('v1224_score', 0)
        ab = 15 if open_pct<=0 else 10 if open_pct<=1 else 5 if open_pct<=2.5 else 0 if open_pct<=4 else -8 if open_pct<=7 else -15
        cands.append({'code':r['code'],'name':r['name'],'base':base,'open_pct':round(open_pct,2),'comp':base+ab,'sel_mode':r['sel_mode']})
    cands.sort(key=lambda x: -x['comp'])
    return cands[:FINAL5]

def prev_trading(trade_dates, d):
    i = find_idx(trade_dates, d)
    return trade_dates[i-1] if i > 0 else None

def run(mode, all_full, trade_dates, relevant_dates):
    agg, day_summ, detail = [], [], {}
    for D in BUY_DAYS:
        PD = prev_trading(trade_dates, D)
        if not PD:
            continue
        hist = build_history(all_full, PD, relevant_dates)
        at, tkls, hot, acc, lb = commons(PD, hist, all_full)
        nt = stage1_mode_A(at, tkls, hot, acc, lb, TOP20) if mode == 'A' else stage1_mode_B(at, tkls, hot, acc, lb, TOP20)
        fin5 = stage2_auction_top5(nt, D, all_full)
        res = []
        for it in fin5:
            kl = all_full[it['code']]; di = find_idx(kl['d'], D)
            if di < 0 or di+1 >= len(kl['h']):
                continue
            buy = kl['o'][di]; ret = (kl['h'][di+1]/buy - 1)*100
            res.append({'code':it['code'],'name':it['name'],'base':it['base'],'open_pct':it['open_pct'],'mode':it['sel_mode'],'buy':round(buy,2),'ret':round(ret,2)})
            agg.append(ret)
        detail[D] = res
        rs = [x['ret'] for x in res]
        av = sum(rs)/len(rs) if rs else 0; wn = sum(1 for x in rs if x>0)/len(rs)*100 if rs else 0
        day_summ.append({'date':D,'n':len(res),'avg':round(av,2),'win':round(wn,1)})
    return agg, day_summ, detail

def main():
    bj = S.datetime.now(S.BEIJING_TZ)
    print(f"两口径对比回测(窗口{N_WIN}日) {bj.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    S.load_sector_mapping()
    stocks = S.get_all_stocks()
    filtered = [s for s in stocks if 2 <= s['price'] <= 80 and s['circ_mv'] <= 500]
    all_full = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=S._MAX_WORKERS) as ex:
        futs = {ex.submit(S.get_kl, s['code'], 160): s for s in filtered}
        for i, fut in enumerate(as_completed(futs), 1):
            s = futs[fut]
            try:
                kl = fut.result()
                if kl:
                    kl['name'] = s['name']; all_full[s['code']] = kl
            except: pass
    print(f"K线成功{len(all_full)}只 {time.time()-t0:.0f}s", flush=True)
    idx = S.get_index_kl(80)
    trade_dates = idx['d'] if idx else []
    relevant_dates = [d for d in trade_dates if d >='2026-06-01']
    global BUY_DAYS
    if len(trade_dates) > 1:
        cands = [d for d in trade_dates if d < trade_dates[-1]]
        BUY_DAYS = cands[-N_WIN:]
    print(f"最近{N_WIN}个买入日: {BUY_DAYS}", flush=True)

    result = {}
    for mode in ('A', 'B'):
        agg, ds, dt = run(mode, all_full, trade_dates, relevant_dates)
        n = len(agg)
        avg = sum(agg)/n if n else 0; med = sorted(agg)[n//2] if n else 0
        win = sum(1 for x in agg if x>0)/n*100 if n else 0
        result[mode] = {'stat':{'n':n,'avg':round(avg,2),'med':round(med,2),'win':round(win,1),
                                'max':round(max(agg),2) if agg else 0,'min':round(min(agg),2) if agg else 0},
                        'daily':ds,'details':dt}
        print(f">>> 口径{mode}  n={n} 均{avg:+.2f}% 中位{med:+.2f}% 胜率{win:.1f}% 最好{max(agg) if agg else 0:+.2f}% 最差{min(agg) if agg else 0:+.2f}%")
        print("各日选5:", {k:[x['name'] for x in v] for k,v in dt.items()})
    json.dump(result, open('/workspace/mode_compare_backtest.json','w',encoding='utf-8'), ensure_ascii=False, indent=2, default=str)
    print("已保存 /workspace/mode_compare_backtest.json")

if __name__ == '__main__':
    main()