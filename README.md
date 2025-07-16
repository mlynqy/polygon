# TRON交易监控与Telegram通知

## 功能简介

- **多用户支持**: 支持多个用户同时使用，每个用户可独立绑定地址
- **动态绑定**: 用户通过Telegram Bot命令动态绑定/解绑TRON地址
- **实时监控**: 实时监控所有绑定的TRON地址交易活动
- **智能通知**: 支持TRX转账和智能合约调用的详细通知
- **数据持久化**: 使用SQLite数据库存储用户绑定信息
- **备注功能**: 每个绑定地址可设置备注，方便识别
- **TRC-20代币支持**: 完整支持USDT、USDC等TRC-20代币监控
- **资源监控**: 监控账户能量和带宽状态
- **多交易类型**: 支持TRX转账、代币转账、质押、投票等多种交易类型
- **API支持**: 支持TronGrid API密钥以提高请求限制
- **WebSocket监控**: 支持WebSocket实时推送和轮询两种监控模式

## 环境要求

- Python 3.7+
- tronpy
- python-telegram-bot
- python-dotenv
- requests

## 安装依赖

```bash
pip install -r requirements.txt
```

## 配置

1. 复制`.env.example`为`.env`，并填写相关参数：
   - `TELEGRAM_BOT_TOKEN`：你的Telegram Bot Token（必需）
   - `TRON_API_KEY`：TronGrid API密钥（可选，建议申请以提高请求限制）
   - `CHECK_INTERVAL`：检查间隔（秒），默认30秒
   - `DB_PATH`：数据库文件路径，默认为`tron_monitor.db`
   - `USE_WEBSOCKET`：是否启用WebSocket实时监控（可选），默认为false
   - `WEBSOCKET_URL`：WebSocket服务器地址（可选），需使用第三方服务

### WebSocket实时监控配置

**重要提示**: TronGrid官方不提供WebSocket服务，以下配置仅为示例

```bash
# 建议使用轮询模式（默认）
USE_WEBSOCKET=false

# 如需使用第三方WebSocket服务，请先注册相应服务
# WEBSOCKET_URL=wss://api.tronsocket.com  # 示例：TronSocket服务
```

**第三方WebSocket服务选项**:
- **TronSocket.com**: 专业的TRON WebSocket服务
- **Bitquery**: 提供TRON实时数据流

## 监控模式

### WebSocket实时监控（需第三方服务）
- **重要说明**: TronGrid官方不提供WebSocket服务
- **替代方案**: 可使用第三方服务如TronSocket.com（需注册账号）
- **优势**: 真正的实时推送，延迟极低（毫秒级）
- **配置**: 设置 `USE_WEBSOCKET=true` 并配置有效的WebSocket URL
- **适用**: 对实时性要求高且愿意使用第三方服务的场景
- **资源**: 网络连接稳定，CPU占用较低

### 轮询监控
- **优势**: 稳定可靠，兼容性好
- **配置**: 设置 `USE_WEBSOCKET=false`
- **适用**: 网络环境不稳定或对实时性要求不高的场景
- **资源**: 定期API调用，可配置间隔时间
- **延迟**: 取决于CHECK_INTERVAL设置（默认30秒）

### 性能对比

| 特性 | WebSocket模式 | 轮询模式 |
|------|---------------|----------|
| 实时性 | 毫秒级 | 30秒（可配置） |
| 资源占用 | 低 | 中等 |
| 网络要求 | 稳定连接 | 一般 |
| API调用频率 | 极低 | 高 |
| 推荐场景 | 交易频繁地址 | 一般监控需求 |

## 使用方法

### 1. 启动服务

```bash
python tron_monitor.py
```

### 2. 与Bot交互

在Telegram中找到你的Bot，发送以下命令：

- `/start` - 查看欢迎信息和命令列表
- `/bind <地址> <备注>` - 绑定TRON地址进行监控
- `/list` - 查看已绑定的地址列表
- `/unbind <地址>` - 解绑指定地址
- `/resources <地址>` - 查看指定地址的能量和带宽状态
- `/status` - 查看监控系统状态、连接状态和统计信息
- `/help` - 查看详细帮助信息

### 3. 绑定示例

```
/bind TRX9Pjwn8FbTHPaXyUpqn7ZCm1TaNqJV6j 我的主钱包
/bind TLPcqz5h8FjKrxPaXyUpqn7ZCm1TaNqJV6j 交易所钱包
```

### 获取Telegram Bot Token

1. 在Telegram中搜索`@BotFather`
2. 发送`/newbot`创建新机器人
3. 按提示设置机器人名称和用户名
4. 获得Bot Token
5. 将Bot Token填入`.env`文件中

