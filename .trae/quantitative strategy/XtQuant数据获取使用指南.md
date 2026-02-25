# Role

你是一位精通 QMT (Quant My Trading) 量化交易系统的 Python 专家。

# Constraint (绝对约束)

1. 这是 xtdata 行情模块的 API 文档。请严格基于此文档编写代码。
2. 注意 ContextInfo 对象的使用：在 QMT 内置 Python 环境中，ContextInfo 是全局对象，但在独立进程中需要注意区分。
3. **不要** 使用 tushare 或 pandas\_datareader，必须使用本文档提供的 xtdata 接口。

# API Documentation Content

xtdata是xtquant库中提供行情相关数据的模块，本模块旨在提供精简直接的数据满足量化交易者的数据需求，作为python库的形式可以被灵活添加到各种策略脚本中。

主要提供行情数据（历史和实时的K线和分笔）、财务数据、合约基础信息、板块和行业分类信息等通用的行情数据。

### 运行逻辑

xtdata提供和MiniQmt的交互接口，本质是和MiniQmt建立连接，由MiniQmt处理行情数据请求，再把结果回传返回到python层。使用的行情服务器以及能获取到的行情数据和MiniQmt是一致的，要检查数据或者切换连接时直接操作MiniQmt即可。

对于数据获取接口，使用时需要先确保MiniQmt已有所需要的数据，如果不足可以通过补充数据接口补充，再调用数据获取接口获取。

对于订阅接口，直接设置数据回调，数据到来时会由回调返回。订阅接收到的数据一般会保存下来，同种数据不需要再单独补充。

### 接口分类

* 行情数据（K线数据、分笔数据，订阅和主动获取的接口）

  * 功能划分（接口前缀）

    * subscribe\_ / unsubscribe\_ 订阅/反订阅

    * get\_ 获取数据

    * download\_ 下载数据

  * 常见用法

    * level1数据的历史部分用`download_history_data`补充，实时部分用`subscribe_XXX`订阅，使用`get_XXX`获取

    * level2数据实时部分用`subscribe_XXX`订阅，用`get_l2_XXX`获取。level2函数无历史数据存储，跨交易日后数据清理

* 财务数据

* 合约基础信息

* 基础行情数据板块分类信息等基础信息

### 常用类型说明

* stock\_code - 合约代码

  * 格式为 `code.market`，例如 '000001.SZ', '600000.SH', '000300.SH'

* period - 周期，用于表示要获取的周期和具体数据类型

  * level1数据

    * `tick` - 分笔数据

    * `1m` - 1分钟线

    * `5m` - 5分钟线

    * `15m` - 15分钟线

    * `30m` - 30分钟线

    * `1h` - 1小时线

    * `1d` - 日线

    * `1w` - 周线

    * `1mon` - 月线

    * `1q` - 季度线

    * `1hy` - 半年线

    * `1y` - 年线

  * 投研版 - 特色数据

    * `warehousereceipt` - 期货仓单

    * `futureholderrank` - 期货席位

    * `interactiveqa` - 互动问答

    * 逐笔成交统计

      * `transactioncount1m` - 逐笔成交统计1分钟级

      * `transactioncount1d` - 逐笔成交统计日级

    * `delistchangebond` - 退市可转债信息

    * `replacechangebond` - 待发可转债信息

    * `specialtreatment` - ST 变更历史

    * 港股通（深港通、沪港通）资金流向

      * `northfinancechange1m` - 港股通资金流向1分钟级

      * `northfinancechange1d` - 港股通资金流向日级

    * `dividendplaninfo` - 红利分配方案信息

    * `historycontract` - 过期合约列表

    * `optionhistorycontract` - 期权历史信息

    * `historymaincontract` - 历史主力合约

    * `stoppricedata` - 涨跌停数据

    * `snapshotindex` - 快照指标数据

* 时间范围，用于指定数据请求范围，表示的范围是`[start_time, end_time]`区间（包含前后边界）中最后不多于`count`个数据

  * start\_time - 起始时间，为空则认为是最早的起始时间

  * end\_time - 结束时间，为空则认为是最新的结束时间

  * count - 数据个数，大于0为正常限制返回个数，等于0为不需要返回，-1为返回全部

  * 通常以`[start_time = '', end_time = '', count = -1]`表示完整数据范围，但数据请求范围过大会导致返回时间变长，需要按需裁剪请求范围

* dividend\_type - 除权方式，用于K线数据复权计算，对`tick`等其他周期数据无效

  * `none` 不复权

  * `front` 前复权

  * `back` 后复权

  * `front_ratio` 等比前复权

  * `back_ratio` 等比后复权

* 其他依赖库 numpy、pandas会在数据返回的过程中使用

  * 本模块会尽可能减少对numpy和pandas库的直接依赖，以允许使用者在不同版本的库之间自由切换

  * pandas库中旧的三维数据结构Panel没有被使用，而是以dict嵌套DataFrame代替（后续可能会考虑使用xarray等的方案，也欢迎使用者提供改进建议）

  * 后文中会按常用规则分别简写为np、pd，如np.ndarray、pd.DataFrame

