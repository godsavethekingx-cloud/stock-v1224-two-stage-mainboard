#!/usr/bin/env python3
"""
Postmortem 0910 复盘优化模块 (6 项落地)
================================================================
基于 9/9收盘 -> 9/10早盘 四报告深度复盘结论, 落地 6 项系统性优化。
本模块作为 run_daily/run_v18 的可选增强层注入, 不动原有四通道核心递进逻辑。

【优化A】消息面注入 "政策发布会日历 + 大宗商品价格异动反推周期板块"
  教训: 9/10 漏掉"四部门金融发布会"(15:00)与"国际油价破百 -> 煤炭/周期涨价"主线。
  实现:
    A1. policy_calendar(): 盘前抓取当日/近3日国新办、部委、四部门发布会日历, 映射到
        (利好板块, 强度, 描述)。网络失败/无条目时返回内置近瞻预案兜底。
    A2. commodity_cycle_sectors(): 抓取油价/金/煤/铜等商品涨跌阈值, 异动即反推对应
        周期板块(煤炭/油气/贵金属/有色/化工), 返回追加的 event_sectors + 强度。
  两者输出合并进 event_sectors, 并写入 report 的 policy_events / cycle_sectors 字段。

【优化B】宏观数据日历自动对时校验
  教训: 把 9/9 已发布的 8月CPI/PPI 误标为 9/10 上午, 导致周期涨价逻辑放置错时点。
  实现: 内置可靠的经济数据发布日历(名称+计划日期), 自动对时: 已过期的标注[已发布]+
      实际影响提示, 未到的保留; 从 roots 反推"距最近风险事件天数"提升 macro_risk。

【优化C】主线置信"寿命衰减"
  教训: 把"昨日涨停数第一的农业"当延续主线, 次日却集体高开兑现回落(均值回归)。
  实现: 传昨日板块涨停统计(zt_cnt_yest), 对昨日涨停>=C_LIFE_ZTCNT 的板块:
    - 若今日该板块未继续明显涨停/未大涨 => 延续置信衰减(c_score *= 衰减), 降 event 权重
    - 在 report 标记 fatigated_sectors
  未提供昨日数据时自动降级(不阻断)。

【优化E】当日竞价确认权重设最高 / 隔夜静态池降黑名单
  教训: 盘中按量比+竞价强度修正选出的票(精艺+6.4/大有+4/出版+4)胜率远高于隔夜静态池。
  实现: 对四个形态通道结果, 依实时 rt 重排打分, 赋予"当日竞价确认分"; 把昨日静态池
      中未获当日确认的票标记 blacklist 候选(降低置信, 不作为最高优先)。

【优化F】固化"高位放量滞涨 -> 次日减仓"逃顶模板
  教训: 杭电 9/9 高位巨量滞涨(换手24%、振幅13%、冲板回落), 9/10 如期 -5.48%。
  实现: detect_top_exit(all_kls, rt, ...): 识别换手>=20% + 振幅>=12% + 冲板回落 +
      量比偏高 + 当日接近涨停后回撤>4% 的高位滞涨股 => 生成逃顶警告(独立于选股)。
"""

import time, json, re, requests
from datetime import datetime, timedelta

# ============ 优化A1: 政策/发布会日历 ============

# 兜底预案: 网络不可用时可注入的基础政策日历(近3日), 覆盖"国新办/部委发布会"常见形态。
# (仅为离线降级兜底, 线上优先抓实时新闻)
POLICY_FALLBACK = [
    # (板块加词, 强度0~100, 标题)
    # 标记: 抓不到时不会凭空强推, 仅作文本提示; 真正驱动靠 commodity + 上层消息面。
]

# 部委发布会关键词 -> 常用板块映射 (与系统 sector 命名对齐)
POLICY_TO_SECTOR = {
    '金融': ['银行', '非银金融', '证券'], '数字货币': ['数字货币', '金融科技'],
    '新能源': ['光伏', '储能', '风电'], '光伏': ['光伏'], '储能': ['储能'],
    '人工智能': ['人工智能', '算力'], '算力': ['算力', 'CPO光通信'],
    '芯片': ['半导体', '芯片'], '半导体': ['半导体'],
    '房地产': ['房地产'], '粮食': ['农业', '种业', '粮食'], '农业': ['农业', '种业'],
    '军工': ['军工'], '低空': ['低空经济'], '机器人': ['机器人'],
    '中医药': ['中药'], '医药': ['医药', '创新药'], '创新药': ['创新药'],
    '消费': ['消费', '零售'], '出口': ['外贸', '跨境电商'], '家电': ['家电'],
    '基建': ['基建', '工程机械'], '水利': ['水利'],
}


