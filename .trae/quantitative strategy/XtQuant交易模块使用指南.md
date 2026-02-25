
### 创建策略

```
#coding=utf-8
from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import StockAccount
from xtquant import xtconstant

class MyXtQuantTraderCallback(XtQuantTraderCallback):
    def on_disconnected(self):
        """
        连接断开
        :return:
        """
        print("connection lost")
    def on_stock_order(self, order):
        """
        委托回报推送
        :param order: XtOrder对象
        :return:
        """
        print("on order callback:")
        print(order.stock_code, order.order_status, order.order_sysid)
    def on_stock_trade(self, trade):
        """
        成交变动推送
        :param trade: XtTrade对象
        :return:
        """
        print("on trade callback")
        print(trade.account_id, trade.stock_code, trade.order_id)
    def on_order_error(self, order_error):
        """
        委托失败推送
        :param order_error:XtOrderError 对象
        :return:
        """
        print("on order_error callback")
        print(order_error.order_id, order_error.error_id, order_error.error_msg)
    def on_cancel_error(self, cancel_error):
        """
        撤单失败推送
        :param cancel_error: XtCancelError 对象
        :return:
        """
        print("on cancel_error callback")
        print(cancel_error.order_id, cancel_error.error_id, cancel_error.error_msg)
    def on_order_stock_async_response(self, response):
        """
        异步下单回报推送
        :param response: XtOrderResponse 对象
        :return:
        """
        print("on_order_stock_async_response")
        print(response.account_id, response.order_id, response.seq)
    def on_account_status(self, status):
        """
        :param response: XtAccountStatus 对象
        :return:
        """
        print("on_account_status")
        print(status.account_id, status.account_type, status.status)

if __name__ == "__main__":
    print("demo test")
    # path为mini qmt客户端安装目录下userdata_mini路径
    path = 'D:\\迅投极速交易终端 睿智融科版\\userdata_mini'
    # session_id为会话编号，策略使用方对于不同的Python策略需要使用不同的会话编号
    session_id = 123456
    xt_trader = XtQuantTrader(path, session_id)
    # 创建资金账号为1000000365的证券账号对象
    acc = StockAccount('1000000365')
    # StockAccount可以用第二个参数指定账号类型，如沪港通传'HUGANGTONG'，深港通传'SHENGANGTONG'
    # acc = StockAccount('1000000365','STOCK')
    # 创建交易回调类对象，并声明接收回调
    callback = MyXtQuantTraderCallback()
    xt_trader.register_callback(callback)
    # 启动交易线程
    xt_trader.start()
    # 建立交易连接，返回0表示连接成功
    connect_result = xt_trader.connect()
    print(connect_result)
    # 对交易回调进行订阅，订阅后可以收到交易主推，返回0表示订阅成功
    subscribe_result = xt_trader.subscribe(acc)
    print(subscribe_result)
    stock_code = '600000.SH'
    # 使用指定价下单，接口返回订单编号，后续可以用于撤单操作以及查询委托状态
    print("order using the fix price:")
    fix_result_order_id = xt_trader.order_stock(acc, stock_code, xtconstant.STOCK_BUY, 200, xtconstant.FIX_PRICE, 10.5, 'strategy_name', 'remark')
    print(fix_result_order_id)
    # 使用订单编号撤单
    print("cancel order:")
    cancel_order_result = xt_trader.cancel_order_stock(acc, fix_result_order_id)
    print(cancel_order_result)
    # 使用异步下单接口，接口返回下单请求序号seq，seq可以和on_order_stock_async_response的委托反馈response对应起来
    print("order using async api:")
    async_seq = xt_trader.order_stock_async(acc, stock_code, xtconstant.STOCK_BUY, 200, xtconstant.FIX_PRICE, 10.5, 'strategy_name', 'remark')
    print(async_seq)
    # 查询证券资产
    print("query asset:")
    asset = xt_trader.query_stock_asset(acc)
    if asset:
        print("asset:")
        print("cash {0}".format(asset.cash))
    # 根据订单编号查询委托
    print("query order:")
    order = xt_trader.query_stock_order(acc, fix_result_order_id)
    if order:
        print("order:")
        print("order {0}".format(order.order_id))
    # 查询当日所有的委托
    print("query orders:")
    orders = xt_trader.query_stock_orders(acc)
    print("orders:", len(orders))
    if len(orders) != 0:
        print("last order:")
        print("{0} {1} {2}".format(orders[-1].stock_code, orders[-1].order_volume, orders[-1].price))
    # 查询当日所有的成交
    print("query trade:")
    trades = xt_trader.query_stock_trades(acc)
    print("trades:", len(trades))
    if len(trades) != 0:
        print("last trade:")
        print("{0} {1} {2}".format(trades[-1].stock_code, trades[-1].traded_volume, trades[-1].traded_price))
    # 查询当日所有的持仓
    print("query positions:")
    positions = xt_trader.query_stock_positions(acc)
    print("positions:", len(positions))
    if len(positions) != 0:
        print("last position:")
        print("{0} {1} {2}".format(positions[-1].account_id, positions[-1].stock_code, positions[-1].volume))
    # 根据股票代码查询对应持仓
    print("query position:")
    position = xt_trader.query_stock_position(acc, stock_code)
    if position:
        print("position:")
        print("{0} {1} {2}".format(position.account_id, position.stock_code, position.volume))
    # 阻塞线程，接收交易推送
    xt_trader.run_forever()
```

