#!/usr/bin/env python3
"""
V18 复盘优化引擎 (2026-09-02 落地)
================================================================
基于 09-02 三组选股回测 + 非一字板涨停拆解 (37 只), 落地四项优化:

【优化1】弱市择时开关
  当日无任何板块涨停>=3家(is_hot) => 判定「弱市/分化日」。
  弱市时: 高标接力通道风险收紧(基因>=6 且 ≤10%仓), 仓位与打分向「盘中动能
  确认 + 反包」倾斜; 停止把隔日静态池当作主推。

【优化2】信号权重重构 - 早盘动能硬过滤 + 突破前高奖励
  用「今日已走强(现价-开盘 动能)」替代「明日预测」。反包通道要求:
  现价-开盘>=2% 且现价红盘(硬过滤)。
  前日(上一基日)突破前高 cotik 命中 => +12 加分(强弱市通用)。

【优化3】次日反包通道 (reversal 前瞻)
  语义: 选出【当日收阴或涨幅<3%】+ 涨停基因>=2 + 缩量回调(量比<=2.2) + 贴MA10企稳
  的标的, 预测【次日反包涨停】。即"今日弱→次日强"的前瞻信号, 而非纳当日已涨停的票。
  (依据: 09-02 非一字涨停 46% 来自前日收阴, 亦即"当日走弱、次日反包"规律)

【优化4】事件驱动板块加权
  外部传入当日催化板块(event_sectors, 来自消息面/地缘/政策), 命中板块
  => 全通道 +10 题材分, 并标记 event 标签。

输出结构兼容 v17.3, 新增: rebound_fan(反包通道) / weak_market(弱市标记) /
hot_sectors / event_sectors。新增(第五通道) leader: 主线放量启动龙头。

【0908 复盘·落地优化】 (优化1~4 + 05/06 见正文)
  优化03 主线资格闸门: buy_eligible/weak_main/cap_good
    - 伪主线(事件点中但板块无涨停/量比弱/技术中性) => 降权6分, 仅观察不主推
    - 资金一致性代理(cap_good): 板块涨停>=3 且 板块量比>=1.2
  优化04 反包通道弱市收紧: 主轴反包×0.7, 非主轴(无事件/热/技术)反包 -14
  优化05 事件主线放开"昨日必涨": 事件/热板块内昨日收跌/平开 => 主线程程+8反转分
  新增    第五通道 leader(主线放量启动龙头): 事件/热板块 + 实时涨幅3~9% + 量比>=1.5
          + 换手>=8% + 贴MA10, 盘中动态卡位当日放量启动龙头(补武汉凡谷/通宇类漏网)。
"""
import time, requests
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict

# 复用 v173 基础组件
from capture_v173 import (_front_qt, build_sector_heat, _match_topic,
                          TOPIC_HEAT_KW, LIMIT_MAIN, LIMIT_GEM)

EVENT_BONUS = 10        # 事件驱动板块加权
BREAKOUT_BONUS = 12     # 前日突破前高奖励
WEAK_MIN_GENE_HIGH = 6  # 弱市下高标接力基因下限
FAN_WEAK_GENE = 2       # 反包通道基因下限
FAN_MAX_TODAY = 3.0     # 反包: 当日(选股日)涨幅上限 —— 收阴或涨幅<3% 才入选

# ---- 事件补涨/农林牧渔因子 (新增, 09-04 复盘) ----
# 教训: 09-04 农林牧渔盘中突发拉升近4%(种业大会+政策加码+厄尔尼诺涨价+粮价预期),
# 涨停22家居首, 而系统因"纯技术回踩/反包"漏掉当日盘中突发的事件驱动板块.
# 优化1: 扩充"粮食/农业事件"为一级事件板块, 盘中突发拉升时对该板块内低位股给补涨分.
# 优化2: 盘中板块瞬时涨幅骤升(>=SPIKE_PCT 且板块内涨停数>=SPIKE_ZT) => 标记"盘中突发拉升",
#        将该板块内 尚未涨停(涨幅<LIMIT) 的低位/低基因 补涨候选提前纳入观察.
AGRI_EVENT_KW = ['种业','农业','粮食','农产品','饲料','养殖','猪肉','糖','棉','豆',
                 '种子','种植','厄尔尼诺','极端天气','粮价','土地流转']
SPIKE_PCT = 3.0          # 板块盘中涨幅>=3% 视为突发拉升
SPIKE_ZT = 2             # 且板块内涨停>=2家 确认热度
EVENT_PICK_BONUS = 8     # 突发拉升板块内低位补涨加分

# ---- 个股级公告催化加权 (新增, 09-07 复盘) ----
# 来源: announce_catalyst(新浪7x24 + 同花顺个股级公告)。对命中"S级/AB级重点公告"的主板
# 个股, 在四通道内叠加"公告分": 强重组(S>=90)自动优先, S>=80并购/扩产、S>=70战投/合同次之;
# 停牌/无量票只标注不硬推(避免"追公告但无买点")。与板块级 event_sectors 互补——后者是
# "板块层面+10", 这里是"个股层面按强度累加", 让新华传媒这类个股公告真正进入个股评分。
ANN_S90, ANN_S80, ANN_S70, ANN_SBASE = 35, 24, 15, 8   # 强度档 -> 公告分
ANN_SUSPEND_RATIO = 0.15            # 当日量比水位低于此 且 当日几乎无涨幅 => 停牌/无量
ANN_PICK_S = 90                     # 强公告直通"公告提示榜"的强度门槛(重大重组/借壳/控股权)

def ann_bonus_of(strength):
    """公告刺激强度 S -> 四通道内叠加的公告分."""
    if strength >= 90: return ANN_S90
    if strength >= 80: return ANN_S80
    if strength >= 70: return ANN_S70
    return ANN_SBASE

def _classify_em_title(title):
    """东方财富公告标题 -> (类型, 强度) 或 None(非催化). 精简分级, 剔除无交易价值的中介文本."""
    neg = ['问询函', '评估报告', '审计报告', '机构调研', '业绩说明会', '减持', '解禁', '质押',
           '立案', '警示', '终止并', '退市', '监事会', '职工代表大会', '招募说明书', '评级报告',
           '更正', '保荐', '核查意见', '律师事实', '法律意见书', '股东大会', '年审', '独立董事意见']
    if any(k in title for k in neg):
        return None
    # (关键词列表, 强度, 类型) —— 用子串匹配, 避免依赖 re 模块
    for kws, s, t in [
        (['发行股份购买资产', '发行股份及支付现金购买资产', '重大资产重组', '借壳', '重组上市', '控制权变更', '购买资产并募集配套资金'], 92, '资产注入/借壳/重组'),
        (['收购控股权', '拟收购', '现金收购', '收购标的', '收购股权', '收购公司', '并购重组'], 86, '收购/并购'),
        (['跨界', '切入AI', '切入算力', '切入机器人', '切入新能源', '切入光伏', '切入半导体', '切入储能', '切入数据要素'], 82, '跨界投资'),
        (['重大经营协议', '重大协议', '重大合同', '中标', '签署战略合作', '签署合作协议', '签署供货协议', '签署采购协议'], 78, '重大合同/订单'),
        (['拟投建', '扩产', '投资亿元', '新建产能', '新建项目', '投资建设项目'], 76, '扩产/项目投资'),
        (['股权转让', '协议转让', '引入战投', '战略投资者', '战略投资'], 72, '战投/股权转让'),
        (['业绩预增', '业绩预告', '回购注销', '注销注册资本', '中期分红', '增持计划', '回购股份', '回购进展', '回购公司'], 60, '业绩预增/回购注销'),
    ]:
        if any(k in title for k in kws):
            return (t, s)
    return None