def policy_calendar(days=3, proxies=None, headers=None):
    """
    尝试抓取当日/近days日政策发布会线索, 返回 [(sector_list, strength, title)]。
    网络失败或空则返回 [] (不硬造)。真正兜底交给上层 event_sectors 传入。
    """
    out = []
    try:
        # 例: 中国政府网 / 国新办发布会预告(简化抓取 scio.gov.cn 发布会预告)
        url = 'http://www.scio.gov.cn/xwfbh/zxdt/index.htm'
        kw = ['新闻发布会', '发布会', '国新办', '发改委', '商务部', '工信部',
              '央行', '金融监管', '证监会', '财政部', '外汇局']
        r = requests.get(url, timeout=8, proxies=proxies,
                         headers=(headers or {'User-Agent': 'Mozilla/5.0'}))
        r.encoding = 'utf-8'
        for line in r.text.split('\n'):
            for k in kw:
                if k in line:
                    hit = [k]
                    secs = []
                    for bk in POLICY_TO_SECTOR:
                        if bk in line:
                            for s in POLICY_TO_SECTOR[bk]:
                                if s not in secs:
                                    secs.append(s)
                    if secs:
                        out.append((secs, 60 if '金融' in line else 45, line.strip()[:60]))
                    break
            if len(out) >= 5:
                break
    except Exception:
        pass
    # 去重
    seen = set(); final = []
    for secs, st, title in out:
        key = tuple(secs)
        if key in seen:
            continue
        seen.add(key); final.append((secs, st, title))
    return final


def policy_to_event_sectors():
    """折中方案: 返回一个由政策兜底映射出的候选板块列表(供 merge event_sectors 参考)。
    强度<阈值则不强推, 仅作提示。"""
    cand = []
    try:
        for secs, st, title in policy_calendar(days=3):
            cand.append({'sectors': secs, 'strength': st, 'title': title})
    except Exception:
        pass
    return cand


# ============ 优化A2: 大宗商品价格异动 -> 周期板块 ============

COMMODITY_API = {
    # (显示名, 抓取方式) : 阈值, 强度, 对应板块(系统命名)
    'oil':      dict(threshold_low=-2.5, threshold_up=2.0,  up_strength=85, down_strength=60,
                     up_sectors=['石油', '煤炭', '油气开采'], down_sectors=['航空'],
                     name='国际油价'),
    'gold':     dict(threshold_up=0.8, up_strength=70, down_strength=45,
                     up_sectors=['贵金属', '黄金'], down_sectors=['黄金'],
                     name='黄金现货'),
    'coal':     dict(threshold_up=2.0, up_strength=80, down_strength=55,
                     up_sectors=['煤炭'], down_sectors=[],
                     name='动力煤'),
    'copper':   dict(threshold_up=1.0, up_strength=65, down_strength=40,
                     up_sectors=['有色金属', '铜'], down_sectors=[],
                     name='铜价'),
}


def _fetch_quote_map(codes):
    """腾讯行情(qt.gtimg.cn)批量抓取, 返回 {symbol: {pct, price, open, hi, lo, amp}}.
    codes: 如 'hf_GC','hf_CL','nf_ZC0','nf_CU0' (腾讯代码). 失败自动降级为空."""
    out = {}
    if not codes:
        return out
    try:
        import requests as _rq
        r = _rq.get('https://qt.gtimg.cn/q=' + ','.join(codes), timeout=8,
                    headers={'User-Agent': 'Mozilla/5.0'})
        r.encoding = 'gbk'
        for line in r.text.split('\n'):
            line = line.strip()
            if not line or '="' not in line:
                continue
            sym = line.split('=')[0].replace('v_', '')
            body = line.split('="', 1)[1].rstrip('";')
            p = body.split('~')
            if len(p) < 40:
                continue
            try:
                price = float(p[3]); prev = float(p[4])
                pct = (price / prev - 1) * 100 if prev else 0.0
                opn = float(p[5]); hi = float(p[33]); lo = float(p[34])
                amp = (hi - lo) / prev * 100 if prev else 0.0
                out[sym] = {'price': price, 'pct': round(pct, 2), 'open': opn,
                            'hi': hi, 'lo': lo, 'amp': round(amp, 2)}
            except Exception:
                continue
    except Exception:
        pass
    return out


