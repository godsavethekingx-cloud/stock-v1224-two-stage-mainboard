#!/usr/bin/env python3
"""
v12.26 实盘选股系统 — 动态regime+连板接力版
================================================================
基于v12.24回测验证的最优评分逻辑，适配实盘实时数据

【核心特性】
  1. 自动量比识别: 缩量/放量自动检测, 建议最优regime
  2. BULL模式: 主线龙头优先 + 激进追强 + 连板续涨奖励
  3. RANGE_BULL模式: 5攻1防 + 板块热度优先
  4. CORRECTION模式: 防守为主 + 板块热度加分
  5. RELAY模式(v12.26新增): 连板接力策略, 缩量回踩日专用
  6. USER模式: 用户条件+涨停基因+放量蓄势+可买性
  7. 动态权重: RSI/放量时间/距前高根据regime自适应调整
  8. 一字板预警: 风险分级(HIGH/MEDIUM/LOW/SAFE), 区分可追vs不可追
  9. 板块热度强化: 权重提升至25%, 主线确认额外加成
  10. 20+技术因子: 涨停基因/量价关系/板块轮动/起爆初期/局部牛市

【用法】
  python3 scan_v1224.py                          # 交互式(输入态势判断)
  python3 scan_v1224.py --regime BULL            # 指定BULL态势
  python3 scan_v1224.py --regime RANGE_BULL      # 指定震荡偏多
  python3 scan_v1224.py --regime CORRECTION      # 指定回调
  python3 scan_v1224.py --regime RELAY           # 指定连板接力(缩量回踩日)
  python3 scan_v1224.py --regime USER            # 用户条件模式(涨停基因+放量蓄势+可买性)
  python3 scan_v1224.py --top 10                 # 输出TOP10(默认TOP6)
"""
import json, requests, time, random, os, sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict

BEIJING_TZ = timezone(timedelta(hours=8))
def now_bj(): return datetime.now(BEIJING_TZ)
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# 代理配置: 沙箱通过HTTP代理访问外部API
_PROXIES = None
def _get_proxies():
    global _PROXIES
    if _PROXIES is not None:
        return _PROXIES if _PROXIES else None
    import socket
    try:
        sock = socket.create_connection(('qt.gtimg.cn', 443), timeout=3)
        sock.close()
        _PROXIES = False  # 直连可用
    except:
        _PROXIES = {'http': 'http://127.0.0.1:18080', 'https': 'http://127.0.0.1:18080'}
    return _PROXIES if _PROXIES else None

# ==================== 板块定义 ====================
DEFENSIVE_SECTORS = {'贵金属', '煤炭', '电力电网', '消费医药', '军工', '食品饮料', '银行保险', '家电'}

# 保留SECTOR_KW作为名称匹配的fallback (v3: 62个板块全覆盖)
SECTOR_KW = {
    # === 原有21个板块 ===
    'PCB': ['电路','覆铜','奥士康','生益','华正','博敏','方正科技','景旺','沪电','兴森','胜宏','明阳','骏亚','中京','普林','依顿','金安','超声','超华','崇达','科翔','逸豪','金百泽'],
    '6G通信': ['通宇','凡谷','大富','盛路','硕贝德','灿勤','信维','麦捷','春兴','通光','中富通','中光','超讯','纵横','华脉','瑞斯','广和通','移为','移远','高新兴','三环','中贝通信','通光线缆','东方通信','波导','长江通信','烽火','特发信息','永鼎','国脉','邦讯'],
    'CPO光通信': ['光迅','华工','铭普','中贝','三安','亨通','烽火','特发','永鼎','中际','新易盛','天孚','光库','太辰','博创','腾景','剑桥','联特','德科立','源杰'],
    '先进封装': ['封测','封装','长电','通富','华天','晶方','甬矽','环旭','安靠','气派','同兴达'],
    '电子特气': ['特气','气体','金宏','华特','和远','凯美','南大','雅克','正帆','昊华','广钢'],
    '半导体': ['半导体','芯片','中芯','华虹','北方华创','中微','沪硅','立昂微','中晶','卓胜','圣邦','拓荆','江丰','有研','阿石创','安集','鼎龙'],
    '煤炭': ['煤','山西焦','兖州','陕西煤','中煤','潞安','昊华能源','兰花','阳泉','盘江','恒源','靖远','大同'],
    '贵金属': ['黄金','白银','山东黄金','紫金','中金黄金','赤峰','银泰','湖南黄金','恒邦','荣华','四川黄金','招金'],
    'AI算力': ['算力','浪潮','中科','紫光','拓维','神州','直真','润建','英维克','高澜','曙光','富信'],
    '数字货币': ['数字','飞天','恒宝','翠微','广电','数字认证','格尔','信安','御银','创识','雄帝','朗科'],
    '商业航天': ['航天','火箭','卫星','天银','盟升','星网','海格','北斗','航天电子','航天动力','航天晨光','中国卫星','中国卫通','星网宇达','神剑','中天火箭'],
    '电力电网': ['电力','电网','特高压','许继','国电','华银','涪陵','桂东','岷江','西昌','乐山','闽东','粤电力','晋控','明星'],
    '消费医药': ['医药','消费','白酒','茅台','五粮液','汾酒','泸州','古井','迎驾','今世缘','口子窖','老白干','水井坊','舍得','酒鬼','福元','浙江医药','南京医药','冀衡','中国医药','亿帆'],
    '军工': ['军工','航发','中航','沈飞','西飞','洪都','成飞','贵航','航天科技','航天通信','北方导航','光电股份','中光学'],
    '新能源': ['新能源','锂电','宁德','比亚迪','光伏','隆基','通威','阳光','风电','金风','明阳智能','天合','晶澳','东方日升'],
    '房地产': ['地产','万科','保利','招商','绿地','华夏幸福','碧桂园','融创','龙湖','金地','华发','城建'],
    '半导体材料': ['硅片','靶材','光刻胶','抛光液','抛光垫','江丰','有研','阿石创','鼎龙','安集','沪硅','立昂','中晶','雅克','南大','华特','晶升'],
    '电子化学品': ['电子化学','湿电子化学','光刻胶','溶剂','显影液','剥离液','清洗液','晶瑞','江化微','西陇','格林达','新莱','上海新阳'],
    '电子布': ['电子布','玻纤布','宏和','中材','华正','生益','建滔','中纺'],
    'MLCC元件': ['MLCC','电容','电阻','被动元件','风华','三环','火炬','鸿远','宏达','法拉','江海','顺络'],
    '铜箔': ['铜箔','嘉元','诺德','铜冠','中一','德福','超华','金安'],
    # === v2新增15个板块 ===
    '人形机器人': ['机器人','减速器','伺服','谐波','绿的','鸣志','步科','中大力德','兆易','拓普','三花','江苏雷利'],
    '低空经济': ['低空','eVTOL','飞行汽车','无人机','纵横','莱斯','宗申','万丰','中信海直','纵横股份'],
    '固态电池': ['固态电池','半固态','硫化物','氧化物','电解质','科达利','骄成','德邦','圣泉','元利'],
    '华为产业链': ['华为','鸿蒙','欧拉','鲲鹏','昇腾','常山北明','润和','软通','诚迈'],
    '铜缆高速连接': ['铜缆','高速连接','DAC','AEC','精达','沃尔','鼎通','瑞可达','神宇','兆龙'],
    '存储芯片': ['存储','DRAM','NAND','HBM','兆易','江波龙','北京君正','普冉','澜起','佰维','德明利'],
    '第三代半导体': ['碳化硅','氮化镓','SiC','GaN','三安','士兰','斯达','天岳','华润微','英诺'],
    '数据要素': ['数据要素','数据资产','数据交易','数据确权','易华录','太极','深桑达','广电运通','同花顺'],
    '信创国产软件': ['信创','国产软件','操作系统','数据库','中间件','中国软件','金山','中望','龙芯','海光','浪潮'],
    '液冷数据中心': ['液冷','数据中心','冷却','散热','英维克','曙光数创','高澜','申菱','同飞','飞龙'],
    '稀土永磁': ['稀土','永磁','磁材','北方稀土','中科三环','金力','正海','横店','厦门钨业','盛和'],
    '创新药CXO': ['创新药','CXO','CRO','CDMO','药明','凯莱英','泰格','康龙','博腾','皓元','百济'],
    '智能驾驶': ['智能驾驶','自动驾驶','ADAS','激光雷达','域控','德赛西威','中科创达','四维图新','华阳'],
    '储能': ['储能','储能系统','PCS','BMS','阳光电源','宁德','派能','南都','科华','上能'],
    '卫星互联网': ['卫星互联网','低轨卫星','星链','北斗','中国卫星','中国卫通','海格','华力创通','灿勤'],
    # === v3新增26个板块 ===
    '消费电子': ['消费电子','苹果产业链','立讯','歌尔','蓝思','京东方','领益','工业富联','传音','鹏鼎','东山'],
    '光伏': ['光伏','太阳能','多晶硅','硅片','电池片','组件','隆基','通威','东方日升','大全','晶澳','天合','爱旭','福莱特','福斯特','迈为','帝尔激光'],
    '锂电池': ['锂电池','锂电','正极','负极','隔膜','电解液','宁德','比亚迪','亿纬','国轩','欣旺达','德赛','璞泰来','恩捷','天赐','新宙邦'],
    '券商': ['券商','证券','东方财富','中信证券','华泰','国泰君安','海通','招商','广发','东方证券','中信建投'],
    '汽车零部件': ['汽车零部件','汽车配件','伯特利','拓普','华阳','德赛西威','保隆','华域','福耀','银轮'],
    '游戏传媒': ['游戏','传媒','影视','完美世界','三七互娱','吉比特','巨人','光线传媒','芒果超媒','世纪华通','华策影视','中文在线'],
    'AI应用': ['AI应用','AIGC','大模型','ChatGPT','昆仑万维','科大讯飞','金山办公','同花顺','三六零','万兴','拓尔思'],
    '钠离子电池': ['钠电池','钠离子','华阳股份','圣阳','七彩化学','多氟多','容百'],
    '锂矿': ['锂矿','锂资源','碳酸锂','天齐锂业','赣锋','盐湖','西藏矿业','藏格','永兴','中矿','雅化'],
    '医疗器械': ['医疗器械','医疗设备','迈瑞','联影','开立','欧普康视','乐普','健帆','爱博','南微'],
    '化工新材料': ['化工','新材料','万华化学','卫星化学','宝丰能源','荣盛石化','恒力石化','桐昆','新和成','国瓷材料'],
    '家电': ['家电','白色家电','美的','格力','海尔','老板','海信','三花智控','盾安','长虹'],
    '食品饮料': ['食品','饮料','白酒','啤酒','调味品','茅台','五粮液','泸州老窖','汾酒','海天','伊利','东鹏'],
    '钢铁': ['钢铁','钢','宝钢','华菱','首钢','太钢','中信特钢','鞍钢','方大特钢','永兴'],
    '有色金属': ['有色金属','铜','铝','锌','锡','钴','镍','紫金矿业','洛阳钼业','中国铝业','铜陵有色','锡业','云南铜业'],
    '脑机接口': ['脑机接口','BCI','脑电','三博脑科','冠昊','爱朋','南京熊猫','创新医疗'],
    '教育': ['教育','培训','职业教育','在线教育','中公','传智','昂立','豆神','全通','科德'],
    '旅游酒店': ['旅游','酒店','景区','长白山','张家界','黄山','峨眉山','桂林','西安旅游','首旅','锦江','华天','宋城'],
    '跨境电商': ['跨境电商','出海','安克','致欧','华凯','赛维','焦点科技','吉宏'],
    '农业': ['农业','种植','养殖','种业','猪','牧原','温氏','新希望','大北农','隆平高科','登海','荃银'],
    '环保': ['环保','环境','污水','固废','危废','伟明','瀚蓝','旺能','高能','首创','上海环境'],
    '纺织服装': ['纺织','服装','服饰','雅戈尔','海澜','森马','太平鸟','报喜鸟','七匹狼','罗莱'],
    '美容护理': ['美容','化妆品','护肤','珀莱雅','上海家化','贝泰妮','华熙','爱美客','丸美','水羊'],
    '装修建材': ['建材','水泥','防水','管材','涂料','东方雨虹','北新建材','伟星','三棵树','蒙娜丽莎','坚朗','兔宝宝','海螺水泥'],
    '交通运输': ['交通','运输','物流','航运','港口','快递','中远海控','顺丰','韵达','申通','上港','宁波港'],
    '银行保险': ['银行','保险','招商银行','宁波银行','平安','中国人寿','中国太保','工商银行','建设银行','农业银行'],
    # === v4新增: 周末消息面重点板块 ===
    '工业母机': ['机床','数控','精密','海天精工','秦川','华东数控','宇晶','沈阳机床','亚威','华明','宇环','思进','科德','拓斯达','创世纪','田中','华东重机','海德曼','浙海德曼','纽威','国盛智科','日发','青海华鼎'],
    '半导体设备': ['半导体设备','刻蚀','薄膜','清洗','检测','北方华创','中微','拓荆','盛美','华海清科','芯源微','中科飞测','精测','长川','华峰','至纯','万业','金海通','联动科技'],
    '芯片设计': ['芯片设计','IC设计','FPGA','模拟芯片','数字芯片','韦尔','兆易','卓胜','圣邦','北京君正','紫光国微','澜起','全志','瑞芯微','国民技术','中颖','富瀚','景嘉微','安路','国芯'],
}

