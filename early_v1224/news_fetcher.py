#!/usr/bin/env python3
"""
v9.5 实时消息面采集模块
======================
三源聚合: 同花顺(主力) + 华尔街见闻(补充) + 财联社(最快)
自动提取: 板块热点、个股利好、涨价通知、产能信息
替代硬编码的 NEWS_SECTOR_KW，实现消息面动态更新

使用方式:
    from news_fetcher import fetch_realtime_news, analyze_news_to_sectors
    news = fetch_realtime_news()
    sectors = analyze_news_to_sectors(news)
"""

import requests, json, hashlib, time, re
from collections import Counter
from datetime import datetime


# ============ 三源API定义 ============

# --- 源1: 同花顺新闻（主力源，无需认证，标准JSON） ---
THS_NEWS_URL = 'https://news.10jqka.com.cn/tapp/news/push/stock/'
THS_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://news.10jqka.com.cn/',
}

def fetch_ths_news(pages=2):
    """获取同花顺实时新闻（A股相关）"""
    articles = []
    for page in range(1, pages + 1):
        try:
            params = {
                'page': page,
                'tag': '',
                'track': 'website',
                'pagesize': 50,
            }
            r = requests.get(THS_NEWS_URL, params=params, headers=THS_HEADERS, timeout=15)
            if r.status_code != 200: continue
            data = r.json()
            if data.get('code') != '200': continue
            for item in data.get('data', {}).get('list', []):
                articles.append({
                    'source': '同花顺',
                    'title': item.get('title', ''),
                    'digest': item.get('digest', '') or item.get('short', ''),
                    'tags': [t.get('name', '') for t in item.get('tags', [])],
                    'stocks': item.get('stock', []),
                    'ctime': int(item.get('ctime', 0)),
                    'importance': int(item.get('import', 0)),
                    'url': item.get('url', ''),
                })
        except Exception as e:
            print(f"  同花顺获取失败(page={page}): {e}")
    return articles


# --- 源2: 华尔街见闻7x24快讯（补充宏观视角） ---
WSCN_NEWS_URL = 'https://api-one-wscn.awtmt.com/apiv1/content/lives'
WSCN_HEADERS = {
    'User-Agent': 'Mozilla/5.0',
}

def fetch_wscn_news(limit=50):
    """获取华尔街见闻实时快讯"""
    articles = []
    try:
        params = {
            'channel': 'global-channel',
            'client': 'pc',
            'limit': limit,
        }
        r = requests.get(WSCN_NEWS_URL, params=params, headers=WSCN_HEADERS, timeout=15)
        if r.status_code != 200: return articles
        data = r.json()
        if data.get('code') != 20000: return articles
        for item in data.get('data', {}).get('items', []):
            content = item.get('content_text', '') or ''
            # 去除HTML标签
            content = re.sub(r'<[^>]+>', '', content)
            articles.append({
                'source': '华尔街见闻',
                'title': item.get('title', ''),
                'digest': content[:200],
                'tags': [],
                'stocks': [],
                'ctime': item.get('display_time', 0),
                'importance': 0,
                'url': item.get('uri', ''),
            })
    except Exception as e:
        print(f"  华尔街见闻获取失败: {e}")
    return articles


# --- 源3: 财联社电报（最快，需签名） ---
CLS_NEWS_URL = 'https://www.cls.cn/v1/roll/get_roll_list'
CLS_HEADERS = {
    'User-Agent': 'Mozilla/5.0',
    'Referer': 'https://www.cls.cn/telegraph',
}

def cls_sign_params(params):
    """财联社sign签名算法: SHA1(MD5(参数排序序列化))"""
    # 排序参数
    sorted_keys = sorted(params.keys())
    # 序列化
    query_parts = []
    for k in sorted_keys:
        v = params[k]
        if isinstance(v, list):
            for i, item in enumerate(v):
                query_parts.append(f"{k}[{i}]={item}")
        else:
            query_parts.append(f"{k}={v}")
    query_str = '&'.join(query_parts)
    # SHA1
    sha1 = hashlib.sha1(query_str.encode('utf-8')).hexdigest()
    # MD5
    md5 = hashlib.md5(sha1.encode('utf-8')).hexdigest()
    return md5