def commodity_cycle_sectors(proxies=None, headers=None):
    """
    抓大宗商品价格, 异动反推周期板块。多源降级:
      源1: 腾讯期货(hf_/nf_ 原油黄金铜等)  ← 沙盒可通, 优先
      源2: 东财商品板块(fs=m:90+t:3)
    返回:
      add_sectors  : list[str] 追加进 event_sectors
      notes        : list[dict{sector_name,pct,strength,detail}] 供可视化
    """
    add_sectors = []
    notes = []
    # ---- 源1: 腾讯期货 ----（交易时段外可能返回空, 走源2）
    qt_map = _fetch_quote_map(['hf_GC', 'hf_CL', 'hf_CU', 'hf_SI', 'hf_RB',
                               'nf_GC0', 'nf_CL0', 'nf_CU0'])
    name_map_qt = {'GC': 'gold', 'CL': 'oil', 'CU': 'copper',
                   'SI': 'copper', 'RB': 'coal'}
    for sym, q in qt_map.items():
        base = sym.split('_')
        tag = (base[1] if len(base) > 1 else sym)[:2].upper()
        key = name_map_qt.get(tag)
        if not key or q.get('pct') is None:
            continue
        pct = q['pct']; cfg = COMMODITY_API[key]
        if pct >= cfg.get('threshold_up', 99):
            add_sectors.extend(cfg['up_sectors'])
            notes.append({'commodity': cfg['name'], 'pct': pct, 'strength': cfg['up_strength'],
                          'detail': ' '.join(cfg['up_sectors']) + f' 异动上涨{pct:+.1f}%'})
        elif pct <= cfg.get('threshold_low', -99):
            add_sectors.extend(cfg.get('down_sectors', []))
            notes.append({'commodity': cfg['name'], 'pct': pct, 'strength': cfg.get('down_strength', 40),
                          'detail': ' '.join(cfg.get('down_sectors', [])) + f' 回落{pct:+.1f}%'})
    # ---- 源2: 东财商品板块 (降级备选) ----
    if not notes:
        try:
            url = ('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=20&po=1&np=1'
                   '&fltt=2&invt=2&fid=f3&fs=m:90+t:3&fields=f2,f3,f14')
            r = requests.get(url, timeout=7, proxies=proxies,
                             headers={'Referer': 'https://quote.eastmoney.com/'})
            rows = (r.json().get('data') or {}).get('diff') or []
            em_map = {'原油': 'oil', '黄金': 'gold', '铜': 'copper', '动力煤': 'coal'}
            for row in rows:
                nm = str(row.get('f14', '')); pct = row.get('f3')
                if pct is None:
                    continue
                key = next((em_map[k] for k in em_map if k in nm), None)
                if not key:
                    continue
                cfg = COMMODITY_API[key]
                if pct >= cfg.get('threshold_up', 99):
                    add_sectors.extend(cfg['up_sectors'])
                    notes.append({'commodity': cfg['name'], 'pct': round(float(pct), 1),
                                  'strength': cfg['up_strength'],
                                  'detail': ' '.join(cfg['up_sectors']) + f' 异动上涨{float(pct):+.1f}%'})
        except Exception:
            pass
    # 去重
    seen = set(); add_sectors2 = []
    for s in add_sectors:
        if s not in seen:
            seen.add(s); add_sectors2.append(s)
    return add_sectors2, notes


# ============ 优化B: 宏观经济数据日历自动对时校验 ============

# 内置经济日历(名称, 计划发布时间)。"auto" 表示由上层 news 动态追加。
# 这里标注 2026 年常规发布节奏; 真实精确日期可在上层覆盖。
ECON_CALENDAR = [
    # (名称, 计划日期'YYYY-MM-DD', 影响板块, 节假日无交易标记)
    ('中国8月CPI/PPI', '2026-09-09', ['消费', '农业', '周期'], '已发布'),
    ('美国8月CPI', '2026-09-11', ['贵金属', '美元', '周期'], '审判日'),
    ('美国8月PPI', '2026-09-12', ['周期', '贵金属'], ''),
    ('美联储FOMC 9月决议', '2026-09-17', ['周期', '成长'], ''),
    ('中国8月PMI', '2026-08-30', ['周期'], '已发布'),
]


