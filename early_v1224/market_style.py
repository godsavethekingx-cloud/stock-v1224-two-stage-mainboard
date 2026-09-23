#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场风格决策层 (Market Style Arbitrator)  [2026-09-13]
========================================================
目标: 不预判/不固化某个板块, 而是根据当天的【消息面·局势·外围·大盘】用可量化信号
      动态判定市场当下最适配的攻击模式, 再据此决定选股的侧重。

四种风格 (互斥, 得分最高者胜出):
  STYLE_INDEX   = '大盘共振'   适配: 指数放量上行/vso权重搭台, 做与大盘共振的权重主线龙头
  STYLE_EMO     = '情绪题材'   适配: 情绪热/连板梯队健康, 做高标接力+龙头辨识度(情绪票)
  STYLE_CORE    = '抱团核心'   适配: 震荡市/缩量, 资金抱团少数核心, 做确定性抱团核心/趋势白马
  STYLE_DEFEND  = '防御观望'   适配: 弱市+退潮+宏观风险高, 砍仓防守, 仅留低位防御/事件避险

入参全部来自系统既有信号(non-cookie):
  index = analyze_market(get_index_kl())   # 大盘指数共振度(量/价/趋势)
  sent  = assess_market_sentiment(...)     # 情绪温度(涨停/跌停/涨跌比)
  st    = build_sector_tech(...)           # 板块热力(权重 vs 题材的分化)
  zt    = 今日涨停梯队高度/晋级(涨停池推导)  # 连板健康度
  news  = 消息面/外围 信号(可选, 事件强弱评分)

输出:
  {
    'style': 'index'|'emo'|'core'|'defend',
    'style_name': '大盘共振'|'情绪题材'|'抱团核心'|'防御观望',
    'confidence': 0-100,            # 风格判定置信度
    'scores': {...四风格分...},
    'position': '≤80%'|...|'≤10%',
    'position_pct': 0.0-1.0,        # 建议总仓位
    'channel_weights': {rebound, rebound_fan, high_relay, auction_fill, leader},
    'focus_sectors': [...],          # 当前最强主线板块(动态, 不固化)
    'reason': [...],                # 判定依据摘要
    'relay_open': bool,             # 本风格下是否开启高标接力
    'weak_focus': bool,             # 是否集中事件主线低仓
  }