### 请求限制

* 全推数据是市场全部合约的切面数据，是高订阅数场景下的有效解决方案。持续订阅全推数据可以获取到每个合约最新分笔数据的推送，且流量和处理效率都优于单股订阅

* 单股订阅行情是仅返回单股数据的接口，建议单股订阅数量不超过50。如果订阅数较多，建议直接使用全推数据

* 板块分类信息等静态信息更新频率低，无需频繁下载，按周或按日定期下载更新即可

## 接口说明

### 行情接口

#### 订阅单股行情

```
subscribe_quote(stock_code, period='1d', start_time='', end_time='', count=0, callback=None)
```

* 释义

  * 订阅单股的行情数据，返回订阅号

  * 数据推送从callback返回，数据类型和period指定的周期对应

  * 数据范围代表请求的历史部分的数据范围，数据返回后会进入缓存，用于保证数据连续，通常情况仅订阅数据时传`count = 0`即可

* 参数

  * stock\_code - string 合约代码

  * period - string 周期

  * start\_time - string 起始时间

  * end\_time - string 结束时间

  * count - int 数据个数

  * callback - 数据推送回调

    * 回调定义形式为`on_data(datas)`，回调参数`datas`格式为 { stock\_code : \[data1, data2, ...] }

```
def on_data(datas):
    for stock_code in datas:
        	print(stock_code, datas[stock_code])
```

* 返回

  * 订阅号，订阅成功返回`大于0`，失败返回`-1`

* 备注

  * 单股订阅数量不宜过多，详见 接口概述-请求限制

#### 订阅全推行情

```
subscribe_whole_quote(code_list, callback=None)
```

* 释义

  * 订阅全推行情数据，返回订阅号

  * 数据推送从callback返回，数据类型为分笔数据

* 参数

  * code\_list - 代码列表，支持传入市场代码或合约代码两种方式

    * 传入市场代码代表订阅全市场，示例：`['SH', 'SZ']`

    * 传入合约代码代表订阅指定的合约，示例：`['600000.SH', '000001.SZ']`

  * callback - 数据推送回调

    * 回调定义形式为`on_data(datas)`，回调参数`datas`格式为 { stock1 : data1, stock2 : data2, ... }

```
def on_data(datas):
    for stock_code in datas:
        	print(stock_code, datas[stock_code])
```

* 返回

  * 订阅号，订阅成功返回`大于0`，失败返回`-1`

* 备注

  * 订阅后会首先返回当前最新的全推数据

#### 反订阅行情数据

```
unsubscribe_quote(seq)
```

* 释义

  * 反订阅行情数据

* 参数

  * seq - 订阅时返回的订阅号

* 返回

  * 无

* 备注

  * 无

#### 阻塞线程接收行情回调

```
run()
```

* 释义

  * 阻塞当前线程来维持运行状态，一般用于订阅数据后维持运行状态持续处理回调

* 参数

  * seq - 订阅时返回的订阅号

* 返回

  * 无

* 备注

  * 实现方式为持续循环sleep，并在唤醒时检查连接状态，若连接断开则抛出异常结束循环

#### 生成因子数据

```
generate_index_data(formula_name, formula_param = {}, stock_list = [], period = '1d', dividend_type = 'none', start_time = '', end_time = '', fill_mode = 'fixed', fill_value = float('nan'), result_path = None)
```

* 释义

  * 在本地生成因子数据文件，文件格式为feather

* 参数

  * formula\_name:str 模型名称

  * formula\_param:dict 模型参数,例如 {'param1': 1.0, 'param2': 'sym'}

  * stock\_list:list 股票列表

  * period:str 周期

    * 可选范围

      * '1m' '5m' '1d'

  * dividend\_type:str 复权方式

    * 可选范围

      * 'none' - 不复权

      * 'front\_ratio' - 等比前复权

      * 'back\_ratio' - 等比后复权

  * start\_time:str 起始时间 格式为'20240101' 或 '20240101000000'

  * end\_time: str 结束时间 格式为'20241231' 或 '20241231235959'

  * fill\_mode:str 空缺填充方式

    * 可选范围

      * 'fixed' - 固定值填充

      * 'forward' - 向前延续

  * fill\_value:float 填充数值

    * float('nan') - 以NaN填充

  * result\_path:str 结果文件路径，feather格式

* 返回 None

* 备注 必须连接投研端使用，传入的formula\_name需要存在于投研端中

#### 获取行情数据

```
get_market_data(field_list=[], stock_list=[], period='1d', start_time='', end_time='', count=-1, dividend_type='none', fill_data=True)
```

* 释义

  * 从缓存获取行情数据，是主动获取行情的主要接口