def calendar_autocheck(today=None):
    """
    返回:
      recent: 近期已发布影响项 [dict]
      upcoming: 未来待公布 [dict]
      next_days: 距最近待公布风险日的自然日数 (None=未知)
      risk_note: str 提示
    """
    today = today or datetime.now().date()
    recent = []
    upcoming = []
    for name, ds, secs, note in ECON_CALENDAR:
        try:
            d = datetime.strptime(ds, '%Y-%m-%d').date()
        except Exception:
            continue
        if d <= today:
            recent.append({'name': name, 'date': ds, 'sectors': secs,
                           'note': note if d == today else ''})
        else:
            upcoming.append({'name': name, 'date': ds, 'sectors': secs,
                             'note': note, 'days': (d - today).days})
    upcoming.sort(key=lambda x: x['days'])
    next_days = upcoming[0]['days'] if upcoming else None
    risk_note = ''
    if next_days is not None and next_days <= 3:
        risk_note = f"距 {upcoming[0]['name']}({upcoming[0]['date']}) 还有 {next_days} 天, 风险窗口临近"
    return {'recent': recent, 'upcoming': upcoming, 'next_days': next_days,
            'risk_note': risk_note}


def macro_risk_from_calendar(today=None):
    """由日历反推 macro_risk: 'high' if 最近风险事件<=2天 且含审判日级(CPI/FOMC)."""
    info = calendar_autocheck(today)
    if info['next_days'] is not None and info['next_days'] <= 2:
        # 若最近事件是 CPI/FOMC 等审判级
        for u in info['upcoming']:
            if u['days'] == info['next_days'] and any(k in u['name'] for k in ['CPI', 'FOMC', 'PPI']):
                return 'high', info
        return 'medium', info
    return 'low', info


# ============ 优化C: 主线置信寿命衰减 ============

C_LIFE_ZTCNT = 3          # 昨日涨停>=3 记为"强板块"
C_LIFE_DECAY = 0.55       # 今日未延续时的延续置信衰减系数
C_LIFE_HARD = 2.5         # 衰减后仍提升 event 所需的最低涨停(今日) 参考


def fatigate_main_lines(event_sectors, rt=None, match_sector=None, name_to_code=None,
                        zt_cnt_yest=None, zt_cnt_today=None):
    """
    对 event_sectors 中"昨日已强(涨停>=C_LIFE_ZTCNT)但今日未延续"的板块实施寿命衰减。
    入参:
      zt_cnt_yest : {sector: int} 昨日各板块涨停家数(来自上层/上一基日)
      zt_cnt_today: {sector: int} 今日各板块涨停家数(可由 rt+match_sector 统计)
    返回:
      decayed      : list[str] 被寿命衰减的板块(仍保留 event 权重但降级)
      keep         : list[str] 正常延续的板块
      note         : list[str]
    """
    if not zt_cnt_yest:
        return [], list(event_sectors or []), ['无昨日板块涨停数据, 寿命衰减跳过']
    decayed = []; keep = []; note = []
    for sec in (event_sectors or []):
        y = zt_cnt_yest.get(sec, 0)
        if y >= C_LIFE_ZTCNT:
            t = zt_cnt_today.get(sec, 0) if zt_cnt_today else 0
            # 今日若明显继续(>=C_LIFE_HARD)则延续, 否则寿命衰减
            if t >= C_LIFE_HARD:
                keep.append(sec)
            else:
                decayed.append(sec)
                note.append(f"寿命衰减:{sec}(昨涨停{y}家,今涨{t}家,均值回归)")
        else:
            keep.append(sec)
    return decayed, keep, note


def apply_life_penalty(event_sectors, fatigated_list, penalty=4):
    """
    优化C 落点: 对被"寿命衰减"标记且仍在 event_sectors 中的板块施加题材降权(等价
    于 capture_v18 里 weak_main 的自然衰减)。返回 (维持权重板块, 降权板块, 说明)。
    """
    low = [s for s in (event_sectors or []) if s in (fatigated_list or [])]
    norm = [s for s in (event_sectors or []) if s not in (fatigated_list or [])]
    if low:
        note = f"寿命衰减降权: {'/'.join(low)} (昨日强今日未续, 题材权重-{penalty})"
    else:
        note = "无衰减板块重叠, 事件权重维持"
    return norm, low, note


