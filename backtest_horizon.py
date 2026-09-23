#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
超短 vs 波段 收益对比 (基于两阶段选股, 口径A=BULL用户模式)
买入: 阶段2竞价筛TOP5, D日开盘价买入
卖出: 持股k天, 取期间最高价止盈(冲高卖)  vs  持至第k日收盘(对照)
k可选: [1,2,3,5,8,10]
数据延续到最后交易日, 长周期仅统计有完整向前窗口的个股(样本数如实标注)
"""
import json, sys, os, time
from concurrent.futures import ThreadPoolExecutor, as_completed

_SYSTEM = '/workspace/stock-v1224/system'
sys.path.insert(0, _SYSTEM)
import scan_v1224 as S

TOP20 = 20
FINAL5 = 5
HORIZONS = [1, 2, 3, 5, 8, 10]
BUY_DAYS = None

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
    hot = S.detect_hot_sectors(all_tech)
    acc = S.detect_sector_acceleration(base_date, sector_zth)
    daily = []
    for code, data in all_tech.items():
        sc = S.score_v1212(data['tech'], hot, data['name'], code, data['open_pct'])
        daily.append(sc)
    daily.sort(key=lambda x: -x['score'])
    pool = {r['code']: r for r in daily[:20]}
    lb = S.detect_local_bull(pool)
    return all_tech, trunc_kls, hot, acc, lb

def stage1_mode_A(all_tech, trunc_kls, hot, acc, lb, N=20):
    scored_all = []
    for code, data in all_tech.items():
        tech, name, open_pct = data['tech'], data['name'], data['open_pct']
        scored = S.score_v1212(tech, hot, name, code, open_pct)
        scored['v1212_score'] = scored['score']
        us, usg, c1, c2, buyable = S.score_user_mode(scored, trunc_kls[code], lb, acc, 'BULL')
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

def stage2_top5(night_top, buy_date, all_full):
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
        comp = r.get('v1224_score', 0) + (15 if open_pct<=0 else 10 if open_pct<=1 else 5 if open_pct<=2.5 else 0 if open_pct<=4 else -8 if open_pct<=7 else -15)
        cands.append({'code':r['code'],'name':r['name'],'base':r.get('v1224_score',0),'open_pct':open_pct,'comp':comp})
    cands.sort(key=lambda x: -x['comp'])
    return cands[:FINAL5]

def main():
    bj = S.datetime.now(S.BEIJING_TZ)
    print(f"超短vs波段对比 {bj.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    S.load_sector_mapping()
    stocks = S.get_all_stocks()
    filtered = [s for s in stocks if 2 <= s['price'] <= 80 and s['circ_mv'] <= 500]
    all_full = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=S._MAX_WORKERS) as ex:
        futs = {ex.submit(S.get_kl, s['code'], 150): s for s in filtered}
        for i, fut in enumerate(as_completed(futs), 1):
            s = futs[fut]
            try:
                kl = fut.result()
                if kl:
                    kl['name'] = s['name']; all_full[s['code']] = kl
            except: pass
            if i % 500 == 0 or i == len(filtered):
                print(f"K线 {i}/{len(filtered)} 成功{len(all_full)} {time.time()-t0:.0f}s", flush=True)
    idx = S.get_index_kl(80)
    trade_dates = idx['d'] if idx else []
    relevant = [d for d in trade_dates if d >= '2026-06-01']
    global BUY_DAYS
    cands_d = [d for d in trade_dates if d < trade_dates[-1]]
    BUY_DAYS = cands_d[-15:]
    print(f"最近15个买入日: {BUY_DAYS}", flush=True)

    # per-horizon accumulators
    stat = {k: {'peak': [], 'close': []} for k in HORIZONS}
    recc = {}  # record examples
    for D in BUY_DAYS:
        iD = find_idx(trade_dates, D)
        PD = trade_dates[iD-1] if iD > 0 else None
        if not PD: continue
        hist = build_history(all_full, PD, relevant)
        at, tkls, hot, acc, lb = commons(PD, hist, all_full)
        nt = stage1_mode_A(at, tkls, hot, acc, lb, TOP20)
        fin5 = stage2_top5(nt, D, all_full)
        for it in fin5:
            kl = all_full[it['code']]; di = find_idx(kl['d'], D)
            if di < 0: continue
            buy = kl['o'][di]
            ex = {'date':D,'name':it['name'],'code':it['code'],'buy':round(buy,2)}
            for k in HORIZONS:
                end = di + k
                if end >= len(kl['h']):
                    ex[f'h{k}_peak'] = None; ex[f'h{k}_close'] = None
                    continue
                peak = max(kl['h'][di+1:end+1]); ed = kl['d'][end]; cl = kl['c'][end]
                ex[f'h{k}_peak'] = round((peak/buy-1)*100, 2)
                ex[f'h{k}_close'] = round((cl/buy-1)*100, 2)
                stat[k]['peak'].append(ex[f'h{k}_peak'])
                stat[k]['close'].append(ex[f'h{k}_close'])
            recc[D] = ex
    json.dump({'stat':{str(k):{'peak_avg':round(sum(v['peak'])/len(v['peak']),2) if v['peak'] else 0,
                                'peak_win':round(sum(1 for x in v['peak'] if x>0)/len(v['peak'])*100,1) if v['peak'] else 0,
                                'close_avg':round(sum(v['close'])/len(v['close']),2) if v['close'] else 0,
                                'close_win':round(sum(1 for x in v['close'] if x>0)/len(v['close'])*100,1) if v['close'] else 0,
                                'n':len(v['peak'])} for k,v in stat.items()},
              'examples':recc},
              open('/workspace/超短vs波段回测.json','w',encoding='utf-8'), ensure_ascii=False, indent=2, default=str)
    print("\n持股周期 |  N | 区间最高止盈均收益 | 胜率 | 末日收盘均收益 | 胜率")
    for k in HORIZONS:
        v = stat[k]['peak']; c = stat[k]['close']
        pa = sum(v)/len(v) if v else 0; pw = sum(1 for x in v if x>0)/len(v)*100 if v else 0
        ca = sum(c)/len(c) if c else 0; cw = sum(1 for x in c if x>0)/len(c)*100 if c else 0
        print(f"{k}天(波段{k}) | {len(v):>3} | {pa:+.2f}% | {pw:.0f}% | {ca:+.2f}% | {cw:.0f}%")

if __name__ == '__main__':
    main()