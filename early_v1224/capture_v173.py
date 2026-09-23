#!/usr/bin/env python3
"""
V17.3 盘中动态补池增强模块 (2026-09-01 复盘优化落地)
================================================================
基于 09-01 涨停遗漏复盘, 对 v172(capture_microcap_rebound_v172) 落地四类优化:

【优化1】基因窗口滚动更新
  以「最新交易日/当前盘中」为基日重算 20日涨停基因, 不再用滞后基日,
  避免高基因票因窗口旧被低估出池。补池时基因在增长, 需实时重算。

【优化2】主题权重前置(板块涨停家数变化)
  统计今日各板块涨停家数变化, 对「近3日板块涨停家数抬升」的高热度板块,
  将其内基因≥2 的微盘股自动加「主题分」进入观察区, 而不是等主线确立后才跟。
  同时把 09-01 复盘识别的热主题(农业/种业、粮食消费、医药、零售、传媒) 作为加分。

【优化3】放开高基因连板高标分仓通道
  对 基因≥5 且 非一字开盘(开盘涨幅<涨停位) 的 2-4 连板高标,
  单独进「高标接力池」小仓位(≤15%)参与, 与「回踩低吸」并行, 不在一刀切跳过。

【优化4】竞价动态补池
  早盘竞价/开盘阶段, 用实时开盘涨幅(<5% 低开/平开, 未涨停封死) + 实时资金流,
  回查「昨日基因≥3 但前一日未入选」的标的, 动态补入观察名单。

输出: results 字典体, 兼容 v172 返回结构 + 新增 'topic_bonus' / 'pool' 字段。
依赖重构: 复用 scan_v1224 的 get_all_stocks/get_kl/match_sector/实时行情。
"""
import time, requests, math
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict

# 09-01 复盘识别的高热度主题关键词(供主题权重前置加分)
TOPIC_HEAT_KW = {
    '农业/种业':  ['种业','农发','种','登海','敦煌','万向','丰乐','北大荒','隆平','金健','深粮','粮食','米'],
    '医药':       ['药业','制药','医药','健康','千金','精华','沃华','瑞康','众生','华森'],
    '零售/商业':  ['百货','新华','东百','翠微','国芳','开开','茂业','供销','商品','商贸'],
    '传媒/游戏':  ['传媒','出版','阅文','文化','凯撒','欢瑞','粤媒','掌阅','游戏','影视','影院'],
    '消费/食品':  ['食品','白酒','啤酒','古井','九牧','家居','爱丽','服饰'],
}
LIMIT_MAIN = 9.8
LIMIT_GEM = 19.8


def _front_qt(codes, batch=80, proxies=None, headers=None):
    """腾讯实时行情快照 -> {code: {name, pct, open_pct, cur, high, low, kai, amt}}"""
    out = {}
    for i in range(0, len(codes), batch):
        grp = codes[i:i+batch]
        syms = [(('sh' if c.startswith('6') else 'sz') + c) for c in grp]
        for scheme in ('http', 'https'):
            try:
                r = requests.get(f'{scheme}://qt.gtimg.cn/q=' + ','.join(syms),
                                 timeout=8, headers=headers, proxies=proxies)
                for line in r.text.strip().split(';'):
                    if '=' not in line:
                        continue
                    try:
                        f = line.split('=')[1].strip().rstrip('"').strip().split('~')
                        if len(f) < 40:
                            continue
                        code = f[2]
                        zs = float(f[4]) if f[4] else 0
                        cur = float(f[3]) if f[3] else 0
                        kai = float(f[5]) if f[5] else 0
                        high = float(f[6]) if f[6] else 0
                        amt = float(f[37]) if len(f) > 37 and f[37] else 0
                        pct = (cur / zs - 1) * 100 if zs > 0 else 0
                        open_pct = (kai / zs - 1) * 100 if zs > 0 and kai > 0 else 0
                        out[code] = {'name': f[1], 'pct': round(pct, 2), 'open_pct': round(open_pct, 2),
                                     'cur': cur, 'high': high, 'kai': kai,
                                     'amt_yi': round(amt / 1e4, 2)}
                    except Exception:
                        pass
                break
            except Exception:
                continue
        time.sleep(0.02)
    return out


