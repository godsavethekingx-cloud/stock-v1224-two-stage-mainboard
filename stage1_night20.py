#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段1: 当晚23:00 (晚间模式) 全盘扫描选出 TOP20
=========================================
数据用: 最新收盘日K线 + 该日 open_pct (收盘后可知)
自动化就绪: 日期动态取最新交易日, 非交易日自动跳过.
输出: /workspace/stock-v1224/system/night20_latest.json
"""
import json, sys, os, time
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

_SYSTEM = '/workspace/stock-v1224/system'
sys.path.insert(0, _SYSTEM)
import scan_v1224 as S

N = 20
OUT = os.path.join(_SYSTEM, 'night20_latest.json')

beijing = datetime.now(timezone(timedelta(hours=8)))
TODAY = beijing.strftime('%Y-%m-%d')


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


def main():
    bj = datetime.now(timezone(timedelta(hours=8)))
    print(f"[阶段1] 晚间全盘选TOP{N}  {bj.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

    # 交易日校验: 以沪指最新一根K线日期判断, 仅当==今天才视为当日收盘后运行
    try:
        index_kl_ref = S.get_index_kl(80)
    except Exception as e:
        print(f"!! 无法获取指数K线: {e}, 终止")
        return 1
    if not index_kl_ref or not index_kl_ref['d']:
        print("!! 指数K线为空, 终止")
        return 1
    REF_DATE = index_kl_ref['d'][-1]
    if not REF_DATE.startswith(TODAY):
        print(f"今天{TODAY}非A股交易日 (最新指数K线{REF_DATE}), 跳过.")
        with open('/workspace/edge/night20_skip.txt', 'a') as f:
            f.write(f"{bj.strftime('%Y-%m-%d %H:%M:%S')} 非交易日跳过 (latest index {REF_DATE})\n")
        return 0
    print(f"今日为交易日, REF_DATE={REF_DATE}, 继续执行")

    S.load_sector_mapping()
    stocks = S.get_all_stocks()
    filtered = [s for s in stocks if 2 <= s['price'] <= 80 and s['circ_mv'] <= 500]
    print(f"全量{len(stocks)} -> 过滤后{len(filtered)}", flush=True)

    all_full = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=S._MAX_WORKERS) as ex:
        futs = {ex.submit(S.get_kl, s['code'], 100): s for s in filtered}
        for i, fut in enumerate(as_completed(futs), 1):
            s = futs[fut]
            try:
                kl = fut.result()
                if kl:
                    kl['name'] = s['name']; all_full[s['code']] = kl
            except Exception:
                pass
            if i % 500 == 0 or i == len(filtered):
                print(f"  K线 {i}/{len(filtered)} 成功{len(all_full)} 用时{time.time()-t0:.0f}s", flush=True)
    print(f"K线成功{len(all_full)}只 用时{time.time()-t0:.0f}s", flush=True)

    # 技术面 + open_pct (open_pct 用 REF_DATE 开盘 vs 昨收, 收盘后可知)
    all_tech, trunc_kls = {}, {}
    for code, kl in all_full.items():
        t = truncate_to(kl, REF_DATE)
        if not t or len(t['d']) < 7:
            continue
        tech = S.analyze_tech(t)
        if not tech:
            continue
        oi = find_idx(kl['d'], REF_DATE)
        if oi <= 0:
            continue
        lc = kl['c'][oi-1]; op = kl['o'][oi]
        open_pct = (op/lc - 1)*100 if lc > 0 else 0
        if open_pct >= 9.5:
            continue
        all_tech[code] = {'tech': tech, 'name': kl['name'], 'open_pct': round(open_pct, 2)}
        trunc_kls[code] = t

    # 板块历史(截至 REF_DATE)
    index_kl = index_kl_ref
    trade_dates = index_kl['d']
    rel = [d for d in trade_dates if d >= '2026-01-01' and d <= REF_DATE]
    truncated = {c: truncate_to(kl, REF_DATE) for c, kl in all_full.items() if truncate_to(kl, REF_DATE)}
    sector_zth = S.build_sector_zt_history(truncated, rel) if truncated else {}

    hot_sectors = S.detect_hot_sectors(all_tech)
    sector_accel = S.detect_sector_acceleration(REF_DATE, sector_zth)
    daily_scores = []
    for code, data in all_tech.items():
        sc = S.score_v1212(data['tech'], hot_sectors, data['name'], code, data['open_pct'])
        sc['v1212_score'] = sc['score']; daily_scores.append(sc)
    daily_scores.sort(key=lambda x: -x['score'])
    pool = {r['code']: r for r in daily_scores[:20]}
    local_bull = S.detect_local_bull(pool)

    # 口径: 全量 BULL 用户模式 + DUAL_COND优先 (15日回测胜率/收益均占优)
    scored_all = []
    for code, data in all_tech.items():
        tech, name, open_pct = data['tech'], data['name'], data['open_pct']
        scored = S.score_v1212(tech, hot_sectors, name, code, open_pct)
        scored['v1212_score'] = scored['score']
        us, usg, c1, c2, buyable = S.score_user_mode(scored, trunc_kls[code], local_bull, sector_accel, 'BULL')
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
        if x['code'] not in sel: x['sel_mode'] = 'DUAL_COND'; topn.append(x); sel.add(x['code'])
    for x in oc1:
        if len(topn) >= N: break
        if x['code'] not in sel: x['sel_mode'] = 'COND1'; topn.append(x); sel.add(x['code'])
    for x in oc2:
        if len(topn) >= N: break
        if x['code'] not in sel: x['sel_mode'] = 'COND2'; topn.append(x); sel.add(x['code'])
    for x in buyable:
        if len(topn) >= N: break
        if x['code'] not in sel: x['sel_mode'] = 'BULL'; topn.append(x); sel.add(x['code'])
    topn.sort(key=lambda x: -x['v1224_score'])

    print(f"\n===== 今晚 TOP{N} ({REF_DATE}收盘) =====")
    # ---- 完整面板数据(供日报/审计使用) ----
    market = S.analyze_market(index_kl_ref) or {}
    regime = 'BULL'
    panel = {
        'run_time': bj.strftime('%Y-%m-%d %H:%M:%S'),
        'ref_date': REF_DATE,
        'regime': regime,
        'suggested_regime': market.get('suggested_regime', regime),
        'market': market,
        'hot_sectors': dict(hot_sectors),
        'sector_accel': dict(sector_accel),
        'local_bull': sorted(set(local_bull)) if isinstance(local_bull, (list, set)) else local_bull,
        'top': [],
    }

    out = []
    for i, r in enumerate(topn, 1):
        klf = all_full[r['code']]
        close = klf['c'][-1]
        sig = list(r.get('signals') or [])
        advice = S.generate_trade_advice(r, regime, sector_accel, local_bull)
        secs = S.match_sector(r['name'], r['code'])
        core = {'rank': i, 'code': r['code'], 'name': r['name'],
                'v1224_score': r['v1224_score'], 'sel_mode': r['sel_mode'],
                'open_pct': round(all_tech[r['code']]['open_pct'], 2),
                'close': round(close, 2), 'signals': sig,
                # 详细字段(日报)
                'yest_pct': round(r.get('yest_pct', 0), 2),
                'rsi6': round(r.get('rsi6', 0), 1),
                'max_consec': r.get('max_consec', 0),
                'zt_30d': r.get('zt_30d', 0),
                'is_defensive': r.get('is_defensive', False),
                'yest_close': round(r.get('yest_close', close), 2),
                'ma5': round(r.get('ma5', 0), 2),
                'ma10': round(r.get('ma10', 0), 2),
                'sectors': secs,
                'trade_advice': advice,
                }
        core['signals_full'] = core['signals']
        out.append(core)
        panel['top'].append(core)
        op_show = all_tech[r['code']]['open_pct']
        print(f"{i:>2}. {r['name']:<8} {r['code']:<8} 分{r['v1224_score']:.1f} {r['sel_mode']:<9} 竞价开盘{op_show:+.1f}% 收{close:.2f} 信号{';'.join(sig[:3])}", flush=True)

    os.makedirs(_SYSTEM, exist_ok=True)   # 自愈: 确保阶段1输出目录存在
    os.makedirs('/workspace/edge', exist_ok=True)
    data = {'run_time': bj.strftime('%Y-%m-%d %H:%M:%S'), 'ref_date': REF_DATE,
            'regime': 'BULL', 'top': out}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # 完整面板(供日报)
    PANEL = os.path.join(_SYSTEM, 'night20_panel.json')
    with open(PANEL, 'w', encoding='utf-8') as f:
        json.dump(panel, f, ensure_ascii=False, indent=2)
    with open('/workspace/edge/night20_run.txt', 'a') as f:
        f.write(json.dumps({'run_time': data['run_time'], 'ref_date': REF_DATE,
                            'count': len(out)}, ensure_ascii=False) + "\n")
    print(f"\n已保存 {OUT} / {PANEL}  ({len(out)}只)")


if __name__ == '__main__':
    sys.exit(main())