def fetch_cls_news(rn=50):
    """获取财联社电报快讯"""
    articles = []
    try:
        params = {
            'refresh_type': 1,
            'rn': rn,
            'last_time': int(time.time()),
            'os': 'web',
            'sv': '8.7.9',
            'app': 'CailianpressWeb',
        }
        params['sign'] = cls_sign_params(params)
        r = requests.get(CLS_NEWS_URL, params=params, headers=CLS_HEADERS, timeout=15)
        if r.status_code != 200: return articles
        data = r.json()
        if data.get('errno') != '0':
            print(f"  财联社返回错误: {data.get('errno')} {data.get('msg')}")
            return articles
        for item in data.get('data', {}).get('roll_data', []):
            content = item.get('content', '') or ''
            content = re.sub(r'<[^>]+>', '', content)
            articles.append({
                'source': '财联社',
                'title': item.get('brief', ''),
                'digest': content[:200],
                'tags': [],
                'stocks': [],
                'ctime': item.get('ctime', 0),
                'importance': item.get('importance', 0),
                'url': f"https://www.cls.cn/telegraph/{item.get('id', '')}",
            })
    except Exception as e:
        print(f"  财联社获取失败: {e}")
    return articles


# ============ 统一采集入口 ============

def fetch_realtime_news():
    """三源聚合获取实时财经新闻，返回去重后的新闻列表"""
    all_news = []

    print("📡 采集实时消息面...")
    t0 = time.time()

    # 同花顺（主力源，A股相关度最高）
    print("  [1/3] 同花顺...")
    ths = fetch_ths_news(pages=2)
    print(f"    获取 {len(ths)} 条")
    all_news.extend(ths)

    # 华尔街见闻（宏观/全球视角）
    print("  [2/3] 华尔街见闻...")
    wscn = fetch_wscn_news(limit=50)
    print(f"    获取 {len(wscn)} 条")
    all_news.extend(wscn)

    # 财联社电报（最快，盘中突发）
    print("  [3/3] 财联社...")
    cls = fetch_cls_news(rn=50)
    print(f"    获取 {len(cls)} 条")
    all_news.extend(cls)

    # 去重（按标题相似度）
    seen_titles = set()
    unique_news = []
    for a in all_news:
        title = a['title'].strip()
        if not title or len(title) < 5: continue
        # 简单去重：前10个字符相同视为重复
        key = title[:15]
        if key in seen_titles: continue
        seen_titles.add(key)
        unique_news.append(a)

    # 按时间倒序
    unique_news.sort(key=lambda x: x.get('ctime', 0), reverse=True)

    elapsed = time.time() - t0
    print(f"\n📡 消息面采集完成: {len(unique_news)}条去重新闻 (耗时{elapsed:.1f}s)")
    return unique_news


# ============ 消息面 → 板块/个股分析引擎 ============