# ==================== 加载精确板块映射表 ====================
_SECTOR_MAPPING = None
_CODE_TO_SECTORS = {}

def load_sector_mapping():
    """加载板块-个股精确映射表(JSON)"""
    global _SECTOR_MAPPING, _CODE_TO_SECTORS
    if _SECTOR_MAPPING is not None:
        return
    mapping_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sector_mapping.json')
    if os.path.exists(mapping_path):
        try:
            with open(mapping_path, 'r', encoding='utf-8') as f:
                _SECTOR_MAPPING = json.load(f)
            _CODE_TO_SECTORS = _SECTOR_MAPPING.get('code_to_sectors', {})
            print(f"  板块映射表已加载: {len(_CODE_TO_SECTORS)}只个股, {_SECTOR_MAPPING.get('total_sectors', 0)}个板块")
        except Exception as e:
            print(f"  板块映射表加载失败: {e}, 使用名称匹配fallback")
            _SECTOR_MAPPING = {}
    else:
        print(f"  板块映射表不存在, 使用名称匹配fallback")
        _SECTOR_MAPPING = {}

# 属性类标签黑名单: 资金指数/估值风格/地域政策泛化等"非题材板块"标签。
# 若不剔除, 融资融券/沪股通/机构重仓 这类属性会被当成板块聚合涨停数,
# 把 PCB/6G/磷化工 等真题材挤出板块热力与技术面 TOP。
# (0908 复盘: 修复"板块技术面 TOP 被泛化标签污染" )
_SECTOR_ATTR_EXCLUDE = frozenset({
    '融资融券', '转融券', '沪股通', '深股通', '港股通', '沪伦通', '陆股通',
    '富时罗素', '标准普尔', 'MSCI中国', 'MSCI概念', '沪深300', '中证500',
    '上证50', '深证100', '深证成指', '创业板综', '上证180', '中证1000',
    '机构重仓', 'QFII重仓', '基金重仓', '社保重仓', '养老目标', '游资',
    '破净股', '破发股', '破增发价股', '高市净率', '低市净率', '高市盈率',
    '低市盈率', '低估值', '高股息', '高送转', '低价股', '超跌', '次新股',
    '绩优股', '白马股', '蓝筹股', '大盘股', '中盘股', '小盘股', '微盘股',
    '昨日高振幅', '昨日涨停', '昨涨停', '昨日连板', '昨日换手活跃', '活跃股',
    '强势股', '题材股', '高换手', '举牌', '壳资源', '摘帽', '炒壳',
    '西部大开发', '中部崛起', '东北振兴', '长三角', '京津冀', '粤港澳大湾区',
    '参股银行', '参股券商', '参股期货', '参股保险', '参股新三板', '参股民营银行',
    '央国企改革', '国企改革', '地方国资改革', '股权激励',
    '2026中报预增', '2026中报扭亏', '2026中报首亏', '2026中报预减',
    '2025年报预增', '2025年报扭亏', '2025年报首亏',
})

def _industry_only(secs):
    """过滤属性类标签, 只保留真正的行业/题材板块."""
    return [s for s in secs if s not in _SECTOR_ATTR_EXCLUDE]


def match_sector(name, code=None):
    """
    精确匹配个股所属板块
    优先使用code查映射表, fallback用名称关键词匹配
    """
    # 优先: 通过code查精确映射表
    if code and _CODE_TO_SECTORS:
        sectors = _CODE_TO_SECTORS.get(code)
        if sectors:
            return _industry_only(sectors)

    # Fallback: 名称关键词匹配
    r = []
    for sec, kws in SECTOR_KW.items():
        for kw in kws:
            if kw in name:
                r.append(sec)
                break
    return _industry_only(r)

def is_defensive(name, code=None):
    matched = match_sector(name, code)
    return any(s in DEFENSIVE_SECTORS for s in matched)

# ==================== 1. 获取全量股票(腾讯API) ====================
def get_all_stocks():
    stocks = []
    sh = [f'60{i:04d}' for i in range(0, 4001)]
    sz = [f'{i:06d}' for i in range(1, 3000)]
    codes = sh + sz
    codes = [c for c in codes if not c.startswith('688') and not c.startswith('300') and not c.startswith('301')]

    bs = 100
    nb = (len(codes) + bs - 1) // bs
    print(f"  总代码:{len(codes)} 批次:{nb}", flush=True)

    for bi in range(nb):
        batch = codes[bi*bs:(bi+1)*bs]
        syms = [f'sh{c}' if c.startswith('6') else f'sz{c}' for c in batch]
        url = f'http://qt.gtimg.cn/q={",".join(syms)}'
        try:
            r = requests.get(url, timeout=8, headers=HEADERS, proxies=_get_proxies())
            for line in r.text.strip().split(';'):
                if not line.strip():
                    continue
                try:
                    eq = line.index('=')
                    content = line[eq+2:].strip().rstrip('"').rstrip('";').strip('"')
                    f = content.split('~')
                    if len(f) < 50:
                        continue
                    code, name = f[2], f[1]
                    price = float(f[3]) if f[3] else 0
                    lc = float(f[4]) if f[4] else 0
                    if price <= 0 or lc <= 0:
                        continue
                    if 'ST' in name or '退' in name:
                        continue
                    stocks.append({
                        'code': code, 'name': name, 'price': price,
                        'circ_mv': float(f[44]) if len(f) > 44 and f[44] and f[44] != '-' else 0,
                    })
                except:
                    continue
        except:
            pass
        if bi % 15 == 0:
            print(f"  进度:{bi+1}/{nb} 已获取:{len(stocks)}", flush=True)
        time.sleep(0.03)
    return stocks

# ==================== 2. 获取K线(腾讯API) ====================
def get_kl(code, datalen=80):
    p = 'sh' if code.startswith('6') else 'sz'
    # v10.9: 优先 https /kline/kline (规避 WAF 对 fqkline 的 501 拦截)
    parsers = (
        f'https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={p}{code},day,,,{datalen}',
        f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={p}{code},day,,,{datalen},qfq',
        f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={p}{code},day,,,{datalen},qfq',
    )
    for url in parsers:
        try:
            r = requests.get(url, timeout=(5, 10), headers=HEADERS, proxies=_get_proxies())
            d = r.json().get('data', {}).get(f'{p}{code}', {})
            kl = d.get('day', []) or d.get('qfqday', [])
            if kl and len(kl) >= 10:
                return {
                    'd': [k[0] for k in kl],
                    'c': [float(k[2]) for k in kl],
                    'o': [float(k[1]) for k in kl],
                    'h': [float(k[3]) for k in kl],
                    'l': [float(k[4]) for k in kl],
                    'v': [float(k[5])/100 for k in kl],
                }
        except:
            continue
    return None

# ==================== 3. 获取指数K线 ====================
def get_index_kl(datalen=60):
    parsers = (
        f'https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param=sh000001,day,,,{datalen}',
        f'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,{datalen},',
        f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,{datalen},',
    )
    for url in parsers:
        try:
            r = requests.get(url, timeout=(5, 10), headers=HEADERS, proxies=_get_proxies())
            d = r.json().get('data', {}).get('sh000001', {})
            kl = d.get('day', []) or d.get('qfqday', [])
            if kl and len(kl) >= 10:
                return {
                    'd': [k[0] for k in kl],
                    'c': [float(k[2]) for k in kl],
                    'o': [float(k[1]) for k in kl],
                    'h': [float(k[3]) for k in kl],
                    'l': [float(k[4]) for k in kl],
                    'v': [float(k[5]) for k in kl],
                }
        except:
            continue
    return None

# ==================== 4. 获取实时行情(竞价/开盘) ====================
def get_realtime_quote(codes):
    """批量获取实时行情, 用于竞价强度和开盘价"""
    results = {}
    bs = 80
    for i in range(0, len(codes), bs):
        batch = codes[i:i+bs]
        syms = [f'sh{c}' if c.startswith('6') else f'sz{c}' for c in batch]
        for scheme in ('https', 'http'):
            url = f'{scheme}://qt.gtimg.cn/q={",".join(syms)}'
            try:
                r = requests.get(url, timeout=8, headers=HEADERS, proxies=_get_proxies())
                parsed = 0
                for line in r.text.strip().split(';'):
                    if not line.strip():
                        continue
                    try:
                        eq = line.index('=')
                        content = line[eq+2:].strip().rstrip('"').rstrip('";').strip('"')
                        f = content.split('~')
                        if len(f) < 50:
                            continue
                        code = f[2]
                        open_price = float(f[5]) if f[5] else 0
                        last_close = float(f[4]) if f[4] else 0
                        current = float(f[3]) if f[3] else 0
                        high = float(f[6]) if f[6] else 0
                        low = float(f[7]) if f[7] else 0
                        if last_close > 0:
                            open_pct = (open_price / last_close - 1) * 100
                        else:
                            open_pct = 0
                        results[code] = {
                            'open_price': open_price,
                            'last_close': last_close,
                            'current': current,
                            'high': high,
                            'low': low,
                            'open_pct': round(open_pct, 2),
                        }
                        parsed += 1
                    except:
                        continue
                if parsed > 0:
                    break
            except:
                continue
        time.sleep(0.03)
    return results

# ==================== 5. 技术分析(T-1日数据) ====================
def analyze_tech(kl):
    """分析最新K线数据, 使用倒数第二天作为T-1日"""
    dates = kl['d']
    c = kl['c']; o = kl['o']; h = kl['h']; l = kl['l']; v = kl['v']
    n = len(dates)

    if n < 7:
        return None

    # T-1日 = 倒数第一天(最新完整交易日)
    yi = n - 1
    yc = c[yi]; yo = o[yi]; yh = h[yi]; yl = l[yi]; yv = v[yi]
    pc = c[yi-1]; pv = v[yi-1]

    yp = (yc/pc - 1) * 100 if pc > 0 else 0
    ya = (yh - yl) / pc * 100 if pc > 0 else 0
    yang = yc >= yo
    vr = yv / pv if pv > 0 else 1

    t2_pct = (c[yi-1] / c[yi-2] - 1) * 100 if yi >= 2 and c[yi-2] > 0 else 0

    ma5 = sum(c[max(0, yi-4):yi+1]) / min(5, yi+1)
    above_ma5 = yc > ma5 if ma5 > 0 else False
    ma10a = c[max(0, yi-9):yi+1]
    ma10 = sum(ma10a) / len(ma10a) if len(ma10a) >= 5 else 0
    ma20a = c[max(0, yi-19):yi+1]
    ma20 = sum(ma20a) / len(ma20a) if len(ma20a) >= 10 else ma10
    ma_bull = ma5 > ma10 if ma5 > 0 and ma10 > 0 else False

    dev_ma5 = (yc / ma5 - 1) * 100 if ma5 > 0 else 0
    dev_ma10 = (yc / ma10 - 1) * 100 if ma10 > 0 else 0
    dev_ma20 = (yc / ma20 - 1) * 100 if ma20 > 0 else 0

    p5 = (yc / c[yi-5] - 1) * 100 if yi >= 5 and c[yi-5] > 0 else 0

    gains = []
    losses = []
    for j in range(max(1, yi-5), yi+1):
        chg = c[j] - c[j-1]
        if chg > 0:
            gains.append(chg)
        else:
            losses.append(abs(chg))
    avg_gain = sum(gains) / 6 if gains else 0
    avg_loss = sum(losses) / 6 if losses else 0.001
    rsi6 = 100 - 100 / (1 + avg_gain / avg_loss) if avg_loss > 0 else 100

    z5 = z30 = mxc = cur = 0
    for j in range(1, yi+1):
        if c[j-1] > 0:
            dp = (c[j] / c[j-1] - 1) * 100
            if dp >= 9.5:
                z30 += 1
                if j >= yi - 4:
                    z5 += 1
                cur += 1
                mxc = max(mxc, cur)
            else:
                cur = 0

    yzt = yp >= 9.5
    ls = min(yo, yc) - yl
    bd = abs(yc - yo)
    hll = ls > bd * 1.5 if bd > 0 else ls > 0

    h20 = max(h[max(0, yi-19):yi+1]) if yi >= 1 else 0
    bk20 = yc >= h20 * 0.98 if h20 > 0 else False
    bks = yc >= h20 if h20 > 0 else False
    dist_high20 = (yc / h20 - 1) * 100 if h20 > 0 else -10

    vw = v[max(0, yi-4):yi+1]
    av5 = sum(vw) / len(vw) if vw else yv
    vexp = yv > av5 * 1.2 if av5 > 0 else False

    return {
        'yest_close': round(yc, 2), 'yest_pct': round(yp, 2),
        'yest_amp': round(ya, 2), 'is_yang': yang,
        'vol_ratio': round(vr, 2), 'above_ma5': above_ma5,
        'ma_bull': ma_bull, 'ma5': round(ma5, 2), 'ma10': round(ma10, 2),
        'ma20': round(ma20, 2),
        'dev_ma5': round(dev_ma5, 2), 'dev_ma10': round(dev_ma10, 2),
        'dev_ma20': round(dev_ma20, 2),
        'pct_5d': round(p5, 2), 'zt_5d': z5, 'zt_30d': z30,
        'max_consec': mxc, 'is_yest_zt': yzt, 'has_long_lower': hll,
        'breakout_20d': bk20, 'breakout_strong': bks,
        'vol_expanding': vexp, 'high_20d': round(h20, 2),
        'dist_high20': round(dist_high20, 2),
        'rsi6': round(rsi6, 1), 't2_pct': round(t2_pct, 2),
    }