* 参数

  * field\_list - list 数据字段列表，传空则为全部字段

  * stock\_list - list 合约代码列表

  * period - string 周期

  * start\_time - string 起始时间

  * end\_time - string 结束时间

  * count - int 数据个数

  * 默认参数，大于等于0时，若指定了start\_time，end\_time，此时以end\_time为基准向前取count条；若start\_time，end\_time缺省，默认取本地数据最新的count条数据；若start\_time，end\_time，count都缺省时，默认取本地全部数据

  * dividend\_type - string 除权方式

  * fill\_data - bool 是否向后填充空缺数据

* 返回

  * period为`1m``5m``1d`等K线周期时

    * 返回dict { field1 : value1, field2 : value2, ... }

    * field1, field2, ... ：数据字段

    * value1, value2, ... ：pd.DataFrame 数据集，index为stock\_list，columns为time\_list

    * 各字段对应的DataFrame维度相同、索引相同

  * period为`tick`分笔周期时

    * 返回dict { stock1 : value1, stock2 : value2, ... }

    * stock1, stock2, ... ：合约代码

    * value1, value2, ... ：np.ndarray 数据集，按数据时间戳`time`增序排列

* 备注

  * 获取lv2数据时需要数据终端有lv2数据权限

  * 时间范围为闭区间

#### 获取本地行情数据

```
get_local_data(field_list=[], stock_list=[], period='1d', start_time='', end_time='', count=-1,
               dividend_type='none', fill_data=True, data_dir=data_dir)
```

* 释义

  * 从本地数据文件获取行情数据，用于快速批量获取历史部分的行情数据

* 参数

  * field\_list - list 数据字段列表，传空则为全部字段

  * stock\_list - list 合约代码列表

  * period - string 周期

  * start\_time - string 起始时间

  * end\_time - string 结束时间

  * count - int 数据个数

  * dividend\_type - string 除权方式

  * fill\_data - bool 是否向后填充空缺数据

  * data\_dir - string MiniQmt配套路径的userdata\_mini路径，用于直接读取数据文件。默认情况下xtdata会通过连接向MiniQmt直接获取此路径，无需额外设置。如果需要调整，可以将数据路径作为`data_dir`传入，也可以直接修改`xtdata.data_dir`以改变默认值

* 返回

  * period为`1m``5m``1d`K线周期时

    * 返回dict { field1 : value1, field2 : value2, ... }

    * field1, field2, ... ：数据字段

    * value1, value2, ... ：pd.DataFrame 数据集，index为stock\_list，columns为time\_list

    * 各字段对应的DataFrame维度相同、索引相同

  * period为`tick`分笔周期时

    * 返回dict { stock1 : value1, stock2 : value2, ... }

    * stock1, stock2, ... ：合约代码

    * value1, value2, ... ：np.ndarray 数据集，按数据时间戳`time`增序排列

* 备注

  * 仅用于获取level1数据

#### 获取全推数据

```
get_full_tick(code_list)
```

* 释义

  * 获取全推数据

* 参数

  * code\_list - 代码列表，支持传入市场代码或合约代码两种方式

    * 传入市场代码代表订阅全市场，示例：`['SH', 'SZ']`

    * 传入合约代码代表订阅指定的合约，示例：`['600000.SH', '000001.SZ']`

* 返回

  * dict 数据集 { stock1 : data1, stock2 : data2, ... }

* 备注

  * 无

#### 获取除权数据

```
get_divid_factors(stock_code, start_time='', end_time='')
```

* 释义

  * 获取除权数据

* 参数

  * stock\_code - 合约代码

  * start\_time - string 起始时间

  * end\_time - string 结束时间

* 返回

  * pd.DataFrame 数据集

* 备注

  * 无

#### 下载历史行情数据

```
download_history_data(stock_code, period, start_time='', end_time='', incrementally = None)
```

* 释义

  * 补充历史行情数据

* 参数

  * stock\_code - string 合约代码

  * period - string 周期

  * start\_time - string 起始时间

  * end\_time - string 结束时间

  * incrementally - 是否增量下载

    * `bool` - 是否增量下载

    * `None` - 使用`start_time`控制，`start_time`为空则增量下载，增量下载时会从本地最后一条数据往后下载

* 返回

  * 无

* 备注

  * 同步执行，补充数据完成后返回

```
download_history_data2(stock_list, period, start_time='', end_time='', callback=None,incrementally = None)
```

* 释义

  * 补充历史行情数据，批量版本

* 参数

  * stock\_list - list 合约列表

  * period - string 周期

  * start\_time - string 起始时间

  * end\_time - string 结束时间

  * callback - func 回调函数

    * 参数为进度信息dict

      * total - 总下载个数

      * finished - 已完成个数

      * stockcode - 本地下载完成的合约代码

      * message - 本次信息

    * <br />

      ```
      ```

def on\_progress(data):
print(data)
\# {'finished': 1, 'total': 50, 'stockcode': '000001.SZ', 'message': ''}

```

*   返回

    *   无

*   备注

    *   同步执行，补充数据完成后返回
    *   有任务完成时通过回调函数返回进度信息

#### 下载过期（退市）合约信息

```

download\_history\_contracts()

