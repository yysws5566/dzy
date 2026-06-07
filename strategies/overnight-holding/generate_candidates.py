"""
生成一夜持股法初筛候选池（200只）
- 全市场A股基础过滤
- 保存为 candidates_YYYYMMDD.csv
"""
import akshare as ak
import pandas as pd
from datetime import datetime

today_str = datetime.now().strftime('%Y%m%d')
print(f"生成候选池: {today_str}")
print("=" * 60)

# 获取全市场A股
print("获取全市场A股实时行情...")
spot = ak.stock_zh_a_spot_em()
print(f"全市场: {len(spot)} 只")

# 初筛条件（参考用户策略）
# 1. 股价 < 20元
# 2. 流通市值 < 150亿
# 3. 排除ST
# 4. 量比 > 0.8（有基本活跃度）
# 5. 涨跌幅 > -5%（排除暴跌股）

print("\n初筛条件：")
print("  股价 < 20元")
print("  流通市值 < 150亿")
print("  排除ST")
print("  量比 > 0.8")
print("  涨跌幅 > -5%")

df = spot.copy()
df['最新价'] = pd.to_numeric(df['最新价'], errors='coerce')
df['流通市值'] = pd.to_numeric(df['流通市值'], errors='coerce')
df['量比'] = pd.to_numeric(df['量比'], errors='coerce')
df['涨跌幅'] = pd.to_numeric(df['涨跌幅'], errors='coerce')

# 应用过滤
mask = (
    (df['最新价'] < 20) &
    (df['流通市值'] < 1.5e10) &
    (~df['名称'].str.contains('ST|退', na=False)) &
    (df['量比'] > 0.8) &
    (df['涨跌幅'] > -5)
)
filtered = df[mask].copy()
print(f"\n初筛后: {len(filtered)} 只")

# 按涨跌幅排序，取前200只（偏强势）
filtered = filtered.sort_values('涨跌幅', ascending=False).head(200)
print(f"取前200只（偏强势）")

# 保存
output_file = f'candidates_{today_str}.csv'
filtered[['代码', '名称', '最新价', '涨跌幅', '成交额', '量比', '流通市值']].to_csv(
    output_file, index=False, encoding='utf-8-sig'
)
print(f"\n候选池已保存: {output_file}")
print(f"数量: {len(filtered)} 只")