# ==================== 6. 大盘走势分析 ====================
def analyze_market(index_kl):
    dates = index_kl['d']
    c = index_kl['c']; o = index_kl['o']; h = index_kl['h']; l = index_kl['l']; v = index_kl['v']
    n = len(dates)
    if n < 7:
        return None

    yi = n - 1

    consec_up = 0
    for j in range(yi, 0, -1):
        dp = (c[j] / c[j-1] - 1) * 100
        if dp > 0:
            consec_up += 1
        else:
            break

    pct_1d = (c[yi] / c[yi-1] - 1) * 100 if yi >= 1 else 0
    pct_3d = (c[yi] / c[yi-3] - 1) * 100 if yi >= 3 else 0
    pct_5d = (c[yi] / c[yi-5] - 1) * 100 if yi >= 5 else 0

    vol_recent = sum(v[max(0, yi-4):yi+1]) / min(5, yi+1)
    vol_before = sum(v[max(0, yi-9):max(0, yi-4)]) / min(5, max(1, yi-4))
    vol_ratio = vol_recent / vol_before if vol_before > 0 else 1

    ma5 = sum(c[max(0, yi-4):yi+1]) / min(5, yi+1)
    ma10 = sum(c[max(0, yi-9):yi+1]) / min(10, yi+1) if yi >= 9 else ma5
    ma20 = sum(c[max(0, yi-19):yi+1]) / min(20, yi+1) if yi >= 19 else ma10
    above_ma5 = c[yi] > ma5
    above_ma20 = c[yi] > ma20
    ma_bull = ma5 > ma10 > ma20

    t1_yang = c[yi] > o[yi]
    t1_big_yang = (c[yi] / o[yi] - 1) * 100 > 1.5

    # v12.26: 自动量比识别 — 判断缩量/放量，辅助用户选择regime
    vol_shrink = vol_ratio < 0.8
    vol_expand = vol_ratio > 1.2
    # 连续2日缩量检测
    consec_shrink = 0
    for j in range(yi, max(yi-3, 0), -1):
        v_recent_j = v[j] if j >= 0 else 0
        v_before_j = v[j-1] if j-1 >= 0 else 1
        if v_before_j > 0 and v_recent_j / v_before_j < 0.9:
            consec_shrink += 1
        else:
            break

    # 自动建议regime
    if vol_shrink or consec_shrink >= 2:
        suggested_regime = 'RELAY'
        vol_advice = f'缩量信号(量比{vol_ratio:.2f}, 连续{consec_shrink}日缩量) → 建议RELAY连板接力模式'
    elif vol_expand:
        suggested_regime = 'BULL'
        vol_advice = f'放量信号(量比{vol_ratio:.2f}) → 建议BULL/USER模式'
    else:
        suggested_regime = 'RANGE_BULL'
        vol_advice = f'平量(量比{vol_ratio:.2f}) → 建议RANGE_BULL模式'

    return {
        'consec_up': consec_up,
        'pct_1d': round(pct_1d, 2),
        'pct_3d': round(pct_3d, 2),
        'pct_5d': round(pct_5d, 2),
        'vol_ratio': round(vol_ratio, 2),
        'above_ma5': above_ma5, 'above_ma20': above_ma20,
        'ma_bull': ma_bull,
        't1_yang': t1_yang, 't1_big_yang': t1_big_yang,
        'close': round(c[yi], 2),
        # v12.26 新增
        'vol_shrink': vol_shrink,
        'vol_expand': vol_expand,
        'consec_shrink': consec_shrink,
        'suggested_regime': suggested_regime,
        'vol_advice': vol_advice,
    }

# ==================== 7. v12.12 基础评分 ====================
def score_v1212(tech, hot_sectors, name, code, open_pct=0):
    matched = match_sector(name, code)
    bs = ''
    sm = 1.0
    for s in matched:
        if s in hot_sectors:
            bs = s
            stats = hot_sectors[s]
            sm = 1.5 if stats.get('zt', 0) >= 3 else (1.4 if stats.get('surging', 0) >= 3 else (1.2 if stats.get('surging', 0) >= 2 else 1.0))
            break
    if not bs and matched:
        bs = matched[0]

    g = 0
    if tech['zt_30d'] >= 10: g = 15
    elif tech['zt_30d'] >= 5: g = 12
    elif tech['zt_30d'] >= 3: g = 8
    elif tech['zt_30d'] >= 1: g = 4
    if tech['max_consec'] >= 3: g = min(g + 3, 15)

    p = 0
    if tech['yest_amp'] >= 3: p += 5
    if tech['is_yang']: p += 5
    if tech['above_ma5']: p += 5
    if tech['has_long_lower']: p = min(p + 3, 15)
    p = min(p, 15)

    vp = 0
    vr = tech['vol_ratio']
    if vr >= 3: vp = 15
    elif vr >= 2: vp = 12
    elif vr >= 1.5: vp = 8
    elif vr >= 1: vp = 5
    if tech['vol_expanding']: vp = min(vp + 3, 15)

    m = 0
    p5 = tech['pct_5d']
    if p5 >= 20: m = 15
    elif p5 >= 15: m = 12
    elif p5 >= 10: m = 10
    elif p5 >= 5: m = 7
    elif p5 >= 0: m = 3
    if tech['breakout_20d']: m = min(m + 3, 15)

    ss = 0
    if bs in hot_sectors:
        stats = hot_sectors[bs]
        if stats.get('zt', 0) >= 3: ss = 20
        elif stats.get('surging', 0) >= 3: ss = 16
        elif stats.get('surging', 0) >= 2: ss = 12

    cc = 0
    if tech['is_yest_zt']:
        if tech['max_consec'] >= 3: cc = 10
        elif tech['zt_5d'] >= 2: cc = 8
        else: cc = 6

    rsi_s = 0
    rsi = tech['rsi6']
    if 40 <= rsi <= 60: rsi_s = 10
    elif 60 < rsi <= 70: rsi_s = 8
    elif 30 <= rsi < 40: rsi_s = 7
    elif 70 < rsi <= 80: rsi_s = 5
    elif rsi > 80: rsi_s = 2
    else: rsi_s = 3

    base = g + p + vp + m + ss + cc + rsi_s
    score = base * sm

    result = {
        'code': code, 'name': name, 'score': round(score, 1),
        'g': g, 'p': p, 'vp': vp, 'm': m, 'ss': ss, 'cc': cc, 'rsi_s': rsi_s,
        'sector': bs, 'sector_mult': sm,
        'today_open_pct': open_pct,
    }
    result.update(tech)
    return result

# ==================== 8. 热点板块检测(基于T-1日) ====================
def detect_hot_sectors(all_tech_data):
    sector_stats = defaultdict(lambda: {'total': 0, 'surging': 0, 'zt': 0})
    for code, data in all_tech_data.items():
        tech = data['tech']
        name = data['name']
        matched = match_sector(name, code)
        for sec in matched:
            sector_stats[sec]['total'] += 1
            # T-1日涨幅>=5%算异动, >=9.5%算涨停
            if tech['yest_pct'] >= 5:
                sector_stats[sec]['surging'] += 1
            if tech['yest_pct'] >= 9.5:
                sector_stats[sec]['zt'] += 1
    return {sec: stats for sec, stats in sector_stats.items() if stats['surging'] >= 2}

# ==================== 9. 板块轮动检测 ====================
def build_sector_zt_history(all_kls, recent_dates):
    history = defaultdict(lambda: defaultdict(int))
    for code, kl in all_kls.items():
        name = kl.get('name', '')
        matched = match_sector(name, code)
        if not matched:
            continue
        dates = kl['d']
        closes = kl['c']
        for i in range(1, len(dates)):
            if closes[i-1] > 0:
                pct = (closes[i] / closes[i-1] - 1) * 100
                if pct >= 9.5:
                    for sec in matched:
                        history[dates[i]][sec] += 1
    return history

def detect_sector_acceleration(today_date_str, sector_zt_history):
    all_dates = sorted(sector_zt_history.keys())
    target_idx = -1
    for i, d in enumerate(all_dates):
        if today_date_str in d:
            target_idx = i
            break
    if target_idx < 0:
        target_idx = len(all_dates) - 1

    today_zt = sector_zt_history.get(all_dates[target_idx], {})
    yest_zt = sector_zt_history.get(all_dates[target_idx - 1], {}) if target_idx >= 1 else {}
    d2_zt = sector_zt_history.get(all_dates[target_idx - 2], {}) if target_idx >= 2 else {}
    d3_zt = sector_zt_history.get(all_dates[target_idx - 3], {}) if target_idx >= 3 else {}

    result = {}
    all_sectors = set(today_zt.keys()) | set(yest_zt.keys()) | set(d2_zt.keys()) | set(d3_zt.keys())

    for sec in all_sectors:
        t0 = today_zt.get(sec, 0)
        t1 = yest_zt.get(sec, 0)
        t2 = d2_zt.get(sec, 0)
        t3 = d3_zt.get(sec, 0)
        total_4d = t0 + t1 + t2 + t3

        if t1 >= 2 and t2 >= 2:
            result[sec] = '主线确认'
        elif t1 >= 2 and t0 >= 1:
            result[sec] = '加速'
        elif t1 >= 2 and t2 < 2:
            result[sec] = '新起'
        elif t1 >= 1 and t2 >= 1 and t3 >= 1:
            result[sec] = '主线确认'
        elif t0 >= 1 and t1 == 0 and total_4d >= 4:
            # 当日有涨停但T-1无涨停 → 重新活跃, 不判衰减
            result[sec] = '新起'
        elif total_4d >= 4 and t1 == 0 and t0 == 0:
            result[sec] = '衰减'
        elif t1 >= 1 and t2 == 0:
            result[sec] = '新起'

    return result

# ==================== 10. 起爆初期检测 ====================
def detect_early_surge(r, sector_accel, local_bull_sectors):
    bonus = 0
    signals = []

    name = r.get('name', '')
    p5 = r.get('pct_5d', 0)
    rsi = r.get('rsi6', 50)
    vr = r.get('vol_ratio', 1.0)
    above_ma5 = r.get('above_ma5', False)
    has_ll = r.get('has_long_lower', False)
    is_yang = r.get('yest_pct', 0) >= 0
    t2_pct = r.get('t2_pct', 0)
    yest_pct = r.get('yest_pct', 0)
    dist_h20 = r.get('dist_high20', -10)
    zt30 = r.get('zt_30d', 0)
    mc = r.get('max_consec', 0)

    matched = match_sector(name, r.get('code'))

    if 0 <= p5 <= 5 and 35 <= rsi <= 65 and vr < 1.5 and above_ma5:
        bonus += 5
        signals.append('蓄势待发+5')

    sector_hot = False
    for sec in matched:
        if sec in sector_accel and sector_accel[sec] in ('加速', '主线确认', '新起'):
            sector_hot = True
            break
        if sec in local_bull_sectors:
            sector_hot = True
            break

    if has_ll and vr < 1.5 and sector_hot:
        bonus += 8
        signals.append('起爆信号+8')
    elif has_ll and vr < 1.5:
        bonus += 3
        signals.append('长下影缩量+3')

    if t2_pct > yest_pct and is_yang and above_ma5 and t2_pct > 0:
        bonus += 6
        signals.append('加速趋势+6')

    if sector_hot and above_ma5 and zt30 >= 1:
        bonus += 7
        signals.append('板块共振+7')
    elif sector_hot and above_ma5:
        bonus += 4
        signals.append('板块跟随+4')

    if dist_h20 < -10 and above_ma5 and is_yang:
        bonus += 5
        signals.append('低位起爆+5')

    if mc >= 1 and sector_hot and above_ma5:
        bonus += 3
        signals.append('连板+板块+3')

    bonus = min(bonus, 20)
    return bonus, signals