# 板块关键词映射（用于从新闻标题/摘要中提取板块）
SECTOR_KEYWORDS = {
    '人形机器人': ['机器人', '人形', '具身智能', '减速器', '伺服', '谐波', '丝杠', '灵巧手', '关节电机', '宇树', '优必选', '埃斯顿', '昊志', '特斯拉机器人', '执行器'],
    '商业航天': ['航天', '卫星', '火箭', '星链', '卫星互联网', 'SpaceX', '星网', '北斗', '遥感', '空天'],
    '黄金/贵金属': ['黄金', '贵金属', '白银', '金价', '金矿', '避险', '非农', '降息'],
    '稀土永磁': ['稀土', '永磁', '镨钕', '氧化镝', '磁材', '磁性材料'],
    '半导体设备/材料': ['半导体', '芯片', '光刻', '封测', '大基金', '国产替代', '硅片', '靶材', '电子特气', '光刻胶'],
    '算力基础设施': ['算力', '光模块', '液冷', 'IDC', '数据中心', '服务器', '散热', 'AI服务器'],
    '创新药': ['创新药', '生物药', '临床试验', 'CDE', 'ADC', 'GLP-1', '抗体', 'CXO'],
    '氟化工/制冷剂': ['制冷剂', '氟化工', '氢氟酸', '六氟', 'PTFE', 'PVDF'],
    '中报预增/业绩': ['业绩预增', '半年报', '中报', '净利润增长', '预增', '业绩', '超预期', '财报'],
    '存储芯片': ['存储', 'DRAM', 'NAND', 'HBM', 'DDR5', '闪存', 'SSD'],
    '汽车零部件': ['汽车零部件', '智能驾驶', '线控底盘', '一体化压铸', '空气悬架', '热管理'],
    '算力/AI服务器': ['AI服务器', 'GPU服务器', '算力芯片', '大模型', '英伟达', 'NVIDIA', 'AMD', '寒武纪'],
    '光模块/CPO': ['光模块', 'CPO', '硅光', '800G', '1.6T', '光通信'],
    '化工/新材料': ['化工', '聚氨酯', '钛白粉', 'MDI', '纯碱', '氯碱'],
    '养殖/农业': ['养殖', '生猪', '猪肉', '鸡苗', '白羽鸡', '饲料', '农业', '糖'],
    '大金融': ['券商', '保险', '银行', '金融', '证券'],
    '面板/玻璃基板': ['面板', 'OLED', 'Mini LED', '玻璃基板'],
    'PCB/电子材料': ['PCB', '覆铜板', '铜箔', 'HDI', 'FR-4'],
    '固态电池': ['固态电池', '电解质', '储能', '锂电'],
    'MLCC/被动元器件': ['MLCC', '电容', '电感', '被动元件'],
    # --- v9.5新增: 截图中出现但原系统缺失的板块 ---
    '钻石散热': ['钻石散热', '金刚石散热', '钻石铜复合', '单晶金刚石', '多晶金刚石'],
    '燃气轮机/电力': ['燃气轮机', 'SOFC', '电力设备', '缺电', '电力短缺', '峰电', '电网'],
    'AI电源/BBU电池': ['BBU', '圆柱电池', 'AI电源', '数据中心电源', '备用电池'],
    '液冷设备': ['液冷', '板式换热器', '冷板', '浸没式液冷', '冷却液'],
    '模拟芯片/DrMOS': ['模拟芯片', 'DrMOS', 'ADI', '电源管理', '模拟器件'],
    'EMC/封装材料': ['EMC', '环氧塑封', '封装材料', '塑封料'],
    '覆铜板/铜箔': ['覆铜板', '铜箔', 'CCL', '建滔', '电子布', '半固化片'],
    '华为概念': ['华为', '昇腾', '鸿蒙', '华为汽车', '问界', '智界'],
    '苹果概念': ['苹果', 'Apple', 'iPhone', 'iPad', 'MacBook', 'Vision Pro'],
    '英伟达概念': ['英伟达', 'NVIDIA', 'GB200', 'GB300', 'Blackwell', 'Rubin', 'NVL'],
    '涨价概念': ['涨价', '提价', '价格上调', '调价', '上调价格', '加工费'],
}

# 利好级别关键词
POSITIVE_KEYWORDS = ['涨价', '超预期', '大增', '突破', '创历史新高', '满产', '供不应求', '缺口', '紧缺', '缺货',
                     '订单', '中标', '签约', '获批', '放量', '翻倍', '暴增', '新高', ' uptrend', '增长']
NEGATIVE_KEYWORDS = ['下跌', '暴跌', '下滑', '下降', '亏损', '违约', '退市', '减持', '减持计划', '风险提示', '警告']

# 财报相关关键词
EARNINGS_KEYWORDS = ['净利润', '营收', 'EPS', '每股收益', '毛利率', '净利率', 'ROE', '业绩', '财报', '半年报', '年报']

# 涨价/供给侧关键词
SUPPLY_KEYWORDS = ['涨价', '提价', '价格上调', '加工费', '供给收缩', '产能不足', '满产', '排产', '交付紧张',
                    '产能缺口', '缺货', '紧缺', '供应紧张', '延长交期', 'lead time']


def analyze_news_to_sectors(news_list):
    """
    从新闻列表中分析出当前热点板块和强度
    返回: {板块名: {count, bonus, keywords_found, sample_titles, style}}
    """
    sector_hits = {}

    for news in news_list:
        title = news.get('title', '')
        digest = news.get('digest', '')
        text = f"{title} {digest}"
        source = news.get('source', '')

        for sector, keywords in SECTOR_KEYWORDS.items():
            matched_kws = []
            for kw in keywords:
                if kw in text:
                    matched_kws.append(kw)

            if matched_kws:
                if sector not in sector_hits:
                    sector_hits[sector] = {
                        'count': 0,
                        'keywords_found': [],
                        'sample_titles': [],
                        'positive': 0,
                        'supply_signal': 0,
                        'earnings_signal': 0,
                    }
                sector_hits[sector]['count'] += 1
                sector_hits[sector]['keywords_found'].extend(matched_kws)
                if len(sector_hits[sector]['sample_titles']) < 3:
                    time_str = ''
                    ctime = news.get('ctime', 0)
                    if ctime:
                        time_str = datetime.fromtimestamp(ctime).strftime('%H:%M')
                    sector_hits[sector]['sample_titles'].append(f"[{source} {time_str}] {title[:40]}")

                # 利好/利空判断
                if any(kw in text for kw in POSITIVE_KEYWORDS):
                    sector_hits[sector]['positive'] += 1
                if any(kw in text for kw in SUPPLY_KEYWORDS):
                    sector_hits[sector]['supply_signal'] += 1
                if any(kw in text for kw in EARNINGS_KEYWORDS):
                    sector_hits[sector]['earnings_signal'] += 1

    # 计算bonus和style
    for sector, data in sector_hits.items():
        count = data['count']
        # bonus = 基础(出现次数) + 供给侧信号加成 + 利好信号加成
        bonus = min(count * 3, 15)
        if data['supply_signal'] > 0:
            bonus = min(bonus + data['supply_signal'] * 2, 20)
        if data['positive'] > 0:
            bonus = min(bonus + data['positive'] * 1, 20)
        data['bonus'] = bonus

        # style判断
        value_sectors = {'黄金/贵金属', '稀土永磁', '氟化工/制冷剂', '养殖/农业', '大金融',
                        '化工/新材料', '中报预增/业绩', '涨价概念'}
        data['style'] = 'value' if sector in value_sectors else 'growth'

    return sector_hits


