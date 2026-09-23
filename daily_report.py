#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主板两阶段选股系统 日报生成器 daily_report.py
=================================================
读取阶段2竞价筛股输出 /workspace/edge/auction_top5.json,
生成当日 HTML 日报, 存于 /workspace/stock-v1224/reports/ 并以 ref_date 命名.

用法:
  python3 daily_report.py
"""
import json, os, sys
from datetime import datetime, timezone, timedelta
from html import escape

SYSTEM = '/workspace/stock-v1224/system'
AUCTION = '/workspace/edge/auction_top5.json'
REPORT_DIR = os.path.join(os.path.dirname(SYSTEM), 'reports')  # /workspace/stock-v1224/reports

beijing = datetime.now(timezone(timedelta(hours=8)))


def _fmt_num(x, nd=3):
    if x is None:
        return '-'
    try:
        return f"{x:.{nd}f}"
    except (TypeError, ValueError):
        return str(x)


def build_html(data, gen_time):
    date = data.get('date', '')
    ref_date = data.get('date', '')
    method = data.get('method', '')
    sell_rule = data.get('sell_rule', {})
    top5 = data.get('top5', [])
    all20 = data.get('all_20_auction', [])

    # 表头配色
    title = f"主板两阶段选股 · 竞价筛股日报 {date}"

    # TOP5 表格行
    rows = []
    for i, c in enumerate(top5, 1):
        name = escape(str(c.get('name', '')))
        code = str(c.get('code', ''))
        open_px = _fmt_num(c.get('open_px'))
        open_pct = c.get('open_pct')
        open_pct_s = f"{open_pct:+.2f}%" if open_pct is not None else '-'
        base = _fmt_num(c.get('base_score'), 1)
        comp = _fmt_num(c.get('comp'), 1)
        sell = c.get('sell', {})
        tp = _fmt_num(sell.get('take_profit'))
        sl = _fmt_num(sell.get('stop_loss'))
        tp_p = sell.get('take_profit_pct', '-')
        sl_p = sell.get('stop_loss_pct', '-')
        mode = escape(str(c.get('sel_mode', '')))
        rows.append(f"""
      <tr>
        <td>{i}</td>
        <td><b>{name}</b></td>
        <td class="mono">{code}</td>
        <td class="mono">{open_px}</td>
        <td>{open_pct_s}</td>
        <td class="mono">{base}</td>
        <td class="mono">{comp}</td>
        <td class="mono tp">+{tp_p}% / {tp}</td>
        <td class="mono sl">-{sl_p}% / {sl}</td>
        <td>{mode}</td>
      </tr>""")
    top5_rows = ''.join(rows)

    # 全部20只竞价情况
    all_rows = []
    for c in all20:
        name = escape(str(c.get('name', '')))
        code = str(c.get('code', ''))
        # all_20_auction 中 open_pct 字段实际为 [open_pct, open_price] 二元组
        op_raw = c.get('open_pct')
        if isinstance(op_raw, (list, tuple)):
            op = op_raw[0] if len(op_raw) > 0 else None
        else:
            op = op_raw
        op_s = f"{op:+.2f}%" if op is not None else '-'
        flag = ''
        if op is not None and op >= 9.5:
            flag = ' <span class="tag red">剔除·一字涨停</span>'
        all_rows.append(
            f'      <tr><td class="mono">{code}</td><td>{name}</td>'
            f'<td>{op_s}{flag}</td></tr>')
    all_rows_s = ''.join(all_rows)

    tp_rule = sell_rule.get('target_pct', '-')
    sl_rule = sell_rule.get('stop_pct', '-')
    trail = escape(str(sell_rule.get('trail_note', '')))

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
          background: #f4f6fb; color: #1a2333; margin: 0; padding: 24px 16px; }}
  .wrap {{ max-width: 960px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .sub {{ color: #64748b; font-size: 13px; margin-bottom: 18px; }}
  .sec {{ background: #fff; border-radius: 10px; padding: 18px 20px;
          box-shadow: 0 1px 3px rgba(16,24,40,.08); margin-bottom: 18px; }}
  .sec h2 {{ font-size: 16px; margin: 0 0 12px; border-left: 4px solid #2563eb;
             padding-left: 10px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ padding: 9px 8px; text-align: left; border-bottom: 1px solid #eef2f7; }}
  th {{ background: #f8fafc; color: #475569; font-weight: 600; }}
  tr:hover td {{ background: #f8fafc; }}
  .mono {{ font-family: "SF Mono", Consolas, Menlo, monospace; }}
  .tp {{ color: #dc2626; font-weight: 600; }}
  .sl {{ color: #16a34a; font-weight: 600; }}
  .tag {{ font-size: 11px; padding: 1px 6px; border-radius: 4px; }}
  .tag.red {{ background: #fef2f2; color: #dc2626; }}
  .rule {{ background: #f1f5f9; border-radius: 8px; padding: 12px 14px; font-size: 13px;
          color: #334155; line-height: 1.7; }}
  .footer {{ color: #94a3b8; font-size: 12px; margin-top: 8px; text-align: center; }}
  @media (max-width: 640px) {{ table {{ font-size: 12px; }}
          td, th {{ padding: 7px 5px; }} }}
</style>
</head>
<body>
<div class="wrap">
  <h1>主板两阶段选股 · 竞价筛股日报</h1>
  <div class="sub">交易日期 {escape(ref_date)} · 生成于 {escape(gen_time)} · 午后持股策略：当日开盘买入 · 持股1天冲高落袋</div>

  <div class="sec">
    <h2>今日 TOP5（竞价重排 · 买入建议）</h2>
    <table>
      <tr><th>#</th><th>名称</th><th>代码</th><th>开盘价</th><th>竞价涨跌</th>
          <th>基础分</th><th>综合分</th><th>止盈 (+{tp_rule}%)</th><th>止损 (-{sl_rule}%)</th><th>选股模式</th></tr>
      {top5_rows}
    </table>
    <div class="sub" style="margin-top:10px">方法：{escape(method)}</div>
  </div>

  <div class="sec">
    <h2>卖出纪律（价格触发，非时间触发）</h2>
    <div class="rule">
      止盈：冲高至 <b>+{tp_rule}%</b>（或更高）落袋；<b>+5% 以内不手动止盈</b>，让盈利奔跑。<br>
      止损：盘中 / 盘尾跌破 <b>-{sl_rule}%</b> 即走，不死扛。<br>
      移动纪律：{trail}
    </div>
  </div>

  <div class="sec">
    <h2>昨夜 TOP20 今日竞价一览</h2>
    <table>
      <tr><th>代码</th><th>名称</th><th>竞价涨跌</th></tr>
      {all_rows_s}
    </table>
  </div>

  <div class="footer">主板两阶段选股系统 v12.24 · 仅供研究参考，不构成投资建议</div>
</div>
</body>
</html>"""
    return html


def main():
    if not os.path.exists(AUCTION):
        print(f"!! 找不到阶段2输出 {AUCTION}, 日报未生成")
        return 1
    data = json.load(open(AUCTION, encoding='utf-8'))
    ref_date = data.get('date', '')
    if not ref_date:
        print("!! auction_top5.json 缺少 date 字段")
        return 1

    os.makedirs(REPORT_DIR, exist_ok=True)
    out_path = os.path.join(REPORT_DIR, f"{ref_date}.html")
    gen_time = beijing.strftime('%Y-%m-%d %H:%M:%S')

    html = build_html(data, gen_time)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    n = len(data.get('top5', []))
    print(f"[daily_report] 日报已生成: {out_path}  (TOP{n}, ref_date={ref_date})")
    return 0


if __name__ == '__main__':
    sys.exit(main())