def _build_announcement_map(stocks):
    """抓取个股级公告(东方财富主源 + 新浪7x24 + 同花顺补充), 分类为 {code:{type,strength,title}}.
    仅主板(60/00)。东财自带代码且覆盖周末盘后公告, 作为主源优先注入; 失败则退回新浪/同花顺。"""
    ann = {}
    def _upsert(code, a_type, strength, title):
        if not _is_mb_code(code):
            return
        cur = ann.get(code)
        if cur is None or strength > cur['strength']:
            ann[code] = {'type': a_type, 'strength': strength, 'title': (title or '')[:80]}
    # ── 主源: 东方财富公告披露(覆盖周五收盘后/周末盘后公告, 自带代码) ──
    try:
        import urllib.request, json
        HDR = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com/'}
        for page in (1, 2, 3):
            url = ('https://np-anotice-stock.eastmoney.com/api/security/ann'
                   '?sr=-1&page_size=100&page_index=%d&ann_type=A&client_source=web') % page
            try:
                d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=15))
            except Exception:
                break
            lst = d.get('data', {}).get('list', []) or []
            if not lst:
                break
            for a in lst:
                cs = a.get('codes')
                title = (a.get('title') or '').strip()
                if not cs or not title:
                    continue
                c = str(cs[0].get('stock_code') or '')
                if not _is_mb_code(c):
                    continue
                cl = _classify_em_title(title)
                if cl:
                    _upsert(c, cl[0], cl[1], title)
            if len(ann) >= 25:
                break
    except Exception:
        pass
    if ann:
        return ann
    nl = {}
    for s in stocks:
        nm = s.get('name') or ''
        c = str(s.get('code') or '')
        if nm:
            nl[nm] = c
    try:
        from announce_catalyst import fetch_ths_stock_news, fetch_sina_7x24, classify_announce
    except Exception:
        return ann
    def _upsert(code, a_type, strength, title):
        if not _is_mb_code(code):
            return
        cur = ann.get(code)
        if cur is None or strength > cur['strength']:
            ann[code] = {'type': a_type, 'strength': strength, 'title': (title or '')[:80]}
    # 同花顺: 自带代码字段, 最稳
    try:
        for code, name, txt, is_mb in fetch_ths_stock_news(pages=2):
            if not is_mb:
                continue
            cl = classify_announce(txt)
            if cl:
                _upsert(code, cl[0], cl[1], txt)
    except Exception:
        pass
    # 新浪: 文本匹配名称->代码
    try:
        for _ts, txt in fetch_sina_7x24(pages=2):
            if not any(k in txt for k in ['公告', '拟', '收购', '重组', '扩产', '跨界', '中标', '股权', '定增', '增持', '回购']):
                continue
            cl = classify_announce(txt)
            if not cl:
                continue
            hit = None
            for nm in sorted(nl, key=len, reverse=True):
                if len(nm) >= 3 and nm in txt:
                    hit = nl[nm]; break
            if hit:
                _upsert(hit, cl[0], cl[1], txt)
    except Exception:
        pass
    return ann

def _is_mb_code(code):
    code = str(code)
    return code.startswith('60') or code.startswith('00')

# ---- 监管/情绪退潮因子 (新增, 09-03 复盘) ----
# 教训: 监管重点监控情绪/高标/强基因连板股, 这些票在监管高压期是打压对象而非接力强度。
REG_CUM_WIN = 12        # 累计涨幅统计窗口(交易日)
REG_CUM_100 = 100.0     # 累计涨幅惩罚起点(%)
REG_CUM_180 = 180.0     # 严重爆炒阈值
REG_CUM_250 = 250.0     # 极端爆炒阈值
REG_STREAK_CRASH = 4.0  # 连板股当日回落<=-4%视为负反馈(崩塌)
REG_HARD_PEN = 60       # 硬剔除阈值: 监管名单/极端爆炒/连板崩塌
REG_RETREAT_HITS = 4   # 高标负反馈家数>=该值 => 全局"情绪退潮期"
# 已知监管/风险名单(code: 原因) —— 随盘后公告/重点监控新闻更新
REG_SUSPECT = {
    '603221': '异动+将停牌核查(250%爆炒)', '600721': '交易所重点监控',
    '002084': '停牌核查(高标总龙头)', '605179': '一字跌停+风险提示',
    '600540': '证监立案+问询', '003005': '减持', '601086': '减持+质押+提示炒作',
    '002418': '澄清无相关业务', '000017': '澄清/自查',
}


def detect_weak_market(sector_heat):
    """弱市判定: 无任何板块涨停>=3家."""
    hot = sorted([(s, h['zt_cnt']) for s, h in sector_heat.items() if h['is_hot']],
                 key=lambda x: -x[1])
    return (len(hot) == 0), hot


# ============ [2026-09-11 弱市集中事件主线 G 层] ============
# 痛点: 弱市+强主线(如 9/11 PCB/AI算力链13家、绿电10家)同时存在时, 原系统因
#   risk_high -> `if not risk_high` 直接跳过 high_relay, 且 emotional_retreat 时
#   `high_relay.clear()` 整体清空, 连不带"主线龙头/接力"两个最能抓涨停的技术通道,
#   结果弱市下回踩通道装了 23 只阴跌股, 而真正涨停的华胜天成/风语筑/闽东电力没被集中推。
# 策略: weak_focus=True 时——
#   1) high_relay 不再被 risk_high 直接跳过, 改为"事件/热板块内的高标接力"低仓参与(≤10%);
#   2) emotional_retreat 不再整体清空 high_relay, 而是仅保留事件主线/热板块内梯队, 压到极小仓;
#   3) 整体不扩展个股数量, 只把仓位/评分向事件主线集中(聚焦而非撒网)。


def _sf(x, nd=2):
    return round(x, nd) if x is not None else None


def _is20(code):
    return code.startswith('30') or code.startswith('688')