def generate_sector_report(sector_hits, min_count=2):
    """生成消息面分析报告"""
    if not sector_hits:
        print("  ⚠️ 未检测到热点板块")
        return

    # 按bonus排序
    sorted_sectors = sorted(sector_hits.items(), key=lambda x: x[1]['bonus'], reverse=True)

    print(f"\n📰 实时消息面板块分析（{len(sorted_sectors)}个热点）:")
    print("=" * 70)

    for i, (sector, data) in enumerate(sorted_sectors):
        if data['count'] < min_count: continue
        style_icon = '🟡价值' if data['style'] == 'value' else '🔵成长'
        supply_tag = ' 🔥供给侧信号' if data['supply_signal'] > 0 else ''
        positive_tag = f' ✅利好{data["positive"]}条' if data['positive'] > 0 else ''

        print(f"  [{data['bonus']:2d}] {style_icon} {sector}{supply_tag}{positive_tag}")

        # 去重关键词展示
        unique_kws = list(dict.fromkeys(data['keywords_found']))[:8]
        if unique_kws:
            print(f"      关键词: {', '.join(unique_kws)}")

        for title in data['sample_titles'][:2]:
            print(f"      · {title}")


def extract_stock_mentions(news_list):
    """从新闻中提取被提及的个股"""
    stock_mentions = Counter()
    for news in news_list:
        # 从同花顺的结构化数据中提取
        stocks = news.get('stocks', [])
        for s in stocks:
            name = s.get('name', '')
            code = s.get('stockCode', '')
            if name:
                stock_mentions[name] += 1

        # 从标题中用正则提取A股名称（XX股份/XX科技/XX集团等）
        title = news.get('title', '')
        matches = re.findall(r'([\u4e00-\u9fa5]{2,4}(?:股份|科技|集团|电子|电气|材料|能源|医药|生物|化工|机械|设备|控股))', title)
        for m in matches:
            stock_mentions[m] += 1

    return stock_mentions.most_common(30)


# ============ 导出为选股系统可用的格式 ============

def export_to_scan_format(sector_hits):
    """
    将分析结果导出为 scan_stocks_v9.py 可用的 NEWS_SECTOR_KW 格式
    返回可直接替换的字典
    """
    result = {}
    for sector, data in sector_hits.items():
        if data['count'] < 1: continue
        # 收集所有出现过的关键词
        unique_kws = list(dict.fromkeys(data['keywords_found']))
        result[sector] = {
            'bonus': data['bonus'],
            'keywords': unique_kws,
            'style': data['style'],
        }
    return result


# ============ 独立测试 ============

if __name__ == '__main__':
    print("=" * 70)
    print("v9.5 实时消息面采集测试")
    print("=" * 70)

    # 1. 采集新闻
    news = fetch_realtime_news()

    # 2. 分析板块
    sector_hits = analyze_news_to_sectors(news)

    # 3. 生成报告
    generate_sector_report(sector_hits, min_count=1)

    # 4. 个股提及
    stock_mentions = extract_stock_mentions(news)
    if stock_mentions:
        print(f"\n📊 被新闻提及的个股TOP20:")
        print("-" * 50)
        for name, count in stock_mentions[:20]:
            print(f"  {name}: {count}次提及")

    # 5. 导出格式
    exported = export_to_scan_format(sector_hits)
    print(f"\n📋 可导出的板块数: {len(exported)}")
    for sector, info in sorted(exported.items(), key=lambda x: -x[1]['bonus'])[:5]:
        print(f"  {sector}: bonus={info['bonus']}, 关键词={info['keywords'][:5]}")