# ==================== 11. 局部牛市检测 ====================
def detect_local_bull(pool):
    sector_zt = defaultdict(int)
    for code, r in pool.items():
        if r.get('is_yest_zt', False) or r.get('yest_pct', 0) >= 9.5:
            matched = match_sector(r.get('name', ''), code)
            for sec in matched:
                sector_zt[sec] += 1
    return {sec: cnt for sec, cnt in sector_zt.items() if cnt >= 2}

# ==================== 12. 竞价评分 ====================
def auction_score(r):
    open_pct = r.get('today_open_pct', 0)
    vr = r.get('vol_ratio', 1.0)

    if -1 <= open_pct <= 0 and vr >= 2:
        return 95, '极强(低开放量)'
    if 0 < open_pct <= 1 and vr >= 1.5:
        return 88, '强(平开放量)'
    if -1 <= open_pct <= 0:
        return 85, '强(低开)'
    if 0 < open_pct <= 1:
        return 82, '强(小幅高开)'
    if -3 <= open_pct < -1:
        return 78, '中(低开-1~-3%)'
    if 1 < open_pct <= 2:
        return 75, '中(小高开)'
    if 2 < open_pct <= 4:
        return 60, '弱(高开2-4%)'
    if open_pct > 4:
        return 40, '极弱(高开>4%)'
    return 50, '极弱(深低开<-3%)'

# ==================== ★ v12.24 BULL模式: 主线龙头优先 ====================
def score_bull_v1224(r, local_bull_sectors, sector_accel, hot_sectors):
    base = r.get('v1212_score', r.get('score', 0))
    matched = match_sector(r.get('name', ''), r.get('code'))
    is_zt = r.get('is_yest_zt', False)
    open_pct = r.get('today_open_pct', 0)
    mc = r.get('max_consec', 0)
    zt5 = r.get('zt_5d', 0)

    # 1. 板块热度 (权重40%, 最高+20)
    sector_heat = 0
    local_bonus = 0
    accel_bonus = 0
    sector_hot = False
    mainline = False

    for sec in matched:
        if sec in local_bull_sectors:
            local_bonus += 10
            sector_hot = True
        if sec in sector_accel:
            status = sector_accel[sec]
            if status == '主线确认':
                accel_bonus += 8; sector_hot = True; mainline = True
            elif status == '加速':
                accel_bonus += 6; sector_hot = True
            elif status == '新起':
                accel_bonus += 5; sector_hot = True
            elif status == '衰减':
                accel_bonus -= 3
        if sec in hot_sectors:
            stats = hot_sectors[sec]
            if stats.get('zt', 0) >= 3:
                accel_bonus += 5; sector_hot = True
            elif stats.get('zt', 0) >= 2:
                accel_bonus += 3
    sector_heat = local_bonus + accel_bonus

    # 2. 主线龙头识别 (权重20%, 最高+12)
    leader_bonus = 0
    if is_zt and mainline:
        leader_bonus = 12
    elif is_zt and sector_hot:
        leader_bonus = 8
    elif is_zt and mc >= 2:
        leader_bonus = 5

    # 3. 连板续涨奖励 (权重15%, 最高+8)
    continuation_bonus = 0
    if is_zt:
        if mc >= 3: continuation_bonus = 8
        elif mc >= 2: continuation_bonus = 5
        elif zt5 >= 2: continuation_bonus = 3

    # 4. 起爆初期信号 (权重10%)
    surge_bonus, surge_signals = detect_early_surge(r, sector_accel, local_bull_sectors)

    # 5. 追涨风险控制 (BULL大幅放宽)
    chase_penalty = 0
    chase_reason = ''
    if is_zt and open_pct > 7 and not sector_hot:
        chase_penalty = -8
        chase_reason = f'高开{open_pct:.1f}%无板块-8'
    elif is_zt and open_pct > 7 and sector_hot:
        chase_penalty = -3
        chase_reason = f'高开{open_pct:.1f}%轻罚-3'
    elif mc >= 5 and open_pct > 5 and not sector_hot:
        chase_penalty = -5
        chase_reason = f'{mc}连板高开{open_pct:.1f}%无板块-5'

    final = base + sector_heat * 0.40 + leader_bonus * 0.20 + continuation_bonus * 0.15 + surge_bonus * 0.10 + chase_penalty
    total_bonus = sector_heat + leader_bonus + continuation_bonus + surge_bonus + chase_penalty

    all_signals = surge_signals[:]
    if local_bonus: all_signals.append(f"局部牛+{local_bonus}")
    if accel_bonus: all_signals.append(f"轮动{accel_bonus:+d}")
    if leader_bonus: all_signals.append(f"龙头+{leader_bonus}")
    if continuation_bonus: all_signals.append(f"连板续涨+{continuation_bonus}")
    if chase_penalty: all_signals.append(chase_reason)

    return round(final, 1), all_signals, total_bonus

# ==================== v12.23 CORRECTION模式 ====================
def score_correction_v1223(r, local_bull_sectors, sector_accel):
    g = 0
    zt30 = r.get('zt_30d', 0)
    if zt30 >= 10: g = 10
    elif zt30 >= 5: g = 8
    elif zt30 >= 3: g = 5
    elif zt30 >= 1: g = 3
    mc = r.get('max_consec', 0)
    if mc >= 3: g = min(g + 2, 10)

    p = 0
    has_ll = r.get('has_long_lower', False)
    yest_amp = r.get('yest_amp', abs(r.get('yest_pct', 0)) + 2)
    is_yang = r.get('yest_pct', 0) >= 0
    above_ma5 = r.get('above_ma5', False)
    if has_ll: p += 6
    if yest_amp >= 3: p += 3
    if is_yang: p += 3
    if above_ma5: p += 3
    p = min(p, 15)

    vp = 0
    vr = r.get('vol_ratio', 1.0)
    if vr < 1: vp = 8
    elif vr < 1.5: vp = 6
    elif vr >= 3: vp = 10
    elif vr >= 2: vp = 7
    else: vp = 5

    pq = 0
    dev10 = abs(r.get('dev_ma10', 0))
    dev20 = abs(r.get('dev_ma20', 0))
    if dev10 <= 2: pq = 8
    elif dev10 <= 4: pq = 6
    elif dev10 <= 7: pq = 3
    if dev20 <= 3: pq += 6
    elif dev20 <= 5: pq += 4
    elif dev20 <= 8: pq += 2
    if r.get('dev_ma20', 0) >= 0: pq += 3
    dist_h20 = r.get('dist_high20', -10)
    if -20 <= dist_h20 <= -5: pq += 3
    pq = min(pq, 20)

    ss = 0
    name = r.get('name', '')
    if is_defensive(name, r.get('code')): ss = 10
    zt5 = r.get('zt_5d', 0)
    is_zt = r.get('is_yest_zt', False)
    if zt5 >= 1 and not is_zt: ss = min(ss + 3, 15)

    m = 0
    p5 = r.get('pct_5d', 0)
    if -3 <= p5 <= 5: m = 10
    elif 5 < p5 <= 12: m = 7
    elif -8 <= p5 < -3: m = 6
    elif 12 < p5 <= 20: m = 4
    elif p5 > 20: m = 2

    rsi_s = 0
    rsi = r.get('rsi6', 50)
    if 30 <= rsi <= 50: rsi_s = 10
    elif 50 < rsi <= 65: rsi_s = 8
    elif 25 <= rsi < 30: rsi_s = 6
    elif 65 < rsi <= 75: rsi_s = 4
    elif rsi > 75: rsi_s = 1
    else: rsi_s = 3

    cc = 0
    if is_zt:
        if mc >= 3: cc = 5
        elif zt5 >= 2: cc = 3
        else: cc = 2

    sg = 0
    if 0 <= p5 <= 8 and above_ma5 and is_yang: sg += 3
    if has_ll and vr < 1.5: sg += 2
    sg = min(sg, 5)

    rk = 0
    if mc >= 6: rk = 15
    elif mc >= 5: rk = 10
    elif mc >= 4: rk = 5
    if p5 > 25: rk += 8
    elif p5 > 20: rk += 5
    if rsi > 80: rk += 5
    if abs(r.get('dev_ma5', 0)) > 8: rk += 3
    rk = min(rk, 25)

    t1_score = (g + p + vp + pq + ss + m + rsi_s + cc + sg - rk)
    auc, auc_label = auction_score(r)
    base_score = t1_score * 0.6 + auc * 0.4

    sector_heat_boost = 0
    matched = match_sector(r.get('name', ''), r.get('code'))
    for sec in matched:
        if sec in local_bull_sectors: sector_heat_boost += 6
        if sec in sector_accel:
            status = sector_accel[sec]
            if status == '主线确认': sector_heat_boost += 4
            elif status == '加速': sector_heat_boost += 3
            elif status == '新起': sector_heat_boost += 2

    surge_bonus, surge_signals = detect_early_surge(r, sector_accel, local_bull_sectors)
    final_score = base_score + surge_bonus * 0.15 + sector_heat_boost * 0.15

    all_signals = surge_signals[:]
    if sector_heat_boost: all_signals.append(f"板块热度+{sector_heat_boost}")

    return round(final_score, 1), all_signals, auc, auc_label

# ==================== v12.23 RANGE_BULL模式: 5攻1防 ====================
def score_range_bull_v1223(r, local_bull_sectors, sector_accel):
    bull_base = r.get('v1212_score', r.get('score', 0))

    low_buy_bonus = 0
    vr = r.get('vol_ratio', 1.0)
    above_ma5 = r.get('above_ma5', False)
    is_yang = r.get('yest_pct', 0) >= 0
    has_ll = r.get('has_long_lower', False)
    dev_ma5 = r.get('dev_ma5', 0)
    rsi = r.get('rsi6', 50)
    p5 = r.get('pct_5d', 0)

    if vr < 1.2 and above_ma5 and -3 <= dev_ma5 <= 2: low_buy_bonus += 8
    if vr < 1.3 and is_yang and above_ma5: low_buy_bonus += 4
    if has_ll and vr < 1.5: low_buy_bonus += 4
    if 35 <= rsi <= 55: low_buy_bonus += 3
    if 0 <= p5 <= 8: low_buy_bonus += 3
    low_buy_bonus = min(low_buy_bonus, 18)

    local_bonus = 0
    matched = match_sector(r.get('name', ''), r.get('code'))
    for sec in matched:
        if sec in local_bull_sectors: local_bonus += 8

    surge_bonus, surge_signals = detect_early_surge(r, sector_accel, local_bull_sectors)

    accel_bonus = 0
    for sec in matched:
        if sec in sector_accel:
            status = sector_accel[sec]
            if status == '主线确认': accel_bonus += 6
            elif status == '加速': accel_bonus += 5
            elif status == '新起': accel_bonus += 4
            elif status == '衰减': accel_bonus -= 3

    def_bonus = 0
    if is_defensive(r.get('name', ''), r.get('code')): def_bonus += 5
    dist_h20 = r.get('dist_high20', -10)
    if -20 <= dist_h20 <= -5: def_bonus += 3

    weighted = low_buy_bonus * 0.20 + local_bonus * 0.15 + surge_bonus * 0.15 + accel_bonus * 0.15 + def_bonus * 0.10
    weighted = min(weighted, 14)
    final = bull_base + weighted

    all_signals = surge_signals[:]
    if low_buy_bonus: all_signals.append(f"低吸+{low_buy_bonus}")
    if local_bonus: all_signals.append(f"局部牛+{local_bonus}")
    if accel_bonus: all_signals.append(f"轮动{accel_bonus:+d}")
    if def_bonus: all_signals.append(f"防守+{def_bonus}")

    total_bonus = low_buy_bonus + local_bonus + surge_bonus + accel_bonus + def_bonus
    return round(final, 1), all_signals, total_bonus

# ==================== v12.25 USER模式: 用户条件+涨停基因+可买性 ====================