### 获取TronGrid API密钥

1. 访问[TronGrid官网](https://www.trongrid.io/)
2. 注册账户并申请API密钥
3. 免费版本有一定的请求限制，付费版本可获得更高限制

**注意**: TronGrid仅提供HTTP API，不提供WebSocket服务。如需实时监控，请考虑使用第三方WebSocket服务或使用轮询模式。

## 运行

```bash
python tron_monitor.py
```

## 通知格式

### TRX转账通知
```
新交易！我的钱包 15.500000 TRX

交易类型：#入账

交易币种：#TRX

交易金额：15.500000 TRX

出账地址：TQn9Y2khEsLMG73Lyub9kU...  （← 交易所）

入账地址：TRX9Pjwn...  （← 我的钱包）

交易时间：2024-01-15 14:30:25

交易哈希：a1b2c3d4e5f6...

区块哈希：f6e5d4c3b2a1...

区块高度：12345678

📊 账户资源状态：
⚡ 能量：45,230/50,000
📡 带宽：1,200/5,000
```

### TRC-20代币转账通知
```
新交易！USDT钱包 100.000000 USDT

交易类型：#出账

交易币种：#USDT

交易金额：100.000000 USDT

出账地址：TRX9Pjwn...  （← USDT钱包）

入账地址：TQn9Y2khEsLMG73Lyub9kU...

交易时间：2024-01-15 14:35:12

交易哈希：b2c3d4e5f6a1...

区块哈希：e5f6a1b2c3d4...

区块高度：12345679

代币合约：TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t

📊 账户资源状态：
⚡ 能量：42,180/50,000
📡 带宽：1,250/5,000
```

### 质押/解质押通知
```
新交易！主钱包 1000.000000 TRX

交易类型：#质押

交易币种：#TRX

交易金额：1000.000000 TRX

出账地址：TRX9Pjwn...  （← 主钱包）

入账地址：TRX9Pjwn...  （← 主钱包）

交易时间：2024-01-15 14:40:30

交易哈希：c3d4e5f6a1b2...

区块哈希：f6a1b2c3d4e5...

区块高度：12345680

质押类型：能量
质押金额：1000.000000 TRX
```

## 支持的交易类型

### 基础交易
- **TRX转账** (`TransferContract`) - 原生TRX代币转账
- **TRC-10代币转账** (`TransferAssetContract`) - TRON原生代币转账

### 智能合约交易
- **TRC-20代币转账** (`TriggerSmartContract`) - 支持USDT、USDC、JST、WIN、BTT等主流代币
- **智能合约调用** - 其他DeFi协议交互

### 资源管理
- **质押交易** (`FreezeBalanceContract`) - 质押TRX获取能量或带宽
- **解质押交易** (`UnfreezeBalanceContract`) - 解除TRX质押

### 治理交易
- **超级代表投票** (`VoteWitnessContract`) - 参与TRON网络治理

### 支持的TRC-20代币
- **USDT** (TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t)
- **USDC** (TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8)
- **JST** (TCFLL5dx5ZJdKnWuesXxi1VPwjLVmWZZy9)
- **WIN** (TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7)
- **BTT** (TKkeiboTkxXKJpbmVFbv4a8ov5rAfRDMf9)
- **TUSD** (TUpMhErZL2fhh4sVNULAbNKLokS4GjC1F4)
- **ETH** (THb4CqiFdwNHsWsQCs4JhzwjMWys4aqCbF)
- **BTC** (TN3W4H6rK2ce4vX9YnFQHwKENnHjoxb3m9)
- 以及其他符合TRC-20标准的代币

## 注意事项

- 建议申请TronGrid API密钥以避免请求限制
- 监控间隔不宜设置过短，避免频繁请求API
- 确保网络连接稳定，程序会在异常时发送错误通知
- 首次运行时会处理最近的交易，避免重复通知

## 故障排除

1. **无法获取交易数据**
   - 检查网络连接
   - 验证TRON地址格式是否正确
   - 确认API密钥是否有效

2. **Telegram消息发送失败**
   - 检查Bot Token是否正确
   - 确认Chat ID是否正确
   - 确保机器人已添加到目标聊天中

3. **程序异常退出**
   - 查看控制台错误信息
   - 检查环境变量配置
   - 确认所有依赖包已正确安装

## 扩展功能

可以根据需要扩展以下功能：
- 支持多地址监控
- 添加交易金额过滤
- 支持TRC20代币交易解析
- 添加Web界面管理
- 数据库存储交易记录