"""
# 四风格得分的基础权重模板
# 每个风格 = 对"五类信号"的偏好系数
_STYLE_T = {
    'index':    {'idx': 1.0, 'emo': 0.3, 'ladder': 0.4, 'diff': 1.0, 'def': 0.3},
    'emo':      {'idx': 0.3, 'emo': 1.0, 'ladder': 1.0, 'diff': 0.4, 'def': 0.3},
    'core':     {'idx': 0.5, 'emo': 0.3, 'ladder': 0.3, 'diff': 0.4, 'def': 1.0},
    'defend':   {'idx': 0.3, 'emo': 0.2, 'ladder': 0.2, 'diff': 0.3, 'def': 0.3},
}

# 风格 -> 总仓位建议 (按风险)
_POS = {
    'index':   ('≤80%', 0.8),
    'emo':     ('≤70%', 0.7),
    'core':    ('≤50%', 0.5),
    'defend':  ('≤10%', 0.1),
}


def decide_market_style(index=None, sentiment=None, st_order=None, st_map=None,
                        zt_height=0, zt_count=0, macro_risk='low', news=None,
                        style_hint=None):
    """
    综合多信号判定当日市场风格。

    index    : analyze_market 返回值 (指数共振度)
    sentiment: assess_market_sentiment 返回值 (情绪温度)
    st_order/st_map: build_sector_tech 返回值 (板块热力, 用于整体/权重vs题材分化)
    zt_height: 今日最高连板高度(如 3 = 出现3连板); 0 表示无数据
    zt_count : 今日涨停家数
    macro_risk: 'high'|'low'|'medium'  日历宏观风险
    news     : 消息面/外围/局势结构化信号 dict(可选):
                {'strength':0-100, 'impact':'pos'|'neg'|'mixed',
                 'sectors':[...指向的板块], 'inst_focus':bool(是否机构抱团偏好)}
    style_hint: 人工指定(可选), 覆盖自动判定(用于用户干预或回测)
    """
    # ---- 1. 归一化四个信号向量 ----
    # 指数共振分 idx_off: + 放量上行/连续收阳/站上MA20 => 高分(利于 index 风格)
    idx_off = 0.0
    if index:
        idx_off += (1.0 if index.get('ma_bull') else 0)
        idx_off += (1.0 if index.get('vol_expand') else 0)
        idx_off += (1.0 if index.get('above_ma20') else 0)
        idx_off += (0.5 if index.get('pct_5d', 0) > 3 else 0)
        idx_off += (1.0 if index.get('pct_1d', 0) < -1 else 0)   # 指数大跌 => 反向压制 index 风格
        idx_off += (0.0 if not index.get('t1_yang') else 0.3)
    # 情绪温度 emo_off: 涨停多/跌停少/涨跌比健康 => 利于 emo
    emo_off = 0.0
    if sentiment:
        s = sentiment.get('sentiment_score', 50)
        lvl = sentiment.get('sentiment_level', 'neutral')
        emo_off += (s - 50) / 25.0                      # 52分起为正
        if lvl in ('hot', 'warm'):
            emo_off += 0.5
        elif lvl in ('cool', 'cold'):
            emo_off -= 0.5
    # 连板梯队健康度 ladder_off: 高度越高且涨停越多 => 情绪票可做
    ladder_off = 0.0
    if zt_height and zt_count:
        ladder_off = min(2.0, zt_height * 0.4 + zt_count / 60.0)
    elif zt_count:
        ladder_off = min(1.0, zt_count / 80.0)
    # 权重 vs 题材分化 diff_off: 板块热力中是否"大面积上涨/权重主线显现"
    #   (st_order 前若干板块的 avg_pct 均值越高, 越像大盘共振普涨)
    diff_off = 0.0
    if st_map and st_order:
        tops = st_order[:8]
        strong_cnt = sum(1 for s in tops if st_map[s]['state'] == 'strong')
        ignite_cnt = sum(1 for s in tops if st_map[s]['state'] == 'ignite')
        zt_in_top = sum(st_map[s]['zt_cnt'] for s in tops)
        diff_off += min(2.0, strong_cnt * 0.4 + ignition_from_zts(zt_in_top))
        if strong_cnt >= 3:          # 板块普涨强势 => 大盘共振特征
            diff_off += 0.8
    # 防御信号 def_off: 宏观风险高 + 指数弱 + 情绪冷 => 压制所有进攻风格
    def_off = 0.0
    if macro_risk == 'high':
        def_off += 1.5
    if index and index.get('pct_1d', 0) < -1.5:
        def_off += 0.8
    if sentiment and sentiment.get('sentiment_level') in ('cold', 'cool'):
        def_off += 0.6

    # 消息面/外围: 事件指向具体板块 => 情绪题材发酵; 机构/资金偏好 => 抱团核心受益
    inst_boost = 0.0
    has_sector_news = False
    if news:
        strength = news.get('strength', 0) / 100.0
        impact = news.get('impact', 'mixed')
        if news.get('sectors'):
            ladder_off += strength * 0.6
            has_sector_news = True
        if news.get('inst_focus'):
            inst_boost += strength * 0.8
        if impact == 'neg':
            def_off += strength * 0.5
        # [2026-09-14] 美股盘中走弱(us_weak): 外围风险边际压制情绪题材
        #   -> 抬升防御分(不切换风格, 而是降低情绪票优先级/拉高风控), 即使未命中具体板块。
        if news.get('us_weak'):
            def_off += 0.22
            ladder_off -= 0.12   # 削弱"事件发酵"信号, 情绪票降档

    # 抱团核心判定门: 指数未放量上行(非主升) + 机构重仓偏好强 + 情绪不够热
    #   => 更可能是"震荡市机构缩容抱团", 而非指数共振普涨。
    core_arbitration = (inst_boost > 0.3 and idx_off < 2.5 and emo_off < 0.6
                        and not (index and index.get('vol_expand') and index.get('ma_bull')))

    # ---- 2. 计算四风格得分 ----
    sig = {'idx': idx_off, 'emo': emo_off, 'ladder': ladder_off + inst_boost,
           'diff': diff_off, 'def': def_off}
    scores = {}
    for st_name, tmpl in _STYLE_T.items():
        sc = sum(sig[k] * tmpl[k] for k in tmpl)
        # 防御风格: 防御信号高则升档; 否则靠基础
        if st_name == 'defend':
            sc = def_off * 2.2 - (emo_off + ladder_off) * 0.4
        scores[st_name] = round(max(0, sc), 2)

    # ---- 3. 裁决 (允许人工 hint 覆盖) ----
    if style_hint in _STYLE_T:
        style = style_hint
    elif core_arbitration:
        # 抱团门命中(缩量震荡 + 机构重仓偏好 + 情绪不热) => 判为抱团核心,
        # 即便 index 得分略高也覆盖(避免"缩量震荡"被误判成"放量共振普涨")。
        style = 'core'
    else:
        style = max(scores, key=scores.get)

    srt = sorted(scores.items(), key=lambda x: -x[1])
    top_score = srt[0][1]
    second_score = srt[1][1] if len(srt) > 1 else 0
    confidence = int(min(100, 40 + (top_score - second_score) * 30)) if (style == srt[0][0]) else 70

    pos_lbl, pos_pct = _POS[style]
    fuse_relay = (style in ('emo', 'index')) and def_off < 1.2

    # 通道权重: 风格决定"该把子弹放在哪个通道"
    cw = {
        'rebound': 1.0, 'rebound_fan': 1.0,
        'high_relay': 0.0, 'auction_fill': 1.0, 'leader': 0.0,
    }
    if style == 'emo':
        cw = {'rebound': 0.4, 'rebound_fan': 0.3, 'high_relay': 1.0, 'auction_fill': 0.8, 'leader': 1.0}
    elif style == 'index':
        cw = {'rebound': 0.5, 'rebound_fan': 0.4, 'high_relay': 0.6, 'auction_fill': 0.6, 'leader': 1.0}
    elif style == 'core':
        cw = {'rebound': 1.0, 'rebound_fan': 0.8, 'high_relay': 0.0, 'auction_fill': 0.2, 'leader': 0.3}
    # defend: 全部压制, 只留低位回踩防御
    if style == 'defend':
        cw = {'rebound': 0.6, 'rebound_fan': 0.2, 'high_relay': 0.0, 'auction_fill': 0.0, 'leader': 0.0}

    # 动态 focus_sectors: 不强固化, 由"板块热力 top + 事件/外围指向"共同决定
    focus_sectors = []
    if st_order and st_map:
        focus_sectors = [s for s in st_order[:8]
                         if st_map[s]['state'] in ('strong', 'ignite', 'breakout')][:6]
    if news and news.get('sectors'):
        for s in news['sectors']:
            if s not in focus_sectors:
                focus_sectors.append(s)

    reason = []
    reason.append(f"指数{'放量上行/多头' if idx_off>0 else '偏弱'}(idx_off={idx_off:+.2f})")
    reason.append(f"情绪{_lvl(sentiment) if sentiment else 'N/A'}(emo_off={emo_off:+.2f})")
    reason.append(f"连板梯队(max{zt_height}板/涨停{zt_count}家, ladder_off={ladder_off:+.2f})")
    reason.append(f"宏观风险={macro_risk}(def_off={def_off:+.2f})")
    reason.append('风格分: ' + ' / '.join(f'{k}{v:+.1f}' for k, v in scores.items()))
    if news:
        reason.append(f"消息面: 强度{news.get('strength',0)} 影响{news.get('impact')} 机构偏好={news.get('inst_focus')}")

    return {
        'style': style,
        'style_name': _NAMES[style],
        'confidence': confidence,
        'scores': scores,
        'position': pos_lbl,
        'position_pct': pos_pct,
        'channel_weights': cw,
        'focus_sectors': focus_sectors,
        'relay_open': fuse_relay,
        'weak_focus': (style == 'defend') or (fuse_relay and macro_risk == 'high'),
        'reason': reason,
    }


_NAMES = {'index': '大盘共振', 'emo': '情绪题材', 'core': '抱团核心', 'defend': '防御观望'}


def ignition_from_zts(zt_in_top):
    """用板块涨停数估算"点火度": 前8板块涨停越多越强(上限2)."""
    return min(2.0, zt_in_top / 8.0)


def _lvl(sentiment):
    return sentiment.get('sentiment_level', 'N/A')


def main():
    # 自测: 无外部数据时给出合理默认
    r = decide_market_style(macro_risk='high')
    print('风格:', r['style_name'], '置信', r['confidence'], '仓位', r['position'])
    print('通道权重:', r['channel_weights'])
    print('reason:', *r['reason'], sep='\n  - ')


if __name__ == '__main__':
    main()