def capture_v18(all_kls, circ_mv_map, match_sector, rt=None, stocks=None,
                event_sectors=None, top_n=12, theme_pool=6, high_relay_n=4,
                macro_risk='low', weak_focus=False, focus_codes=None):
    # [weak_focus 白名单] 昨日涨停梯队代码(纯数字), 命中即视为"事件主线内", 强制参与接力评分。
    focus_codes = set(focus_codes) if focus_codes else set()
    """
    V18 主函数。
    入参:
      all_kls     : {code: kline}, kline 含 c/o/h/l/v (收盘/开盘/最高/最低/量)
      circ_mv_map : {code: 流通市值(亿)}
      match_sector: fn(name, code)->[sector]
      rt          : 实时快照 {code:{open_pct,pct,amt_yi,...}}
      stocks      : 全市场 [{code,name,circ_mv}]
      event_sectors: 当日催化板块 list[str] (可选, 消息面/地缘/政策)
    返回: dict, 结构兼容 v173, 新增 rebound_fan/weak_market/event_sectors.
    """
    event_sectors = event_sectors or []
    sector_heat = {}
    if stocks and rt:
        sector_heat = build_sector_heat(stocks, rt, match_sector)
    weak_market, hot = detect_weak_market(sector_heat)
    hot_set = {s for s, _ in hot}

    # ---- 板块技术面判定 (09-04 新增) ----
    # 用户要求: 锁定板块放量/异动/日线突破回踩/板块涨停家数, 反哺个股推荐。
    # st_map: {板块: {avg_pct, vol_ratio, break_ratio, pullback_ratio, zt_cnt, state,...}}
    # state: strong/ignite/breakout/pullback/decline/neutral -> 对命中个股加分/减分。
    from sector_tech import build_sector_tech, sector_tech_bonus
    st_map, st_order = build_sector_tech(stocks, rt, all_kls, match_sector)

    event_set = set(event_sectors)
    # 事件板块命中函数
    def ev_bonus(name, code):
        if not event_set:
            return 0, []
        hit = [s for s in match_sector(name, code) if s in event_set]
        return (EVENT_BONUS if hit else 0), hit

    # 个股级公告催化: 四通道内按刺激强度叠加"公告分"(见 loop 内 ann_bonus)。
    # 停牌/无量票只标注不硬推, 避免"追公告但无买点"。抓取失败降级为空, 不影响其他通道。
    ann_map = {}
    ann_suspended = []
    try:
        ann_map = _build_announcement_map(stocks)
    except Exception:
        ann_map = {}

    rebound = []      # 低位回踩池
    rebound_fan = []  # 反包通道(新增)
    high_relay = []   # 高标接力池
    auction_fill = [] # 竞价/动能补池
    leader = []       # <<0908 第五通道: 主线放量启动龙头>>
    holdable = []     # <<0909 前瞻四滤网: 可拿3日(T+2 溢价, 防"买了没涨还跌")>>
    retreat_hits = 0  # 高标负反馈计数(情绪退潮检测)
    reg_suppress = 0  # 监管/爆炒硬剔除计数(情绪退潮检测)
    # 宏观风险(非农/FOMC/CPI 等确定性事件临近) === 资金避险开关
    # 09-04 复盘: 今晚非农公布 -> 资金避险下杀, 系统此前未预估高位接力/当日走强股的崩塌风险。
    # risk_mode=high 时: 整体降仓位偏好, 聚焦防御板块 + 低位, 严打当日大涨/高位连板。
    risk_high = (macro_risk == 'high')
    risk_defense_pools = ['高股息', '银行', '贵金属', '公用事业']  # 常规防御
    # essence: 循环内先以初始值参与各通道, 循环结束后按累计计数重算真值(见末尾)
    emotional_retreat = False
    # [0911 weak_focus] 弱市集中事件主线: 关闭"整体清空高位接力"的一刀切, 只把仓位压到极小仓并保留事件梯队。
    weak_focus_local = weak_focus

    for code, kl in all_kls.items():
        mv = circ_mv_map.get(code, 0) or 0
        if not (0 < mv < 300):
            continue
        c = kl['c']; v = kl['v']; h = kl.get('h', c)
        n = len(c); ti = n - 1
        if ti < 26 or c[ti] * (v[ti] if ti < len(v) else 0) < 5000:
            continue
        yi = ti - 1
        if yi < 21 or c[yi - 1] <= 0:
            continue
        cur = c[ti]
        yest_pct = (c[yi] / c[yi - 1] - 1) * 100
        ma5 = sum(c[ti-4:ti+1]) / 5 if ti >= 4 else cur
        ma10 = sum(c[ti-9:ti+1]) / 10 if ti >= 9 else cur
        ma20 = sum(c[ti-19:ti+1]) / 20 if ti >= 19 else cur
        dev_ma10 = (cur / ma10 - 1) * 100 if ma10 > 0 else 0
        dev_ma20 = (cur / ma20 - 1) * 100 if ma20 > 0 else 0
        if dev_ma20 < -12:
            continue

        # 基因(截至前一日 yi) + 连板 + 昨日涨停
        gene = sum(1 for j in range(max(0, yi - 20), yi) if c[j] / c[j - 1] - 1 >= 0.095)
        streak = 0; tk = ti
        while tk >= 1 and c[tk] / c[tk - 1] - 1 >= 0.095:
            streak += 1; tk -= 1
        yest_zt = c[yi] / c[yi - 1] - 1 >= 0.095

        # 前日(基于 yi) 是否突破前高: 前一日收盘 > 之前20日最高(e·0.995)
        hi20_prior = max(h[max(0, yi-20):yi]) if len(h) > max(0, yi-20) else 0
        breakout = (hi20_prior > 0) and c[yi] >= hi20_prior * 0.995

        rt_d = (rt or {}).get(code)
        rt_pct = rt_d['pct'] if rt_d else yest_pct
        open_pct = rt_d['open_pct'] if rt_d else 0.0
        not_yiz = (rt_d['open_pct'] < (LIMIT_GEM if _is20(code) else LIMIT_MAIN) * 0.99) if rt_d else True
        mom = (rt_pct - open_pct) if rt_d else None   # 早盘动能: 现价-开盘

        vol20 = sum(v[max(0, yi-20):yi]) / max(1, min(20, yi))
        today_ratio = (v[ti] / vol20) if vol20 > 0 else 0.0
        today_pct = (cur / c[yi] - 1) * 100

        # 个股级公告催化分: 命中的非停牌/无量票 -> ann_bonus 叠加进四通道评分并打标签;
        # 停牌/无量由循环外的"公示提示榜"统一处理(这里不强塞进形态通道)。
        ann = ann_map.get(code)
        ann_bonus = 0
        ann_tag = ''
        # [2026-09-14 优化4] 公告催化的量比/开冲确认门槛。
        # 依据: 9/14 键邦股份(并购公告, 量比+开冲达标)兑现涨停, 而紫光/哈森/胜通(量比温和)未启动;
        #       追"只有公告但无量能启动"的票易买在启动前被套。故强度>=S80的重磅公告若当日
        #       量比>=ANN_CONFIRM_VOLR 且低开/温和开冲(<=+1%, 留有买点) => 视为"启动确认", 全额给分;
        #       否则量比/形态未确认时降为半档, 避免把"无量公告票"推成高优先。
        ANN_CONFIRM_VOLR = 1.5     # 公告启动确认所需量比下限
        ANN_STRONG_LOW = 80        # 强度>=此档视为重磅公告, 需要更严的启动确认
        _ann_ok_confirmed = False
        if ann and not (today_ratio < ANN_SUSPEND_RATIO and abs(today_pct) < 0.5):
            _s_ = ann['strength']
            # 量比/开冲确认: 量比>=1.5 且开冲温和(留有买点) 视为已启动
            if today_ratio >= ANN_CONFIRM_VOLR and open_pct is not None and open_pct <= 1.0:
                _ann_ok_confirmed = True
                ann_bonus = ann_bonus_of(_s_)
                ann_tag = f'公告S{_s_} {ann["type"]}·启动确认'
            else:
                # 未达启动确认: 重磅(>=S80)降半档, 常规较低档也降半档; 仍打标签但优先级下调
                ann_bonus = ann_bonus_of(_s_) // 2
                ann_tag = f'公告S{_s_} {ann["type"]}·待启动'

        # 主题加分 + 事件驱动加权
        topic = _match_topic(kl.get('name', code))
        topic_bonus = 0
        hot_flag = False
        for sec in match_sector(kl.get('name', code), code):
            if sec in hot_set:
                hot_flag = True; break
        if topic:
            topic_bonus += 6
        if hot_flag:
            topic_bonus += 8
        ev, ev_hit = ev_bonus(kl.get('name', code), code)
        # [weak_focus 白名单] 昨日涨停梯队代码(纯数字)命中 -> 视为事件主线内, 强烈参与
        _focus_hit = code in focus_codes
        if _focus_hit:
            ev_hit = ev_hit or ['涨停梯队']
            ev += 14 if weak_focus_local else 8
        topic_bonus += ev
        # 板块技术面(放量/异动/突破/回踩/涨停数) → 叠加个股加分
        secs = match_sector(kl.get('name', code), code)
        st_bonus, st_info = sector_tech_bonus(secs, st_map)
        topic_bonus += st_bonus

        # <<0908·优化03/06>> 主线资格闸门: 板块资金一致性 + 技术面状态 决定"可买"而非"仅观察"
        # 教训: 券商被 event_sectors 点中(涨停0家)仍列入"买得进"拖累版本C。事件板块只代表"消息面点中",
        # 不=真主线; 真正主线需板块有涨停聚集(资金一致)或技术面 ignite/breakout。
        best_state = None; best_zt = 0; cap_good = False
        for _sec in secs:
            _st = st_map.get(_sec)
            if not _st:
                continue
            if _st.get('zt_cnt', 0) > best_zt:
                best_zt = _st['zt_cnt']; best_state = _st.get('state')
            if _st.get('zt_cnt', 0) >= 3 and _st.get('vol_ratio', 0) >= 1.2:
                cap_good = True          # 资金一致性代理: 板块涨停>=3 且 板块量比>=1.2
        _strong = (best_state in ('ignite', 'breakout', 'strong'))
        buy_eligible = bool(ev_hit) or hot_flag or _strong
        weak_main = bool(ev_hit) and not hot_flag and not _strong   # 伪主线: 事件点中却无资金/热/技术验证
        if weak_main:
            buy_eligible = False
            topic_bonus -= 6             # 伪主线降权(仅观察不主推)

        # ============ 监管/情绪退潮因子 (新增) ============
        # 量化代理: 短中期累计涨幅(爆炒) + 连板高标负反馈(今日跳水)
        # + 已知监管名单(异动/重点监控/停牌核查/澄清/减持/立案) => 硬剔除
        reg_pen = 0; reg_reason = ''
        win = max(3, min(REG_CUM_WIN, ti))
        base = c[ti - win] if (win <= ti and c[ti - win] > 0) else 0
        cum = (cur / base - 1) * 100 if base > 0 else 0.0
        if cum >= REG_CUM_250:
            reg_pen += REG_HARD_PEN; reg_reason = f'累计{cum:.0f}%极端爆炒'
        elif cum >= REG_CUM_180:
            reg_pen += 30; reg_reason = f'累计{cum:.0f}%爆炒'
        elif cum >= REG_CUM_100:
            reg_pen += 12; reg_reason = f'累计{cum:.0f}%高位'
        if streak >= 2 and rt_pct is not None and rt_pct <= -REG_STREAK_CRASH:
            reg_pen += 30
            reg_reason = (reg_reason + '/' if reg_reason else '') + f'{streak}板负反馈'
            retreat_hits += 1
        if code in REG_SUSPECT:
            reg_reason = (reg_reason + '/' if reg_reason else '') + '监管:' + REG_SUSPECT[code]
            reg_pen += REG_HARD_PEN
        if reg_pen >= REG_HARD_PEN:          # 硬剔除: 全通道排除监管/极端爆炒/连板崩塌
            reg_suppress += 1
            continue

        # ============ 通道B: 高标接力池 (弱市收紧 + 宏观避险收紧) ============
        # [0911 weak_focus 重构] 原逻辑用"20日涨停次数 gene>=阈值"衡量高标, 但连板龙头基因天然低
        # (连续涨停占天数少, 如 闽东电力 gene=1/3天3板、瑞尔特 gene=2/4天4板、鼎信通讯 gene=1/3天3板),
        # dev_ma10 又常 +15%~+30% 触及高位区, 这些"最能连板"的梯队反而被 gene 门槛挡在接力池外。
        # 故集中主线模式下高标接力的入选核心改为「事件/热板块内的 ≥2连板(或首板强势启动)」,
        # gene 仅作加分、不再作硬门槛; 弱市/风险窗下用 mr 系数降仓、非主线参与收紧。
        _relay_core = (streak >= 2) or (streak >= 1 and gene >= 2)
        risk_gene_hard = 8 if risk_high else (WEAK_MIN_GENE_HIGH if weak_market else 5)
        _gene_ok = (gene >= risk_gene_hard) or (weak_focus_local and _relay_core)
        if _gene_ok and streak >= 2 and not_yiz:
            # 宏观避险期(非农/FOMC临近): 默认高标接力不参与; [weak_focus] 时允许"事件/热板块内"
            # 高标接力低仓参与(≤10%仓), 抓 闽东电力/鼎信通讯 这类主线上的连板龙头。
            _relay_open = (not risk_high) or (weak_focus_local and (ev_hit or hot_flag))

            # ---- [2026-09-14 优化2] 高标追高风控 ----
            # 依据: 9/14复盘——高标(连板>=3)若当日竞价/量能未获确认(高开低走/无量/动能转弱),
            #       次日追单极易被套(瑞尔特/鼎信/远望谷高位接盘)。这里分两级:
            #   level1: streak>=3 且今日确认不足(非红盘 或 量比<1 或 开盘高开>+3), 直接挡掉接力;
            #   level2: streak>=2 且今日仅微红(涨幅<3%)或量比偏低 => mr 降为 0.5(轻仓试探)。
            _high_tail = streak >= 3
            _confirm_ok = False
            if rt_d is not None:
                _confirm_ok = (rt_pct > 0) and (today_ratio >= 1.0) and (open_pct is not None and open_pct <= 3.0)
            if _high_tail and not _confirm_ok:
                _relay_open = False            # 高标(3板+)无当日确认 => 直接排除接力, 防高位接盘
                _confirm_note = '3板+无确认·高标风控'
            else:
                _confirm_note = ''
                if streak >= 2 and rt_d is not None and rt_pct < 3:
                    mr = 0.5                   # 温和连板但今日动能不足 => 降仓试探
                else:
                    mr = None                  # 标记未覆盖, 用下方默认 mr

            if _relay_open and not (weak_market and rt_pct < 0):   # 弱市要求今日红盘
                if mr is None:
                    mr = 0.6 if (weak_focus_local and risk_high) else 1.0   # 风险窗再降仓
                # 非主线高标: 常规模式按基因计分; 集中主线模式下仅保留事件/热板块内梯队
                _base = gene * 8 + streak * 6 + (8 if mv <= 60 else 4)
                if weak_focus_local:
                    _base += 10 if (ev_hit or hot_flag) else -16   # 主线梯队加分/散点接力重罚
                hr_score = int((_base + topic_bonus + ann_bonus - reg_pen) * mr)
                high_relay.append({
                    'code': code, 'name': kl.get('name', code), 'mv': round(mv),
                    'score': round(hr_score), 'gene': gene, 'streak': streak,
                    'today_pct': _sf(rt_pct), 'open_pct': _sf(open_pct),
                    'support': '分时5日线', 'support_price': _sf(ma5),
                    'stop_loss': _sf(ma5 * 0.95),
                    'sectors': match_sector(kl.get('name', code), code)[:3],
                    'pool': '高标接力' + ('(集中主线≤10%仓)' if weak_focus_local else '(弱市≤10%仓)'),
                    'ann_strength': ann['strength'] if ann else 0,
                    'ann_type': ann['type'] if ann else '',
                    'signals': [f'基因{gene}', f'{streak}连板', '非一字',
                                '弱市收紧' if weak_market else '',
                                '聚焦主线' if weak_focus_local else '',
                                '事件+' + '+'.join(ev_hit) if ev_hit else '',
                                ann_tag if ann_tag else ''],
                })

        # ============ 通道A: 低位回踩池 (弱市降权高基因小盘) ============
        if (yest_pct < 3.0 or yest_zt) and dev_ma10 < 0:
            if today_ratio >= 1.0 or today_pct > 0:
                if weak_market and today_pct <= -4:
                    continue                       # 弱市: 深跌自由落体不进回踩
                # 优化2: 弱市降权高基因(高基因不轮动反成杀跌重灾区)
                score = min((20 if weak_market else 40), gene * (5 if weak_market else 10))
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
                    score += 4 if not weak_market else 8    # 弱市更看重今日企稳转强
                if weak_market and today_pct < -1:
                    score -= 8
                if breakout:
                    score += BREAKOUT_BONUS          # 优化2: 前日突破前高
                # <<0908·优化05>> 事件主线放开"昨日必涨"窗口: 命中事件/热板块的票, 昨日收跌/平开
                # 今日突发放量 = 资金刚进场的强反转(0908: 武汉凡谷前日收跌、今日放量启动),
                # 主线内冲破严苛的连日形态约束, 给"主线反转分"(该逻辑在第五通道已有实时版, 这里补技术回踩版).
                if (ev_hit or hot_flag) and yest_pct < 2:
                    score += 8               # <<0908·优化05>> 事件主线反转分
                score += topic_bonus + ann_bonus - reg_pen
                sig = [f'市值{mv:.0f}亿·弹性', f'基因{gene}次', '低位回踩',
                       f'主题+{topic_bonus}' if topic_bonus else '',
                       ann_tag if ann_tag else '']
                if (ev_hit or hot_flag) and yest_pct < 2:
                    sig.append('事件主线反转(昨日平/跌)')
                if weak_main:
                    sig.append('伪主线·仅观察')
                if reg_reason:
                    sig.append('风控:' + reg_reason)
                if breakout:
                    sig.append('前日突破前高')
                rebound.append({
                    'code': code, 'name': kl.get('name', code), 'mv': round(mv),
                    'score': round(score), 'gene': gene,
                    'yest_pct': _sf(yest_pct), 'today_pct': _sf(today_pct),
                    'vol_ratio_today': _sf(today_ratio),
                    'dev_ma10': _sf(dev_ma10), 'dev_ma20': _sf(dev_ma20),
                    'ma5': _sf(ma5), 'ma10': _sf(ma10), 'ma20': _sf(ma20),
                    'breakout': breakout, 'topic': topic, 'topic_bonus': topic_bonus,
                    'buy_eligible': buy_eligible, 'weak_main': weak_main,
                    'ann_strength': ann['strength'] if ann else 0,
                    'ann_type': ann['type'] if ann else '',
                    'support': 'MA10', 'support_price': _sf(ma10),
                    'stop_loss': _sf(ma10 * 0.94),
                    'sectors': match_sector(kl.get('name', code), code)[:4],
                    'pool': '低位回踩', 'signals': sig,
                })

        # ============ 通道F: 次日反包通道(修正语义) ============
        # 反向定义: 选出【当日收阴或涨幅<3%】(当日弱) + 涨停基因 + 缩量回调 + 贴MA10企稳
        # 的标的, 预测【次日反包涨停】。即"今日弱→次日强"前瞻, 而非收今日已涨停的票。
        tday = rt_pct                       # 当日(选股日)涨幅
        if (gene >= FAN_WEAK_GENE and rt_d and tday is not None
                and tday < FAN_MAX_TODAY and today_ratio <= 2.2
                and -8 <= dev_ma10 <= 2 and dev_ma20 > -9):
            fs = gene * 8
            if tday < 0:
                fs += 8                     # 当日收阴蓄势
            else:
                fs += 3                     # 当日小阳(0~2.9% 待反包)
            if -5 <= dev_ma10 <= 1:
                fs += 10                    # 贴MA10企稳最佳
            if today_ratio <= 1.6:
                fs += 6                     # 缩量回调(蓄势)
            fs += 6 if mv <= 80 else (3 if mv <= 160 else 0)
            # <<0908·优化04>> 反包通道弱市收紧: 退潮期反包成功率高位崩塌(沃特/天洋/锦龙/旭光全灭),
            # 主轴反包打7折, 非主轴(无事件/非热/非主线技术)反包重罚——只保留事件/热板块内反包。
            if weak_market:
                if not (ev_hit or hot_flag or _strong):
                    fs -= 14                # 弱市: 非主轴反包重罚
                else:
                    fs = int(fs * 0.7)      # 弱市: 主轴反包也降权(退潮期容错低)
            # <<0910·优化D>> 反包池宏观风险窗口收紧: 9/11 CPI/非农等审判日前, 市场普跌环境下
            # "今日弱→次日强"的反包逻辑失效概率陡增(捷荣-9.97/天洋-6.5/共进-5.15全灭)。
            # 策略: risk_high 时反包整体砍半; 非主轴(非事件/非热)反包直接重罚到难入选。
            if risk_high:
                if not (ev_hit or hot_flag or _strong):
                    fs -= 20                # 风险窗·非主轴反包重罚(近剔除)
                else:
                    fs = int(fs * 0.5)      # 风险窗·主轴反包砍半(容错极低)
            fs += topic_bonus + ann_bonus - reg_pen
            if yest_zt:
                fs += 4                     # 前日涨停今日回踩(强低位)
            if fs >= 18:
                fan_sig = [f'当日{tday:+.1f}%', f'基因{gene}',
                           '收阴蓄势' if tday < 0 else '小阳待反包',
                           f'量比{today_ratio:.1f}缩量']
                if reg_reason:
                    fan_sig.append('风控:' + reg_reason)
                if -5 <= dev_ma10 <= 1:
                    fan_sig.append('贴MA10企稳')
                if ann_tag:
                    fan_sig.append(ann_tag)
                if weak_market and not (ev_hit or hot_flag or _strong):
                    fan_sig.append('弱市非主轴·观察')
                elif weak_market:
                    fan_sig.append('弱市·主轴反包7折')
                elif risk_high:
                    fan_sig.append('风险窗·反包已砍半:LOW' if not (ev_hit or hot_flag or _strong) else '风险窗·主轴反包砍半')
                rebound_fan.append({
                    'code': code, 'name': kl.get('name', code), 'mv': round(mv),
                    'score': round(fs), 'gene': gene,
                    'today_pct': _sf(tday), 'open_pct': _sf(open_pct),
                    'yest_pct': _sf(yest_pct), 'vol_ratio_today': _sf(today_ratio),
                    'dev_ma10': _sf(dev_ma10), 'ma5': _sf(ma5), 'ma10': _sf(ma10),
                    'breakout': False, 'topic_bonus': topic_bonus, 'topic': topic,
                    'buy_eligible': buy_eligible, 'weak_main': weak_main,
                    'ann_strength': ann['strength'] if ann else 0,
                    'ann_type': ann['type'] if ann else '',
                    'support': 'MA10', 'support_price': _sf(ma10),
                    'stop_loss': _sf(ma10 * 0.94),
                    'sectors': match_sector(kl.get('name', code), code)[:4],
                    'pool': '反包通道(次日涨停预告)', 'signals': fan_sig,
                })

        # ============ 通道C: 竞价/动能补池 ============
        if rt_d and rt_d.get('amt_yi', 0) >= 0.5:
            floor = 2 if yest_zt else 3
            open_ok = (not yest_zt and open_pct < 5) or \
                      (yest_zt and open_pct < 7 and rt_d.get('amt_yi', 0) >= 1.5)
            if gene >= floor and open_ok:
                if weak_market and not (mom is not None and mom >= 2.0 and rt_pct > 0):
                    continue                       # 弱市硬过滤: 需盘中动能确认
                fill_score = gene * 6 + topic_bonus + ann_bonus + (4 if yest_zt else 0) \
                             + (BREAKOUT_BONUS if breakout else 0) - reg_pen
                # 09-04 复盘: 情绪退潮期追"当日已大涨"的大阳/近涨停股, 次日崩塌概率高
                # (大晟-9.9/杭电-7.1/千金-7.8 全灭)。当日涨幅越高惩罚越重。
                if rt_pct is not None and rt_pct >= 5:
                    over = rt_pct - 5
                    fill_score -= int(over * 2.5)   # 5%+2.5分, 10%再减12.5分
                if rt_pct is not None and rt_pct >= 9 and emotional_retreat:
                    fill_score -= 20                # 退潮期近涨停坚决不追
                if emotional_retreat and gene >= 5 and mom is not None and mom < 2:
                    fill_score -= 10                # 退潮期高位滞涨(无动能)杀跌
                if risk_high:
                    fill_score -= 15                # 宏观避险(非农/FOMC): 降动能追高
                    if rt_pct is not None and rt_pct >= 8:
                        continue                    # 避险期当日+8%以上动能股直接舍弃
                auction_fill.append({
                    'code': code, 'name': kl.get('name', code), 'mv': round(mv),
                    'gene': gene, 'score': round(fill_score),
                    'open_pct': _sf(open_pct), 'amt_yi': rt_d.get('amt_yi'),
                    'today_pct': _sf(rt_pct), 'momentum': _sf(mom),
                    'breakout': breakout, 'topic': topic, 'topic_bonus': topic_bonus,
                    'buy_eligible': buy_eligible, 'weak_main': weak_main,
                    'ann_strength': ann['strength'] if ann else 0,
                    'ann_type': ann['type'] if ann else '',
                    'vol_ratio_today': _sf(today_ratio),
                    'sectors': match_sector(kl.get('name', code), code)[:4],
                    'reg_reason': reg_reason if (reg_reason and reg_pen >= 10) else '',
                    'support': 'MA10', 'support_price': _sf(ma10),
                    'stop_loss': _sf(ma10 * 0.94),
                    'pool': '竞价补池(动能确认)',
                    'signals': [f'基因{gene}', '低开+动能' if weak_market else '低开有量',
                                f'突破前高' if breakout else '',
                                f'风控:{reg_reason}' if reg_reason else '',
                                f'事件+{"+".join(ev_hit)}' if ev_hit else '',
                                f'主题+{topic_bonus}' if topic_bonus else '',
                                ann_tag if ann_tag else ''],
                })

        # ============ 通道G(第五通道): 主线放量启动龙头 <<0908>>> ============
        # 复盘: 5G/算力信息抓到了, 但武汉凡谷(量比2.82·涨停)/通宇(量比3.08·+5.26%)这类
        # "当日竞价+量比突变直接启动"的主线龙头, 因不在任何技术形态窗口而漏网。
        # 解法: 对【事件板块 或 热板块】成分股, 用"实时涨幅3~9%未封 + 量比突变 + 高换手"
        # 在盘中动态卡位(盘中该股涨到+6%时即命中, 收盘可能+10封板)。
        # 用实时 rt_pct 而非收盘 today_pct, 保证盘中实时跑时能提前捕捉。
        is_leader_sector = bool(ev_hit) or hot_flag
        turnover_pct = (v[ti] / (mv * 1e8 / cur) * 100) if (mv > 0 and cur > 0) else 0.0
        if (is_leader_sector and rt_d and rt_pct is not None
                and 3.0 <= rt_pct <= 9.0       # 主线放量上攻 3~9%(未封, 主升启动段, 可买)
                and today_ratio >= 1.5          # 量比突变(相对20日均量放量)
                and turnover_pct >= 8.0         # 换手充分: 资金真实进场
                and dev_ma10 > -6 and not_yiz): # 未一字未涨停
            l_score = 40 + min(25, (today_ratio - 1.5) * 12) + (10 if ev_hit else 4) \
                      + (8 if turnover_pct >= 12 else 0) + ann_bonus - reg_pen
            if yest_pct < 2:
                l_score += 8     # 昨日缩量/平开、今日突发放量 = 资金刚进场(启动龙头优于已连续走强)
            leader.append({
                'code': code, 'name': kl.get('name', code), 'mv': round(mv),
                'score': round(l_score), 'gene': gene,
                'today_pct': _sf(rt_pct), 'yest_pct': _sf(yest_pct),
                'vol_ratio_today': _sf(today_ratio), 'turnover': _sf(turnover_pct),
                'dev_ma10': _sf(dev_ma10), 'ma10': _sf(ma10), 'ma20': _sf(ma20),
                'ann_strength': ann['strength'] if ann else 0,
                'ann_type': ann['type'] if ann else '',
                'support': 'MA10', 'support_price': _sf(ma10),
                'stop_loss': _sf(ma10 * 0.94),
                'sectors': match_sector(kl.get('name', code), code)[:4],
                'pool': '主线放量启动龙头(第五通道)',
                'is_event': bool(ev_hit), 'hot': hot_flag,
                'cap_good': cap_good,
                'signals': [f'量比{today_ratio:.1f}/换手{turnover_pct:.0f}%',
                            '事件+' + '+'.join(ev_hit) if ev_hit else '热板块龙头',
                            '昨日缩量今日放量' if yest_pct < 2 else '主线上攻',
                            ann_tag if ann_tag else ''],
            })

        # ============ 前瞻四滤网 <<0909>>> ============
        # 用户痛点: 单纯捕捉"今日涨停"⇒买在情绪末端(次日即卖点), 追进去没涨幅还吃跌幅。
        # 此滤波把评价从"今日涨没涨"切换为"买进去能否多拿几日且回撤可控":
        #   红灯项【一票否决】: 高位钝化接盘 / 假启动过热 / 孤门无梯队 / 缩量滞涨 / 伪主线
        #   绿灯项累加成 hold_score(可拿3日分 0~100), 并绑定止损位 + T+2 溢价目标。
        # 前瞻定义: 首板/启动段 + 板块有梯队接力 + 温和放量 + 事件可续 ⇒ 才可能"多拿2天仍有溢价"。
        day5 = (cur / c[ti - 5] - 1) * 100 if ti >= 5 else cum
        red_flags = []
        hs = 20
        # ---- 红灯一票否决(唯一硬卡: 宁可错过不追高) ----
        if streak >= 3 or day5 > 30 or dev_ma10 > 10:
            red_flags.append(f'高位:{streak}板/5日{day5:.0f}%/乖离{dev_ma10:.0f}%')
        if (today_ratio > 5) or (open_pct is not None and open_pct >= 5):
            red_flags.append(f'过热:量比{today_ratio:.1f}(>5)或高开{open_pct:.1f}%(追高)')
        if not (cap_good or hot_flag or ev_hit or _strong):
            red_flags.append('孤门:板块无梯队接力资金')
        if today_ratio < 1.0 and today_pct > 0:
            red_flags.append('缩量上涨:无资金承接')
        if weak_main:
            red_flags.append('伪主线(事件点中无资金验证)')
        # ---- 绿灯加权(可拿3日分): 阶梯式加分拉出梯度, 全力避免"全票满分" ----
        if cap_good:
            hs += 20                              # 板块涨停>=3 且 板块量比>=1.2: 资金真实聚集
        elif hot_flag:
            hs += 12                              # 热板块(有赚钱效应)
        elif _strong:
            hs += 8                               # 板块主流技术面
        if ev_hit:
            hs += 8                               # 事件/涨价/政策催化可续
        if streak < 2 and day5 < 15 and dev_ma10 < 6:
            hs += 12                              # 首板/启动段, 非高位末端
        if 1.5 <= today_ratio <= 2.5:
            hs += 12                              # 温和放量(量价匹配真启动)
        elif 1.0 <= today_ratio < 1.5:
            hs += 5                               # 略放量
        if cap_good and _strong and not weak_main:
            hs += 8                               # 主流+资金双确认
        if (open_pct is None or open_pct <= 3) and -4 <= dev_ma10 <= 4:
            hs += 8                               # 买点安全(竞价不过热 + 贴均线)
        if breakout:
            hs += 6
        if yest_pct < 2 and (ev_hit or hot_flag or _strong):
            hs += 6                               # 昨日弱+今日事件放量启动(0908 武汉凡谷类)
        hs += gene * 2
        hs = max(0, min(100, hs + topic_bonus - reg_pen))
        if not red_flags and hs >= 70 and buy_eligible and not_yiz:
            # 止损: 买入支撑位跌破即"逻辑失效"离场; T+2 溢价: 启动段主升弹性目标.
            sl_price = min(ma5 * 0.97, ma10 * 0.94)
            holdable.append({
                'code': code, 'name': kl.get('name', code), 'mv': round(mv),
                'gene': gene, 'streak': streak,
                'day5_pct': _sf(day5), 'vol_ratio_today': _sf(today_ratio),
                'open_pct': _sf(open_pct), 'dev_ma10': _sf(dev_ma10),
                'today_pct': _sf(today_pct),
                'hold_score': round(hs), 'hold_target': 'T+2: +8~+15%',
                'support': 'MA10', 'support_price': _sf(ma10),
                'stop_loss': _sf(sl_price),
                'buy_eligible': buy_eligible, 'weak_main': weak_main,
                'cap_good': cap_good, 'red_flags': red_flags,
                'sectors': match_sector(kl.get('name', code), code)[:3],
                'pool': '前瞻可拿3日(四滤网)',
                'signals': [f'量比{today_ratio:.1f}', f'5日{day5:.0f}%',
                            '首板启动' if streak < 2 else f'{streak}板',
                            ann_tag if ann_tag else ''],
            })

    rebound.sort(key=lambda x: -x['score'])
    rebound_fan.sort(key=lambda x: -x['score'])
    high_relay.sort(key=lambda x: -x['score'])
    auction_fill.sort(key=lambda x: -x['score'])
    leader.sort(key=lambda x: -x['score'])
    holdable.sort(key=lambda x: -x['hold_score'])

    # 情绪退潮期判定: 高标/连板负反馈家数 >= 阈值, 或 弱市下监管/爆炒硬剔除>=2 => 关停高标接力
    # (后者命中"监管点名多+弱市"即认定情绪退潮, 如 爱丽/百花/海鸥/一鸣 被打压)
    emotional_retreat = retreat_hits >= REG_RETREAT_HITS or (weak_market and reg_suppress >= 2)
    if emotional_retreat:
        if weak_focus_local:
            # [0911 weak_focus] 退潮期不整体清空接力, 仅剔除"非事件/非热板块"的散点接力,
            # 保留事件主线上有梯队支撑的高标(如 PCB/AI算力链、绿电), 压到极小仓参与。
            high_relay = [x for x in high_relay if any(
                s in (event_set | hot_set) for s in x.get('sectors', []))]
        else:
            high_relay.clear()          # 情绪退潮: 不参与高位接力

    # ---- 公告催化提示榜 (新增): 强公告直通 ----
    # 公布分只在个股进入四条形态通道后才生效, 但强公告股常因基因低/均线不符合形态门槛而落榜
    # (如新华传媒 S95 若无量/不属回踩反包形态)。故对【S>=90 强公告】或【停牌/无量且 S>=70 的
    # 重点公告】, 只要未在四通道体现, 即单独入选"公告提示榜", 不参与四通道竞争但保证不外流。
    channel_codes = {x['code'] for pool in (rebound, rebound_fan, high_relay, auction_fill) for x in pool}
    ann_picks = []
    for code, a in ann_map.items():
        if code in channel_codes:
            continue
        kl = all_kls.get(code)
        name = (kl.get('name', code) if kl else code)
        mv_v = circ_mv_map.get(code, 0) or 0
        price = 0.0; tod_pct = 0.0; susp = False
        if kl and kl.get('c'):
            cc, vv = kl['c'], kl['v']
            ti = len(cc) - 1; yi = ti - 1
            if yi >= 0:
                price = cc[ti]
                vol20 = sum(vv[max(0, yi-20):yi]) / max(1, min(20, yi))
                tod_ratio = (vv[ti] / vol20) if vol20 > 0 else 0.0
                tod_pct = (cc[ti] / cc[yi] - 1) * 100
                susp = (tod_ratio < ANN_SUSPEND_RATIO and abs(tod_pct) < 0.5)
        rt_pct = None
        rt_d = (rt or {}).get(code)
        if rt_d:
            rt_pct = rt_d.get('pct')
        if a['strength'] >= ANN_PICK_S or (susp and a['strength'] >= 70):
            ann_picks.append({
                'code': code, 'name': name, 'mv': round(mv_v),
                'price': price, 'today_pct': _sf(tod_pct), 'rt_pct': _sf(rt_pct),
                'strength': a['strength'], 'type': a['type'], 'title': a.get('title', ''),
                'suspended': susp,
                'note': '停牌/无量, 无买点仅关注' if susp else '强公告直通, 可不依赖形态关注',
            })
    ann_picks.sort(key=lambda x: -x['strength'])
    ann_suspended = [x for x in ann_picks if x['suspended']]

    report = {
        'rebound_n': len(rebound), 'rebound_fan_n': len(rebound_fan),
        'high_relay_n': len(high_relay), 'auction_fill_n': len(auction_fill),
        'leader_n': len(leader), 'forward_n': len(holdable),
        'weak_market': weak_market,
        'emotional_retreat': emotional_retreat,
        'macro_risk': macro_risk,
        'retreat_hits': retreat_hits,
        'reg_suppress': reg_suppress,
        'hot_sectors': hot[:8],
        'event_sectors': event_sectors,
        'ann_catalysts_found': len(ann_map),
        'ann_picks': ann_picks[:15],
        'ann_suspended': ann_suspended[:10],
    }
    # 板块技术面 top-12 摘要 (供可视化: 放量/异动/突破回踩/涨停数/状态)
    st_top = []
    for sec in st_order[:12]:
        s = st_map[sec]
        st_top.append({'sector': sec, 'state': s['state'], 'zt_cnt': s['zt_cnt'],
                       'avg_pct': s['avg_pct'], 'vol_ratio': s['vol_ratio'],
                       'break_ratio': s['break_ratio'], 'pullback_ratio': s['pullback_ratio']})
    report['sector_tech'] = st_top
    return {
        'rebound': rebound[:top_n], 'rebound_fan': rebound_fan[:theme_pool],
        'high_relay': high_relay[:high_relay_n],
        'auction_fill': auction_fill[:theme_pool], 'auction_fill_full': auction_fill,
        'leader': leader[:top_n], 'leader_full': leader,
        'forward': holdable[:top_n], 'forward_full': holdable,
        'report': report, 'sector_tech_order': st_order, 'sector_tech_map': st_map,
    }