```

*   释义 
    *   下载过期（退市）合约信息，过期（退市）标的列表可以通过get_stock_list_in_sector获取

*   参数 
    *   None

*   返回 
    *   无

*   备注 
    *   同步执行，补充数据完成后返回
    *   过期板块名称可以通过 `print([i for i in xtdata.get_sector_list() if "过期" in i])` 查看
    *   下载完成后，可以通过 `xtdata.get_instrument_detail()` 查看过期（退市）合约信息

#### 获取节假日数据

```

get\_holidays()

```

*   释义 
    *   获取截止到当年的节假日日期

*   参数 
    *   无

*   返回 
    *   list，为8位的日期字符串格式

*   备注 
    *   无

#### 获取交易日历

```

get\_trading\_calendar(market, start\_time = '', end\_time = '')

```

*   释义 
    *   获取指定市场交易日历

*   参数 
    *   market - str 市场
    *   start_time - str 起始时间，8位字符串。为空表示当前市场首个交易日时间
    *   end_time - str 结束时间，8位字符串。为空表示当前时间

*   返回 
    *   返回list，完整的交易日列表

*   备注 
    *   结束时间可以填写未来时间，获取未来交易日。需要下载节假日列表。

#### 可转债基础信息的下载

```

download\_cb\_data()

```

*   释义 
    *   下载全部可转债信息

*   参数 
    *   无

*   返回 
    *   无

*   备注 
    *   无

#### 获取可转债基础信息

```

get\_cb\_info(stockcode)

```

*   释义 
    *   返回指定代码的可转债信息

*   参数 
    *   stockcode - str 合约代码（例如`600000.SH`）

*   返回 
    *   dict，可转债信息，具体字段参考[可转债信息字典](https://dict.thinktrader.net/dictionary/bond.html#%E8%8E%B7%E5%8F%96%E5%8F%AF%E8%BD%AC%E5%80%BA%E4%BF%A1%E6%81%AF)

*   备注 
    *   需要先下载可转债数据

#### 获取新股申购信息

```

get\_ipo\_info(start\_time, end\_time)

````

*   释义

    *   返回所选时间范围的新股申购信息

*   参数

    *   start_time: 开始日期（如：'20230327'）
    *   end_time: 结束日期（如：'20230327'）
    *   start_time 和 end_time 为空则返回全部数据

*   返回

    *   list[dict]，新股申购信息

    *   ```
securityCode - string 证券代码
codeName - string 代码简称
market - string 所属市场
actIssueQty - int 发行总量，单位：股
onlineIssueQty - int 网上发行量, 单位：股
onlineSubCode - string 申购代码
onlineSubMaxQty - int 申购上限, 单位：股
publishPrice - float 发行价格
isProfit - int 是否已盈利 0：上市时尚未盈利 1：上市时已盈利
industryPe - float 行业市盈率
afterPE - float 发行后市盈率
````

#### 获取可用周期列表

```
get_period_list()
```

* 释义

  * 返回可用周期列表

* 参数

  * 无

* 返回

  * list 周期列表

#### ETF申赎清单信息下载

```
download_etf_info()
```

* 释义

  * 下载所有ETF申赎清单信息

* 参数

  * 无

* 返回

  * 无

#### ETF申赎清单信息获取

```
get_etf_info()
```

* 释义

  * 获取所有ETF申赎清单信息

* 参数

  * 无

* 返回

  * dict 所有申赎数据

#### 节假日下载

```
download_holiday_data()
```

* 释义

  * 下载节假日数据

* 参数

  * 无

* 返回

  * 无

#### 获取最新交易日k线数据

```
get_full_kline(field_list = [], stock_list = [], period = '1m'
    , start_time = '', end_time = '', count = 1
    , dividend_type = 'none', fill_data = True)
```

* 释义

  * 获取最新交易日k线全推数据,仅支持最新一个交易日，不包含历史值

* 参数

  * 参考`get_market_data`函数

* 返回

  * dict - {field: DataFrame}

### 财务数据接口

#### 获取财务数据

```
get_financial_data(stock_list, table_list=[], start_time='', end_time='', report_type='report_time')
```

* 释义

  * 获取财务数据

* 参数

  * stock\_list - list 合约代码列表

  * table\_list - list 财务数据表名称列表

    * <br />

      ```
      ```

'Balance'          #资产负债表
'Income'           #利润表
'CashFlow'         #现金流量表
'Capital'          #股本表
'Holdernum'        #股东数
'Top10holder'      #十大股东
'Top10flowholder'  #十大流通股东
'Pershareindex'    #每股指标

````

    *   start_time - string 起始时间

    *   end_time - string 结束时间

    *   report_type - string 报表筛选方式

        *   ```
'report_time' 	#截止日期
'announce_time' #披露日期
````

* 返回

  * dict 数据集 { stock1 : datas1, stock2 : data2, ... }

  * stock1, stock2, ... ：合约代码

  * datas1, datas2, ... ：dict 数据集 { table1 : table\_data1, table2 : table\_data2, ... }

    * table1, table2, ... ：财务数据表名

    * table\_data1, table\_data2, ... ：pd.DataFrame 数据集，数据字段详见附录 - 财务数据字段列表