进阶篇
---

XtQuant运行逻辑
-----------

XtQuant封装了策略交易所需要的Python API接口，可以和MiniQMT客户端交互进行报单、撤单、查询资产、查询委托、查询成交、查询持仓以及收到资金、委托、成交和持仓等变动的主推消息。

XtQuant数据字典
-----------

### 交易市场(market)

*   上交所 - `xtconstant.SH_MARKET`
*   深交所 - `xtconstant.SZ_MARKET`
*   北交所 - `xtconstant.MARKET_ENUM_BEIJING`
*   沪港通 - `xtconstant.MARKET_ENUM_SHANGHAI_HONGKONG_STOCK`
*   深港通 - `xtconstant.MARKET_ENUM_SHENZHEN_HONGKONG_STOCK`
*   上期所 - `xtconstant.MARKET_ENUM_SHANGHAI_FUTURE`
*   大商所 - `xtconstant.MARKET_ENUM_DALIANG_FUTURE`
*   郑商所 - `xtconstant.MARKET_ENUM_ZHENGZHOU_FUTURE`
*   中金所 - `xtconstant.MARKET_ENUM_INDEX_FUTURE`
*   能源中心 - `xtconstant.MARKET_ENUM_INTL_ENERGY_FUTURE`
*   广期所 - `xtconstant.MARKET_ENUM_GUANGZHOU_FUTURE`
*   上海期权 - `xtconstant.MARKET_ENUM_SHANGHAI_STOCK_OPTION`
*   深证期权 - `xtconstant.MARKET_ENUM_SHENZHEN_STOCK_OPTION`

### 账号类型(account_type)

*   股票 - `xtconstant.SECURITY_ACCOUNT`
*   信用 - `xtconstant.CREDIT_ACCOUNT`
*   股票期权 - `xtconstant.STOCK_OPTION_ACCOUNT`
*   沪港通 - `xtconstant.HUGANGTONG_ACCOUNT`
*   深港通 - `xtconstant.SHENGANGTONG_ACCOUNT`

### 委托类型(order_type)

*   股票

    *   买入 - `xtconstant.STOCK_BUY`
    *   卖出 - `xtconstant.STOCK_SELL`

*   ETF申赎

    *   申购 - `xtconstant.ETF_PURCHASE`
    *   赎回 - `xtconstant.ETF_REDEMPTION`

### 报价类型(price_type)

提示

1.   市价类型只在实盘环境中生效，模拟环境不支持市价方式报单

*   最新价 - `xtconstant.LATEST_PRICE`
*   指定价 - `xtconstant.FIX_PRICE`

*   上交所/北交所 股票 
    *   最优五档即时成交剩余撤销 - `xtconstant.MARKET_SH_CONVERT_5_CANCEL`
    *   最优五档即时成交剩转限价 - `xtconstant.MARKET_SH_CONVERT_5_LIMIT`
    *   对手方最优价格委托 - `xtconstant.MARKET_PEER_PRICE_FIRST`
    *   本方最优价格委托 - `xtconstant.MARKET_MINE_PRICE_FIRST`

*   深交所 股票 期权 
    *   对手方最优价格委托 - `xtconstant.MARKET_PEER_PRICE_FIRST`
    *   本方最优价格委托 - `xtconstant.MARKET_MINE_PRICE_FIRST`
    *   即时成交剩余撤销委托 - `xtconstant.MARKET_SZ_INSTBUSI_RESTCANCEL`
    *   最优五档即时成交剩余撤销 - `xtconstant.MARKET_SZ_CONVERT_5_CANCEL`
    *   全额成交或撤销委托 - `xtconstant.MARKET_SZ_FULL_OR_CANCEL`

### 委托状态(order_status)

| 枚举变量名 | 值 | 含义 |
| --- | --- | --- |
| xtconstant.ORDER_UNREPORTED | 48 | 未报 |
| xtconstant.ORDER_WAIT_REPORTING | 49 | 待报 |
| xtconstant.ORDER_REPORTED | 50 | 已报 |
| xtconstant.ORDER_REPORTED_CANCEL | 51 | 已报待撤 |
| xtconstant.ORDER_PARTSUCC_CANCEL | 52 | 部成待撤 |
| xtconstant.ORDER_PART_CANCEL | 53 | 部撤（已经有一部分成交，剩下的已经撤单） |
| xtconstant.ORDER_CANCELED | 54 | 已撤 |
| xtconstant.ORDER_PART_SUCC | 55 | 部成（已经有一部分成交，剩下的待成交） |
| xtconstant.ORDER_SUCCEEDED | 56 | 已成 |
| xtconstant.ORDER_JUNK | 57 | 废单 |
| xtconstant.ORDER_UNKNOWN | 255 | 未知 |

### 账号状态(account_status)