def detect_volume_surge(kl_data):
    """检测近10天放量情况: 返回(最大放量倍数, 放量距今天数)"""
    if kl_data is None:
        return 1.0, 0  # 默认值: 无放量, 即日
    v = kl_data['v']
    n = len(v)
    yi = n - 1
    max_ratio = 1.0
    max_day = 0
    for j in range(yi-8, yi+1):
        ref = j - 10
        if ref >= 0 and v[ref] > 0:
            ratio = v[j] / v[ref]
            if ratio > max_ratio:
                max_ratio = ratio
                max_day = yi - j
    return max_ratio, max_day

def check_buyability(r, dist_h10=None):
    """可买性检查v2: 基于涨停个股可买vs不可买对比分析
    可买涨停avg: RSI6=69.7, 偏离MA5=2.4%, 周五涨幅=1.3%, 30日涨停=3.6, 距前高=-6.0%
    不可买avg:   RSI6=90.4, 偏离MA5=11.8%, 周五涨幅=7.7%, 30日涨停=6.5, 距前高=-1.9%
    """
    reasons = []
    score = 100  # 满分100, 低于60不可买

    rsi6 = r.get('rsi6', 50)
    yest_pct = r.get('yest_pct', 0)
    dev_ma5 = r.get('dev_ma5', 0)
    zt_30d = r.get('zt_30d', 0)
    open_pct = r.get('today_open_pct', 0)

    # RSI过高 -> 可能一字板 (可买avg=69.7 vs 不可买avg=90.4, 差异最大因子)
    if rsi6 >= 95:
        score -= 35
        reasons.append(f'RSI6={rsi6:.0f}极高(-35)')
    elif rsi6 >= 88:
        score -= 25
        reasons.append(f'RSI6={rsi6:.0f}偏高(-25)')
    elif rsi6 >= 80:
        score -= 12
        reasons.append(f'RSI6={rsi6:.0f}略高(-12)')

    # 偏离MA5过大 -> 超买 (可买avg=2.4% vs 不可买avg=11.8%)
    if dev_ma5 >= 10:
        score -= 25
        reasons.append(f'偏离MA5={dev_ma5:.1f}%(-25)')
    elif dev_ma5 >= 8:
        score -= 18
        reasons.append(f'偏离MA5={dev_ma5:.1f}%(-18)')
    elif dev_ma5 >= 6:
        score -= 10
        reasons.append(f'偏离MA5={dev_ma5:.1f}%(-10)')

    # T-1涨幅过大 -> 可能高开 (可买avg=1.3% vs 不可买avg=7.7%)
    if yest_pct >= 9.5:
        score -= 30
        reasons.append(f'昨日涨停(-30)')
    elif yest_pct >= 7:
        score -= 22
        reasons.append(f'昨日涨{yest_pct:.1f}%(-22)')
    elif yest_pct >= 5:
        score -= 12
        reasons.append(f'昨日涨{yest_pct:.1f}%(-12)')

    # 30日涨停过多 -> 过热 (可买avg=3.6 vs 不可买avg=6.5)
    if zt_30d >= 7:
        score -= 15
        reasons.append(f'30日涨停{zt_30d}次过热(-15)')
    elif zt_30d >= 6:
        score -= 8
        reasons.append(f'30日涨停{zt_30d}次偏多(-8)')

    # 距前高太近 -> 容易一字板 (可买avg=-6.0% vs 不可买avg=-1.9%)
    if dist_h10 is not None and dist_h10 > -1:
        score -= 10
        reasons.append(f'距前高{dist_h10:.1f}%太近(-10)')

    # 开盘已高开
    if open_pct >= 7:
        score -= 30
        reasons.append(f'开盘{open_pct:.1f}%高开(-30)')
    elif open_pct >= 5:
        score -= 20
        reasons.append(f'开盘{open_pct:.1f}%高开(-20)')

    buyable = score >= 60
    return buyable, score, reasons

def score_user_mode(r, kl_data, local_bull_sectors, sector_accel, market_regime='BULL'):
    """USER模式v2评分: 基于涨停个股特征分析的数据驱动评分
    数据来源: 51只条件筛选股中27只涨停 vs 24只未涨停的统计对比
    关键发现: 放量3-5天前(100%涨停率), RSI<50(86%), 距前高-5~-10%(71%), 4-5x放量(100%)
    v12.26优化: 动态RSI/放量时间/距前高权重根据market_regime自适应调整
    """
    signals = []

    # 获取基础数据
    price = r.get('yest_close', 0)
    rsi6 = r.get('rsi6', 50)
    zt_30d = r.get('zt_30d', 0)
    yest_pct = r.get('yest_pct', 0)
    pct_5d = r.get('pct_5d', 0)
    dev_ma5 = r.get('dev_ma5', 0)
    ma_bull = r.get('ma_bull', False)

    # 计算K线衍生指标
    vol_surge_ratio, vol_surge_day = detect_volume_surge(kl_data)

    # 距前高、10日位置、连续>MA10
    dist_h10 = -10
    price_pos_10d = 50
    consec_ma10 = 0
    if kl_data:
        h = kl_data['h']; c = kl_data['c']; l = kl_data['l']
        n = len(c); yi = n - 1
        if yi >= 9:
            high_10d = max(h[yi-9:yi+1])
            low_10d = min(l[yi-9:yi+1])
            dist_h10 = (c[yi] / high_10d - 1) * 100 if high_10d > 0 else -10
            if high_10d > low_10d:
                price_pos_10d = (c[yi] - low_10d) / (high_10d - low_10d) * 100
            for j in range(yi, max(yi-15, 8), -1):
                ma10_j = sum(c[j-9:j+1]) / 10
                if c[j] > ma10_j:
                    consec_ma10 += 1
                else:
                    break

    # ==================== 10大因子评分 (每项0-10分) ====================
    # v12.26: 根据market_regime动态调整权重
    is_correction = market_regime in ('CORRECTION', 'RELAY')
    is_bull = market_regime in ('BULL', 'USER')

    # 1. 放量倍数 (权重15%) - 4-5x最优(100%涨停率), 5-8x(60%), 3-4x(57%), 8x+(50%), 2.5-3x(38%)
    vol_ratio_s = 0
    if 4 <= vol_surge_ratio < 5:
        vol_ratio_s = 10
        signals.append(f'放量{vol_surge_ratio:.1f}倍(最优)')
    elif 5 <= vol_surge_ratio < 8:
        vol_ratio_s = 8
        signals.append(f'放量{vol_surge_ratio:.1f}倍')
    elif 3 <= vol_surge_ratio < 4:
        vol_ratio_s = 7
        signals.append(f'放量{vol_surge_ratio:.1f}倍')
    elif vol_surge_ratio >= 8:
        vol_ratio_s = 6
        signals.append(f'放量{vol_surge_ratio:.1f}倍(巨量)')
    elif vol_surge_ratio >= 2.5:
        vol_ratio_s = 3
        signals.append(f'放量{vol_surge_ratio:.1f}倍(偏弱)')

    # 2. 放量时间 (权重15%) - v12.26动态: BULL日3-5天前最优, CORRECTION日即日/1天前最优
    vol_day_s = 0
    if is_correction:
        # 回调日: 即日放量+1天前最优(情绪接力逻辑)
        if vol_surge_day == 0:
            vol_day_s = 10
            signals.append('即日放量(回调日最优)')
        elif vol_surge_day == 1:
            vol_day_s = 8
            signals.append('1天前放量(回调日次优)')
        elif vol_surge_day == 2:
            vol_day_s = 6
        elif 3 <= vol_surge_day <= 5:
            vol_day_s = 4
        else:
            vol_day_s = 3
    else:
        # BULL/USER日: 3-5天前最优(蓄势逻辑)
        if 3 <= vol_surge_day <= 5:
            vol_day_s = 10
            signals.append(f'放量{vol_surge_day}天前(完美蓄势)')
        elif vol_surge_day == 0:
            vol_day_s = 7
            signals.append('即日放量')
        elif vol_surge_day >= 6:
            vol_day_s = 5
        elif vol_surge_day == 2:
            vol_day_s = 4
        elif vol_surge_day == 1:
            vol_day_s = 2

    # 3. 距前高 (权重12%) - v12.26动态: BULL日-5~-10%蓄势最优, CORRECTION日-2~0%突破最优
    dist_s = 0
    if is_correction:
        # 回调日: 贴前高(-2~0%)最优(突破前高=打开空间)
        if -2 <= dist_h10 <= 0:
            dist_s = 10
            signals.append(f'距前高{dist_h10:.1f}%(贴前高突破位)')
        elif -5 < dist_h10 < -2:
            dist_s = 7
        elif dist_h10 > 0:
            dist_s = 8
            signals.append(f'破前高{dist_h10:.1f}%(打开空间)')
        elif -10 <= dist_h10 <= -5:
            dist_s = 5
        else:
            dist_s = 3
    else:
        # BULL/USER日: -5~-10%蓄势位最优
        if -10 <= dist_h10 <= -5:
            dist_s = 10
            signals.append(f'距前高{dist_h10:.1f}%(蓄势位)')
        elif dist_h10 < -10:
            dist_s = 8
            signals.append(f'距前高{dist_h10:.1f}%(超跌位)')
        elif -2 <= dist_h10 <= 0:
            dist_s = 5
        elif -5 < dist_h10 < -2:
            dist_s = 4
        elif dist_h10 > 0:
            dist_s = 2

    # 4. RSI6 (权重12%) - v12.26动态: BULL日<50最优, CORRECTION日>70不扣分(强势股特征)
    rsi_s = 0
    if is_correction:
        # 回调日: RSI高=资金聚集度体现, 不扣分
        if rsi6 < 50:
            rsi_s = 8
            signals.append(f'RSI6={rsi6:.0f}(低位)')
        elif rsi6 < 60:
            rsi_s = 6
        elif rsi6 < 70:
            rsi_s = 7
        elif rsi6 < 80:
            rsi_s = 8
            signals.append(f'RSI6={rsi6:.0f}(强势)')
        elif rsi6 < 90:
            rsi_s = 7
        elif rsi6 < 95:
            rsi_s = 5
            signals.append(f'RSI6={rsi6:.0f}(极强,注意一字板)')
        else:
            rsi_s = 3
            signals.append(f'RSI6={rsi6:.0f}(超买,可能一字板)')
    else:
        # BULL/USER日: RSI<50最优(86%涨停率)
        if rsi6 < 50:
            rsi_s = 10
            signals.append(f'RSI6={rsi6:.0f}(低位)')
        elif rsi6 < 60:
            rsi_s = 8
            signals.append(f'RSI6={rsi6:.0f}(偏低)')
        elif rsi6 < 70:
            rsi_s = 6
        elif rsi6 < 80:
            rsi_s = 5
        elif rsi6 < 90:
            rsi_s = 2
        else:
            rsi_s = 0
            signals.append(f'RSI6={rsi6:.0f}(超买)')

    # 5. 价格区间 (权重8%) - 5-10元(71%), 10-20元(59%), <5元(50%), 20-40元(33%), 40-60元(29%)
    price_s = 0
    if 5 <= price < 10:
        price_s = 10
        signals.append(f'价{price:.1f}元(热点)')
    elif 10 <= price < 20:
        price_s = 8
    elif price < 5:
        price_s = 6
    elif 20 <= price < 40:
        price_s = 4
    else:
        price_s = 0

    # 6. 5日涨幅 (权重8%) - 涨停avg=9.8% vs 未涨停avg=18.1%
    pct5_s = 0
    if pct_5d < 10:
        pct5_s = 10
    elif pct_5d < 12:
        pct5_s = 7
    elif pct_5d < 15:
        pct5_s = 4
    elif pct_5d < 18:
        pct5_s = 2
        signals.append(f'5日涨{pct_5d:.0f}%(偏多)')
    else:
        pct5_s = 0
        signals.append(f'5日涨{pct_5d:.0f}%(过热)')

    # 7. 10日位置 (权重5%) - 涨停avg=73.1 vs 未涨停avg=84.2
    pos_s = 0
    if price_pos_10d < 70:
        pos_s = 10
    elif price_pos_10d < 80:
        pos_s = 6
    elif price_pos_10d < 90:
        pos_s = 3
    else:
        pos_s = 0

    # 8. 偏离MA5 (权重5%) - 涨停avg=2.4% vs 未涨停avg=5.2%
    dev_s = 0
    if dev_ma5 < 3:
        dev_s = 10
    elif dev_ma5 < 5:
        dev_s = 6
    elif dev_ma5 < 8:
        dev_s = 3
    else:
        dev_s = 0
        signals.append(f'偏离MA5={dev_ma5:.1f}%(超买)')

    # 9. 涨停基因 (权重5%) - 3-5次(59%), 8+(67%), 6-7(40%)
    zt_s = 0
    if 3 <= zt_30d <= 5:
        zt_s = 10
        signals.append(f'30日涨停{zt_30d}次')
    elif zt_30d <= 2:
        zt_s = 6
    elif zt_30d >= 8:
        zt_s = 5
    elif zt_30d >= 6:
        zt_s = 3
        signals.append(f'30日涨停{zt_30d}次(偏多)')

    # 10. 周五涨幅 (权重5%) - -3~0%(61%), 3-5%(67%), 7%+(29%)
    fri_s = 0
    if -3 <= yest_pct < 0:
        fri_s = 10
    elif 0 <= yest_pct < 5:
        fri_s = 8
    elif 5 <= yest_pct < 7:
        fri_s = 4
    elif yest_pct >= 7:
        fri_s = 0
        signals.append(f'昨日涨{yest_pct:.1f}%(追高风险)')
    elif yest_pct < -3:
        fri_s = 5

    # === 板块热度 (v12.26强化: 上限提升至25, 权重不低于25%) ===
    sector_s = 0
    matched = match_sector(r.get('name', ''), r.get('code'))
    for sec in matched:
        if sec in local_bull_sectors:
            sector_s += 6  # v12.26: 3→6
        if sec in sector_accel:
            status = sector_accel[sec]
            if status == '主线确认': sector_s += 10  # v12.26: 4→10
            elif status == '加速': sector_s += 7    # v12.26: 3→7
            elif status == '新起': sector_s += 5    # v12.26: 2→5
    # v12.26: 日内涨停潮板块额外加分
    # (hot_sectors在USER模式中暂不传入, 此处保留接口)
    sector_s = min(sector_s, 25)  # v12.26: 10→25

    # === 可买性检查 (硬过滤) ===
    buyable, buy_score, buy_reasons = check_buyability(r, dist_h10)
    if buy_reasons:
        signals.extend(buy_reasons)

    # v12.26: 一字板风险预警
    yz_level, yz_label, yz_can_chase, yz_score, yz_factors = check_yiziban_risk(r, dist_h10)
    if yz_factors:
        signals.append(f'一字板预警: {yz_label}({yz_score}分)')
    if yz_level == 'HIGH':
        buyable = False
        signals.append('一字板风险高, 排除')

    # === 总分计算 (10项因子加权 + 板块加分 + 可买性加成) ===
    total = (
        vol_ratio_s * 0.15 +    # 放量倍数
        vol_day_s * 0.15 +      # 放量时间
        dist_s * 0.12 +          # 距前高
        rsi_s * 0.12 +           # RSI6
        price_s * 0.08 +         # 价格区间
        pct5_s * 0.08 +          # 5日涨幅
        pos_s * 0.05 +           # 10日位置
        dev_s * 0.05 +           # 偏离MA5
        zt_s * 0.05 +            # 涨停基因
        fri_s * 0.05             # 周五涨幅
    ) * 10  # 缩放到0-100

    # 板块加分
    total += sector_s

    # 双条件共振加成
    cond1_met = vol_surge_ratio >= 2.5 and zt_30d >= 1
    cond2_met = consec_ma10 >= 4 and zt_30d > 2
    if cond1_met and cond2_met:
        total += 10
        signals.append('双条件共振(+10)')

    # 可买性加成/惩罚
    if buyable:
        total += buy_score * 0.15
        if buy_score >= 80:
            signals.append(f'可买性优({buy_score}分)')
    else:
        total *= 0.3
        signals.append(f'不可买({buy_score}分)')

    return round(total, 1), signals, cond1_met, cond2_met, buyable