# ============ 优化E: 当日竞价确认权重 / 隔夜静态池降黑名单 ============

AUCTION_CONFIRM_BONUS_TOP = 25      # 当日竞价确认加分上限
AUCTION_BLACKLIST_MARK = -15        # 未获当日确认的隔夜票降权

# ---- [2026-09-14 开盘高开过滤纪律 (用户回测驱动)] ----
# 依据: 9/14 推荐回测 --- 开盘冲高<=+2% 的 7 只胜率100% 且含2涨停(豫能/键邦均低开拉板);
#       高开>+2% 的票里出现 +2.06%开盘收-0.63% 的高开回落。高开追单是被套主因。
# 规则:
#   OPEN_OK_MAX   <= +3%   => 可介入(不扣减, 竞价确认保留)
#   OPEN_WATCH_MAX <= +5%  => 降为"观察", 扣分且去掉竞价确认
#   开盘 > +5%            => "高开莫追", 强降分(隔夜高标后高风险追单)
#   低开/平开且温和(<+1%) + 放量(量比>=1.2) + 盘中红盘 => 安全买点加分(最稳形态)
OPEN_OK_MAX = 3.0        # 开盘冲高 <=3% 视为可介入
OPEN_WATCH_MAX = 5.0     # 开盘冲高 3%~5% 降为观察
OPEN_SAFE_REW = 6        # 低开/平开+放量+红盘 -> 安全买点加分
OPEN_HIGH_PENALTY = -10  # 开盘 >5% 高开追高风险扣分
OPEN_WATCH_PENALTY = -5  # 开盘 3%~5% 观察扣分
OPEN_SAFE_VOLR = 1.2     # 安全买点所需的量比下限


def fetch_realtime_stock_quotes(codes):
    """
    用腾讯行情(qt.gtimg.cn)抓某批股票实时快照, 返回适合 auction_confirm_reweight 的 rt 结构:
      {code: {pct, open_pct, vol_ratio}}
    腾讯行情字段: 5开,6收,7最高,8最低,10量,31涨跌,32涨跌%,33最高,34最低,36成交量,37成交额,38换手,49量比,5x。
    失败自动降级为空(不阻断)。
    """
    rt = {}
    if not codes:
        return rt
    codes = list(dict.fromkeys(codes))  # 去重保序
    # 腾讯代码: sh/sz 前缀
    def mk(c):
        c = str(c)
        return ('sh' if c.startswith(('60', '68', '90')) else 'sz') + c
    try:
        codes_t = [mk(c) for c in codes]
        # 批量(每批40)避免URL过长
        import requests as _rq
        for i in range(0, len(codes_t), 40):
            batch = codes_t[i:i + 40]
            r = _rq.get('https://qt.gtimg.cn/q=' + ','.join(batch), timeout=8,
                        headers={'User-Agent': 'Mozilla/5.0'})
            r.encoding = 'gbk'
            for line in r.text.split('\n'):
                line = line.strip()
                if not line or '="' not in line:
                    continue
                sym = line.split('=')[0].replace('v_', '').lower()
                body = line.split('="', 1)[1].rstrip('";')
                p = body.split('~')
                if len(p) < 50:
                    continue
                code = sym[2:]  # 去掉 sh/sz
                try:
                    price = float(p[3]); prev = float(p[4]); opn = float(p[5])
                    pct = (price / prev - 1) * 100 if prev else 0.0
                    open_pct = (opn / prev - 1) * 100 if prev else 0.0
                    volr = float(p[49]) if p[49] else 0.0
                    rt[code] = {'pct': round(pct, 2), 'open_pct': round(open_pct, 2),
                                'vol_ratio': volr}
                except Exception:
                    continue
    except Exception:
        pass
    return rt