* 备注

  * 无

#### 下载财务数据

```
download_financial_data(stock_list, table_list=[])
```

* 释义

  * 下载财务数据

* 参数

  * stock\_list - list 合约代码列表

  * table\_list - list 财务数据表名列表

* 返回

  * 无

* 备注

  * 同步执行，补充数据完成后返回

```
download_financial_data2(stock_list, table_list=[], start_time='', end_time='', callback=None)
```

* 释义

  * 下载财务数据

* 参数

  * stock\_list - list 合约代码列表

  * table\_list - list 财务数据表名列表

  * start\_time - string 起始时间

  * end\_time - string 结束时间

    * 以`m_anntime`披露日期字段，按`[start_time, end_time]`范围筛选

  * callback - func 回调函数

    * 参数为进度信息dict

      * total - 总下载个数

      * finished - 已完成个数

      * stockcode - 本地下载完成的合约代码

      * message - 本次信息

    * <br />

      ```
      ```

def on\_progress(data):
print(data)
\# {'finished': 1, 'total': 50, 'stockcode': '000001.SZ', 'message': ''}

```

*   返回

    *   无

*   备注

    *   同步执行，补充数据完成后返回

### 基础行情信息

#### 获取合约基础信息

```

get\_instrument\_detail(stock\_code, iscomplete)

```

*   释义

    *   获取合约基础信息

*   参数

    *   stock_code - string 合约代码
    *   iscomplete - bool 是否获取全部字段，默认为False

*   返回

    *   dict 数据字典，{ field1 : value1, field2 : value2, ... }，找不到指定合约时返回`None`

    *   iscomplete为False时，返回以下字段

```

ExchangeID - string 合约市场代码
InstrumentID - string 合约代码
InstrumentName - string 合约名称
ProductID - string 合约的品种ID(期货)
ProductName - string 合约的品种名称(期货)
ExchangeCode - string 交易所代码
UniCode - string 统一规则代码
CreateDate - str 上市日期(期货)
OpenDate - str IPO日期(股票)
ExpireDate - int 退市日或者到期日
PreClose - float 前收盘价格
SettlementPrice - float 前结算价格
UpStopPrice - float 当日涨停价
DownStopPrice - float 当日跌停价
FloatVolume - float 流通股本
TotalVolume - float 总股本
LongMarginRatio - float 多头保证金率
ShortMarginRatio - float 空头保证金率
PriceTick - float 最小价格变动单位
VolumeMultiple - int 合约乘数(对期货以外的品种，默认是1)
MainContract - int 主力合约标记，1、2、3分别表示第一主力合约，第二主力合约，第三主力合约
LastVolume - int 昨日持仓量
InstrumentStatus - int 合约停牌状态
IsTrading - bool 合约是否可交易
IsRecent - bool 是否是近月合约
OpenInterestMultiple - int 交割月持仓倍数

```
    *   iscomplete为True时，增加会返回更多合约信息字段，例如

```

ChargeType - int 期货和期权手续费方式 0表示未知，1表示按元/手，2表示按费率，单位为万分比，‱
ChargeOpen - float 开仓手续费(率) 返回-1时该值无效，其余情况参考ChargeType
ChargeClose - float 平仓手续费(率) 返回-1时该值无效，其余情况参考ChargeType
ChargeTodayOpen - float 开今仓(日内开仓)手续费(率) 返回-1时该值无效，其余情况参考ChargeType
ChargeTodayClose - float 平今仓(日内平仓)手续费(率)  返回-1时该值无效，其余情况参考ChargeType
OptionType - int 期权类型 返回-1表示合约为非期权 返回0为期权认购  返回1为期权认沽
......

```
    *   详细合约信息字段见`附录-合约信息字段列表`

*   备注

    *   可用于检查合约代码是否正确
    *   合约基础信息`CreateDate``OpenDate`字段类型由`int`调整为`str`

#### 获取合约类型

```

get\_instrument\_type(stock\_code)

````

*   释义

    *   获取合约类型

*   参数

    *   stock_code - string 合约代码