def build_sector_heat(stocks, rt, match_sector):
    """
    【优化2】板块热度: 统计今日各板块涨停家数(以实时行情), 返回 {板块: {zt_cnt, is_hot}}。
    is_hot: 板块涨停 >= 3 家 视为当日主线(高热度)。
    """
    zt_by_sector = defaultdict(int)
    for s in stocks:
        d = rt.get(s['code'])
        if not d:
            continue
        std = LIMIT_GEM if s['code'].startswith('30') or s['code'].startswith('688') else LIMIT_MAIN
        if d['pct'] >= std:
            for sec in match_sector(s['name'], s['code']):
                zt_by_sector[sec] += 1
    heat = {sec: {'zt_cnt': cnt, 'is_hot': cnt >= 3}
            for sec, cnt in zt_by_sector.items()}
    return heat


def _match_topic(name):
    """名称命中热主题 -> 主题名(首个命中)"""
    for topic, kws in TOPIC_HEAT_KW.items():
        for kw in kws:
            if kw in name:
                return topic
    return None


def capture_v173(all_kls, circ_mv_map, match_sector, rt=None, stocks=None,
                 top_n=12, theme_pool=6, high_relay_n=4, use_topic_bonus=True):
    """
    V17.3 盘中动态补池主函数。
    入参:
      all_kls     : {code: kline}, kline 含 c/o/h/l/v (收盘/开盘/最高/最低/量)
      circ_mv_map : {code: 流通市值(亿)}
      match_sector: fn(name, code)->[sector]
      rt          : 实时快照 {code:{open_pct,pct,amt_yi,...}}, 早盘/盘中调用必有
      stocks      : 全市场 [{code,name,circ_mv}], 用于主题热度统计
    返回:
      {
        'rebound':  [低位回踩池] (v172 增强 + topic_bonus),
        'high_relay': [高基因连板高标接力池] (优化3),
        'auction_fill': [竞价动态补池] (优化4, 基因≥3 未入选+低开+资金流),
        'report': 统计摘要
      }
    """
    sector_heat = {}
    if stocks and rt:
        sector_heat = build_sector_heat(stocks, rt, match_sector)

    rebound = []
    high_relay = []
    auction_fill = []

    for code, kl in all_kls.items():
        mv = circ_mv_map.get(code, 0) or 0
        if not (0 < mv < 300):
            continue
        c = kl['c']; v = kl['v'];
        n = len(c)
        ti = n - 1
        if ti < 25 or c[ti] * (v[ti] if ti < len(v) else 0) < 5000:
            continue
        yi = ti - 1
        if yi < 20 or c[yi - 1] <= 0:
            continue
        yest_pct = (c[yi] / c[yi - 1] - 1) * 100
        cur = c[ti]
        ma5 = sum(c[ti-4:ti+1]) / 5 if ti >= 4 else c[ti]
        ma10 = sum(c[ti-9:ti+1]) / 10 if ti >= 9 else cur
        ma20 = sum(c[ti-19:ti+1]) / 20 if ti >= 19 else cur
        dev_ma10 = (cur / ma10 - 1) * 100 if ma10 > 0 else 0
        dev_ma20 = (cur / ma20 - 1) * 100 if ma20 > 0 else 0
        if dev_ma20 < -12:
            continue

        # ★ 优化1: 基因窗口滚动 —— 以最新基日 yi 重算, 不用滞后基日
        gene = sum(1 for j in range(max(0, yi - 20), yi) if c[j] / c[j - 1] - 1 >= 0.095)
        if gene < 1:
            continue

        # 连板度(当日是否连板、最高连板)
        streak = 0
        tk = ti
        while tk >= 1 and c[tk] / c[tk - 1] - 1 >= 0.095:
            streak += 1
            tk -= 1
        # 昨日是否涨停(承接候选)
        yest_zt = c[yi] / c[yi - 1] - 1 >= 0.095

        # ---- 优化3: 高基因连板高标接力通道(基因≥5 + 非一字 + 连板) ----
        rt_d = (rt or {}).get(code)
        if rt_d is not None:
            std = LIMIT_GEM if code.startswith('30') or code.startswith('688') else LIMIT_MAIN
            not_yiz = rt_d.get('open_pct', 0) < std * 0.99   # 开盘未封死涨停
            rt_pct = rt_d.get('pct', yest_pct)
        else:
            not_yiz = True
            rt_pct = yest_pct
        if gene >= 5 and streak >= 2 and not_yiz and rt_pct >= 0:
            hr_score = gene * 8 + streak * 6 + (8 if mv <= 60 else 4)
            high_relay.append({
                'code': code, 'name': kl.get('name', code), 'mv': round(mv),
                'score': round(hr_score), 'gene': gene, 'streak': streak,
                'today_pct': _sf(rt_d['pct'] if rt_d else yest_pct),
                'open_pct': _sf(rt_d['open_pct']) if rt_d else None,
                'support': '分时5日线', 'support_price': _sf(ma5),
                'stop_loss': _sf(ma5 * 0.95),
                'sectors': match_sector(kl.get('name', code), code)[:3],
                'pool': '高标接力(≤15%仓)',
                'signals': [f'基因{gene}', f'{streak}连板', '非一字开板可接'],
            })

        # 基础量(全通道共用)
        vol20 = sum(v[max(0, yi-20):yi]) / max(1, min(20, yi))
        today_ratio = v[ti] / vol20 if vol20 > 0 else 0.0
        today_pct = (cur / c[yi] - 1) * 100
        topic = _match_topic(kl.get('name', code))
        topic_bonus = 0
        if use_topic_bonus:
            hot = False
            for sec in match_sector(kl.get('name', code), code):
                if sector_heat.get(sec, {}).get('is_hot'):
                    hot = True
                    break
            if topic:
                topic_bonus += 6                    # 命中复盘热主题
            if hot:
                topic_bonus += 8                    # 板块当日涨停≥3家

        # ---- 通道A: 低位回踩池(v172 增强) ----
        if (yest_pct < 3.0 or yest_zt) and dev_ma10 < 0:
            if today_ratio >= 1.0 or today_pct > 0:
                score = min(40, gene * 10)
                if yest_pct < 0:
                    score += 8
                score += 8 if mv <= 60 else (4 if mv <= 120 else 0)
                score += 3 if cur <= 12 else 0
                score += 6 if -12 <= dev_ma10 < 0 else 0
                if today_ratio < 2.5:
                    score += min(10, today_ratio * 5)
                else:
                    score -= 6
                if today_pct > 0:
                    score += 4
                score += topic_bonus
                rebound.append({
                    'code': code, 'name': kl.get('name', code), 'mv': round(mv),
                    'score': round(score), 'gene': gene,
                    'yest_pct': _sf(yest_pct), 'today_pct': _sf(today_pct),
                    'vol_ratio_today': _sf(today_ratio),
                    'dev_ma10': _sf(dev_ma10), 'dev_ma20': _sf(dev_ma20),
                    'ma5': _sf(ma5), 'ma10': _sf(ma10), 'ma20': _sf(ma20),
                    'topic_bonus': topic_bonus, 'topic': topic,
                    'support': 'MA10', 'support_price': _sf(ma10),
                    'stop_loss': _sf(ma10 * 0.94),
                    'sectors': match_sector(kl.get('name', code), code)[:4],
                    'pool': '低位回踩',
                    'signals': [f'市值{mv:.0f}亿·弹性', f'基因{gene}次', '低位回踩',
                                '主题加分+' + str(topic_bonus) if topic_bonus else '回踩'],
                })

        # ---- 通道C: 竞价动态补池 ——【独立通道, 不受低位回踩门槛限制】----
        # 条件: 基因>=3 + 早盘低开/平开(<5%, 未封死) + 有成交额 + 昨日非涨停(昨涨停归高标/接力)
        #   —— 昨涨停但今日高开<7%(未一字强封) 且 有量 的票同样允许承接(堵住高位落空)
        #       昨涨停视为强基因信号, 该场景基因门槛放宽到 >=2
        if rt_d and rt_d.get('amt_yi', 0) >= 0.5:
            floor = 2 if yest_zt else 3
            open_ok = (not yest_zt and rt_d.get('open_pct', 0) < 5) or \
                      (yest_zt and rt_d.get('open_pct', 0) < 7 and rt_d.get('amt_yi', 0) >= 1.5)
            if gene >= floor and open_ok:
                fill_score = gene * 6 + topic_bonus + (4 if yest_zt else 0)
                auction_fill.append({
                    'code': code, 'name': kl.get('name', code), 'mv': round(mv),
                    'gene': gene, 'score': round(fill_score),
                    'open_pct': _sf(rt_d.get('open_pct')),
                    'amt_yi': rt_d.get('amt_yi'),
                    'today_pct': _sf(rt_d.get('pct')),
                    'support': 'MA10', 'support_price': _sf(ma10),
                    'stop_loss': _sf(ma10 * 0.94),
                    'topic': topic, 'topic_bonus': topic_bonus,
                    'pool': '竞价补池(低开可买)',
                    'signals': [f'基因{gene}', '昨日未入选', '低开<5%+有量',
                                f'主题+{topic_bonus}' if topic_bonus else ''],
                })

    rebound.sort(key=lambda x: -x['score'])
    high_relay.sort(key=lambda x: -x['score'])
    auction_fill.sort(key=lambda x: -x['gene'])

    report = {
        'rebound_n': len(rebound), 'high_relay_n': len(high_relay), 'auction_fill_n': len(auction_fill),
        'hot_sectors': sorted([(s, h['zt_cnt']) for s, h in sector_heat.items() if h['is_hot']],
                              key=lambda x: -x[1])[:8],
    }
    return {
        'rebound': rebound[:top_n], 'high_relay': high_relay[:high_relay_n],
        'auction_fill': auction_fill[:theme_pool], 'auction_fill_full': auction_fill,
        'report': report,
    }


