import os
import asyncio
import time
import sqlite3
import re
import json
import websockets
import aiohttp
from tronpy import Tron
from tronpy.providers import HTTPProvider
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv
import requests
from threading import Lock

load_dotenv()

# 环境变量
TRON_API_KEY = os.getenv('TRON_API_KEY')  # TronGrid API Key
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', 30))  # 检查间隔（秒）
DB_PATH = os.getenv('DB_PATH', 'tron_monitor.db')  # 数据库路径
USE_WEBSOCKET = os.getenv('USE_WEBSOCKET', 'true').lower() == 'true'  # 是否使用WebSocket
WEBSOCKET_URL = os.getenv('WEBSOCKET_URL', 'wss://api.trongrid.io/websocket')  # WebSocket地址

# 初始化TRON客户端
if TRON_API_KEY:
    # 使用TronGrid API with HTTPProvider
    client = Tron(HTTPProvider(api_key=TRON_API_KEY))
else:
    # 使用默认节点
    client = Tron(network='mainnet')

# 全局变量
processed_txs = {}  # {address: set(tx_hashes)}
processed_transactions = set()  # 全局已处理交易集合
db_lock = Lock()
bot_instance = None
websocket_connections = {}  # WebSocket连接池
monitored_addresses = set()  # 当前监控的地址集合
websocket_running = False  # WebSocket运行状态

# 常见TRC-20代币合约地址映射
TRC20_TOKENS = {
    'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t': {'symbol': 'USDT', 'decimals': 6, 'name': 'Tether USD'},
    'TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8': {'symbol': 'USDC', 'decimals': 6, 'name': 'USD Coin'},
    'TCFLL5dx5ZJdKnWuesXxi1VPwjLVmWZZy9': {'symbol': 'JST', 'decimals': 18, 'name': 'JUST'},
    'TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7': {'symbol': 'WIN', 'decimals': 6, 'name': 'WINkLink'},
    'TKkeiboTkxXKJpbmVFbv4a8ov5rAfRDMf9': {'symbol': 'BTT', 'decimals': 18, 'name': 'BitTorrent'},
    'TUpMhErZL2fhh4sVNULAbNKLokS4GjC1F4': {'symbol': 'TUSD', 'decimals': 18, 'name': 'TrueUSD'},
    'THb4CqiFdwNHsWsQCs4JhzwjMWys4aqCbF': {'symbol': 'ETH', 'decimals': 18, 'name': 'Ethereum'},
    'TN3W4H6rK2ce4vX9YnFQHwKENnHjoxb3m9': {'symbol': 'BTC', 'decimals': 8, 'name': 'Bitcoin'}
}

# Transfer方法签名
TRANSFER_METHOD_SIGNATURE = 'a9059cbb'