# ==================== v12.26 连板接力策略模块 + 动态权重优化 ====================
def score_relay_v1226(r, kl_data, local_bull_sectors, sector_accel, hot_sectors):
    """v12.26 连板接力策略评分模块
    优化3: 连板接力识别(2连板低位优先, 5+连板高位谨慎)
    优化4&5: 动态RSI/放量时间/距前高权重(连板数自适应调整)
    优化6: 板块热度权重强化(主线龙头额外加成, 上限提升至25)
    """
    signals = []
    mc = r.get('max_consec', 0)
    is_zt = r.get('is_yest_zt', False)
    rsi6 = r.get('rsi6', 50)
    open_pct = r.get('today_open_pct', 0)

    if not is_zt or mc < 2:
        return 0, signals, False

    # 优化3: 连板接力基础分
    if mc >= 5:
        relay_base = 5
        signals.append(f'{mc}连板(高位接力谨慎)')
    elif mc >= 4:
        relay_base = 10
        signals.append(f'{mc}连板(中位接力)')
    elif mc >= 3:
        relay_base = 15
        signals.append(f'{mc}连板(标准接力)')
    else:
        relay_base = 18
        signals.append(f'{mc}连板(低位接力优)')

    # 优化4: 动态RSI权重(连板越多RSI容忍度越高, 权重递减)
    rsi_weight = 0.12
    if mc >= 4:
        rsi_weight = 0.08
        signals.append('动态RSI权重↓0.04(高连板容忍)')
    elif mc >= 3:
        rsi_weight = 0.10
        signals.append('动态RSI权重↓0.02')
    rsi_s = 10 if rsi6 < 70 else (8 if rsi6 < 80 else (5 if rsi6 < 85 else 2))
    rsi_score = rsi_s * rsi_weight * 10

    # 优化5: 动态放量时间/距前高权重
    vol_surge_ratio, vol_surge_day = detect_volume_surge(kl_data) if kl_data else (1.0, 0)
    vol_day_weight = 0.15
    if mc >= 3:
        vol_day_weight = 0.18
        signals.append('动态放量时间权重↑0.03')
    vol_day_s = 8 if vol_surge_day == 0 else (6 if vol_surge_day <= 2 else (5 if vol_surge_day <= 5 else 3))
    vol_day_score = vol_day_s * vol_day_weight * 10

    dist_h10 = -10
    if kl_data:
        h = kl_data['h']; c = kl_data['c']
        n = len(c); yi = n - 1
        if yi >= 9:
            high_10d = max(h[yi-9:yi+1])
            dist_h10 = (c[yi] / high_10d - 1) * 100 if high_10d > 0 else -10
    dist_weight = 0.12
    if mc >= 4:
        dist_weight = 0.08
        signals.append('动态距前高权重↓0.04(高连板容忍)')
    dist_s = 8 if dist_h10 > 0 else (6 if dist_h10 > -3 else (4 if dist_h10 > -8 else 2))
    dist_score = dist_s * dist_weight * 10

    # 优化6: 板块热度权重强化
    matched = match_sector(r.get('name', ''), r.get('code'))
    sector_boost = 0
    for sec in matched:
        if sec in local_bull_sectors:
            sector_boost += 8
            signals.append(f'局部牛{sec}+8')
        if sec in sector_accel:
            status = sector_accel[sec]
            if status == '主线确认':
                sector_boost += 10
                signals.append(f'主线确认{sec}+10')
            elif status == '加速':
                sector_boost += 7
                signals.append(f'板块加速{sec}+7')
            elif status == '新起':
                sector_boost += 5
        if sec in hot_sectors:
            stats = hot_sectors[sec]
            if stats.get('zt', 0) >= 3:
                sector_boost += 6
                signals.append(f'热点涨停潮{sec}+6')
    sector_boost = min(sector_boost, 25)
    signals.append(f'板块热度强化(上限25): +{sector_boost}')

    total = relay_base + rsi_score + vol_day_score + dist_score + sector_boost

    if open_pct > 7:
        total -= 10
        signals.append(f'高开{open_pct:.1f}%(-10)')
    elif open_pct > 5:
        total -= 5
        signals.append(f'高开{open_pct:.1f}%(-5)')

    total = max(0, total)
    return round(total, 1), signals, True

# ==================== v12.26 一字板预警系统升级 ====================
def check_yiziban_risk(r, dist_h10=None):
    """一字板风险预警v2: 基于8/11涨停数据(5只一字板 vs 45只可买)
    一字板特征: RSI>90 + 5日涨停>2 + 距前高<-1% + 5日涨幅>20%
    区分: 可追一字板(板块龙头+连板<3) vs 不可追一字板(连板>5+累计涨幅>50%)
    """
    rsi6 = r.get('rsi6', 50)
    zt_5d = r.get('zt_5d', 0)
    zt_30d = r.get('zt_30d', 0)
    pct_5d = r.get('pct_5d', 0)
    max_consec = r.get('max_consec', 0)
    dev_ma5 = r.get('dev_ma5', 0)
    yest_pct = r.get('yest_pct', 0)

    risk_score = 0
    risk_factors = []

    # 核心预警条件 (每个条件权重不同)
    # 1. RSI>90 (一字板avg=93.4 vs 可买avg=84.2)
    if rsi6 >= 95:
        risk_score += 30
        risk_factors.append(f'RSI6={rsi6:.0f}极高')
    elif rsi6 >= 90:
        risk_score += 20
        risk_factors.append(f'RSI6={rsi6:.0f}偏高')

    # 2. 5日涨停>2次 (一字板avg=2.6 vs 可买avg=1.6)
    if zt_5d >= 3:
        risk_score += 25
        risk_factors.append(f'5日涨停{zt_5d}次')
    elif zt_5d >= 2:
        risk_score += 15
        risk_factors.append(f'5日涨停{zt_5d}次')

    # 3. 距前高<-1% (一字板avg=-1.0% vs 可买avg=-4.7%)
    if dist_h10 is not None and dist_h10 > -1:
        risk_score += 20
        risk_factors.append(f'距前高{dist_h10:.1f}%')

    # 4. 5日涨幅>20% (一字板avg=30.8% vs 可买avg=16.8%)
    if pct_5d > 30:
        risk_score += 20
        risk_factors.append(f'5日涨{pct_5d:.0f}%')
    elif pct_5d > 20:
        risk_score += 10
        risk_factors.append(f'5日涨{pct_5d:.0f}%')

    # 5. 偏离MA5>12% (一字板avg=14.6% vs 可买avg=9.4%)
    if dev_ma5 > 14:
        risk_score += 15
        risk_factors.append(f'偏离MA5={dev_ma5:.1f}%')
    elif dev_ma5 > 12:
        risk_score += 8

    # 6. 昨日涨停
    if yest_pct >= 9.5:
        risk_score += 10
        risk_factors.append('昨日涨停')

    # 风险等级
    if risk_score >= 60:
        risk_level = 'HIGH'
        risk_label = '高度一字板风险'
        can_chase = False
    elif risk_score >= 40:
        # 区分可追vs不可追: 板块龙头+连板<3 = 可追小仓位
        if max_consec <= 2 and zt_30d <= 5:
            risk_level = 'MEDIUM_CHASE'
            risk_label = '中等风险(龙头可小仓追)'
            can_chase = True
        else:
            risk_level = 'MEDIUM_AVOID'
            risk_label = '中等风险(连板过多不建议追)'
            can_chase = False
    elif risk_score >= 20:
        risk_level = 'LOW'
        risk_label = '低风险'
        can_chase = True
    else:
        risk_level = 'SAFE'
        risk_label = '安全'
        can_chase = True

    return risk_level, risk_label, can_chase, risk_score, risk_factors

# ==================== 买卖点建议 ====================
def generate_trade_advice(r, regime, sector_accel, local_bull):
    yest_close = r.get('yest_close', 0)
    ma5 = r.get('ma5', yest_close)
    ma10 = r.get('ma10', yest_close)
    open_pct = r.get('today_open_pct', 0)
    above_ma5 = r.get('above_ma5', False)
    is_zt = r.get('is_yest_zt', False)
    mc = r.get('max_consec', 0)
    rsi = r.get('rsi6', 50)

    name = r.get('name', '')
    matched = match_sector(name, r.get('code'))
    sector_hot = any(s in sector_accel and sector_accel[s] in ('加速', '主线确认', '新起') for s in matched)
    sector_lb = any(s in local_bull for s in matched)

    if above_ma5 and open_pct <= 0:
        buy_point = f"低开/平开买入, 参考价{yest_close * (1 + open_pct/100):.2f}({open_pct:+.1f}%)"
    elif above_ma5 and 0 < open_pct <= 2:
        buy_point = f"小幅高开可追, 回踩MA5({ma5:.2f})加仓"
    elif not above_ma5:
        buy_point = f"回踩MA5({ma5:.2f})附近低吸, 不追高"
    elif open_pct > 2:
        buy_point = f"高开观望, 回调到{yest_close:.2f}附近再入"
    else:
        buy_point = f"MA5({ma5:.2f})上方持有, 跌破观望"

    if ma10 > 0:
        stop_loss = f"止损: MA10({ma10:.2f})或-3%, 先到先出"
    else:
        stop_loss = "止损: -3%"

    if regime == 'BULL' and (sector_hot or sector_lb):
        take_profit = "止盈: 涨停板封不住再卖, 目标+10%以上"
    elif regime == 'BULL':
        take_profit = "止盈: +8%减半仓, 涨停持有"
    elif regime == 'RANGE_BULL':
        take_profit = "止盈: +5~8%分批止盈"
    elif regime == 'CORRECTION':
        take_profit = "止盈: +5%快速止盈, 不贪"
    elif regime == 'RELAY':
        take_profit = "止盈: +5%快速止盈, 涨停封死可持有至次日"
    else:
        take_profit = "止盈: +3%即走, 保守操作"

    if is_zt and mc >= 3:
        hold = "持仓不动, 连板强者恒强"
    elif sector_hot and above_ma5:
        hold = "板块加速中, 持股待涨"
    elif regime == 'BULL' and above_ma5:
        hold = "多头持仓, 跌破MA5减仓"
    elif regime == 'RANGE_BULL' and above_ma5:
        hold = "均衡持仓, +5%分批止盈, 跌破MA10减仓"
    elif regime == 'CORRECTION' and is_defensive(name, r.get('code')):
        hold = "防守仓位, 大盘企稳后加仓"
    elif regime == 'RELAY' and is_zt and mc >= 2:
        hold = "连板接力, 涨停封死持有, 炸板立即走"
    elif rsi > 75:
        hold = "RSI偏高, 注意回调风险, 半仓操作"
    else:
        hold = "正常持仓, 破MA10离场"

    return {'buy_point': buy_point, 'stop_loss': stop_loss,
            'take_profit': take_profit, 'hold_advice': hold}

