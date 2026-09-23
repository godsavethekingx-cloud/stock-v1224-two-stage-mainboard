#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
板块/题材 技术面判定模块 (2026-09-04 新增)
================================================================
目的: 用户明确要求——锁定「板块放量、板块异动、板块日线突破/回踩、板块内个股涨停数量」，
     这些规则应在推荐个股之前对板块本身做技术面体检，并把结果反哺到个股打分。

思路: 板块指数日K没有统一可用接口，因此用「成分股聚合」构造板块技术面快照:
  - 板块放量   : 板块内成分股当日量比(today_ratio)超过阈值(>1.15)的占比 + 板块成交额同比
  - 板块异动   : 板块内成分股今日平均涨幅(等权) 与 板块涨速(涨停数)
  - 板块日线突破: 板块内成分股今日收盘突破前20日新高的占比
  - 板块日线回踩: 板块内成分股缩量(u<=前值) + 贴MA10企稳(-6<=dev_ma10<=1) 的占比
  - 板块涨停家数: 今日实时涨停(pct>=limit)的成分股数量

输出(按板块):
  {
    '<板块名>': {
      'total': 成分股数, 'zt_cnt': 今日涨停家数, 'avg_pct': 板块今日等权涨幅,
      'vol_ratio': 放量占比(0~1), 'break_ratio': 突破新高占比(0~1),
      'pullback_ratio': 回踩企稳占比(0~1), 'surge_ratio': 异动(涨幅>5%)占比,
      'state': 板块技术状态 ['strong','ignite','breakout','pullback','decline','neutral']
    }, ...
  }

state 判定:
  ignite  (启动/加速): zt_cnt>=3 或 (avg_pct>2 且 vol_ratio>0.5)
  breakout(突破新高):  break_ratio>=0.25 且 avg_pct>0
  strong  (强势主线):  zt_cnt>=5 且 avg_pct>1 且 vol_ratio>0.5
  pullback(回踩蓄势):  avg_pct<0 且 pullback_ratio>=0.4 且 vol 未放大(非放量下杀)
  decline (走弱/退潮): avg_pct<-2 且 break_ratio<0.1 且 zt_cnt==0
  其余     neutral

对个股推荐的用法(在 capture_v18 中):
  st = sector_tech_bonus(match_sector(name,code), st_map)
  命中 ignite/breakout/strong => 加分(板块技术面正向)
  命中 decline => 减分(回避退潮板块)
  (存入 results['sector_tech_report'] 供可视化)