def init_database():
    """初始化数据库"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_bindings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                tron_address TEXT NOT NULL,
                remark TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, tron_address)
            )
        ''')
        conn.commit()

def get_user_bindings(chat_id=None):
    """获取用户绑定信息"""
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            if chat_id:
                cursor.execute('SELECT tron_address, remark FROM user_bindings WHERE chat_id = ?', (str(chat_id),))
            else:
                cursor.execute('SELECT chat_id, tron_address, remark FROM user_bindings')
            return cursor.fetchall()

def add_user_binding(chat_id, tron_address, remark):
    """添加用户绑定"""
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    'INSERT OR REPLACE INTO user_bindings (chat_id, tron_address, remark) VALUES (?, ?, ?)',
                    (str(chat_id), tron_address, remark)
                )
                conn.commit()
                return True
            except Exception as e:
                print(f"添加绑定失败: {e}")
                return False

def remove_user_binding(chat_id, tron_address):
    """移除用户绑定"""
    with db_lock:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'DELETE FROM user_bindings WHERE chat_id = ? AND tron_address = ?',
                (str(chat_id), tron_address)
            )
            conn.commit()
            return cursor.rowcount > 0

async def send_telegram_message(chat_id, text):
    """发送Telegram消息"""
    try:
        await bot_instance.send_message(chat_id=chat_id, text=text, parse_mode='Markdown')
        print(f"Telegram消息发送成功 (Chat: {chat_id}): {text[:50]}...")
    except Exception as e:
        print(f"发送Telegram消息失败 (Chat: {chat_id}): {e}")

def is_valid_tron_address(address):
    """验证TRON地址格式"""
    if not address:
        return False
    # TRON地址通常以T开头，长度为34位
    if len(address) == 34 and address.startswith('T'):
        return True
    return False

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/start命令"""
    welcome_text = (
        "🤖 *TRON交易监控Bot*\n\n"
        "欢迎使用TRON交易监控服务！\n\n"
        "*可用命令：*\n"
        "/bind <地址> <备注> - 绑定TRON地址进行监控\n"
        "/list - 查看已绑定的地址\n"
        "/unbind <地址> - 解绑指定地址\n"
        "/resources <地址> - 查看账户资源状态\n"
        "/status - 查看监控系统状态\n"
        "/help - 显示帮助信息\n\n"
        "*示例：*\n"
        "`/bind TRX9Pjwn... 我的钱包`\n\n"
        "💡 *功能特点：*\n"
        "• 支持WebSocket实时监控和轮询监控\n"
        "• 支持监控TRX、TRC-20、TRC-10代币转账\n"
        "• 支持能量/带宽质押与解质押监控\n"
        "• 支持超级代表投票监控\n"
        "• 每个地址可添加备注便于识别\n"
        "• 通知包含详细交易信息和资源状态"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/help命令"""
    help_text = (
        "*📖 使用说明*\n\n"
        "*绑定地址：*\n"
        "`/bind <TRON地址> <备注>`\n"
        "例如：`/bind TRX9Pjwn... 主钱包`\n\n"
        "*查看绑定：*\n"
        "`/list` - 显示所有已绑定的地址\n\n"
        "*解绑地址：*\n"
        "`/unbind <TRON地址>`\n\n"
        "*查看资源：*\n"
        "`/resources <TRON地址>` - 查看能量和带宽状态\n\n"
        "*系统状态：*\n"
        "`/status` - 查看监控系统状态、连接状态和统计信息\n\n"
        "*支持的交易类型：*\n"
        "• TRX转账\n"
        "• TRC-20代币转账（USDT、USDC等）\n"
        "• TRC-10代币转账\n"
        "• 能量/带宽质押和解质押\n"
        "• 超级代表投票\n"
        "• 智能合约调用\n\n"
        "*注意事项：*\n"
        "• 地址必须是有效的TRON地址格式\n"
        "• 每个地址可以设置备注方便识别\n"
        "• 监控到交易后会立即推送通知\n"
        "• 通知包含账户资源状态信息"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def bind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/bind命令"""
    chat_id = update.effective_chat.id
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "❌ 格式错误！\n\n使用方法：`/bind <TRON地址> <备注>`\n\n示例：`/bind TRX9Pjwn... 我的钱包`",
            parse_mode='Markdown'
        )
        return
    
    tron_address = context.args[0]
    remark = ' '.join(context.args[1:])
    
    # 验证地址格式
    if not is_valid_tron_address(tron_address):
        await update.message.reply_text(
            "❌ 无效的TRON地址格式！\n\n请确保地址以T开头且长度为34位。"
        )
        return
    
    # 添加绑定
    if add_user_binding(chat_id, tron_address, remark):
        # 初始化该地址的交易记录
        if tron_address not in processed_txs:
            processed_txs[tron_address] = set()
        
        await update.message.reply_text(
            f"✅ *绑定成功！*\n\n"
            f"*地址：* `{tron_address}`\n"
            f"*备注：* {remark}\n\n"
            f"现在开始监控该地址的交易活动。",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("❌ 绑定失败，请稍后重试。")

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/list命令"""
    chat_id = update.effective_chat.id
    bindings = get_user_bindings(chat_id)
    
    if not bindings:
        await update.message.reply_text("📝 您还没有绑定任何地址。\n\n使用 `/bind <地址> <备注>` 来绑定地址。", parse_mode='Markdown')
        return
    
    message = "📋 *您的绑定地址：*\n\n"
    for i, (address, remark) in enumerate(bindings, 1):
        message += f"{i}. `{address}`\n   📝 {remark}\n\n"
    
    message += "使用 `/unbind <地址>` 来解绑地址。"
    await update.message.reply_text(message, parse_mode='Markdown')

async def unbind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/unbind命令"""
    chat_id = update.effective_chat.id
    
    if len(context.args) != 1:
        await update.message.reply_text(
            "❌ 格式错误！\n\n使用方法：`/unbind <TRON地址>`",
            parse_mode='Markdown'
        )
        return
    
    tron_address = context.args[0]
    
    if remove_user_binding(chat_id, tron_address):
        await update.message.reply_text(
            f"✅ *解绑成功！*\n\n已停止监控地址：`{tron_address}`",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "❌ 解绑失败！\n\n请确认地址是否正确或您是否已绑定该地址。"
        )

async def resources_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/resources命令 - 查看账户资源状态"""
    chat_id = update.effective_chat.id
    
    if len(context.args) != 1:
        await update.message.reply_text(
            "❌ 格式错误！\n\n使用方法：`/resources <TRON地址>`\n\n示例：`/resources TRX9Pjwn...`",
            parse_mode='Markdown'
        )
        return
    
    tron_address = context.args[0]
    
    # 验证地址格式
    if not is_valid_tron_address(tron_address):
        await update.message.reply_text(
            "❌ 无效的TRON地址格式！\n\n请确保地址以T开头且长度为34位。"
        )
        return
    
    # 获取资源信息
    resources = get_account_resources(tron_address)
    
    if resources:
        # 计算资源使用率
        energy_usage_rate = (resources['energy_used'] / max(resources['energy_limit'], 1)) * 100
        bandwidth_usage_rate = (resources['bandwidth_used'] / max(resources['bandwidth_limit'], 1)) * 100
        
        message = f"📊 *账户资源状态*\n\n"
        message += f"*地址：* `{tron_address}`\n\n"
        message += f"⚡ *能量 (Energy)*\n"
        message += f"• 总量：{resources['energy_limit']:,}\n"
        message += f"• 已用：{resources['energy_used']:,}\n"
        message += f"• 可用：{resources['energy_available']:,}\n"
        message += f"• 使用率：{energy_usage_rate:.1f}%\n\n"
        message += f"📡 *带宽 (Bandwidth)*\n"
        message += f"• 总量：{resources['bandwidth_limit']:,}\n"
        message += f"• 已用：{resources['bandwidth_used']:,}\n"
        message += f"• 可用：{resources['bandwidth_available']:,}\n"
        message += f"• 使用率：{bandwidth_usage_rate:.1f}%\n\n"
        
        # 添加资源状态提示
        if energy_usage_rate > 90:
            message += "⚠️ 能量即将耗尽，建议质押TRX获取更多能量\n"
        if bandwidth_usage_rate > 90:
            message += "⚠️ 带宽即将耗尽，建议质押TRX获取更多带宽\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
    else:
        await update.message.reply_text(
            f"❌ 无法获取地址资源信息\n\n请检查地址是否正确或稍后重试。"
        )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/status命令 - 显示监控系统状态"""
    try:
        global websocket_running, monitored_addresses
        
        # 获取用户绑定信息
        chat_id = update.effective_chat.id
        user_bindings = get_user_bindings(chat_id)
        
        # 获取系统状态
        all_bindings = get_user_bindings()
        total_users = len(set(binding[0] for binding in all_bindings))
        total_addresses = len(get_all_monitored_addresses())
        
        message = f"📊 *监控系统状态*\n\n"
        
        # 监控模式
        if USE_WEBSOCKET:
            status_icon = "🟢" if websocket_running else "🔴"
            message += f"🚀 *监控模式*: WebSocket实时监控\n"
            message += f"{status_icon} *连接状态*: {'已连接' if websocket_running else '未连接'}\n"
            message += f"📡 *WebSocket地址*: `{WEBSOCKET_URL}`\n"
        else:
            message += f"⏰ *监控模式*: 轮询监控\n"
            message += f"⏱️ *检查间隔*: {CHECK_INTERVAL}秒\n"
        
        message += f"\n📈 *系统统计*\n"
        message += f"👥 总用户数: {total_users}\n"
        message += f"📍 监控地址数: {total_addresses}\n"
        message += f"🔄 已处理交易: {sum(len(txs) for txs in processed_txs.values())}\n"
        
        message += f"\n👤 *您的绑定*\n"
        if user_bindings:
            message += f"📍 绑定地址数: {len(user_bindings)}\n"
            for address, remark in user_bindings[:3]:  # 只显示前3个
                display_remark = f" ({remark})" if remark else ""
                message += f"• `{address[:10]}...{address[-6:]}`{display_remark}\n"
            if len(user_bindings) > 3:
                message += f"• ... 还有 {len(user_bindings) - 3} 个地址\n"
        else:
            message += f"❌ 暂无绑定地址\n"
            message += f"💡 使用 `/bind <地址> [备注]` 开始监控\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
        
    except Exception as e:
        await update.message.reply_text(f"❌ 获取状态失败: {str(e)}")
        print(f"状态查询错误: {e}")

def get_account_transactions(address, limit=20):
    """获取账户交易记录"""
    try:
        # 使用TronGrid API获取交易
        url = f"https://api.trongrid.io/v1/accounts/{address}/transactions"
        params = {
            'limit': limit,
            'order_by': 'block_timestamp,desc'
        }
        if TRON_API_KEY:
            headers = {'TRON-PRO-API-KEY': TRON_API_KEY}
        else:
            headers = {}
        
        print(f"🔍 正在获取地址 {address[:10]}...{address[-6:]} 的交易记录")
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json().get('data', [])
            print(f"📊 获取到 {len(data)} 笔交易记录")
            
            # 打印最新交易的时间信息用于调试
            if data:
                latest_tx = data[0]
                block_timestamp = latest_tx.get('blockTimeStamp', 0)
                if block_timestamp > 0:
                    import datetime
                    utc_time = datetime.datetime.utcfromtimestamp(block_timestamp / 1000)
                    beijing_time = utc_time + datetime.timedelta(hours=8)
                    print(f"⏰ 最新交易时间: {beijing_time.strftime('%Y-%m-%d %H:%M:%S')} (北京时间)")
            
            return data
        else:
            print(f"❌ 获取交易失败: HTTP {response.status_code}")
            print(f"响应内容: {response.text[:200]}...")
            return []
    except Exception as e:
        print(f"❌ 获取交易异常: {e}")
        return []

def get_address_remark(address, all_bindings):
    """获取地址对应的备注信息"""
    for chat_id, addr, remark in all_bindings:
        if addr == address:
            return remark
    return None

def get_token_info(contract_address):
    """获取代币信息"""
    if contract_address in TRC20_TOKENS:
        return TRC20_TOKENS[contract_address]
    
    # 尝试从API获取代币信息
    try:
        url = f"https://api.trongrid.io/v1/contracts/{contract_address}"
        headers = {'TRON-PRO-API-KEY': TRON_API_KEY} if TRON_API_KEY else {}
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data and len(data['data']) > 0:
                contract_info = data['data'][0]
                return {
                    'symbol': contract_info.get('symbol', 'Unknown'),
                    'decimals': contract_info.get('decimals', 18),
                    'name': contract_info.get('name', 'Unknown Token')
                }
    except Exception as e:
        print(f"获取代币信息失败: {e}")
    
    return {'symbol': 'Unknown', 'decimals': 18, 'name': 'Unknown Token'}

def parse_trc20_transfer(data):
    """解析TRC-20转账数据"""
    try:
        if not data or len(data) < 8:
            return None, None, None
        
        # 检查是否是transfer方法调用
        method_signature = data[:8]
        if method_signature != TRANSFER_METHOD_SIGNATURE:
            return None, None, None
        
        # 解析参数
        params = data[8:]
        if len(params) < 128:  # 64字符 * 2个参数
            return None, None, None
        
        # 提取to地址 (前64字符，去掉前24个0)
        to_address_hex = params[24:64]
        to_address = '41' + to_address_hex  # 添加TRON地址前缀
        
        # 提取金额 (后64字符)
        amount_hex = params[64:128]
        amount = int(amount_hex, 16)
        
        # 转换地址格式
        try:
            to_address_base58 = client.address.from_hex(to_address).base58
        except:
            to_address_base58 = 'Unknown'
        
        return to_address_base58, amount, True
    except Exception as e:
        print(f"解析TRC-20转账数据失败: {e}")
        return None, None, None

def get_account_resources(address):
    """获取账户资源信息（能量和带宽）"""
    try:
        url = f"https://api.trongrid.io/v1/accounts/{address}"
        headers = {'TRON-PRO-API-KEY': TRON_API_KEY} if TRON_API_KEY else {}
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            account_data = data.get('data', [{}])[0]
            
            # 获取能量信息
            energy_limit = account_data.get('account_resource', {}).get('energy_limit', 0)
            energy_used = account_data.get('account_resource', {}).get('energy_used', 0)
            energy_available = energy_limit - energy_used
            
            # 获取带宽信息
            net_limit = account_data.get('account_resource', {}).get('net_limit', 0)
            net_used = account_data.get('account_resource', {}).get('net_used', 0)
            net_available = net_limit - net_used
            
            return {
                'energy_limit': energy_limit,
                'energy_used': energy_used,
                'energy_available': energy_available,
                'bandwidth_limit': net_limit,
                'bandwidth_used': net_used,
                'bandwidth_available': net_available
            }
    except Exception as e:
        print(f"获取账户资源信息失败: {e}")
    
    return None

async def create_websocket_connection():
    """创建WebSocket连接"""
    global websocket_running
    try:
        # 尝试使用不同的连接方式来避免extra_headers兼容性问题
        if TRON_API_KEY:
            # 方法1: 尝试在URL中包含API Key
            url_with_key = f"{WEBSOCKET_URL}?apikey={TRON_API_KEY}"
            try:
                websocket = await websockets.connect(
                    url_with_key,
                    ping_interval=20,
                    ping_timeout=10
                )
            except Exception:
                # 方法2: 如果URL方式失败，尝试基本连接
                print("⚠️ 带API Key的连接失败，尝试基本连接...")
                websocket = await websockets.connect(
                    WEBSOCKET_URL,
                    ping_interval=20,
                    ping_timeout=10
                )
        else:
            # 无API Key的基本连接
            websocket = await websockets.connect(
                WEBSOCKET_URL,
                ping_interval=20,
                ping_timeout=10
            )
            
        websocket_running = True
        print("✅ WebSocket连接已建立")
        return websocket
    except Exception as e:
        print(f"❌ WebSocket连接失败: {e}")
        websocket_running = False
        return None

async def subscribe_to_address(websocket, address):
    """订阅地址的实时交易"""
    try:
        subscribe_message = {
            "subscribe": "transaction",
            "address": address
        }
        await websocket.send(json.dumps(subscribe_message))
        print(f"📡 已订阅地址: {address}")
        return True
    except Exception as e:
        print(f"❌ 订阅地址失败 {address}: {e}")
        return False

async def unsubscribe_from_address(websocket, address):
    """取消订阅地址"""
    try:
        unsubscribe_message = {
            "unsubscribe": "transaction",
            "address": address
        }
        await websocket.send(json.dumps(unsubscribe_message))
        print(f"📡 已取消订阅地址: {address}")
        return True
    except Exception as e:
        print(f"❌ 取消订阅失败 {address}: {e}")
        return False

async def handle_websocket_message(message_data):
    """处理WebSocket接收到的消息"""
    try:
        if 'transaction' in message_data:
            transaction = message_data['transaction']
            tx_hash = transaction.get('txID', '')
            
            # 检查是否已处理过此交易
            if tx_hash in processed_transactions:
                return
                
            # 标记为已处理
            processed_transactions.add(tx_hash)
                
            # 获取相关地址的用户
            affected_addresses = set()
            
            # 从交易中提取相关地址
            if 'raw_data' in transaction and 'contract' in transaction['raw_data']:
                for contract in transaction['raw_data']['contract']:
                    if contract['type'] == 'TransferContract':
                        param = contract['parameter']['value']
                        owner_addr = param.get('owner_address', '')
                        to_addr = param.get('to_address', '')
                        if owner_addr:
                            try:
                                affected_addresses.add(client.address.from_hex(owner_addr).base58)
                            except:
                                pass
                        if to_addr:
                            try:
                                affected_addresses.add(client.address.from_hex(to_addr).base58)
                            except:
                                pass
                    elif contract['type'] == 'TriggerSmartContract':
                        param = contract['parameter']['value']
                        owner_addr = param.get('owner_address', '')
                        if owner_addr:
                            try:
                                affected_addresses.add(client.address.from_hex(owner_addr).base58)
                            except:
                                pass
            
            # 为每个相关地址的用户发送通知
            all_bindings = get_user_bindings()
            for chat_id, address, remark in all_bindings:
                if address in affected_addresses:
                    message = format_transaction_message(transaction, address, remark)
                    if message:
                        await send_telegram_message(chat_id, message)
                            
    except Exception as e:
        print(f"❌ 处理WebSocket消息失败: {e}")

async def websocket_monitor():
    """WebSocket监控主循环"""
    global websocket_running, monitored_addresses
    
    while True:
        try:
            # 创建WebSocket连接
            websocket = await create_websocket_connection()
            if not websocket:
                print("⏳ 5秒后重试WebSocket连接...")
                await asyncio.sleep(5)
                continue
            
            # 获取当前需要监控的地址
            current_addresses = get_all_monitored_addresses()
            
            # 订阅所有地址
            for address in current_addresses:
                await subscribe_to_address(websocket, address)
                monitored_addresses.add(address)
            
            # 监听消息
            async for message in websocket:
                try:
                    message_data = json.loads(message)
                    await handle_websocket_message(message_data)
                except json.JSONDecodeError:
                    print(f"❌ 无法解析WebSocket消息: {message}")
                except Exception as e:
                    print(f"❌ 处理消息时出错: {e}")
                    
                # 检查是否有新的地址需要订阅
                new_addresses = get_all_monitored_addresses()
                addresses_to_add = new_addresses - monitored_addresses
                addresses_to_remove = monitored_addresses - new_addresses
                
                # 订阅新地址
                for address in addresses_to_add:
                    await subscribe_to_address(websocket, address)
                    monitored_addresses.add(address)
                
                # 取消订阅移除的地址
                for address in addresses_to_remove:
                    await unsubscribe_from_address(websocket, address)
                    monitored_addresses.discard(address)
                    
        except websockets.exceptions.ConnectionClosed:
            print("🔄 WebSocket连接已断开，正在重连...")
            websocket_running = False
            await asyncio.sleep(3)
        except Exception as e:
            print(f"❌ WebSocket监控出错: {e}")
            websocket_running = False
            await asyncio.sleep(5)

def get_all_monitored_addresses():
    """获取所有需要监控的地址"""
    try:
        all_bindings = get_user_bindings()
        addresses = {address for _, address, _ in all_bindings}
        return addresses
    except Exception as e:
        print(f"❌ 获取监控地址失败: {e}")
        return set()

def format_transaction_message(tx, monitor_address, remark):
    """格式化交易消息"""
    tx_hash = tx.get('txID', 'Unknown')
    block_timestamp = tx.get('blockTimeStamp', 0)
    
    # 转换为北京时间 (UTC+8) - 使用实际交易时间
    import datetime
    if block_timestamp > 0:
        utc_time = datetime.datetime.utcfromtimestamp(block_timestamp / 1000)
        beijing_time = utc_time + datetime.timedelta(hours=8)
        block_time = beijing_time.strftime('%Y-%m-%d %H:%M:%S')
    else:
        # 如果没有时间戳，使用当前时间
        beijing_time = datetime.datetime.now() + datetime.timedelta(hours=8)
        block_time = beijing_time.strftime('%Y-%m-%d %H:%M:%S')
    
    # 获取区块信息
    block_number = tx.get('blockNumber', 'Unknown')
    block_hash = tx.get('blockHash', 'Unknown')
    
    # 获取合约信息
    contract = tx.get('raw_data', {}).get('contract', [{}])[0]
    contract_type = contract.get('type', 'Unknown')
    
    # 获取所有绑定信息用于地址备注查询
    all_bindings = get_user_bindings()
    
    # 默认值
    amount = 0
    token_symbol = 'TRX'
    from_addr = 'Unknown'
    to_addr = 'Unknown'
    transaction_direction = None  # 改为None，后续如果不是入账或出账就直接返回None
    additional_info = ''
    
    # 根据交易类型解析详细信息
    if contract_type == 'TransferContract':
        # TRX转账
        parameter = contract.get('parameter', {}).get('value', {})
        from_addr_hex = parameter.get('owner_address', '')
        to_addr_hex = parameter.get('to_address', '')
        amount = parameter.get('amount', 0) / 1000000  # TRX转换
        token_symbol = 'TRX'
        
        # 转换地址格式 - 修复地址转换逻辑
        try:
            from tronpy.keys import to_base58check_address
            if from_addr_hex:
                # 确保地址是41字节的hex格式
                if len(from_addr_hex) == 42 and from_addr_hex.startswith('41'):
                    from_addr = to_base58check_address(from_addr_hex)
                elif len(from_addr_hex) == 40:
                    from_addr = to_base58check_address('41' + from_addr_hex)
                else:
                    from_addr = from_addr_hex  # 可能已经是base58格式
            
            if to_addr_hex:
                # 确保地址是41字节的hex格式
                if len(to_addr_hex) == 42 and to_addr_hex.startswith('41'):
                    to_addr = to_base58check_address(to_addr_hex)
                elif len(to_addr_hex) == 40:
                    to_addr = to_base58check_address('41' + to_addr_hex)
                else:
                    to_addr = to_addr_hex  # 可能已经是base58格式
        except Exception as e:
            print(f"地址转换错误: {e}")
            # 如果转换失败，尝试直接使用原始地址
            from_addr = from_addr_hex if from_addr_hex else 'Unknown'
            to_addr = to_addr_hex if to_addr_hex else 'Unknown'
        
        # 判断出入账类型 - 只处理与监控地址直接相关的交易
        if from_addr == monitor_address:
            transaction_direction = '出账'
        elif to_addr == monitor_address:
            transaction_direction = '入账'
        else:
            # 跳过与监控地址无关的交易
            return None
            
    elif contract_type == 'TriggerSmartContract':
        # 智能合约调用（可能是TRC-20转账）
        parameter = contract.get('parameter', {}).get('value', {})
        contract_addr_hex = parameter.get('contract_address', '')
        owner_addr_hex = parameter.get('owner_address', '')
        data = parameter.get('data', '')
        
        # 修复地址转换逻辑
        try:
            from tronpy.keys import to_base58check_address
            if owner_addr_hex:
                if len(owner_addr_hex) == 42 and owner_addr_hex.startswith('41'):
                    from_addr = to_base58check_address(owner_addr_hex)
                elif len(owner_addr_hex) == 40:
                    from_addr = to_base58check_address('41' + owner_addr_hex)
                else:
                    from_addr = owner_addr_hex
            
            if contract_addr_hex:
                if len(contract_addr_hex) == 42 and contract_addr_hex.startswith('41'):
                    contract_addr = to_base58check_address(contract_addr_hex)
                elif len(contract_addr_hex) == 40:
                    contract_addr = to_base58check_address('41' + contract_addr_hex)
                else:
                    contract_addr = contract_addr_hex
        except Exception as e:
            print(f"合约地址转换错误: {e}")
            from_addr = owner_addr_hex if owner_addr_hex else 'Unknown'
            contract_addr = contract_addr_hex if contract_addr_hex else 'Unknown'
        
        # 尝试解析TRC-20转账
        trc20_to_addr, trc20_amount, is_trc20 = parse_trc20_transfer(data)
        
        if is_trc20 and trc20_to_addr and trc20_amount:
            # 这是TRC-20代币转账
            to_addr = trc20_to_addr
            token_info = get_token_info(contract_addr)
            token_symbol = token_info['symbol']
            decimals = token_info['decimals']
            amount = trc20_amount / (10 ** decimals)
            
            # 判断出入账类型 - 只处理与监控地址直接相关的交易
            if from_addr == monitor_address:
                transaction_direction = '出账'
            elif to_addr == monitor_address:
                transaction_direction = '入账'
            else:
                # 跳过与监控地址无关的交易
                return None
            
            additional_info = f"\n\n代币合约：{contract_addr}"
        else:
            # 普通智能合约调用 - 跳过非转账合约交易
            return None
    
    elif contract_type == 'FreezeBalanceContract':
        # 质押能量/带宽 - 跳过通知
        return None
    
    elif contract_type == 'UnfreezeBalanceContract':
        # 解质押能量/带宽 - 跳过通知
        return None
    
    elif contract_type == 'VoteWitnessContract':
        # 投票 - 跳过投票交易
        return None
    
    elif contract_type == 'TransferAssetContract':
        # TRC-10代币转账
        parameter = contract.get('parameter', {}).get('value', {})
        from_addr_hex = parameter.get('owner_address', '')
        to_addr_hex = parameter.get('to_address', '')
        asset_name = parameter.get('asset_name', 'Unknown')
        amount_raw = parameter.get('amount', 0)
        
        # 修复地址转换逻辑
        try:
            from tronpy.keys import to_base58check_address
            if from_addr_hex:
                if len(from_addr_hex) == 42 and from_addr_hex.startswith('41'):
                    from_addr = to_base58check_address(from_addr_hex)
                elif len(from_addr_hex) == 40:
                    from_addr = to_base58check_address('41' + from_addr_hex)
                else:
                    from_addr = from_addr_hex
            
            if to_addr_hex:
                if len(to_addr_hex) == 42 and to_addr_hex.startswith('41'):
                    to_addr = to_base58check_address(to_addr_hex)
                elif len(to_addr_hex) == 40:
                    to_addr = to_base58check_address('41' + to_addr_hex)
                else:
                    to_addr = to_addr_hex
            
            # 解析代币名称
            try:
                token_symbol = bytes.fromhex(asset_name).decode('utf-8') if asset_name != 'Unknown' else 'TRC10'
            except:
                token_symbol = 'TRC10'
        except Exception as e:
            print(f"TRC10地址转换错误: {e}")
            from_addr = from_addr_hex if from_addr_hex else 'Unknown'
            to_addr = to_addr_hex if to_addr_hex else 'Unknown'
            token_symbol = 'TRC10'
        
        amount = amount_raw  # TRC-10代币通常没有小数位
        
        # 判断出入账类型 - 只处理与监控地址直接相关的交易
        if from_addr == monitor_address:
            transaction_direction = '出账'
        elif to_addr == monitor_address:
            transaction_direction = '入账'
        else:
            # 跳过与监控地址无关的交易
            return None
    
    # 使用实际TRON地址显示
    from_addr_display = from_addr
    to_addr_display = to_addr
    
    # 如果交易方向不是入账或出账，直接跳过
    if transaction_direction not in ['入账', '出账']:
        return None
    
    # 调试信息：打印地址转换结果
    print(f"🔍 交易地址信息: from={from_addr} to={to_addr} monitor={monitor_address} direction={transaction_direction}")
    
    # 获取账户资源信息（如果是监控地址）
    resource_info = ''
    if monitor_address in [from_addr, to_addr]:
        resources = get_account_resources(monitor_address)
        if resources:
            resource_info = f"\n\n📊 账户资源状态：\n"
            resource_info += f"⚡ 能量：{resources['energy_available']:,}/{resources['energy_limit']:,}\n"
            resource_info += f"📡 带宽：{resources['bandwidth_available']:,}/{resources['bandwidth_limit']:,}"
    
    # 构建新格式消息 - 只保留出入账类型
    direction_emoji = {
        '入账': '📈',
        '出账': '📉'
    }
    
    emoji = direction_emoji.get(transaction_direction, '🔄')
    
    # 根据交易方向调整标题
    if transaction_direction == '入账':
        title = f"💰 收到转账！{remark}"
    elif transaction_direction == '出账':
        title = f"💸 发出转账！{remark}"
    else:
        title = f"🔔 新交易！{remark}"
    
    message = f"{title}\n\n"
    message += f"交易类型：{emoji} {transaction_direction}\n\n"
    message += f"交易币种：💎 {token_symbol}\n"
    
    # 在交易币种下方显示交易金额
    if amount > 0:
        message += f"交易金额：💰 {amount:.6f} {token_symbol}\n\n"
    else:
        message += "\n"
    
    message += f"发送方：👤 {from_addr_display}\n\n"
    message += f"接收方：👤 {to_addr_display}\n\n"
    message += f"交易时间：⏰ {block_time}\n\n"
    message += f"交易哈希：🔗 {tx_hash}\n\n"
    message += f"区块高度：📦 {block_number}"
    
    # 添加额外信息
    if additional_info:
        message += additional_info
    
    # 添加资源信息
    if resource_info:
        message += resource_info
    
    return message

async def monitor_single_address(address, chat_users):
    """监控单个地址的交易"""
    try:
        # 获取最新交易
        transactions = get_account_transactions(address)
        
        if not transactions:
            print(f"⚠️ 地址 {address[:10]}...{address[-6:]} 暂无交易数据")
            return
        
        # 检查新交易
        new_transactions = []
        for tx in transactions:
            tx_hash = tx.get('txID')
            if tx_hash and tx_hash not in processed_txs.get(address, set()):
                new_transactions.append(tx)
                if address not in processed_txs:
                    processed_txs[address] = set()
                processed_txs[address].add(tx_hash)
                
                # 打印新交易的基本信息
                contract_type = tx.get('raw_data', {}).get('contract', [{}])[0].get('type', 'Unknown')
                block_timestamp = tx.get('blockTimeStamp', 0)
                if block_timestamp > 0:
                    import datetime
                    utc_time = datetime.datetime.utcfromtimestamp(block_timestamp / 1000)
                    beijing_time = utc_time + datetime.timedelta(hours=8)
                    time_str = beijing_time.strftime('%H:%M:%S')
                else:
                    time_str = '未知时间'
                
                print(f"🆕 发现新交易: {tx_hash[:10]}... 类型: {contract_type} 时间: {time_str}")
        
        # 发送新交易通知给所有绑定该地址的用户
        for tx in reversed(new_transactions):  # 按时间顺序发送
            for chat_id, remark in chat_users:
                try:
                    message = format_transaction_message(tx, address, remark)
                    if message:  # 只发送与监控地址相关的交易
                        await send_telegram_message(chat_id, message)
                        print(f"✅ 已发送交易通知给用户 {chat_id}")
                    else:
                        print(f"ℹ️ 跳过与监控地址无关的交易")
                except Exception as msg_error:
                    print(f"❌ 发送消息失败 (用户 {chat_id}): {msg_error}")
                await asyncio.sleep(0.5)  # 避免发送过快
        
        if new_transactions:
            print(f"✅ 地址 {address[:10]}...{address[-6:]} 处理了 {len(new_transactions)} 笔新交易")
        else:
            print(f"ℹ️ 地址 {address[:10]}...{address[-6:]} 无新交易")
            
    except Exception as e:
        print(f"❌ 监控地址 {address[:10]}...{address[-6:]} 时出现错误: {e}")
        # 发送错误通知给绑定该地址的用户
        error_msg = f"⚠️ 监控地址异常\n\n地址: {address[:10]}...{address[-6:]}\n错误: {str(e)}"
        for chat_id, remark in chat_users:
            try:
                await send_telegram_message(chat_id, error_msg)
            except:
                pass  # 忽略发送错误通知时的异常

async def monitor_transactions():
    """监控所有绑定的交易"""
    print("开始监控TRON交易...")
    print(f"检查间隔: {CHECK_INTERVAL}秒")
    
    while True:
        try:
            # 获取所有用户绑定
            all_bindings = get_user_bindings()
            
            if not all_bindings:
                print("暂无绑定地址，等待用户绑定...")
                await asyncio.sleep(CHECK_INTERVAL)
                continue
            
            # 按地址分组用户
            address_users = {}
            for chat_id, address, remark in all_bindings:
                if address not in address_users:
                    address_users[address] = []
                address_users[address].append((chat_id, remark))
            
            print(f"当前监控 {len(address_users)} 个地址，共 {len(all_bindings)} 个绑定")
            
            # 并发监控所有地址
            tasks = []
            for address, users in address_users.items():
                task = monitor_single_address(address, users)
                tasks.append(task)
            
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            
        except Exception as e:
            print(f"监控过程中出现全局错误: {e}")
        
        # 等待下次检查
        await asyncio.sleep(CHECK_INTERVAL)

async def start_bot_and_monitor():
    """启动Bot和监控服务"""
    global bot_instance
    
    # 创建Bot应用
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    bot_instance = app.bot
    
    # 添加命令处理器
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("bind", bind_command))
    app.add_handler(CommandHandler("list", list_command))
    app.add_handler(CommandHandler("unbind", unbind_command))
    app.add_handler(CommandHandler("resources", resources_command))
    app.add_handler(CommandHandler("status", status_command))
    
    print("🤖 Telegram Bot已启动")
    
    # 根据配置选择监控模式
    if USE_WEBSOCKET:
        print("⚠️ WebSocket模式已配置，但TronGrid不提供WebSocket服务")
        print("🔄 自动切换到轮询监控模式")
        print(f"📊 监控间隔: {CHECK_INTERVAL}秒")
        monitor_task = asyncio.create_task(monitor_transactions())
    else:
        print("⏰ 使用轮询监控模式")
        print(f"📊 监控间隔: {CHECK_INTERVAL}秒")
        monitor_task = asyncio.create_task(monitor_transactions())
    
    print("📡 开始监控TRON地址...")
    
    # 启动Bot
    try:
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        
        # 等待监控任务
        await monitor_task
        
    except KeyboardInterrupt:
        print("\n🛑 正在停止监控...")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        monitor_task.cancel()

def main():
    """主函数"""
    # 检查必要的环境变量
    if not TELEGRAM_BOT_TOKEN:
        print("错误: 未设置TELEGRAM_BOT_TOKEN")
        print("请在.env文件中设置TELEGRAM_BOT_TOKEN")
        return
    
    print("TRON交易监控服务启动中...")
    print(f"数据库路径: {DB_PATH}")
    print(f"检查间隔: {CHECK_INTERVAL}秒")
    
    # 初始化数据库
    init_database()
    print("数据库初始化完成")
    
    # 启动服务
    try:
        asyncio.run(start_bot_and_monitor())
    except KeyboardInterrupt:
        print("\n服务已停止")

if __name__ == '__main__':
    main()