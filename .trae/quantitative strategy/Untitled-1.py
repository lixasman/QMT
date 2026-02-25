import sys
import os

# ==================== 核心修改区 ====================
# 请找到你电脑上 QMT 的安装目录，精确到 site-packages 文件夹
# 例如："D:\\国金QMT\\bin.x64\\Lib\\site-packages"
qmt_path = r"C:\你的QMT安装目录\bin.x64\Lib\site-packages" 

# 将 QMT 的自带库路径强行插入到系统环境变量的最前面！
sys.path.insert(0, qmt_path) 
# ====================================================

# 现在导入的 xtdata，绝对是 QMT 客户端自带的最新原装正版
from xtquant import xtdata
import pandas as pd

print("正在更新因子列表...")
xtdata.download_metatable_data() 

table_name = "factor_technical"
stock_code = "510300.SH" 

print(f"开始下载 {stock_code} 的 {table_name} 数据...")
xtdata.download_history_data(stock_code, table_name, '', '', incrementally=False) 
print("云端数据下载完成！\n")

# 这里填入 QMT 的主目录
xt_install_path = r"D:\国金证券QMT交易端" 
factor_name = "macdc"

path = os.path.join(xt_install_path, "datadir", "EP")
data_file = os.path.join(path, f"{factor_name}_Xdat2", "data.fe")

print(f"尝试读取本地文件: {data_file}")

if os.path.exists(data_file):
    factor_data = pd.read_feather(data_file)
    print("\n✅ 数据读取成功！前5行数据如下：")
    print(factor_data.head())
else:
    print(f"\n❌ 未找到文件，请检查安装路径 ({xt_install_path}) 是否正确。")