# ==================== 主函数 ====================
def main():
    bj = now_bj()
    today_str = bj.strftime('%Y-%m-%d')

    # 解析参数
    regime_arg = None
    top_n = 20
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == '--regime' and i + 1 < len(sys.argv):
            regime_arg = sys.argv[i + 1].upper()
        elif arg == '--top' and i + 1 < len(sys.argv):
            top_n = int(sys.argv[i + 1])

    print(f"v12.26 实盘选股系统(动态regime+连板接力版)  {bj.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)
    print(f"核心: BULL激进主线龙头 | RANGE_BULL 5攻1防 | CORRECTION板块热度防守 | RELAY连板接力 | USER用户条件")

    # 加载板块映射表
    print("\n[0] 加载板块映射表...")
    load_sector_mapping()

    # Step 1: 获取全量股票
    print("\n[1] 获取全量股票列表...")
    stocks = get_all_stocks()
    print(f"  获取: {len(stocks)}只")

    # Step 2: 基础筛选
    print("\n[2] 基础筛选(价格2-80, 市值<500亿)...")
    filtered = [s for s in stocks if 2 <= s['price'] <= 80 and s['circ_mv'] <= 500]
    print(f"  过滤后: {len(filtered)}只")

    # Step 3: 抽样获取K线(热点优先)
    if regime_arg == 'USER':
        SAMPLE_SIZE = len(filtered)  # 全量扫描
    else:
        SAMPLE_SIZE = 1500
    print(f"\n[3] 抽样获取K线(目标{SAMPLE_SIZE}只)...")
    hot_stocks = [s for s in filtered if match_sector(s['name'], s.get('code'))]
    other_stocks = [s for s in filtered if s not in hot_stocks]
    random.seed(42)
    if len(hot_stocks) >= SAMPLE_SIZE:
        sampled = hot_stocks[:SAMPLE_SIZE]
    else:
        need = SAMPLE_SIZE - len(hot_stocks)
        sampled = hot_stocks + random.sample(other_stocks, min(need, len(other_stocks)))
    print(f"  热点板块:{len(hot_stocks)}只, 随机抽样:{len(sampled)-len(hot_stocks)}只, 总计:{len(sampled)}只")

    all_kls = {}
    for i, s in enumerate(sampled):
        if i % 100 == 0:
            print(f"  K线进度:{i}/{len(sampled)}", flush=True)
        kl = get_kl(s['code'], 80)
        if kl:
            kl['name'] = s['name']
            all_kls[s['code']] = kl
        time.sleep(0.03)
    print(f"  K线获取成功: {len(all_kls)}只")

    # Step 4: 获取沪指K线
    print(f"\n[4] 获取沪指K线...")
    index_kl = get_index_kl(60)
    market = analyze_market(index_kl) if index_kl else None
    if market:
        print(f"  沪指: T-1:{market.get('pct_1d',0):+.2f}% 3日:{market.get('pct_3d',0):+.2f}% 5日:{market.get('pct_5d',0):+.2f}%")
        print(f"  连涨:{market.get('consec_up',0)}天 MA5{'上方' if market.get('above_ma5') else '下方'} MA20{'上方' if market.get('above_ma20') else '下方'}")
        # v12.26: 自动量比识别
        print(f"  量比: {market.get('vol_ratio',1.0):.2f} | {market.get('vol_advice','')}")
    else:
        print("  ✗ 沪指K线获取失败!")

    # Step 5: 获取实时行情(竞价/开盘)
    print(f"\n[5] 获取实时行情...")
    all_codes = list(all_kls.keys())
    rt_data = get_realtime_quote(all_codes)
    print(f"  实时行情: {len(rt_data)}只")

    # Step 6: 技术分析
    print(f"\n[6] 技术分析...")
    all_tech = {}
    for code, kl in all_kls.items():
        tech = analyze_tech(kl)
        if not tech:
            continue
        open_pct = rt_data.get(code, {}).get('open_pct', 0)
        # 排除已涨停开盘的
        if open_pct >= 9.5:
            continue
        all_tech[code] = {
            'tech': tech,
            'name': kl.get('name', ''),
            'open_pct': open_pct,
        }
    print(f"  技术分析成功: {len(all_tech)}只")

    # Step 7: 热点板块检测
    print(f"\n[7] 检测热点板块...")
    hot_sectors = detect_hot_sectors(all_tech)
    if hot_sectors:
        for sec, st in sorted(hot_sectors.items(), key=lambda x: -x[1]['surging'])[:5]:
            print(f"  热点: {sec}(异动={st['surging']},涨停={st['zt']})")
    else:
        print("  无明显热点板块")

    # Step 8: 板块轮动检测
    print(f"\n[8] 检测板块轮动...")
    all_dates_set = set()
    for code, kl in all_kls.items():
        for d in kl['d']:
            all_dates_set.add(d)
    all_dates_sorted = sorted(all_dates_set)
    min_date = (bj - timedelta(days=45)).strftime('%Y-%m-%d')
    relevant_dates = [d for d in all_dates_sorted if min_date <= d]
    sector_zt_history = build_sector_zt_history(all_kls, relevant_dates)
    sector_accel = detect_sector_acceleration(today_str, sector_zt_history)
    if sector_accel:
        for sec, status in sorted(sector_accel.items(), key=lambda x: -len(x[0])):
            print(f"  轮动: {sec} = {status}")
    else:
        print("  无明显轮动信号")

    # Step 9: 大盘态势判断
    print(f"\n[9] 大盘态势判断...")
    if regime_arg:
        regime = regime_arg
        if regime == 'BULL':
            regime_name = '强势上行(用户指定)'
            confidence = 0.95
        elif regime == 'RANGE_BULL':
            regime_name = '震荡偏多(用户指定)'
            confidence = 0.75
        elif regime == 'USER':
            regime_name = '用户条件模式(涨停基因+放量蓄势)'
            confidence = 0.85
        elif regime == 'RELAY':
            regime_name = '连板接力模式(缩量回踩/回调日专用)'
            confidence = 0.85
        else:
            regime = 'CORRECTION'
            regime_name = '回调防守(用户指定)'
            confidence = 0.85
    else:
        # 交互式输入
        print("\n  请输入您的大盘走势判断:")
        print("    1. BULL (强势上行, 追主线龙头)")
        print("    2. RANGE_BULL (震荡偏多, 5攻1防)")
        print("    3. CORRECTION (回调防守, 防守优先)")
        print("    4. RELAY (连板接力, 缩量回踩日专用)")
        print("    5. USER (用户条件模式, 涨停基因+放量蓄势)")
        # v12.26: 自动建议
        if market and market.get('suggested_regime'):
            print(f"\n  >> 系统建议: {market['suggested_regime']} ({market.get('vol_advice','')})")
        try:
            choice = input("\n  请选择(1/2/3/4/5): ").strip()
        except:
            choice = '2'
        if choice == '1':
            regime = 'BULL'
            regime_name = '强势上行(用户判断)'
            confidence = 0.95
        elif choice == '3':
            regime = 'CORRECTION'
            regime_name = '回调防守(用户判断)'
            confidence = 0.85
        elif choice == '4':
            regime = 'RELAY'
            regime_name = '连板接力(用户判断)'
            confidence = 0.85
        elif choice == '5':
            regime = 'USER'
            regime_name = '用户条件模式(涨停基因+放量蓄势)'
            confidence = 0.85
        else:
            regime = 'RANGE_BULL'
            regime_name = '震荡偏多(用户判断)'
            confidence = 0.75

    print(f"\n  态势: [{regime}] {regime_name} (置信度:{confidence:.0%})")

    # Step 10: 评分选股
    print(f"\n[10] v12.24评分选股...")
    daily_scores = []
    for code, data in all_tech.items():
        tech = data['tech']
        name = data['name']
        open_pct = data['open_pct']
        scored = score_v1212(tech, hot_sectors, name, code, open_pct)
        scored['v1212_score'] = scored['score']
        daily_scores.append(scored)

    daily_scores.sort(key=lambda x: -x['score'])
    top20 = daily_scores[:20]
    top6_12 = daily_scores[:top_n]

    pool = {}
    for r in top20:
        pool[r['code']] = r

    local_bull = detect_local_bull(pool)
    if local_bull:
        print(f"  局部牛: {local_bull}")

    # ==================== 评分逻辑 ====================
    if regime == 'USER':
        # USER模式: 全量评分+可买性过滤+双条件优先
        scored_all = []
        for code, data in all_tech.items():
            tech = data['tech']
            name = data['name']
            open_pct = data['open_pct']
            scored = score_v1212(tech, hot_sectors, name, code, open_pct)
            scored['v1212_score'] = scored['score']
            kl_data = all_kls.get(code)
            user_score, user_signals, c1_met, c2_met, buyable = score_user_mode(scored, kl_data, local_bull, sector_accel)
            scored['v1224_score'] = user_score
            scored['surge_signals'] = user_signals
            scored['cond1_met'] = c1_met
            scored['cond2_met'] = c2_met
            scored['is_buyable'] = buyable
            scored['is_defensive'] = is_defensive(name, code)
            scored['is_local_bull'] = any(s in local_bull for s in match_sector(name, code)) if local_bull else False
            scored_all.append(scored)

        # 排除不可买的
        buyable_list = [s for s in scored_all if s.get('is_buyable', True)]
        buyable_list.sort(key=lambda x: -x['v1224_score'])

        # 双条件优先
        both_met = [s for s in buyable_list if s.get('cond1_met') and s.get('cond2_met')]
        only_c1 = [s for s in buyable_list if s.get('cond1_met') and not s.get('cond2_met')]
        only_c2 = [s for s in buyable_list if s.get('cond2_met') and not s.get('cond1_met')]

        top6 = []
        selected = set()

        # 双条件优先(最多10只)
        for s in both_met:
            if len(top6) >= min(10, top_n):
                break
            if s['code'] not in selected:
                s['v1219_mode'] = 'DUAL_COND'
                top6.append(s)
                selected.add(s['code'])

        # 条件1补充
        for s in only_c1:
            if len(top6) >= top_n:
                break
            if s['code'] not in selected:
                s['v1219_mode'] = 'COND1_VOL_ZT'
                top6.append(s)
                selected.add(s['code'])

        # 条件2补充
        for s in only_c2:
            if len(top6) >= top_n:
                break
            if s['code'] not in selected:
                s['v1219_mode'] = 'COND2_MA_ZT'
                top6.append(s)
                selected.add(s['code'])

        # 其他可买个股补充
        for s in buyable_list:
            if len(top6) >= top_n:
                break
            if s['code'] not in selected:
                s['v1219_mode'] = 'BULL'
                top6.append(s)
                selected.add(s['code'])

        top6.sort(key=lambda x: -x['v1224_score'])

    elif regime == 'RELAY':
        # v12.26 RELAY模式: 连板接力策略
        scored_all = []
        for code, data in all_tech.items():
            tech = data['tech']
            name = data['name']
            open_pct = data['open_pct']
            scored = score_v1212(tech, hot_sectors, name, code, open_pct)
            scored['v1212_score'] = scored['score']
            kl_data = all_kls.get(code)

            # 连板接力评分
            relay_score, relay_signals, is_relay = score_relay_v1226(
                scored, kl_data, local_bull, sector_accel, hot_sectors)

            # USER模式动态评分(RELAY权重)
            user_score, user_signals, c1_met, c2_met, buyable = score_user_mode(
                scored, kl_data, local_bull, sector_accel, market_regime='RELAY')

            # 取较高分
            if is_relay and relay_score > 0:
                final_score = relay_score + user_score * 0.3
                scored['v1224_score'] = round(final_score, 1)
                scored['surge_signals'] = relay_signals + user_signals[:3]
                scored['v1219_mode'] = 'RELAY'
            else:
                scored['v1224_score'] = user_score * 0.7
                scored['surge_signals'] = user_signals
                scored['v1219_mode'] = 'USER_FALLBACK'

            # 一字板风险预警
            dist_h10_val = -10
            if kl_data:
                h_arr = kl_data['h']; c_arr = kl_data['c']
                n_k = len(c_arr); yi_k = n_k - 1
                if yi_k >= 9:
                    high_10d = max(h_arr[yi_k-9:yi_k+1])
                    dist_h10_val = (c_arr[yi_k] / high_10d - 1) * 100 if high_10d > 0 else -10
            yz_level, yz_label, yz_can_chase, yz_score, yz_factors = check_yiziban_risk(scored, dist_h10_val)
            scored['yz_level'] = yz_level
            scored['yz_label'] = yz_label
            if yz_factors:
                scored['surge_signals'].append(f'一字板: {yz_label}')

            scored['is_buyable'] = buyable and yz_level != 'HIGH'
            scored['is_defensive'] = is_defensive(name, code)
            scored['is_local_bull'] = any(s in local_bull for s in match_sector(name, code)) if local_bull else False
            scored['is_relay'] = is_relay
            scored_all.append(scored)

        # 连板接力优先, 非连板补充
        relay_stocks = [s for s in scored_all if s.get('is_relay') and s.get('is_buyable', True)]
        relay_stocks.sort(key=lambda x: -x['v1224_score'])

        fallback_stocks = [s for s in scored_all if not s.get('is_relay') and s.get('is_buyable', True)]
        fallback_stocks.sort(key=lambda x: -x['v1224_score'])

        top6 = []
        selected = set()

        for s in relay_stocks:
            if len(top6) >= min(15, top_n):
                break
            if s['code'] not in selected:
                top6.append(s)
                selected.add(s['code'])

        for s in fallback_stocks:
            if len(top6) >= top_n:
                break
            if s['code'] not in selected:
                top6.append(s)
                selected.add(s['code'])

        top6.sort(key=lambda x: -x['v1224_score'])

    elif regime == 'BULL':
        scored_t20 = []
        for r in top20:
            r = dict(r)
            bull_score, signals, bonus = score_bull_v1224(r, local_bull, sector_accel, hot_sectors)
            r['v1224_score'] = bull_score
            r['v1219_mode'] = 'BULL'
            r['surge_signals'] = signals
            r['total_bonus'] = bonus
            r['is_defensive'] = is_defensive(r.get('name', ''), r.get('code'))
            r['is_local_bull'] = any(s in local_bull for s in match_sector(r.get('name', ''), r.get('code')))
            auc, auc_label = auction_score(r)
            r['auction_v1219'] = auc
            r['auction_label'] = auc_label
            scored_t20.append(r)

        scored_t20.sort(key=lambda x: -x['v1224_score'])
        top6 = scored_t20[:top_n]

    elif regime == 'RANGE_BULL':
        scored_all = []
        for r in top20:
            r = dict(r)
            range_score, range_signals, range_bonus = score_range_bull_v1223(r, local_bull, sector_accel)
            corr_score, corr_signals, _, _ = score_correction_v1223(r, local_bull, sector_accel)
            matched = match_sector(r.get('name', ''), r.get('code'))
            is_lb = any(s in local_bull for s in matched) if local_bull else False

            all_signals = range_signals if range_signals else corr_signals
            scored_all.append({
                **r,
                'v1224_range_score': range_score,
                'v1224_corr_score': corr_score,
                'surge_signals': all_signals,
                'total_bonus': range_bonus,
                'is_local_bull': is_lb,
                'is_defensive': is_defensive(r.get('name', ''), r.get('code')),
            })

        by_range = sorted(scored_all, key=lambda x: -x['v1224_range_score'])
        by_corr = sorted(scored_all, key=lambda x: -x['v1224_corr_score'])

        selected = set()
        top6 = []

        # 局部牛优先(1只进攻)
        for s in by_range:
            if s.get('is_local_bull') and s['code'] not in selected:
                s['v1224_score'] = s['v1224_range_score']
                s['v1219_mode'] = 'LOCAL_BULL'
                top6.append(s)
                selected.add(s['code'])
                break

        # 进攻席位(补到5个)
        for s in by_range:
            if len([t for t in top6 if t.get('v1219_mode') in ('BULL', 'LOCAL_BULL', 'SURGE')]) >= 5:
                break
            if s['code'] not in selected:
                s['v1224_score'] = s['v1224_range_score']
                s['v1219_mode'] = 'BULL'
                top6.append(s)
                selected.add(s['code'])

        # 防守席位(1个)
        for s in by_corr:
            if s['code'] not in selected:
                s['v1224_score'] = s['v1224_corr_score']
                s['v1219_mode'] = 'CORRECTION'
                top6.append(s)
                selected.add(s['code'])
                break

        # 补齐
        while len(top6) < top_n:
            for s in by_corr:
                if len(top6) >= top_n:
                    break
                if s['code'] not in selected:
                    s['v1224_score'] = s['v1224_corr_score']
                    s['v1219_mode'] = 'CORRECTION'
                    top6.append(s)
                    selected.add(s['code'])

        top6.sort(key=lambda x: -x['v1224_score'])

    else:  # CORRECTION
        scored = []
        for code, r in pool.items():
            r = dict(r)
            bull_score, bull_signals, bull_bonus = score_bull_v1224(r, local_bull, sector_accel, hot_sectors)
            corr_score, corr_signals, _, _ = score_correction_v1223(r, local_bull, sector_accel)
            matched = match_sector(r.get('name', ''), r.get('code'))
            is_lb = any(s in local_bull for s in matched) if local_bull else False

            all_signals = bull_signals if bull_signals else corr_signals
            scored.append({
                **r,
                'v1224_bull_score': bull_score,
                'v1224_corr_score': corr_score,
                'surge_signals': all_signals,
                'total_bonus': bull_bonus,
                'is_local_bull': is_lb,
                'is_defensive': is_defensive(r.get('name', ''), r.get('code')),
            })

        local_bull_stocks = sorted([s for s in scored if s['is_local_bull']], key=lambda x: -x['v1224_bull_score'])
        surge_stocks = sorted([s for s in scored if not s['is_local_bull'] and s.get('total_bonus', 0) >= 10], key=lambda x: -x['v1224_bull_score'])
        by_v1212 = sorted(scored, key=lambda x: -x.get('v1212_score', 0))
        by_corr = sorted(scored, key=lambda x: -x['v1224_corr_score'])

        selected = set()
        top6 = []
        n_agg = 3

        for s in local_bull_stocks:
            if s['code'] not in selected:
                s['v1224_score'] = s['v1224_bull_score']
                s['v1219_mode'] = 'LOCAL_BULL'
                top6.append(s)
                selected.add(s['code'])
                break

        for s in by_v1212:
            if len([t for t in top6 if t.get('v1219_mode') in ('BULL', 'LOCAL_BULL', 'SURGE')]) >= n_agg:
                break
            if s['code'] not in selected:
                s['v1224_score'] = s.get('v1212_score', 0)
                s['v1219_mode'] = 'BULL'
                top6.append(s)
                selected.add(s['code'])

        for s in surge_stocks:
            if len([t for t in top6 if t.get('v1219_mode') in ('BULL', 'LOCAL_BULL', 'SURGE')]) >= n_agg:
                break
            if s['code'] not in selected:
                s['v1224_score'] = s['v1224_bull_score']
                s['v1219_mode'] = 'SURGE'
                top6.append(s)
                selected.add(s['code'])

        for s in by_corr:
            if len(top6) >= top_n:
                break
            if s['code'] not in selected:
                s['v1224_score'] = s['v1224_corr_score']
                s['v1219_mode'] = 'CORRECTION'
                top6.append(s)
                selected.add(s['code'])

        while len(top6) < top_n:
            for s in by_corr:
                if len(top6) >= top_n:
                    break
                if s['code'] not in selected:
                    s['v1224_score'] = s['v1224_corr_score']
                    s['v1219_mode'] = 'CORRECTION'
                    top6.append(s)
                    selected.add(s['code'])

        top6.sort(key=lambda x: -x['v1224_score'])

    # ==================== 输出结果 ====================
    print(f"\n{'='*100}")
    print(f"  v12.24 TOP{top_n} 选股结果  {bj.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  态势: [{regime}] {regime_name}")
    if hot_sectors:
        top_hot = sorted(hot_sectors.items(), key=lambda x: -x[1]['surging'])[:3]
        hot_str = ', '.join(f"{s}(异动{st['surging']}/涨停{st['zt']})" for s, st in top_hot)
        print(f"  热点: {hot_str}")
    if sector_accel:
        print(f"  轮动: {sector_accel}")
    if local_bull:
        print(f"  局部牛: {local_bull}")
    print(f"{'='*100}")

    for i, r in enumerate(top6):
        name = r.get('name', '')
        code = r.get('code', '')
        score = r.get('v1224_score', 0)
        v12_score = r.get('v1212_score', 0)
        yest_pct = r.get('yest_pct', 0)
        open_pct = r.get('today_open_pct', 0)
        mode = r.get('v1219_mode', '')
        df = "[防守]" if r.get('is_defensive') else ""
        lb = "[局部牛]" if r.get('is_local_bull') else ""
        rl = "[接力]" if r.get('is_relay') else ""
        yz = f"[{r.get('yz_label','')}]" if r.get('yz_label') else ""
        sg = r.get('surge_signals', [])
        sg_str = f"  {sg}" if sg else ""
        matched = match_sector(name, code)
        sec_str = '/'.join(matched) if matched else '-'

        print(f"\n  {'━'*96}")
        print(f"  {i+1}. {name[:10]:<12}({code})  评分:{score:.1f}  板块:{sec_str}")
        print(f"     T-1涨幅:{yest_pct:+.1f}%  竞价开盘:{open_pct:+.1f}%  RSI:{r.get('rsi6',0):.0f}  连板:{r.get('max_consec',0)}  30日涨停:{r.get('zt_30d',0)}  模式:{mode} {df}{lb}{rl}{yz}")
        if sg_str:
            print(f"     信号:{sg_str}")

        advice = generate_trade_advice(r, regime, sector_accel, local_bull)
        print(f"     买入: {advice['buy_point']}")
        print(f"     止损: {advice['stop_loss']}")
        print(f"     止盈: {advice['take_profit']}")
        print(f"     持仓: {advice['hold_advice']}")

    # 保存结果
    output = {
        'version': 'v12.24',
        'scan_time': bj.strftime('%Y-%m-%d %H:%M:%S'),
        'regime': regime,
        'regime_name': regime_name,
        'confidence': confidence,
        'market': market,
        'hot_sectors': {k: v for k, v in hot_sectors.items()},
        'sector_accel': sector_accel,
        'local_bull': local_bull,
        'top_picks': [{
            'rank': i + 1,
            'code': r.get('code', ''),
            'name': r.get('name', ''),
            'score': r.get('v1224_score', 0),
            'v1212_score': r.get('v1212_score', 0),
            'yest_pct': r.get('yest_pct', 0),
            'open_pct': r.get('today_open_pct', 0),
            'rsi6': r.get('rsi6', 0),
            'max_consec': r.get('max_consec', 0),
            'zt_30d': r.get('zt_30d', 0),
            'mode': r.get('v1219_mode', ''),
            'is_defensive': r.get('is_defensive', False),
            'is_local_bull': r.get('is_local_bull', False),
            'is_relay': r.get('is_relay', False),
            'yz_level': r.get('yz_level', ''),
            'yz_label': r.get('yz_label', ''),
            'signals': r.get('surge_signals', []),
            'sectors': match_sector(r.get('name', ''), r.get('code')),
            'trade_advice': generate_trade_advice(r, regime, sector_accel, local_bull),
            'yest_close': r.get('yest_close', 0),
            'ma5': r.get('ma5', 0),
            'ma10': r.get('ma10', 0),
        } for i, r in enumerate(top6)],
    }
    output_path = '/workspace/v1224_picks.json'
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n{'='*100}")
    print(f"  结果已保存: {output_path}")
    print(f"{'='*100}")


if __name__ == '__main__':
    main()