"""
from collections import defaultdict

LIMIT_MAIN = 9.8
LIMIT_GEM = 19.8
MIN_SECTOR_MEMBERS = 3          # 少于该成员数的板块视为噪声, 不判定
VOL_RATIO_TH  = 1.15            # 今日量比超过该值视为"放量成分股"
BREAK_RATIO_TH = 0.25           # 突破新高成分占比达该值视为板块突破
PULLBACK_RATIO_TH = 0.4         # 回踩企稳成分占比达该值视为板块回踩
IGNITE_SURGE_TH = 0.15          # 异动(涨幅>5%)成分占比


def build_sector_tech(stocks, rt, all_kls, match_sector):
    """
    板块技术面聚合主函数。
    all_kls: {code: kline}, kline 含 c/o/h/l/v(量,手)
    rt     : {code: {pct, open_pct, amt_yi,...}} 实时快照(收盘/盘中皆可)
    stocks : [{code,name,circ_mv}]
    返回  (st_map, order) : st_map 见模块doc, order为按热度排序的板块名列表
    """
    # 1. 板块成分
    members = defaultdict(list)          # sector -> [code]
    for s in stocks:
        for sec in match_sector(s.get('name', ''), s['code']):
            members[sec].append(s['code'])

    st_map = {}
    for sec, codes in members.items():
        if len(codes) < MIN_SECTOR_MEMBERS:
            continue
        n = len(codes)
        zt_cnt = 0
        sum_pct = 0.0
        pct_cnt = 0
        vol_up = 0          # 放量成分数
        brk = 0             # 突破新高成分数
        pbk = 0             # 回踩企稳成分数
        surge = 0           # 异动成分数(涨幅>5%)
        has_k = 0
        for code in codes:
            rd = rt.get(code)
            if rd:
                std = LIMIT_GEM if (code.startswith('30') or code.startswith('688')) else LIMIT_MAIN
                if rd.get('pct', 0) >= std:
                    zt_cnt += 1
                sum_pct += rd.get('pct', 0) or 0
                pct_cnt += 1
                if (rd.get('pct', 0) or 0) > 5:
                    surge += 1
            kl = all_kls.get(code)
            if not kl:
                continue
            c = kl['c']; v = kl['v']; h = kl.get('h', c)
            ti = len(c) - 1; yi = ti - 1
            if ti < 21 or c[yi - 1] <= 0:
                continue
            has_k += 1
            # 量比 (今日量 / 前20日均量)
            vol20 = sum(v[max(0, yi - 20):yi]) / max(1, min(20, yi))
            tr = (v[ti] / vol20) if vol20 > 0 else 0.0
            if tr >= VOL_RATIO_TH:
                vol_up += 1
            # 今日收盘突破前20日新高
            hi20 = max(h[max(0, yi - 20):yi])
            if hi20 > 0 and c[ti] >= hi20 * 0.995:
                brk += 1
            # 回踩企稳: 缩量 + 贴MA10企稳
            ma10 = sum(c[ti - 9:ti + 1]) / 10
            dev = (c[ti] / ma10 - 1) * 100 if ma10 > 0 else 0
            if tr <= 1.6 and -6 <= dev <= 1:
                pbk += 1

        denom = max(1, pct_cnt)
        avg_pct = round(sum_pct / denom, 2) if pct_cnt else 0.0
        vol_ratio = round(vol_up / max(1, has_k), 2)
        brk_ratio = round(brk / max(1, has_k), 2)
        pbk_ratio = round(pbk / max(1, has_k), 2)
        srg_ratio = round(surge / max(1, denom), 2)

        # state 判定
        if zt_cnt >= 5 and avg_pct > 1 and vol_ratio > 0.5:
            state = 'strong'
        elif zt_cnt >= 3 or (avg_pct > 2 and vol_ratio > 0.5):
            state = 'ignite'
        elif brk_ratio >= BREAK_RATIO_TH and avg_pct > 0:
            state = 'breakout'
        elif avg_pct < 0 and pbk_ratio >= PULLBACK_RATIO_TH and vol_ratio < 0.5:
            state = 'pullback'
        elif avg_pct < -2 and brk_ratio < 0.1 and zt_cnt == 0:
            state = 'decline'
        else:
            state = 'neutral'

        st_map[sec] = {
            'total': len(codes), 'zt_cnt': zt_cnt, 'avg_pct': avg_pct,
            'vol_ratio': vol_ratio, 'break_ratio': brk_ratio,
            'pullback_ratio': pbk_ratio, 'surge_ratio': srg_ratio,
            'state': state, 'denom': has_k,
        }

    order = sorted(st_map.keys(),
                   key=lambda s: (st_map[s]['zt_cnt'], st_map[s]['avg_pct']),
                   reverse=True)
    return st_map, order


# 板块技术状态 → 个股加分 (供 capture_v18 使用)
STATE_BONUS = {
    'strong':   12,
    'ignite':   9,
    'breakout': 8,
    'pullback': 4,
    'neutral':  0,
    'decline':  -8,
}


def sector_tech_bonus(matched_secs, st_map):
    """
    对一只股票命中的板块取其"最强正向"状态加分。
    返回 (bonus, hit_state_info)。若无任何命中或板块未判定返回 (0, None)。
    """
    if not st_map:
        return 0, None
    best = 0
    info = None
    for sec in matched_secs:
        st = st_map.get(sec)
        if not st:
            continue
        b = STATE_BONUS.get(st['state'], 0)
        if b > best:
            best = b
            info = (sec, st['state'], st['zt_cnt'])
    # 若命中的板块都属退潮/走弱(无正向状态), 应用负分而非0
    if best == 0:
        neg = None
        for sec in matched_secs:
            st = st_map.get(sec)
            if st and st['state'] == 'decline':
                neg = (sec, 'decline', st['zt_cnt'])
                break
        if neg:
            return STATE_BONUS['decline'], neg
    return best, info


if __name__ == '__main__':
    print('板块技术面判定模块加载成功。')
    print('  - build_sector_tech(stocks, rt, all_kls, match_sector) → (st_map, order)')
    print('  - sector_tech_bonus(matched_secs, st_map) → (bonus, info)')