*   返回

    *   dict 数据字典，{ type1 : value1, type2 : value2, ... }，找不到指定合约时返回`None`

        *   type1, type2, ... ：string 合约类型
        *   value1, value2, ... ：bool 是否为该类合约

    *   ```
'index'		#指数
'stock'		#股票
'fund'		#基金
'etf'		#ETF
````

* 备注

  * 无

#### 获取交易日列表

```
get_trading_dates(market, start_time='', end_time='', count=-1)
```

* 释义

  * 获取交易日列表

* 参数

  * market - string 市场代码

  * start\_time - string 起始时间

  * end\_time - string 结束时间

  * count - int 数据个数

* 返回

  * list 时间戳列表，\[ date1, date2, ... ]

* 备注

  * 无

#### 获取板块列表

```
get_sector_list()
```

* 释义

  * 获取板块列表

* 参数

  * 无

* 返回

  * list 板块列表，\[ sector1, sector2, ... ]

* 备注

  * 需要下载板块分类信息

#### 获取板块成分股列表

```
get_stock_list_in_sector(sector_name)
```

* 释义

  * 获取板块成分股列表

* 参数

  * sector\_name - string 版块名称

* 返回

  * list 成分股列表，\[ stock1, stock2, ... ]

* 备注

  * 需要板块分类信息

#### 下载板块分类信息

```
download_sector_data()
```

* 释义

  * 下载板块分类信息

* 参数

  * 无

* 返回

  * 无

* 备注

  * 同步执行，下载完成后返回

#### 创建板块目录节点

```
create_sector_folder(parent_node, folder_name, overwrite)
```

* 释义

  * 创建板块目录节点

* 参数

  * parent\_node - string 父节点，’ ‘为 '我的‘ （默认目录）

  * folder\_name - string 要创建的板块目录名称

  * overwrite- bool 是否覆盖，如果目标节点已存在，为True时跳过，为False时在folder\_name后增加数字编号，编号为从1开始自增的第一个不重复的值。 默认为True

* 返回

  * folder\_name2 - string 实际创建的板块目录名

* 备注

  * 无

#### 创建板块

```
create_sector(parent_node, sector_name, overwrite)
```

* 释义

  * 创建板块

* 参数

  * parent\_node - string 父节点，’ ‘为 '我的‘ （默认目录）

  * sector\_name - string 板块名称

  * overwrite- bool 是否覆盖，如果目标节点已存在，为True时跳过，为False时在sector\_name后增加数字编号，编号为从1开始自增的第一个不重复的值。 默认为True

* 返回

  * sector\_name2 - string 实际创建的板块名

* 备注

  * 无

#### 添加自定义板块

```
add_sector(sector_name, stock_list)
```

* 释义

  * 添加自定义板块

* 参数

  * sector\_name - string 板块名称

  * stock\_list - list 成分股列表

* 返回

  * 无

* 备注

  * 无

#### 移除板块成分股

```
remove_stock_from_sector(sector_name, stock_list)
```

* 释义

  * 创建板块

* 参数

  * sector\_name - string 板块名称

  * stock\_list- list 成分股列表

* 返回

  * result - bool 操作成功为True，失败为False

* 备注

  * 无

#### 移除自定义板块

```
remove_sector(sector_name)
```

* 释义

  * 移除自定义板块

* 参数

  * sector\_name - string 板块名称

* 返回

  * 无

* 备注

  * 无

#### 重置板块

```
reset_sector(sector_name, stock_list)
```

* 释义

  * 重置板块

* 参数

  * sector\_name - string 板块名称

  * stock\_list- list 成分股列表

* 返回

  * result - bool 操作成功为True，失败为False

* 备注

  * 无

#### 获取指数成分权重信息

```
get_index_weight(index_code)
```

* 释义

  * 获取指数成分权重信息

* 参数

  * index\_code - string 指数代码

* 返回

  * dict 数据字典，{ stock1 : weight1, stock2 : weight2, ... }

* 备注

  * 需要下载指数成分权重信息

#### 下载指数成分权重信息

```
download_index_weight()
```

* 释义

  * 下载指数成分权重信息

* 参数

  * 无

* 返回

  * 无

* 备注

  * 同步执行，下载完成后返回

## 附录

### 行情数据字段列表

#### tick - 分笔数据

```
'time'                  #时间戳
'lastPrice'             #最新价
'open'                  #开盘价
'high'                  #最高价
'low'                   #最低价
'lastClose'             #前收盘价
'amount'                #成交总额
'volume'                #成交总量
'pvolume'               #原始成交总量
'stockStatus'           #证券状态
'openInt'               #持仓量
'lastSettlementPrice'   #前结算
'askPrice'              #委卖价
'bidPrice'              #委买价
'askVol'                #委卖量
'bidVol'                #委买量
'transactionNum'		#成交笔数
```

#### 1m / 5m / 1d - K线数据

```
'time'                  #时间戳
'open'                  #开盘价
'high'                  #最高价
'low'                   #最低价
'close'                 #收盘价
'volume'                #成交量
'amount'                #成交额
'settelementPrice'      #今结算
'openInterest'          #持仓量
'preClose'              #前收价
'suspendFlag'           #停牌标记 0 - 正常 1 - 停牌 -1 - 当日起复牌
```

#### 除权数据

```
'interest'        		#每股股利（税前，元）
'stockBonus'      		#每股红股（股）
'stockGift'       		#每股转增股本（股）
'allotNum'        		#每股配股数（股）
'allotPrice'      		#配股价格（元）
'gugai'           		#是否股改, 对于股改，在算复权系数时，系统有特殊算法
'dr'              		#除权系数
```

#### l2quote - level2实时行情快照

```
'time'                  #时间戳
'lastPrice'             #最新价
'open'                  #开盘价
'high'                  #最高价
'low'                   #最低价
'amount'                #成交额
'volume'                #成交总量
'pvolume'               #原始成交总量
'openInt'               #持仓量
'stockStatus'           #证券状态
'transactionNum'        #成交笔数
'lastClose'             #前收盘价
'lastSettlementPrice'   #前结算
'settlementPrice'       #今结算
'pe'                    #市盈率
'askPrice'              #多档委卖价
'bidPrice'              #多档委买价
'askVol'                #多档委卖量
'bidVol'                #多档委买量
```

#### l2order - level2逐笔委托

```
'time'                  #时间戳
'price'                 #委托价
'volume'                #委托量
'entrustNo'             #委托号
'entrustType'           #委托类型
'entrustDirection'      #委托方向
```

#### l2transaction - level2逐笔成交

```
'time'                  #时间戳
'price'                 #成交价
'volume'                #成交量
'amount'                #成交额
'tradeIndex'            #成交记录号
'buyNo'                 #买方委托号
'sellNo'                #卖方委托号
'tradeType'             #成交类型
'tradeFlag'             #成交标志
```

#### l2quoteaux - level2实时行情补充（总买总卖）

```
'time'                  #时间戳
'avgBidPrice'           #委买均价
'totalBidQuantity'      #委买总量
'avgOffPrice'           #委卖均价
'totalOffQuantity'      #委卖总量
'withdrawBidQuantity'   #买入撤单总量
'withdrawBidAmount'     #买入撤单总额
'withdrawOffQuantity'   #卖出撤单总量
'withdrawOffAmount'     #卖出撤单总额
```

#### l2orderqueue - level2委买委卖一档委托队列

```
'time'                  #时间戳
'bidLevelPrice'         #委买价
'bidLevelVolume'        #委买量
'offerLevelPrice'       #委卖价
'offerLevelVolume'      #委卖量
'bidLevelNumber'        #委买数量
'offLevelNumber'        #委卖数量
```

### 数据字典

#### 证券状态

```
0,10 - 默认为未知
11 - 开盘前S
12 - 集合竞价时段C
13 - 连续交易T
14 - 休市B
15 - 闭市E
16 - 波动性中断V
17 - 临时停牌P
18 - 收盘集合竞价U
19 - 盘中集合竞价M
20 - 暂停交易至闭市N
21 - 获取字段异常
22 - 盘后固定价格行情
23 - 盘后固定价格行情完毕
```

#### 委托类型

* level2逐笔委托 - `entrustType` 委托类型

* level2逐笔成交 - `tradeType` 成交类型

```
0 - 未知
1 - 正常交易业务
2 - 即时成交剩余撤销
3 - ETF基金申报
4 - 最优五档即时成交剩余撤销
5 - 全额成交或撤销
6 - 本方最优价格
7 - 对手方最优价格
```

#### 委托方向

* level2逐笔委托 - `entrustDirection` 委托方向

  * 注：上交所的撤单信息在逐笔委托的委托方向，区分撤买撤卖

```
1 - 买入
2 - 卖出
3 - 撤买（上交所）
4 - 撤卖（上交所）
```

#### 成交标志

* level2逐笔成交 - `tradeFlag` 成交标志

  * 注：深交所的在逐笔成交的成交标志，只有撤单，没有方向

```
0 - 未知
1 - 外盘
2 - 内盘
3 - 撤单（深交所）
```

#### 现金替代标志

* ETF申赎清单成份股现金替代标志

```
0 - 禁止现金替代（必须有股票）
1 - 允许现金替代（先用股票，股票不足的话用现金替代
2 - 必须现金替代
3 - 非沪市（股票）退补现金替代
4 - 非沪市（股票）必须现金替代
5 - 非沪深退补现金替代
6 - 非沪深必须现金替代
7 - 港市退补现金替代（仅适用于跨沪深ETF产品）
8 - 港市必须现金替代（仅适用于跨沪深港ETF产品）
```

### 合约信息字段列表

```
'ExchangeID' 				#合约市场代码
'InstrumentID' 				#合约代码
'InstrumentName' 			#合约名称
'Abbreviation' 				#合约名称的拼音简写
'ProductID' 				#合约的品种ID（期货）
'ProductName' 				#合约的品种名称（期货）
'UnderlyingCode' 			#标的合约
'ExtendName' 				#扩位名称
'ExchangeCode' 				#交易所代码
'RzrkCode' 					#rzrk代码
'UniCode' 					#统一规则代码
'CreateDate' 				#上市日期（期货）
'OpenDate' 					#IPO日期（股票）
'ExpireDate' 				#退市日或者到期日
'PreClose' 					#前收盘价格
'SettlementPrice' 			#前结算价格
'UpStopPrice' 				#当日涨停价
'DownStopPrice' 			#当日跌停价
'FloatVolume' 				#流通股本
'TotalVolume' 				#总股本
'AccumulatedInterest' 		#自上市付息日起的累积未付利息额（债券）
'LongMarginRatio' 			#多头保证金率
'ShortMarginRatio' 			#空头保证金率
'PriceTick' 				#最小变价单位
'VolumeMultiple' 			#合约乘数（对期货以外的品种，默认是1）
'MainContract' 				#主力合约标记，1、2、3分别表示第一主力合约，第二主力合约，第三主力合约
'MaxMarketOrderVolume' 		#市价单最大下单量
'MinMarketOrderVolume' 		#市价单最小下单量
'MaxLimitOrderVolume' 		#限价单最大下单量
'MinLimitOrderVolume' 		#限价单最小下单量
'MaxMarginSideAlgorithm' 	#上期所大单边的处理算法
'DayCountFromIPO' 			#自IPO起经历的交易日总数
'LastVolume' 				#昨日持仓量
'InstrumentStatus' 			#合约停牌状态
'IsTrading' 				#合约是否可交易
'IsRecent' 					#是否是近月合约
'IsContinuous' 				#是否是连续合约
'bNotProfitable' 			#是否非盈利状态
'bDualClass' 				#是否同股不同权
'ContinueType' 				#连续合约类型
'secuCategory' 				#证券分类
'secuAttri' 				#证券属性
'MaxMarketSellOrderVolume' 	#市价卖单最大单笔下单量
'MinMarketSellOrderVolume' 	#市价卖单最小单笔下单量
'MaxLimitSellOrderVolume' 	#限价卖单最大单笔下单量
'MinLimitSellOrderVolume' 	#限价卖单最小单笔下单量
'MaxFixedBuyOrderVol' 		#盘后定价委托数量的上限（买）
'MinFixedBuyOrderVol' 		#盘后定价委托数量的下限（买）
'MaxFixedSellOrderVol' 		#盘后定价委托数量的上限（卖）
'MinFixedSellOrderVol' 		#盘后定价委托数量的下限（卖）
'HSGTFlag' 					#标识港股是否为沪港通或深港通标的证券。沪港通:0-非标的，1-标的，2-历史标的；深港通:0-非标的，3-标的，4-历史标的，5-是沪港通也是深港通
'BondParValue' 				#债券面值
'QualifiedType' 			#投资者适当性管理分类
'PriceTickType' 			#价差类别（港股用），1-股票，3-债券，4-期权，5-交易所买卖基金
'tradingStatus' 			#交易状态
'OptUnit' 					#期权合约单位
'MarginUnit' 				#期权单位保证金
'OptUndlCode' 				#期权标的证券代码或可转债正股标的证券代码
'OptUndlMarket' 			#期权标的证券市场或可转债正股标的证券市场
'OptLotSize' 				#期权整手数
'OptExercisePrice' 			#期权行权价或可转债转股价
'NeeqExeType' 				#全国股转转让类型，1-协议转让方式，2-做市转让方式，3-集合竞价+连续竞价转让方式（当前全国股转并未实现），4-集合竞价转让
'OptExchFixedMargin' 		#交易所期权合约保证金不变部分
'OptExchMiniMargin' 		#交易所期权合约最小保证金
'Ccy' 						#币种
'IbSecType' 				#IB安全类型，期货或股票
'OptUndlRiskFreeRate' 		#期权标的无风险利率
'OptUndlHistoryRate' 		#期权标的历史波动率
'EndDelivDate' 				#期权行权终止日
'RegisteredCapital' 		#注册资本（单位:百万）
'MaxOrderPriceRange' 		#最大有效申报范围
'MinOrderPriceRange' 		#最小有效申报范围
'VoteRightRatio' 			#同股同权比例
'm_nMinRepurchaseDaysLimit' #最小回购天数
'm_nMaxRepurchaseDaysLimit' #最大回购天数
'DeliveryYear' 				#交割年份
'DeliveryMonth' 			#交割月
'ContractType' 				#标识期权，1-过期，2-当月，3-下月，4-下季，5-隔季，6-隔下季
'ProductTradeQuota' 		#期货品种交易配额
'ContractTradeQuota' 		#期货合约交易配额
'ProductOpenInterestQuota' 	#期货品种持仓配额
'ContractOpenInterestQuota' #期货合约持仓配额
'ChargeType' 				#期货和期权手续费方式，0-未知，1-按元/手，2-按费率
'ChargeOpen' 				#开仓手续费率，-1表示没有
'ChargeClose' 				#平仓手续费率，-1表示没有
'ChargeClose'				#平仓手续费率，-1表示没有
'ChargeTodayOpen'			#开今仓（日内开仓）手续费率，-1表示没有
'ChargeTodayClose'			#平今仓（日内平仓）手续费率，-1表示没有
'OptionType'				#期权类型，-1为非期权，0为期权认购，1为期权认沽
'OpenInterestMultiple'		#交割月持仓倍数
```

### 代码示例

#### 时间戳转换

```
import time
def conv_time(ct):
    '''
    conv_time(1476374400000) --> '20161014000000.000'
    '''
    local_time = time.localtime(ct / 1000)
    data_head = time.strftime('%Y%m%d%H%M%S', local_time)
    data_secs = (ct - int(ct)) * 1000
    time_stamp = '%s.%03d' % (data_head, data_secs)
    return time_stamp
```