def auction_confirm_reweight(pool, rt=None, is_auction_runtime=False):
    """
    把某个池子(如 forward / rebound)的条目按"当日竞价/盘中确认"重排打分。
    规则:
      - 有实时 rt 且今日放量上涨(量比>=1.2 且 today_pct>=0) => 加分置信;
      - 开盘冲高/资金注入(open_pct 温和+今日红盘) => 视为竞价确认;
      - 无实时确认 / 今日走弱 => 标记 blacklist 候选(降权), 不作为最高优先。
    [2026-09-14 新增] 开盘高开过滤纪律(用户回测驱动):
      - 开盘冲高 <=+3%  => 可介入(不扣减)
      - 开盘冲高 3%~5% => 观察扣分, 去掉竞价确认
      - 开盘冲高 >+5%  => 高开莫追强降分
      - 低开/平开温和(<=+1%) + 放量(量比>=1.2) + 盘中红盘 => 安全买点加分
      输出: 条目标签加入 OPEN_OK / OPEN_WATCH / OPEN_HIGH / OPEN_SAFE, 便于推荐界面展示可操作状态。
    """
    out = []
    for x in (pool or []):
        x = dict(x)
        code = x.get('code')
        base = x.get('score', 0)
        conf = x.get('conf_bonus', 0)
        flag = ''
        rd = None
        if rt and code:
            rd = rt.get(code)
        if rd:
            pct = rd.get('pct')
            volr = rd.get('vol_ratio')
            open_pct = rd.get('open_pct')
            open_flag = None      # 可操作状态标签
            # ── [新增] 开盘高开过滤纪律 ──
            if open_pct is not None:
                if open_pct <= OPEN_OK_MAX:
                    # 低开/平开温和 + 放量 + 红盘 => 最稳安全买点
                    if open_pct <= 1.0 and volr and volr >= OPEN_SAFE_VOLR and pct is not None and pct >= 0:
                        conf += OPEN_SAFE_REW
                        open_flag = 'OPEN_SAFE'
                    else:
                        open_flag = 'OPEN_OK'
                elif open_pct <= OPEN_WATCH_MAX:
                    conf += OPEN_WATCH_PENALTY          # 3%~5% 观察
                    open_flag = 'OPEN_WATCH'
                else:
                    conf += OPEN_HIGH_PENALTY           # >5% 高开莫追
                    open_flag = 'OPEN_HIGH'
            # ── 原有竞价确认(仅对开盘不追高的票保留) ──
            if pct is not None:
                if volr and volr >= 1.2 and pct >= 0 and open_pct is not None and open_pct <= OPEN_OK_MAX:
                    conf += min(AUCTION_CONFIRM_BONUS_TOP, int((volr - 1.0) * 12) + (4 if pct >= 3 else 2))
                    flag = '竞价确认'
                elif pct < -3:
                    conf -= 12
                    flag = '今日转弱'
            if open_flag:
                flag = flag or open_flag
        is_auction = bool(rd and (rd.get('vol_ratio') or 0) and (rd.get('pct') or 0) >= 0)
        if is_auction_runtime and not is_auction:
            conf -= 6
        x['score'] = base + conf
        x['conf_bonus'] = conf
        if open_pct is not None:
            x['open_pct'] = round(open_pct, 2)
        if flag:
            x['signals'] = x.get('signals', []) + [flag]
        out.append(x)
    out.sort(key=lambda z: -z.get('score', 0))
    # 追加一行辅助: 附带可操作状态字段到每个条目
    return out


# ============ 优化F: 高位放量滞涨 -> 逃顶模板 ============

