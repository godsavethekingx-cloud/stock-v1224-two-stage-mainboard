#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
超短止盈/止损规则优化 (基于最近15日两阶段TOP5, 口径A=BULL)
买入: D日开盘; 持有至D+1日内触发止盈/止损, 否则D+1收盘卖出.
因无T+1分时, 用D+1日高低点 + 两档顺序口径(保守=先止"损/触发SL"最差; 乐观=先止盈)框定真实结果.
"""
import json, sys, os, time
from concurrent.futures import ThreadPoolExecutor, as_completed

_SYSTEM = '/workspace/stock-v1224/system'
sys.path.insert(0, _SYSTEM)
import scan_v1224 as S

TOP20 = 20; FINAL5 = 5
BUY_DAYS = None

def find_idx(dates, target):
    for i, d in enumerate(dates):
        if d.startswith(target):
            return i
    return -1

def truncate_to(kl, upto_date):
    idx = find_idx(kl['d'], upto_date)
    if idx < 0: return None
    return {'d':kl['d'][:idx+1],'c':kl['c'][:idx+1],'o':kl['o'][:idx+1],'h':kl['h'][:idx+1],'l':kl['l'][:idx+1],'v':kl['v'][:idx+1]}

def build_history(all_full, upto_date, relevant):
    rel=[d for d in relevant if d<=upto_date]
    trunc={c:truncate_to(kl,upto_date) for c,kl in all_full.items()}
    trunc={c:t for c,t in trunc.items() if t}
    return S.build_sector_zt_history(trunc, rel) if trunc else {}

def commons(base_date, hist, all_full):
    at, tkls = {}, {}
    for code,kl in all_full.items():
        t=truncate_to(kl,base_date)
        if not t or len(t['d'])<7: continue
        tech=S.analyze_tech(t)
        if not tech: continue
        oi=find_idx(kl['d'],base_date)
        if oi<=0: continue
        lc=kl['c'][oi-1]; op=kl['o'][oi]; opct=(op/lc-1)*100 if lc>0 else 0
        if opct>=9.5: continue
        at[code]={'tech':tech,'name':kl['name'],'open_pct':opct}; tkls[code]=t
    hot=S.detect_hot_sectors(at); acc=S.detect_sector_acceleration(base_date,hist)
    daily=[]
    for code,d in at.items():
        sc=S.score_v1212(d['tech'],hot,d['name'],code,d['open_pct']); daily.append(sc)
    daily.sort(key=lambda x:-x['score'])
    lb=S.detect_local_bull({r['code']:r for r in daily[:20]})
    return at,tkls,hot,acc,lb

def stage1_mode_A(at,tkls,hot,acc,lb,N=20):
    scored_all=[]
    for code,d in at.items():
        tech,name,opct=d['tech'],d['name'],d['open_pct']
        sc=S.score_v1212(tech,hot,name,code,opct); sc['v1212_score']=sc['score']
        us,usg,c1,c2,buy=S.score_user_mode(sc,tkls[code],lb,acc,'BULL')
        sc['v1224_score']=us; sc['signals']=usg; sc['cond1_met'],sc['cond2_met'],sc['is_buyable']=c1,c2,buy
        scored_all.append(sc)
    buyable=[x for x in scored_all if x.get('is_buyable',True)]
    buyable.sort(key=lambda x:-x['v1224_score'])
    both=[x for x in buyable if x.get('cond1_met') and x.get('cond2_met')]
    oc1=[x for x in buyable if x.get('cond1_met') and not x.get('cond2_met')]
    oc2=[x for x in buyable if x.get('cond2_met') and not x.get('cond1_met')]
    topn,sel=[],set()
    for x in both:
        if len(topn)>=N: break
        if x['code'] not in sel: x['sel_mode']='DUAL_COND'; topn.append(x); sel.add(x['code'])
    for x in oc1:
        if len(topn)>=N: break
        if x['code'] not in sel: x['sel_mode']='COND1'; topn.append(x); sel.add(x['code'])
    for x in oc2:
        if len(topn)>=N: break
        if x['code'] not in sel: x['sel_mode']='COND2'; topn.append(x); sel.add(x['code'])
    for x in buyable:
        if len(topn)>=N: break
        if x['code'] not in sel: x['sel_mode']='BULL'; topn.append(x); sel.add(x['code'])
    topn.sort(key=lambda x:-x['v1224_score'])
    return topn

def stage2_top5(night_top, d, all_full):
    cands=[]
    for r in night_top:
        kl=all_full[r['code']]; di=find_idx(kl['d'],d)
        if di<=0: continue
        lc=kl['c'][di-1]; op=kl['o'][di]; opct=(op/lc-1)*100 if lc>0 else 0
        if opct>=9.5: continue
        ab=15 if opct<=0 else 10 if opct<=1 else 5 if opct<=2.5 else 0 if opct<=4 else -8 if opct<=7 else -15
        cands.append({'code':r['code'],'name':r['name'],'comp':r.get('v1224_score',0)+ab})
    cands.sort(key=lambda x:-x['comp'])
    return cands[:FINAL5]

def main():
    S.load_sector_mapping(); stocks=S.get_all_stocks()
    filtered=[s for s in stocks if 2<=s['price']<=80 and s['circ_mv']<=500]
    all_full={}
    with ThreadPoolExecutor(max_workers=S._MAX_WORKERS) as ex:
        futs={ex.submit(S.get_kl,s['code'],160):s for s in filtered}
        for i,f in enumerate(as_completed(futs),1):
            s=futs[f]
            try:
                kl=f.result()
                if kl: kl['name']=s['name']; all_full[s['code']]=kl
            except: pass
    idx=S.get_index_kl(80); tdates=idx['d'] if idx else []
    relevant=[d for d in tdates if d>='2026-06-01']
    global BUY_DAYS
    BUY_DAYS=[d for d in tdates if d<tdates[-1]][-15:]

    trades=[]
    for D in BUY_DAYS:
        iD=find_idx(tdates,D); PD=tdates[iD-1] if iD>0 else None
        if not PD: continue
        hist=build_history(all_full,PD,relevant)
        at,tkls,hot,acc,lb=commons(PD,hist,all_full)
        nt=stage1_mode_A(at,tkls,hot,acc,lb,TOP20)
        fin5=stage2_top5(nt,D,all_full)
        for it in fin5:
            kl=all_full[it['code']]; di=find_idx(kl['d'],D)
            if di<0 or di+1>=len(kl['h']): continue
            buy=kl['o'][di]
            nxt=di+1
            trades.append({'date':D,'name':it['name'],'code':it['code'],
                           'buy':buy,'h':kl['h'][nxt],'l':kl['l'][nxt],'close':kl['c'][nxt]})
    print(f"收集交易 {len(trades)} 笔", flush=True)

    TP_GRID=[1,2,3,4,5,6,8,10,15,'NONE']
    SL_GRID=[0.5,1,1.5,2,3,5,'NONE']
    rows=[]
    for tp in TP_GRID:
        for sl in SL_GRID:
            cons=[]; opt=[]
            for t in trades:
                tp_p=buy if tp=='NONE' else None
                hi=t['h']; lo=t['l']; cl=t['close']
                hit_tp = hi >= t['buy']*(1+tp/100) if tp!='NONE' else False
                hit_sl = lo <= t['buy']*(1-sl/100) if sl!='NONE' else False
                if hit_tp and hit_sl:
                    cons.append(-sl); opt.append(tp)
                elif hit_tp:
                    cons.append(tp); opt.append(tp)
                elif hit_sl:
                    cons.append(-sl); opt.append(-sl)
                else:
                    r=(cl/t['buy']-1)*100; cons.append(r); opt.append(r)
            ca=sum(cons)/len(cons); cw=sum(1 for x in cons if x>0)/len(cons)*100
            oa=sum(opt)/len(opt); ow=sum(1 for x in opt if x>0)/len(opt)*100
            rows.append({'tp':tp,'sl':sl,'n':len(trades),
                         'cons_avg':round(ca,2),'cons_win':round(cw,1),
                         'opt_avg':round(oa,2),'opt_win':round(ow,1)})
    # 排序: 保守口径下按均收益降序, 同时展示胜率
    rows.sort(key=lambda r:-r['cons_avg'])
    print("\n=== 止盈/止损网格 (按保守口径均收益排序, 前15) ===")
    print(f"{'止盈%':>6}{'止损%':>6}{'N':>4}{'保守均值':>9}{'保守胜率':>8}{'乐观均值':>9}{'乐观胜率':>8}")
    for r in rows[:15]:
        tp='无' if r['tp']=='NONE' else r['tp']
        sl='无' if r['sl']=='NONE' else r['sl']
        print(f"{str(tp):>6}{str(sl):>7}{r['n']:>4}{r['cons_avg']:>+8.2f}%{r['cons_win']:>8.1f}%{r['opt_avg']:>+8.2f}%{r['opt_win']:>7.1f}%")
    json.dump({'trades':[{k:(v if k!='buy' else round(v,2)) for k,v in t.items()} for t in trades],
               'grid':rows}, open('/workspace/超短止盈止损优化.json','w',encoding='utf-8'), ensure_ascii=False, indent=2, default=str)
    print("\n已保存 /workspace/超短止盈止损优化.json")

if __name__=='__main__':
    main()