def run_v18(lib='default', top_n=12, theme_pool=6, high_relay_n=4,
            event_sectors=None, max_workers=8, data_cache=None,
            proxies=None, headers=None, macro_risk='low', weak_focus=False,
            focus_codes=None):
    """便捷入口: 拉全市场 + 60日K线 + 实时快照 -> capture_v18."""
    from scan_v1224 import (get_all_stocks, get_kl, match_sector, HEADERS as _H,
                            _get_proxies, load_sector_mapping)
    # 关键修复: V18 独立入口此前从不加载板块映射表, 导致 _CODE_TO_SECTORS 为空,
    # match_sector 退化成"用名字关键词匹配" -> 板块/事件/热板块/板块技术分全部联动失效。
    load_sector_mapping()
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
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        kls = dict((c, k) for c, k in
                   zip(codes, ex.map(lambda x: get_kl(x, 60), codes)) if k)
    # 关键修复: get_kl 返回的 K 线不含 name, 导致 kl.get('name') 拿到代码,
    # 板块/主题/公告名匹配全用代码去做 -> 全部落空。这里用真名回填。
    nmap2 = {s['code']: s['name'] for s in stocks if s.get('name')}
    for c, k in kls.items():
        k['name'] = nmap2.get(c, c)
    return capture_v18(kls, mv, match_sector, rt=rt, stocks=stocks,
                       event_sectors=event_sectors, top_n=top_n,
                       theme_pool=theme_pool, high_relay_n=high_relay_n,
                       macro_risk=macro_risk, weak_focus=weak_focus,
                       focus_codes=focus_codes)


if __name__ == '__main__':
    print('V18 复盘优化引擎加载成功。')
    print('  - run_v18(event_sectors=[...]) 运行;')
    print('  - capture_v18(...) 直接喂数据。')