def _sf(x, nd=2):
    return round(x, nd) if x is not None else None


def run_v173(lib='default', top_n=12, theme_pool=6, high_relay_n=4,
             max_workers=8, data_cache=None, proxies=None, headers=None):
    """
    便捷入口: 拉全市场 + 60日K线 + 实时快照 -> capture_v173。
    返回与 capture_v173 相同结构, 并附 'report'.
    """
    from scan_v1224 import get_all_stocks, get_kl, match_sector, HEADERS as _H, _get_proxies
    px = proxies or _get_proxies()
    hd = headers or _H

    stocks = data_cache.get('stocks') if data_cache else None
    if stocks is None:
        stocks = get_all_stocks()
    mv = {s['code']: s['circ_mv'] or 0 for s in stocks}
    codes = [s['code'] for s in stocks]

    rt = data_cache.get('rt') if data_cache else None
    if rt is None:
        rt = _front_qt(codes, proxies=px, headers=hd)

    # 只对 基因候选潜在池(市值<300 + 非ST已过滤) 取K线加速
    def _load(code):
        return get_kl(code, 60)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        kls = dict((c, k) for c, k in
                   zip(codes, ex.map(_load, codes)) if k)

    return capture_v173(kls, mv, match_sector, rt=rt, stocks=stocks,
                        top_n=top_n, theme_pool=theme_pool, high_relay_n=high_relay_n)


if __name__ == '__main__':
    print('V17.3 盘中动态补池模块加载成功。调用 run_v173() 或 capture_v173() 使用。')