| 枚举变量名 | 值 | 含义 |
| --- | --- | --- |
| xtconstant.ACCOUNT_STATUS_INVALID | -1 | 无效 |
| xtconstant.ACCOUNT_STATUS_OK | 0 | 正常 |
| xtconstant.ACCOUNT_STATUS_WAITING_LOGIN | 1 | 连接中 |
| xtconstant.ACCOUNT_STATUSING | 2 | 登陆中 |
| xtconstant.ACCOUNT_STATUS_FAIL | 3 | 失败 |
| xtconstant.ACCOUNT_STATUS_INITING | 4 | 初始化中 |
| xtconstant.ACCOUNT_STATUS_CORRECTING | 5 | 数据刷新校正中 |
| xtconstant.ACCOUNT_STATUS_CLOSED | 6 | 收盘后 |
| xtconstant.ACCOUNT_STATUS_ASSIS_FAIL | 7 | 穿透副链接断开 |
| xtconstant.ACCOUNT_STATUS_DISABLEBYSYS | 8 | 系统停用（总线使用-密码错误超限） |
| xtconstant.ACCOUNT_STATUS_DISABLEBYUSER | 9 | 用户停用（总线使用） |

XtQuant数据结构说明
-------------

### 资产XtAsset

| 属性 | 类型 | 注释 |
| --- | --- | --- |
| account_type | int | 账号类型，参见[数据字典 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E8%B4%A6%E5%8F%B7%E7%B1%BB%E5%9E%8B-account-type) |
| account_id | str | 资金账号 |
| cash | float | 可用金额 |
| frozen_cash | float | 冻结金额 |
| market_value | float | 持仓市值 |
| total_asset | float | 总资产 |

### 委托XtOrder