def detect_top_exit(all_kls, rt=None, circ_mv_map=None,
                    turnover_pct=None, turnover_map=None):
    """
    识别"高位放量滞涨 / 冲板回落"的高危换手股, 生成逃顶警告。
    特征(杭电9/9案例):
      - 当日换手>=20% 且 振幅>=12% 且 冲高后回撤大(收盘距日内高>=5%)
      - 或 换手>=24% 且 收盘接近涨停但炸板回落
    入参: 可传 turnover_map {code:%}; 未传则尝试用 rt 反推。
    返回: [ {code,name,reason,risk} ... ] 独立于选股池的逃顶警告。
    """
    warns = []
    if not rt:
        return warns
    for code, rd in rt.items():
        if not rd:
            continue
        kl = all_kls.get(code)
        name = (kl.get('name', code) if kl else code)
        pct = rd.get('pct')
        hi = rd.get('high_pct')      # 或由 k线推断
        open_pct = rd.get('open_pct')
        turn = None
        if turnover_map:
            turn = turnover_map.get(code)
        mv = (circ_mv_map or {}).get(code, 0) or 0
        if pct is None or mv <= 0:
            continue
        # 用 k线收盘/最高反推当日振幅与距高点回撤
        amp = 0; retr = 0; cur = None
        if kl and kl.get('c'):
            c = kl['c']; h = kl.get('h'); ti = len(c) - 1
            cur = c[ti]
            prev = c[ti - 1] if ti >= 1 else cur
            hi_today = h[ti] if h and len(h) > ti else cur
            if prev > 0:
                amp = (hi_today - (kl.get('l', [prev])[ti] if len(kl.get('l', [prev])) > ti else hi_today)) / prev * 100
                retr = (hi_today - cur) / prev * 100
        if turn is None:
            if cur and mv > 0:
                v = kl['v'][ti] if kl and kl.get('v') and len(kl['v']) > ti else 0
                turn = (v / (mv * 1e8 / cur) * 100) if (mv > 0 and cur > 0) else 0
        # 核心判定
        cond_hi = amp >= 12 and retr >= 5     # 巨幅震荡 + 冲高回落
        cond_turn = turn and turn >= 20       # 换手爆表
        cond_close_zt = pct >= 8 and retr >= 4  # 近涨停炸板
        if (cond_hi and cond_turn) or (cond_close_zt and cond_turn):
            reason = []
            if turn >= 24: reason.append(f'换手{turn:.0f}%爆表(>24%)')
            elif cond_turn: reason.append(f'换手{turn:.0f}%高位')
            if cond_hi: reason.append(f'振幅{amp:.0f}%+回落{retr:.0f}%')
            if cond_close_zt: reason.append(f'冲板{pct:.0f}%炸板回落{retr:.0f}%')
            warns.append({'code': code, 'name': name, 'mv': round(mv),
                          'reason': '; '.join(reason) or '高位放量滞涨',
                          'risk': min(95, 60 + turn + amp), 'pct': _r(pct)})
    warns.sort(key=lambda x: -x['risk'])
    return warns[:20]


def _r(x, nd=2):
    try:
        return round(x, nd)
    except Exception:
        return x


# ============ 对外统一接口 ============

def run_postmortem_layer(all_kls=None, rt=None, circ_mv_map=None,
                         event_sectors=None, today=None,
                         zt_cnt_yest=None, zt_cnt_today=None,
                         turnover_map=None, proxies=None, headers=None,
                         top_exit=True):
    """
    一次性执行优化 A/B/C/E/F, 返回增强后的 event_sectors 与附加信息。
    用于 run_v18 / run_daily 在调用 capture_v18 前/后合并。
    """
    today = today or datetime.now().date()
    out = {}

    # A1+A2
    a1 = policy_to_event_sectors()
    a2_sectors, a2_notes = commodity_cycle_sectors(proxies, headers)
    out['policy_events'] = a1
    out['cycle_sectors'] = a2_sectors
    out['cycle_notes'] = a2_notes

    # B
    out['calendar'] = calendar_autocheck(today)
    mrisk, cal = macro_risk_from_calendar(today)
    out['calendar_macro_risk'] = mrisk

    # C (若提供昨日数据)
    decayed, keep, cnote = fatigate_main_lines(
        event_sectors, rt, zt_cnt_yest=zt_cnt_yest, zt_cnt_today=zt_cnt_today)
    out['fatigated_sectors'] = decayed
    out['life_note'] = cnote

    # F
    if top_exit and rt:
        out['top_exit'] = detect_top_exit(all_kls, rt, circ_mv_map,
                                          turnover_map=turnover_map)

    # A 合并: 商品周期板块追加进 event_sectors
    merged = list(event_sectors or [])
    for s in a2_sectors:
        if s not in merged:
            merged.append(s)
    out['event_sectors_merged'] = merged
    return merged, out


if __name__ == '__main__':
    import sys
    print('postmortem_optimize 0910 优化模块加载成功')
    print('  - policy_calendar / commodity_cycle_sectors : 优化A 消息面增强')
    print('  - calendar_autocheck / macro_risk_from_calendar : 优化B 日历校验')
    print('  - fatigate_main_lines : 优化C 主线寿命衰减')
    print('  - auction_confirm_reweight : 优化E 当日确认权重')
    print('  - detect_top_exit : 优化F 逃顶模板')