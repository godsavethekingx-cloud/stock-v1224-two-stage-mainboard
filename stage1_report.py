#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段1 全盘扫描日报生成器 stage1_report.py
=========================================
读取 stage1_night20.py 产出的完整面板数据
/workspace/stock-v1224/system/night20_panel.json,
渲染为"主板两阶段选股系统 · 阶段1 全盘扫描日报" HTML,
存于 /workspace/stock-v1224/reports/<ref_date>_stage1.html.

用法:
  python3 stage1_report.py
"""
import json, os, sys
from html import escape

SYSTEM = '/workspace/stock-v1224/system'
PANEL = os.path.join(SYSTEM, 'night20_panel.json')
REPORT_DIR = os.path.join(os.path.dirname(SYSTEM), 'reports')  # /workspace/stock-v1224/reports

REGIME_NAME = {'BULL': '强势上行', 'RANGE_BULL': '震荡偏多', 'CORRECTION': '回调防守',
               'RELAY': '连板接力', 'USER': '用户条件'}
REGIME_DESC = {
    'BULL': '5攻0防，激进主线龙头',
    'RANGE_BULL': '5攻1防，板块热度 + 连板接力优先',
    'CORRECTION': '板块热度防守为主',
    'RELAY': '连板接力/缩量回踩日',
    'USER': '涨停基因 + 放量蓄势 + 可买性',
}

MAINLINE_MAX = 24   # 主线标签最多展示数


def fnum(x, nd=2, dash='-'):
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return dash


def build_html(p):
    ref = p.get('ref_date', '')
    run_time = p.get('run_time', '')
    market = p.get('market') or {}
    sug = p.get('suggested_regime', p.get('regime', 'BULL'))
    sug_name = REGIME_NAME.get(sug, sug)
    hot = p.get('hot_sectors') or {}
    accel = p.get('sector_accel') or {}
    local_bull = p.get('local_bull') or []
    top = p.get('top') or []

    # ---- 0. 大盘判态 ----
    close = market.get('close')
    close_s = fnum(close)
    pct1 = market.get('pct_1d')
    pct1_s = f"{pct1:+.2f}%" if pct1 is not None else '-'
    pct3 = market.get('pct_3d'); pct5 = market.get('pct_5d')
    vr = market.get('vol_ratio')
    vr_advice = market.get('vol_advice', f'量比{vr:.2f}' if vr else '-')
    consec_up = market.get('consec_up', 0)
    above_ma5 = market.get('above_ma5', False)
    above_ma20 = market.get('above_ma20', False)
    t1_yang = market.get('t1_yang', False)
    ma_pos = ('MA5 上方' if above_ma5 else 'MA5 下方')
    if above_ma20:
        ma_pos += '，站上 MA20'
    elif not above_ma5:
        ma_pos += '，MA20 下方'
    else:
        ma_pos += '，MA20 下方未完全转强'

    # ---- 1. 热点板块：涨停/异动统计 ----
    hot_sorted = sorted(hot.items(), key=lambda kv: -kv[1].get('surging', 0))
    top_hot = hot_sorted[:10]
    # 主线确认 / 加速
    main_lines = sorted(((s, st) for s, st in accel.items()
                         if st in ('主线确认', '加速')), key=lambda kv: -({'主线确认': 2, '加速': 1}[kv[1]] if kv[1] in ('主线确认', '加速') else 0))
    main_tags = main_lines[:MAINLINE_MAX]
    other_hot = [s for s, _ in hot_sorted[10:] if s not in accel][:8]

    # ---- 2. 候选TOP20 ----
    def mode_color(m):
        return {'DUAL_COND': 'bull', 'COND1': 'bull', 'COND2': 'bull', 'BULL': 'localbull'}.get(m, 'corr')
    top_rows = []
    for c in top:
        mode = str(c.get('sel_mode', ''))
        zt_col = 'up' if (c.get('yest_pct') or 0) >= 9.5 else ''
        yest = c.get('yest_pct')
        yest_s = f"{yest:+.2f}" if yest is not None else '-'
        op = c.get('open_pct')
        op_s = f"{op:+.2f}" if op is not None else '-'
        op_cls = 'down' if (op or 0) < 0 else ('up' if (op or 0) > 0 else '')
        sec = '/'.join((c.get('sectors') or [])[:3]) or '-'
        top_rows.append(f"""
      <tr>
        <td class="rank">{c.get('rank')}</td>
        <td>{escape(c.get('name', ''))}</td>
        <td class="code">{c.get('code', '')}</td>
        <td class="num"><b>{fnum(c.get('v1224_score'), 1)}</b></td>
        <td class="num {zt_col}">{yest_s}</td>
        <td class="num {op_cls}">{op_s}</td>
        <td class="num">{fnum(c.get('rsi6'), 0)}</td>
        <td class="num">{c.get('max_consec', 0)}</td>
        <td class="num">{c.get('zt_30d', 0)}</td>
        <td><span class="mode {mode_color(mode)}">{escape(mode)}</span></td>
        <td>{escape(sec)}</td>
      </tr>""")
    top20_rows = ''.join(top_rows)

    # ---- 3. 核心买入池 Top5 ----
    advice_cards = []
    rank_labels = ('①', '②', '③', '④', '⑤')
    for idx, c in enumerate(top[:5]):
        ta = c.get('trade_advice') or {}
        label = rank_labels[idx] if idx < len(rank_labels) else f'{idx + 1}.'
        sec = '/'.join((c.get('sectors') or [])[:3]) or '-'
        sigs = c.get('signals') or []
        sig_items = ''.join(
            f'<span class="sig-item">{escape(str(s))}</span>' for s in sigs[:5])
        advice_cards.append(f"""
      <div class="advice-card">
        <div class="a-head"><span class="a-name">{label} {escape(c.get('name', ''))}</span><span class="a-score">评分 {fnum(c.get('v1224_score'), 1)}</span></div>
        <div class="a-sectors">{escape(sec)}</div>
        <div class="row"><span class="k">买入</span>{escape(ta.get('buy_point', '-'))}</div>
        <div class="row"><span class="k">止损</span>{escape(ta.get('stop_loss', '-'))}</div>
        <div class="row"><span class="k">止盈</span>{escape(ta.get('take_profit', '-'))}</div>
        <div class="row"><span class="k">持仓</span>{escape(ta.get('hold_advice', '-'))}</div>
        <div class="sig"><span class="sig-list">{sig_items}</span></div>
      </div>""")
    cards_html = ''.join(advice_cards)

    # ---- 4. 操作建议文案 ----
    if consec_up >= 2 and above_ma5:
        ops = [
            f"判态为<b>震荡偏多（{REGIME_DESC.get(p.get('regime', 'BULL'))}）</b>：沪指连涨{consec_up}日且站稳 MA5，进攻仓可配置连板/题材龙头，保留防守仓应对 MA20 未站上前的波动。",
            "<b>主线集中度较高</b>：候选股多集中于上述主线，注意同质化波动风险，避免满仓单一主线。",
            "<b>开盘确认</b>：本报告为盘后静态扫描，实时竞价与盘中数据需次日开盘后复核；连板高标波动剧烈，只做题材跟随、不追最后一波。",
            "<b>仓位与止损</b>：单票 ≤15% 仓位，统一执行 -3% / MA10 双止损，+5~8% 分批止盈，纪律优先于观点。",
        ]
    else:
        ops = [
            f"判态为<b>{REGIME_NAME.get(p.get('regime', 'BULL'))}（{REGIME_DESC.get(p.get('regime', 'BULL'))}）</b>：结合大盘量价与均线位置，动态调整进攻/防守仓比例。",
            "<b>主线集中度较高</b>：候选股多集中于热点主线，注意同质化波动风险，避免满仓单一主线。",
            "<b>开盘确认</b>：本报告为盘后静态扫描，实时竞价与盘中数据需次日开盘后复核；连板高标波动剧烈，只做题材跟随。",
            "<b>仓位与止损</b>：单票 ≤15% 仓位，统一执行 -3% / MA10 双止损，+5~8% 分批止盈，纪律优先于观点。",
        ]
    ops_html = ''.join(
        f'<li style="background:var(--bg2);border:1px solid var(--rule);border-radius:var(--radius-md);padding:12px 14px;font-size:14px;">{o}</li>'
        for o in ops)
    risks_html = """
      <li><span class="dot r"></span><span><b>连板高标退潮风险：</b>候选多集中于连板/局部净值标的，一旦主线熄火或情绪降温，高位股回撤幅度大。</span></li>
      <li><span class="dot r"></span><span><b>追高溢价风险：</b>T-1 大面积涨停后竞价高开易透支，追入常遇回踩，务必等回踩企稳。</span></li>
      <li><span class="dot w"></span><span><b>均线未完全转强：</b>沪指若仍在 MA20 下方，指数层面未完全确认趋势，放量跌破 MA5 需快速降仓。</span></li>
      <li><span class="dot w"></span><span><b>外围 / 宏观扰动：</b>数据为静态快照，未纳入盘后公告、外围隔夜及宏观日历事件，实盘前应复核最新消息面。</span></li>
      <li><span class="dot b"></span><span><b>数据时效：</b>结果基于抓取时点行情，样本为重点板块抽样，非全库逐只满分，仅供参考。</span></li>
    """

    # 热点标签
    hot_tags = ''.join(
        f'<span class="tag {"" if i < 3 else ""}">{escape(s)} · 涨停{d.get("zt", 0)}/异动{d.get("surging", 0)}</span>'
        for i, (s, d) in enumerate(top_hot))
    other_tags = ''.join(f'<span class="tag">{escape(s)}</span>' for s in other_hot)
    if other_tags:
        other_tags = f'<span class="tag">… 其余资金异动板块</span>' + other_tags
    main_tags_html = ''
    for s, st in main_tags:
        tag_cls = 'accel' if st == '加速' else 'main'
        main_tags_html += f'<span class="tag {tag_cls}">{escape(s)} · {escape(st)}</span>'

    lb_s = '/'.join(str(x) for x in local_bull[:18]) if local_bull else '无'

    title = f"主板两阶段选股 · 阶段1 全盘扫描日报 {ref}"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  :root {{
    --bg:#FFFFFF; --bg2:#F6F8FB; --rule:#DCE3EC; --ink:#1F2733;
    --muted:#64748B; --text-secondary:#3A4756; --page-brand:#0969DA;
    --page-brand-soft:#EAF2FC; --page-brand-soft-strong:#D8E7FB;
    --success:#129A4F; --negative:#D3383D; --warning:#E8930C;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:-apple-system,'PingFang SC','Microsoft YaHei','Noto Sans CJK SC','WenQuanYi Micro Hei',sans-serif;
          color:var(--ink); background:var(--bg); line-height:1.7; }}
  .page__content {{ width:calc(100% - 2.5rem); max-width:1000px; margin:0 auto; }}
  .report-intro {{ margin-bottom:32px; }}
  .report-intro__surface {{ position:relative; isolation:isolate; overflow:hidden;
      color:#0B3A75; background:linear-gradient(135deg,var(--page-brand-soft) 0%,#EEF2F8 100%);
      border-bottom:1px solid var(--rule); padding:48px; }}
  .kicker {{ display:inline-block; font-size:12px; letter-spacing:2px; color:var(--page-brand);
      font-weight:600; border:1px solid var(--rule); background:var(--bg);
      border-radius:999px; padding:2px 12px; margin-bottom:16px; }}
  .report-intro h1 {{ font-size:32px; line-height:1.25; color:#1F2733; }}
  .report-intro__summary {{ max-width:78ch; margin-top:12px; color:var(--text-secondary); font-size:16px; }}
  .intro-meta {{ margin-top:20px; display:flex; flex-wrap:wrap; gap:12px; }}
  .meta-chip {{ background:var(--bg); border:1px solid var(--rule); border-radius:8px;
      padding:8px 14px; font-size:13px; color:var(--text-secondary); display:inline-flex; align-items:center; gap:6px; }}
  .meta-chip b {{ color:#1F2733; font-size:15px; }}
  .badge {{ display:inline-block; border-radius:999px; padding:1px 9px; font-size:12px; font-weight:600; }}
  .badge-blue {{ background:var(--page-brand-soft); color:var(--page-brand); }}
  .badge-up {{ background:rgba(18,154,79,.12); color:var(--success); }}
  main {{ padding:8px 0 16px; }}
  section {{ margin-bottom:48px; }}
  h2.sec {{ display:flex; align-items:center; gap:10px; font-size:20px; color:#1F2733;
      margin-bottom:16px; padding-bottom:8px; border-bottom:2px solid var(--page-brand-soft-strong); }}
  h2.sec .no {{ background:var(--page-brand); color:#fff; font-size:13px; min-width:28px;
      height:28px; line-height:28px; text-align:center; border-radius:8px; }}
  h3 {{ font-size:16px; color:#1F2733; margin:20px 0 12px; }}
  p.lede {{ color:var(--text-secondary); margin-bottom:12px; }}
  .hint {{ font-size:13px; color:var(--muted); background:var(--bg2);
      border-left:3px solid var(--rule); padding:8px 12px; border-radius:0 4px 4px 0; margin:12px 0; }}
  .metric-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }}
  .metric {{ background:var(--bg2); border:1px solid var(--rule); border-radius:8px; padding:16px; }}
  .metric .m-label {{ font-size:12px; color:var(--muted); }}
  .metric .m-val {{ font-size:24px; font-weight:700; color:#1F2733; font-variant-numeric:tabular-nums; }}
  .metric .m-val.up {{ color:var(--success); }}
  .tags {{ display:flex; flex-wrap:wrap; gap:8px; }}
  .tag {{ border-radius:999px; padding:3px 10px; font-size:12px; border:1px solid var(--rule);
      background:var(--bg); color:var(--text-secondary); }}
  .tag.hot {{ border-color:#D3383D; color:#D3383D; background:rgba(211,56,61,.06); }}
  .tag.main {{ border-color:var(--page-brand); color:var(--page-brand); background:var(--page-brand-soft); }}
  .tag.accel {{ border-color:#E8590C; color:#C2440A; background:rgba(232,89,12,.07); }}
  .table-wrap {{ margin:12px 0; overflow-x:auto; }}
  table {{ width:100%; border-collapse:collapse; min-width:680px; }}
  th,td {{ padding:9px 10px; border-bottom:1px solid var(--rule); text-align:left; vertical-align:middle; font-size:14px; }}
  th {{ background:#EEF2F8; color:var(--text-secondary); font-weight:600; white-space:nowrap; }}
  tbody tr:hover {{ background:var(--page-brand-soft); }}
  td.num,th.num {{ text-align:right; }}
  td.up {{ color:var(--success); }} td.down {{ color:var(--negative); }}
  .rank {{ color:var(--page-brand); font-weight:700; }}
  .code {{ font-family:'SF Mono',Consolas,monospace; color:var(--muted); font-size:13px; }}
  .mode {{ display:inline-block; border-radius:4px; padding:1px 8px; font-size:12px; font-weight:600; }}
  .mode.bull {{ background:rgba(211,56,61,.10); color:#D3383D; }}
  .mode.localbull {{ background:rgba(130,80,223,.10); color:#6f36c9; }}
  .mode.corr {{ background:rgba(232,147,12,.12); color:#B57A05; }}
  .advice-grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:16px; }}
  .advice-card {{ border:1px solid var(--rule); border-radius:12px; padding:16px;
      background:var(--bg); display:flex; flex-direction:column; gap:6px; }}
  .advice-card .a-head {{ display:flex; justify-content:space-between; align-items:baseline; }}
  .advice-card .a-name {{ font-weight:700; font-size:16px; color:#1F2733; }}
  .advice-card .a-score {{ color:var(--page-brand); font-weight:700; }}
  .advice-card .a-sectors {{ font-size:12px; color:var(--muted); margin-bottom:4px; }}
  .advice-card .row {{ display:flex; gap:8px; font-size:13px; }}
  .advice-card .row .k {{ color:var(--muted); white-space:nowrap; min-width:28px; }}
  .advice-card .sig {{ font-size:12px; color:var(--text-secondary); }}
  .sig-list {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:4px; }}
  .sig-item {{ font-size:12px; border-radius:999px; padding:1px 9px; background:var(--bg2); color:var(--text-secondary); }}
  .risk-list {{ list-style:none; display:grid; gap:12px; }}
  .risk-list li {{ display:flex; gap:12px; align-items:flex-start;
      background:var(--bg2); border:1px solid var(--rule); border-radius:8px; padding:12px 14px;
      font-size:14px; color:var(--text-secondary); }}
  .risk-list li .dot {{ width:8px; height:8px; border-radius:50%; margin-top:8px; flex:none; }}
  .risk-list li .dot.r {{ background:#D3383D; }}
  .risk-list li .dot.w {{ background:var(--warning); }}
  .risk-list li .dot.b {{ background:var(--page-brand); }}
  footer {{ border-top:1px solid var(--rule); padding:32px 0 48px; }}
  footer .sources li {{ font-size:13px; color:var(--muted); margin-bottom:6px; }}
  .disclaimer {{ margin-top:20px; font-size:13px; color:var(--muted); background:var(--bg2);
      border:1px dashed var(--rule); padding:12px 16px; border-radius:8px; }}
  @media (max-width:720px) {{
    .report-intro__surface {{ padding:24px; }}
    .report-intro h1 {{ font-size:24px; }}
    .advice-grid {{ grid-template-columns:1fr; }}
    .metric-grid {{ grid-template-columns:repeat(2,1fr); }}
  }}
</style>
</head>
<body>
  <header class="report-intro">
    <div class="report-intro__surface">
      <span class="kicker">STOCK SELECTION · 主板两阶段选股系统 v12.24</span>
      <h1>全盘扫描选股日报 · {ref}</h1>
      <p class="report-intro__summary">
        系统以<b>{escape(sug_name)}</b>态势完成全市场扫描：沪指 {close_s} 点（{pct1_s}），
        {escape(vr_advice)}。引擎从全量标的中全盘扫描，综合技术面、板块共振、
        连板基因与资金轮动，筛选出 {len(top)} 只候选；前 5 只评分显著领先，
        偏向小盘题材与连板接力方向。
      </p>
      <div class="intro-meta">
        <span class="meta-chip">态势 <b>{escape(sug_name)}</b> <span class="badge badge-blue">{escape(p.get('regime', 'BULL'))}</span></span>
        <span class="meta-chip">沪指 <b class="up">{close_s}</b> <span class="badge badge-up">{pct1_s}</span></span>
        <span class="meta-chip">热点主线 <b>{escape((main_tags[0][0] if main_tags else (top_hot[0][0] if top_hot else '-')))}</b></span>
        <span class="meta-chip">候选 <b>{len(top)}</b> 只</span>
        <span class="meta-chip">运行 <b>{escape(run_time)}</b></span>
      </div>
    </div>
  </header>

  <div class="page__content">
    <main>
      <!-- 01 大盘环境 -->
      <section>
        <h2 class="sec"><span class="no">01</span>大盘环境与态势判定</h2>
        <div class="metric-grid">
          <div class="metric"><div class="m-label">上证指数</div><div class="m-val up">{close_s}</div><div class="m-sub">1日 {pct1_s} · 3日 {fnum(pct3)}% · 5日 {fnum(pct5)}%</div></div>
          <div class="metric"><div class="m-label">量比</div><div class="m-val">{fnum(vr, 2)}</div><div class="m-sub">{escape(vr_advice)}</div></div>
          <div class="metric"><div class="m-label">连涨天数</div><div class="m-val">{consec_up} 天</div><div class="m-sub">{'T-1 收阳' if t1_yang else 'T-1 收阴'}</div></div>
          <div class="metric"><div class="m-label">均线位置</div><div class="m-val" style="font-size:19px">{ma_pos}</div><div class="m-sub">{'多头排列' if market.get('ma_bull') else '未完全多头排列'}</div></div>
          <div class="metric"><div class="m-label">系统判态</div><div class="m-val" style="color:var(--page-brand)">{escape(sug)}</div><div class="m-sub">{escape(REGIME_DESC.get(sug, ''))}</div></div>
        </div>
        <p class="hint">判态依据：沪指 {pct1_s}、量比 {fnum(vr, 2)}，系统根据量价自动建议"{escape(sug_name)}"模式。候选筛选以板块热度 + 连板接力优先。</p>
      </section>

      <!-- 02 热点与主线 -->
      <section>
        <h2 class="sec"><span class="no">02</span>热点板块与主线确认</h2>
        <h3>当日资金异动板块 TOP（涨停数 / 异动数）</h3>
        <div class="tags" style="margin-bottom:16px;">{hot_tags or '<span class="tag">暂无显著异动</span>'}</div>
        <h3>主线确认（加速 / 轮动确认方向）</h3>
        <div class="tags">{main_tags_html or '<span class="tag">暂无明确主线确认</span>'}</div>
        <p class="lede" style="margin-top:16px;">本地牛 / 资金集聚板块：<b>{escape(lb_s)}</b>，与当日涨停集聚方向一致性较高。</p>
      </section>

      <!-- 03 全盘扫描候选 TOP20 -->
      <section>
        <h2 class="sec"><span class="no">03</span>全盘扫描候选 TOP20</h2>
        <p class="lede">按 v12.24 复合评分排序，字段：T-1 收盘涨幅、竞价开盘、RSI6、连板高度、30 日涨停数、命中模式、核心板块。</p>
        <div class="table-wrap">
          {top20_rows and f'<table><thead><tr><th>#</th><th>名称</th><th>代码</th><th class="num">评分</th><th class="num">T-1涨幅</th><th class="num">竞价开盘</th><th class="num">RSI6</th><th>连板</th><th>30日涨停</th><th>模式</th><th>核心板块</th></tr></thead><tbody>{top20_rows}</tbody></table>' or '<p class="hint">暂无候选数据</p>'}
        </div>
      </section>

      <!-- 04 系统核心买入池 -->
      <section>
        <h2 class="sec"><span class="no">04</span>系统核心买入池（Top5 操作指引）</h2>
        <p class="lede">以下为引擎给出买卖点、止损止盈与持仓建议的高置信标的，均来自 v12.24 评分与连板接力逻辑。</p>
        <div class="advice-grid">
          {cards_html}
          <div class="advice-card" style="background:var(--page-brand-soft); border-color:var(--page-brand-soft-strong);">
            <div class="a-head"><span class="a-name">操作纪律</span></div>
            <div class="row" style="font-size:13px; color:#0B3A75;">多数 TOP 候选 T-1 已涨停，次日不宜盲目追高，优先等竞价或盘中回踩 MA5 / 平台支撑企稳再介入。</div>
            <div class="row" style="font-size:13px; color:#0B3A75;">严格按系统止损位（MA10 或 -3%）先出，不破位持有；按判态止盈纪律分批落袋。</div>
            <div class="row" style="font-size:13px; color:#0B3A75;">以"板块主线共振 + 连板高度 + 放量"为前提，单票轻仓、分散 3-5 只。</div>
          </div>
        </div>
      </section>

      <!-- 05 操作建议与风险 -->
      <section>
        <h2 class="sec"><span class="no">05</span>操作建议与风险提示</h2>
        <h3>核心操作建议</h3>
        <ul style="list-style:none; display:grid; gap:12px;">{ops_html}</ul>
        <h3>风险卡片</h3>
        <ul class="risk-list">{risks_html}</ul>
      </section>
    </main>

    <footer>
      <div class="sources">
        <h2>数据来源与运行说明</h2>
        <ol>
          <li>行情数据：腾讯财经 qt.gtimg.cn / 腾讯K线 web.ifzq.gtimg.cn（实时报价 + 日K线）。</li>
          <li>板块映射：本地 sector_mapping.json（3612 只个股 / 992 个板块）+ 名称关键词 fallback。</li>
          <li>引擎：stock-v1224 两阶段选股系统，scan_v1224.py（v12.24 评分），阶段1输出 night20_latest.json。</li>
          <li>运行时间：{escape(run_time)} · 交易日期 {ref}。</li>
        </ol>
        <div class="disclaimer">免责声明：本报告由量化选股系统自动生成，仅供研究参考，不构成任何投资建议。股市有风险，入市需谨慎；据此操作，风险自负。</div>
      </div>
    </footer>
  </div>
</body>
</html>"""
    return html


def main():
    if not os.path.exists(PANEL):
        print(f"!! 找不到阶段1面板数据 {PANEL}")
        return 1
    p = json.load(open(PANEL, encoding='utf-8'))
    ref = p.get('ref_date', '')
    if not ref:
        print("!! night20_panel.json 缺少 ref_date")
        return 1
    os.makedirs(REPORT_DIR, exist_ok=True)
    out_path = os.path.join(REPORT_DIR, f"{ref}_stage1.html")
    html = build_html(p)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    n = len(p.get('top', []))
    print(f"[stage1_report] 阶段1日报已生成: {out_path}  (TOP{n}, ref_date={ref})")
    return 0


if __name__ == '__main__':
    sys.exit(main())