| 属性 | 类型 | 注释 |
| --- | --- | --- |
| account_type | int | 账号类型，参见[数据字典 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E8%B4%A6%E5%8F%B7%E7%B1%BB%E5%9E%8B-account-type) |
| account_id | str | 资金账号 |
| stock_code | str | 证券代码，例如"600000.SH" |
| order_id | int | 订单编号 |
| order_sysid | str | 柜台合同编号 |
| order_time | int | 报单时间 |
| order_type | int | 委托类型，参见[数据字典 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E5%A7%94%E6%89%98%E7%B1%BB%E5%9E%8B-order-type) |
| order_volume | int | 委托数量 |
| price_type | int | 报价类型，该字段在返回时为柜台返回类型，不等价于下单传入的price_type，枚举值不一样功能一样，参见[数据字典 在新窗口打开](https://dict.thinktrader.net/innerApi/enum_constants.html#enum-ebrokerpricetype-%E4%BB%B7%E6%A0%BC%E7%B1%BB%E5%9E%8B) |
| price | float | 委托价格 |
| traded_volume | int | 成交数量 |
| traded_price | float | 成交均价 |
| order_status | int | 委托状态，参见[数据字典 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E5%A7%94%E6%89%98%E7%8A%B6%E6%80%81-order-status) |
| status_msg | str | 委托状态描述，如废单原因 |
| strategy_name | str | 策略名称 |
| order_remark | str | 委托备注，最大 24 个英文字符 |
| direction | int | 多空方向，股票不适用； |
| offset_flag | int | 交易操作，用此字段区分股票买卖，期货开、平仓，期权买卖等；参见[数据字典 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E4%BA%A4%E6%98%93%E6%93%8D%E4%BD%9C-offset-flag) |

### 成交XtTrade

| 属性 | 类型 | 注释 |
| --- | --- | --- |
| account_type | int | 账号类型，参见[数据字典 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E8%B4%A6%E5%8F%B7%E7%B1%BB%E5%9E%8B-account-type) |
| account_id | str | 资金账号 |
| stock_code | str | 证券代码 |
| order_type | int | 委托类型，参见[数据字典 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E5%A7%94%E6%89%98%E7%B1%BB%E5%9E%8B-order-type) |
| traded_id | str | 成交编号 |
| traded_time | int | 成交时间 |
| traded_price | float | 成交均价 |
| traded_volume | int | 成交数量 |
| traded_amount | float | 成交金额 |
| order_id | int | 订单编号 |
| order_sysid | str | 柜台合同编号 |
| strategy_name | str | 策略名称 |
| order_remark | str | 委托备注，最大 24 个英文字符( |
| direction | int | 多空方向，股票不适用；|
| offset_flag | int | 交易操作，用此字段区分股票买卖，期货开、平仓，期权买卖等；参见[数据字典 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E4%BA%A4%E6%98%93%E6%93%8D%E4%BD%9C-offset-flag) |

### 持仓XtPosition

| 属性 | 类型 | 注释 |
| --- | --- | --- |
| account_type | int | 账号类型，参见[数据字典 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E8%B4%A6%E5%8F%B7%E7%B1%BB%E5%9E%8B-account-type) |
| account_id | str | 资金账号 |
| stock_code | str | 证券代码 |
| volume | int | 持仓数量 |
| can_use_volume | int | 可用数量 |
| open_price | float | 开仓价（返回与成本价一致） |
| market_value | float | 市值 |
| frozen_volume | int | 冻结数量 |
| on_road_volume | int | 在途股份 |
| yesterday_volume | int | 昨夜拥股 |
| avg_price | float | 成本价 |
| direction | int | 多空方向，股票不适用；参见[数据字典 在新窗口打开] |

### 异步下单委托反馈XtOrderResponse

| 属性 | 类型 | 注释 |
| --- | --- | --- |
| account_type | int | 账号类型，参见[数据字典 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E8%B4%A6%E5%8F%B7%E7%B1%BB%E5%9E%8B-account-type) |
| account_id | str | 资金账号 |
| order_id | int | 订单编号 |
| strategy_name | str | 策略名称 |
| order_remark | str | 委托备注 |
| seq | int | 异步下单的请求序号 |

### 异步撤单委托反馈XtCancelOrderResponse

| 属性 | 类型 | 注释 |
| --- | --- | --- |
| account_type | int | 账号类型，参见[数据字典 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E8%B4%A6%E5%8F%B7%E7%B1%BB%E5%9E%8B-account-type) |
| account_id | str | 资金账号 |
| order_id | int | 订单编号 |
| order_sysid | str | 柜台委托编号 |
| cancel_result | int | 撤单结果（0 成功，-1 失败） |
| seq | int | 异步撤单的请求序号 |

### 下单失败错误XtOrderError

| 属性 | 类型 | 注释 |
| --- | --- | --- |
| account_type | int | 账号类型，参见[数据字典 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E8%B4%A6%E5%8F%B7%E7%B1%BB%E5%9E%8B-account-type) |
| account_id | str | 资金账号 |
| order_id | int | 订单编号 |
| error_id | int | 下单失败错误码 |
| error_msg | str | 下单失败具体信息 |
| strategy_name | str | 策略名称 |
| order_remark | str | 委托备注 |

### 撤单失败错误XtCancelError

| 属性 | 类型 | 注释 |
| --- | --- | --- |
| account_type | int | 账号类型，参见[数据字典 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E8%B4%A6%E5%8F%B7%E7%B1%BB%E5%9E%8B-account-type) |
| account_id | str | 资金账号 |
| order_id | int | 订单编号 |
| market | int | 交易市场 0:上海 1:深圳 |
| order_sysid | str | 柜台委托编号 |
| error_id | int | 下单失败错误码 |
| error_msg | str | 下单失败具体信息 |

### 账号状态XtAccountStatus

| 属性 | 类型 | 注释 |
| --- | --- | --- |
| account_type | int | 账号类型，参见[数据字典 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E8%B4%A6%E5%8F%B7%E7%B1%BB%E5%9E%8B-account-type) |
| account_id | str | 资金账号 |
| status | int | 账号状态，参见[数据字典 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E8%B4%A6%E5%8F%B7%E7%8A%B6%E6%80%81-account-status) |

### 账号信息XtAccountInfo

| 属性 | 类型 | 注释 |
| --- | --- | --- |
| account_type | int | 账号类型，参见[数据字典 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E8%B4%A6%E5%8F%B7%E7%B1%BB%E5%9E%8B-account-type) |
| account_id | str | 资金账号 |
| broker_type | int | 同 account_type |
| platform_id | int | 平台号 |
| account_classification | int | 账号分类 |
| login_status | int | 账号状态，参见[数据字典 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E8%B4%A6%E5%8F%B7%E7%8A%B6%E6%80%81-account-status) |


XtQuant API说明
-------------

### 系统设置接口

#### 创建API实例

```
XtQuantTrader(path, session_id)
```

*   释义 
    *   创建XtQuant API的实例

*   参数 
    *   path - str MiniQMT客户端userdata_mini的完整路径
    *   session_id - int 与MiniQMT通信的会话ID，不同的会话要保证不重

*   返回 
    *   XtQuant API实例对象

*   备注 
    *   后续对XtQuant API的操作都需要该实例对象
    *   通常情况下只需要创建一个XtQuant API实例

*   示例

```
path = 'D:\\迅投极速交易终端 睿智融科版\\userdata_mini'
# session_id为会话编号，策略使用方对于不同的Python策略需要使用不同的会话编号
session_id = 123456
#后续的所有示例将使用该实例对象
xt_trader = XtQuantTrader(path, session_id)
```

#### 注册回调类

```
register_callback(callback)
```

*   释义 
    *   将回调类实例对象注册到API实例中，用以消息回调和主推

*   参数 
    *   callback - XtQuantTraderCallback 回调类实例对象

*   返回 
    *   无

*   备注 
    *   无

*   示例

```
# 创建交易回调类对象，并声明接收回调
class MyXtQuantTraderCallback(XtQuantTraderCallback)：
	...
	pass
callback = MyXtQuantTraderCallback()
#xt_trader为XtQuant API实例对象
xt_trader.register_callback(callback)
```

#### 准备API环境

```
start()
```

*   释义 
    *   启动交易线程，准备交易所需的环境

*   参数 
    *   无

*   返回 
    *   无

*   备注 
    *   无

*   示例

```
# 启动交易线程
#xt_trader为XtQuant API实例对象
xt_trader.start()
```

#### 创建连接

```
connect()
```

*   释义 
    *   连接MiniQMT

*   参数 
    *   无

*   返回 
    *   连接结果信息，连接成功返回0，失败返回非0

*   备注 
    *   该连接为一次性连接，断开连接后不会重连，需要再次主动调用

*   示例

```
# 建立交易连接，返回0表示连接成功
#xt_trader为XtQuant API实例对象
connect_result = xt_trader.connect()
print(connect_result)
```

#### 停止运行

```
stop()
```

*   释义 
    *   停止API接口

*   参数 
    *   无

*   返回 
    *   无

*   备注 
    *   无

*   示例

```
#xt_trader为XtQuant API实例对象
xt_trader.stop()
```

#### 阻塞当前线程进入等待状态

```
run_forever()
```

*   释义 
    *   阻塞当前线程，进入等待状态，直到stop函数被调用结束阻塞

*   参数 
    *   无

*   返回 
    *   无

*   备注 
    *   无

*   示例

```
#xt_trader为XtQuant API实例对象
xt_trader.run_forever()
```

#### 开启主动请求接口的专用线程

```
set_relaxed_response_order_enabled(enabled)
```

*   释义

    *   控制主动请求接口的返回是否从额外的专用线程返回，以获得宽松的数据时序

*   参数

    *   enabled - bool 是否开启，默认为False关闭

*   返回

    *   无

*   备注

    *   如果开启，在on_stock_order等推送回调中调用同步请求不会卡住，但查询和推送的数据在时序上会变得不确定

    *   ```
timeline	t1	t2	t3	t4
callback	push1	push2	push3	resp4
do		query4 ------------------^
``` 
    *   例如：分别在t1 t2 t3时刻到达三条委托数据，在on_push1中调用同步委托查询接口query_orders()

    *   未开启宽松时序时，查询返回resp4会在t4时刻排队到push3完成之后处理，这使得同步等待结果的查询不能返回而卡住执行

    *   开启宽松时序时，查询返回的resp4由专用线程返回，程序正常执行，但此时查到的resp4是push3之后的状态，也就是说resp4中的委托要比push2 push3这两个前一时刻推送的数据新，但在更早的t1时刻就进入了处理

    *   使用中请根据策略实际情况来开启，通常情况下，推荐在on_stock_order等推送回调中使用查询接口的异步版本，如`query_stock_orders_async`

### 操作接口

#### 订阅账号信息

```
subscribe(account)
```

*   释义 
    *   订阅账号信息，包括资金账号、委托信息、成交信息、持仓信息

*   参数 
    *   account - StockAccount 资金账号

*   返回 
    *   订阅结果信息，订阅成功返回0，订阅失败返回-1

*   备注 
    *   无

*   示例 
    *   订阅资金账号1000000365

```
account = StockAccount('1000000365')
#xt_trader为XtQuant API实例对象
subscribe_result = xt_trader.subscribe(account)
```

#### 反订阅账号信息

```
unsubscribe(account)
```

*   释义 
    *   反订阅账号信息

*   参数 
    *   account - StockAccount 资金账号

*   返回 
    *   反订阅结果信息，订阅成功返回0，订阅失败返回-1

*   备注 
    *   无

*   示例 
    *   订阅资金账号1000000365

```
account = StockAccount('1000000365')
#xt_trader为XtQuant API实例对象
unsubscribe_result = xt_trader.unsubscribe(account)
```

#### 股票同步报单

```
order_stock(account, stock_code, order_type, order_volume, price_type, price, strategy_name, order_remark)
```

*   释义 
    *   对股票进行下单操作

*   参数 
    *   account - StockAccount 资金账号
    *   stock_code - str 证券代码，如'600000.SH'
    *   order_type - int 委托类型
    *   order_volume - int 委托数量，股票以'股'为单位，债券以'张'为单位
    *   price_type - int 报价类型
    *   price - float 委托价格
    *   strategy_name - str 策略名称
    *   order_remark - str 委托备注

*   返回 
    *   系统生成的订单编号，成功委托后的订单编号为大于0的正整数，如果为-1表示委托失败

*   备注 
    *   无

*   示例 
    *   股票资金账号1000000365对浦发银行买入1000股，使用限价价格10.5元, 委托备注为'order_test'

```
account = StockAccount('1000000365')
#xt_trader为XtQuant API实例对象
order_id = xt_trader.order_stock(account, '600000.SH', xtconstant.STOCK_BUY, 1000, xtconstant.FIX_PRICE, 10.5, 'strategy1', 'order_test')
```

#### 股票异步报单

```
order_stock_async(account, stock_code, order_type, order_volume, price_type, price, strategy_name, order_remark)
```

*   释义 
    *   对股票进行异步下单操作，异步下单接口如果正常返回了下单请求序号seq，会收到on_order_stock_async_response的委托反馈

*   参数 
    *   account - StockAccount 资金账号
    *   stock_code - str 证券代码， 如'600000.SH'
    *   order_type - int 委托类型
    *   order_volume - int 委托数量，股票以'股'为单位，债券以'张'为单位
    *   price_type - int 报价类型
    *   price - float 委托价格
    *   strategy_name - str 策略名称
    *   order_remark - str 委托备注

*   返回 
    *   返回下单请求序号seq，成功委托后的下单请求序号为大于0的正整数，如果为-1表示委托失败

*   备注 
    *   如果失败，则通过下单失败主推接口返回下单失败信息

*   示例 
    *   股票资金账号1000000365对浦发银行买入1000股，使用限价价格10.5元，委托备注为'order_test'

```
account = StockAccount('1000000365')
#xt_trader为XtQuant API实例对象
seq = xt_trader.order_stock_async(account, '600000.SH', xtconstant.STOCK_BUY, 1000, xtconstant.FIX_PRICE, 10.5, 'strategy1', 'order_test')
```

#### 股票同步撤单

```
cancel_order_stock(account, order_id)
```

*   释义 
    *   根据订单编号对委托进行撤单操作

*   参数 
    *   account - StockAccount 资金账号
    *   order_id - int 同步下单接口返回的订单编号,对于期货来说，是order结构中的order_sysid字段

*   返回 
    *   返回是否成功发出撤单指令，0: 成功, -1: 表示撤单失败

*   备注 
    *   无

*   示例 
    *   股票资金账号1000000365对订单编号为order_id的委托进行撤单

```
account = StockAccount('1000000365')
order_id = 100
#xt_trader为XtQuant API实例对象
cancel_result = xt_trader.cancel_order_stock(account, order_id)
```

#### 股票同步撤单

```
cancel_order_stock_sysid(account, market, order_sysid)
```

*   释义 
    *   根据券商柜台返回的合同编号对委托进行撤单操作

*   参数 
    *   account - StockAccount 资金账号
    *   market - int 交易市场
    *   order_sysid - str 券商柜台的合同编号

*   返回 
    *   返回是否成功发出撤单指令，0: 成功， -1: 表示撤单失败

*   备注 
    *   无

*   示例 
    *   股票资金账号1000000365对柜台合同编号为order_sysid的上交所委托进行撤单

```
account = StockAccount('1000000365')
market = xtconstant.SH_MARKET
order_sysid = "100" 
#xt_trader为XtQuant API实例对象
cancel_result = xt_trader.cancel_order_stock_sysid(account, market, order_sysid)
```

#### 股票异步撤单

```
cancel_order_stock_async(account, order_id)
```

*   释义 
    *   根据订单编号对委托进行异步撤单操作

*   参数 
    *   account - StockAccount 资金账号
    *   order_id - int 下单接口返回的订单编号，对于期货来说，是order结构中的order_sysid

*   返回 
    *   返回撤单请求序号, 成功委托后的撤单请求序号为大于0的正整数, 如果为-1表示委托失败

*   备注 
    *   如果失败，则通过撤单失败主推接口返回撤单失败信息

*   示例 
    *   股票资金账号1000000365对订单编号为order_id的委托进行异步撤单

```
account = StockAccount('1000000365')
order_id = 100
#xt_trader为XtQuant API实例对象
cancel_result = xt_trader.cancel_order_stock_async(account, order_id)
```

#### 股票异步撤单

```
cancel_order_stock_sysid_async(account, market, order_sysid)
```

*   释义 
    *   根据券商柜台返回的合同编号对委托进行异步撤单操作

*   参数 
    *   account - StockAccount 资金账号
    *   market - int 交易市场
    *   order_sysid - str 券商柜台的合同编号

*   返回 
    *   返回撤单请求序号, 成功委托后的撤单请求序号为大于0的正整数, 如果为-1表示委托失败

*   备注 
    *   如果失败，则通过撤单失败主推接口返回撤单失败信息

*   示例 
    *   股票资金账号1000000365对柜台合同编号为order_sysid的上交所委托进行异步撤单

```
account = StockAccount('1000000365')
market = xtconstant.SH_MARKET
order_sysid = "100" 
#xt_trader为XtQuant API实例对象
cancel_result = xt_trader.cancel_order_stock_sysid_async(account, market, order_sysid)
```


#### 外部交易数据录入

```
sync_transaction_from_external(operation, data_type, account, deal_list)
```

*   释义

    *   通用数据导出

*   参数

    *   operation - str 操作类型，有"UPDATE","REPLACE","ADD","DELETE"
    *   data_type - str 数据类型，有"DEAL"
    *   account - StockAccount 资金账号
    *   deal_list - list 成交列表,每一项是Deal成交对象的参数字典,键名参考官网数据字典,大小写保持一致

*   返回

    *   result - dict 结果反馈信息

*   示例

```
deal_list = [
    			{'m_strExchangeID':'SF', 'm_strInstrumentID':'ag2407'
        		, 'm_strTradeID':'123456', 'm_strOrderSysID':'1234566'
        		, 'm_dPrice':7600, 'm_nVolume':1
        		, 'm_strTradeDate': '20240627'
            	}
]
resp = xt_trader.sync_transaction_from_external('ADD', 'DEAL', acc, deal_list)
print(resp)
#成功输出示例：{'msg': 'sync transaction from external success'}
#失败输出示例：{'error': {'msg': '[0-0: invalid operation type: ADDD], '}}
``` 

### 股票查询接口

#### 资产查询

```
query_stock_asset(account)
```

*   释义 
    *   查询资金账号对应的资产

*   参数 
    *   account - StockAccount 资金账号

*   返回 
    *   该账号对应的资产对象[XtAsset 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E8%B5%84%E4%BA%A7xtasset)或者None

*   备注 
    *   返回None表示查询失败

*   示例 
    *   查询股票资金账号1000000365对应的资产数据

```
account = StockAccount('1000000365')
#xt_trader为XtQuant API实例对象
asset = xt_trader.query_stock_asset(account)
```

#### 委托查询

```
query_stock_orders(account, cancelable_only = False)
```

*   释义 
    *   查询资金账号对应的当日所有委托

*   参数 
    *   account - StockAccount 资金账号
    *   cancelable_only - bool 仅查询可撤委托

*   返回 
    *   该账号对应的当日所有委托对象[XtOrder 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E5%A7%94%E6%89%98xtorder)组成的list或者None

*   备注 
    *   None表示查询失败或者当日委托列表为空

*   示例 
    *   查询股票资金账号1000000365对应的当日所有委托

```
account = StockAccount('1000000365')
#xt_trader为XtQuant API实例对象
orders = xt_trader.query_stock_orders(account, False)
```

#### 成交查询

```
query_stock_trades(account)
```

*   释义 
    *   查询资金账号对应的当日所有成交

*   参数 
    *   account - StockAccount 资金账号

*   返回 
    *   该账号对应的当日所有成交对象[XtTrade 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E6%88%90%E4%BA%A4xttrade)组成的list或者None

*   备注 
    *   None表示查询失败或者当日成交列表为空

*   示例 
    *   查询股票资金账号1000000365对应的当日所有成交

```
account = StockAccount('1000000365')
#xt_trader为XtQuant API实例对象
trades = xt_trader.query_stock_trades(account)
```

#### 持仓查询

```
query_stock_positions(account)
```

*   释义 
    *   查询资金账号对应的持仓

*   参数 
    *   account - StockAccount 资金账号

*   返回 
    *   该账号对应的最新持仓对象[XtPosition 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E6%8C%81%E4%BB%93xtposition)组成的list或者None

*   备注 
    *   None表示查询失败或者当日持仓列表为空

*   示例 
    *   查询股票资金账号1000000365对应的最新持仓

```
account = StockAccount('1000000365')
#xt_trader为XtQuant API实例对象
positions = xt_trader.query_stock_positions(account)
```


### 其他查询接口


#### 账号信息查询

```
query_account_infos()
```

*   释义

    *   查询所有资金账号

*   参数

    *   无

*   返回

    *   list 账号信息列表

        *   [ XtAccountInfo ]

*   备注

    *   无

#### 账号状态查询

```
query_account_status()
```

*   释义

    *   查询所有账号状态

*   参数

    *   无

*   返回

    *   list 账号状态列表

        *   [ XtAccountStatus ]

*   备注

    *   无

#### 普通柜台资金查询

```
query_com_fund(account)
```

*   释义 
    *   划拨业务查询普通柜台的资金

*   参数 
    *   account - StockAccount 资金账号

*   返回 
    *   result - dict 资金信息，包含以下字段 
        *   success - bool
        *   erro - str
        *   currentBalance - double 当前余额
        *   enableBalance - double 可用余额
        *   fetchBalance - double 可取金额
        *   interest - double 待入账利息
        *   assetBalance - double 总资产
        *   fetchCash - double 可取现金
        *   marketValue - double 市值
        *   debt - double 负债

#### 普通柜台持仓查询

```
query_com_position(account)
```

*   释义 
    *   划拨业务查询普通柜台的持仓

*   参数 
    *   account - StockAccount 资金账号

*   返回 
    *   result - list 持仓信息列表[position1, position2, ...] 
        *   position - dict 持仓信息，包含以下字段 
            *   success - bool
            *   error - str
            *   stockAccount - str 股东号
            *   exchangeType - str 交易市场
            *   stockCode - str 证券代码
            *   stockName - str 证券名称
            *   totalAmt - float 总量
            *   enableAmount - float 可用量
            *   lastPrice - float 最新价
            *   costPrice - float 成本价
            *   income - float 盈亏
            *   incomeRate - float 盈亏比例
            *   marketValue - float 市值
            *   costBalance - float 成本总额
            *   bsOnTheWayVol - int 买卖在途量
            *   prEnableVol - int 申赎可用量

#### 通用数据导出

```
export_data(account, result_path, data_type, start_time = None, end_time = None, user_param = {})
```

*   释义

    *   通用数据导出

*   参数

    *   account - StockAccount 资金账号
    *   result_path - str 导出路径，包含文件名及.csv后缀，如'C:\Users\Desktop\test\deal.csv'
    *   data_type - str 数据类型，如'deal'
    *   start_time - str 开始时间（可缺省）
    *   end_time - str 结束时间（可缺省）
    *   user_param - dict 用户参数（可缺省）

*   返回

    *   result - dict 结果反馈信息

*   示例

```
resp = xt_trader.export_data(acc, 'C:\\Users\\Desktop\\test\\deal.csv', 'deal')
print(resp)
#成功输出示例：{'msg': 'export success'}
#失败输出示例：{'error': {'errorMsg': 'can not find account info, accountID:2000449 accountType:2'}}
``` 

#### 通用数据查询

```
query_data(account, result_path, data_type, start_time = None, end_time = None, user_param = {})
```

*   释义

    *   通用数据查询，利用export_data接口导出数据后再读取其中的数据内容，读取完毕后删除导出的文件

*   参数

同export_data

*   返回

    *   result - dict 数据信息

*   示例

```
data = xt_trader.query_data(acc, 'C:\\Users\\Desktop\\test\\deal.csv', 'deal')
print(data)
#成功输出示例：
#    account_id    account_Type    stock_code    order_type    ...  
#0    2003695    2    688488.SH    23    ...
#1    2003695    2    000096.SZ    23    ...
#失败输出示例：{'error': {'errorMsg': 'can not find account info, accountID:2000449 accountType:2'}}
``` 

### 回调类

```
class MyXtQuantTraderCallback(XtQuantTraderCallback):
    def on_disconnected(self):
        """
        连接状态回调
        :return:
        """
        print("connection lost")
    def on_account_status(self, status):
        """
        账号状态信息推送
        :param response: XtAccountStatus 对象
        :return:
        """
        print("on_account_status")
        print(status.account_id, status.account_type, status.status)
    def on_stock_order(self, order):
        """
        委托信息推送
        :param order: XtOrder对象
        :return:
        """
        print("on order callback:")
        print(order.stock_code, order.order_status, order.order_sysid)
    def on_stock_trade(self, trade):
        """
        成交信息推送
        :param trade: XtTrade对象
        :return:
        """
        print("on trade callback")
        print(trade.account_id, trade.stock_code, trade.order_id)
    def on_order_error(self, order_error):
        """
        下单失败信息推送
        :param order_error:XtOrderError 对象
        :return:
        """
        print("on order_error callback")
        print(order_error.order_id, order_error.error_id, order_error.error_msg)
    def on_cancel_error(self, cancel_error):
        """
        撤单失败信息推送
        :param cancel_error: XtCancelError 对象
        :return:
        """
        print("on cancel_error callback")
        print(cancel_error.order_id, cancel_error.error_id, cancel_error.error_msg)
    def on_order_stock_async_response(self, response):
        """
        异步下单回报推送
        :param response: XtOrderResponse 对象
        :return:
        """
        print("on_order_stock_async_response")
        print(response.account_id, response.order_id, response.seq)
```

#### 连接状态回调

```
on_disconnected()
```

*   释义 
    *   失去连接时推送信息

*   参数 
    *   无

*   返回 
    *   无

*   备注 
    *   无

#### 账号状态信息推送

```
on_account_status(data)
```

*   释义 
    *   账号状态信息变动推送

*   参数 
    *   data - [XtAccountStatus 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E8%B4%A6%E5%8F%B7%E7%8A%B6%E6%80%81xtaccountstatus) 账号状态信息

*   返回 
    *   无

*   备注 
    *   无

#### 委托信息推送

```
on_stock_order(data)
```

*   释义 
    *   委托信息变动推送,例如已成交数量，委托状态变化等

*   参数 
    *   data - [XtOrder 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E5%A7%94%E6%89%98xtorder) 委托信息

*   返回 
    *   无

*   备注 
    *   无

#### 成交信息推送

```
on_stock_trade(data)
```

*   释义 
    *   成交信息变动推送

*   参数 
    *   data - [XtTrade 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E6%88%90%E4%BA%A4xttrade) 成交信息

*   返回 
    *   无

*   备注 
    *   无

#### 下单失败信息推送

```
on_order_error(data)
```

*   释义 
    *   下单失败信息推送

*   参数 
    *   data - [XtOrderError 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E4%B8%8B%E5%8D%95%E5%A4%B1%E8%B4%A5%E9%94%99%E8%AF%AFxtordererror) 下单失败信息

*   返回 
    *   无

*   备注 
    *   无

#### 撤单失败信息推送

```
on_cancel_error(data)
```

*   释义 
    *   撤单失败信息的推送

*   参数 
    *   data - [XtCancelError 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E6%92%A4%E5%8D%95%E5%A4%B1%E8%B4%A5%E9%94%99%E8%AF%AFxtcancelerror) 撤单失败信息

*   返回 
    *   无

*   备注 
    *   无

#### 异步下单回报推送

```
on_order_stock_async_response(data)
```

*   释义 
    *   异步下单回报推送

*   参数 
    *   data - [XtOrderResponse 在新窗口打开](http://dict.thinktrader.net/nativeApi/xttrader.html#%E5%BC%82%E6%AD%A5%E4%B8%8B%E5%8D%95%E5%A7%94%E6%89%98%E5%8F%8D%E9%A6%88xtorderresponse) 异步下单委托反馈

*   返回 
    *   无

*   备注 
    *   无


