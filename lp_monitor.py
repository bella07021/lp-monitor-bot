# lp_monitor.py
import requests
import json
import os
from datetime import datetime
import hashlib
import subprocess
import time

class LPMonitor:
    def __init__(self):
        # 直接从环境变量读取所有配置
        self.dune_api_key = os.getenv('DUNE_API_KEY')
        self.tg_bot_token = os.getenv('TG_BOT_TOKEN')
        self.tg_chat_id = os.getenv('TG_CHAT_ID')
        self.dune_query_id = os.getenv('DUNE_QUERY_ID')
        
        # 验证必要的环境变量
        self._validate_config()
        
        # 文件路径配置
        self.data_dir = 'lp_data'
        self.data_file = f'{self.data_dir}/latest_positions.json'
        self.history_file = f'{self.data_dir}/history.json'
        
    def _validate_config(self):
        """验证必要的配置是否存在"""
        required_vars = {
            'DUNE_API_KEY': self.dune_api_key,
            'TG_BOT_TOKEN': self.tg_bot_token, 
            'TG_CHAT_ID': self.tg_chat_id,
            'DUNE_QUERY_ID': self.dune_query_id
        }
        
        missing_vars = [var for var, value in required_vars.items() if not value]
        if missing_vars:
            raise Exception(f"缺少必要的环境变量: {', '.join(missing_vars)}")
    
    def execute_dune_query(self):
        """执行Dune查询获取LP头寸数据"""
        execute_url = f"https://api.dune.com/api/v1/query/{self.dune_query_id}/execute"
        headers = {
            "X-Dune-API-Key": self.dune_api_key,
            "Content-Type": "application/json"
        }
        
        print(f"执行Dune查询: {self.dune_query_id}")
        try:
            response = requests.post(execute_url, headers=headers, timeout=30)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"Dune API请求失败: {e}")
            return None
            
        execution_id = response.json()['execution_id']
        print(f"执行ID: {execution_id}")
        
        # 等待查询完成
        status_url = f"https://api.dune.com/api/v1/execution/{execution_id}/status"
        for i in range(30):  # 最多等待5分钟
            try:
                status_response = requests.get(status_url, headers=headers, timeout=30)
                status_response.raise_for_status()
                status = status_response.json()['state']
                print(f"查询状态: {status}")
                
                if status == 'QUERY_STATE_COMPLETED':
                    break
                elif status in ['QUERY_STATE_FAILED', 'QUERY_STATE_CANCELLED']:
                    print(f"查询失败: {status}")
                    return None
                    
            except requests.exceptions.RequestException as e:
                print(f"状态检查失败: {e}")
                return None
                
            time.sleep(10)
        else:
            print("查询超时")
            return None
        
        # 获取结果
        results_url = f"https://api.dune.com/api/v1/execution/{execution_id}/results"
        try:
            results_response = requests.get(results_url, headers=headers, timeout=30)
            results_response.raise_for_status()
            return results_response.json()['result']['rows']
        except requests.exceptions.RequestException as e:
            print(f"获取结果失败: {e}")
            return None
    
    def load_previous_data(self):
        """加载之前的数据"""
        try:
            with open(self.data_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {'positions': [], 'timestamp': None}
    
    def save_current_data(self, data):
        """保存当前数据"""
        os.makedirs(self.data_dir, exist_ok=True)
        with open(self.data_file, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        # 同时保存历史记录
        history_data = {}
        try:
            with open(self.history_file, 'r') as f:
                history_data = json.load(f)
        except FileNotFoundError:
            pass
            
        timestamp = datetime.now().isoformat()
        history_data[timestamp] = data
        
        with open(self.history_file, 'w') as f:
            json.dump(history_data, f, indent=2, ensure_ascii=False)
    
    def calculate_position_hash(self, position):
        """计算头寸的哈希值用于比较"""
        key_fields = [
            str(position.get('tokenId', '')),
            str(position.get('liquidity_L', '')),
            str(position.get('amount0', '')),
            str(position.get('amount1', '')),
            str(position.get('usd_value', ''))
        ]
        position_str = '-'.join(key_fields)
        return hashlib.md5(position_str.encode()).hexdigest()
    
    def compare_positions(self, old_positions, new_positions):
        """比较新旧数据，找出变动"""
        changes = {
            'added': [],
            'removed': [],
            'modified': [],
            'timestamp': datetime.now().isoformat()
        }
        
        old_positions_dict = {str(p['tokenId']): p for p in old_positions}
        new_positions_dict = {str(p['tokenId']): p for p in new_positions}
        
        # 找出新增的头寸
        for token_id in set(new_positions_dict.keys()) - set(old_positions_dict.keys()):
            changes['added'].append(new_positions_dict[token_id])
        
        # 找出移除的头寸
        for token_id in set(old_positions_dict.keys()) - set(new_positions_dict.keys()):
            changes['removed'].append(old_positions_dict[token_id])
        
        # 找出修改的头寸
        for token_id in set(old_positions_dict.keys()) & set(new_positions_dict.keys()):
            old_hash = self.calculate_position_hash(old_positions_dict[token_id])
            new_hash = self.calculate_position_hash(new_positions_dict[token_id])
            if old_hash != new_hash:
                changes['modified'].append({
                    'old': old_positions_dict[token_id],
                    'new': new_positions_dict[token_id]
                })
        
        return changes
    
    def format_position_display(self, position):
        """格式化单个头寸的显示"""
        token_id = position.get('tokenId', '')
        usd_value = float(position.get('usd_value', 0))
        p_lower = position.get('p_lower_uset', 0)
        p_upper = position.get('p_upper_uset', 0)
        status = position.get('status', 'UNKNOWN')
        
        # 处理科学计数法显示
        def format_price(price):
            if isinstance(price, str) and '+' in price:
                return '∞'
            try:
                price_float = float(price)
                if price_float > 1000000:  # 太大的数用科学计数法
                    return f"{price_float:.2e}"
                else:
                    return f"{price_float:.4f}"
            except:
                return str(price)
        
        p_lower_str = format_price(p_lower)
        p_upper_str = format_price(p_upper)
        
        status_emoji = "🟢" if status == 'ACTIVE' else "🟡"
        status_text = "ACTIVE" if status == 'ACTIVE' else "OUT_OF_RANGE"
        
        return f"""  • NFT#{token_id}
    💰 总价值: ${usd_value:,.2f}
    📈 价格区间: {p_lower_str} - {p_upper_str} USDT
    🎯 状态: {status_emoji} {status_text}"""
    
    def format_change_message(self, changes, current_positions):
        """格式化变动信息用于TG推送"""
        message = "🔔 LP头寸变动警报\n"
        message += f"⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        # 变动摘要
        if changes['added']:
            message += f"🆕 新增头寸: {len(changes['added'])}个\n"
            for pos in changes['added'][:3]:
                usd_value = float(pos.get('usd_value', 0))
                message += f"  • NFT#{pos['tokenId']} - ${usd_value:,.2f}\n"
            if len(changes['added']) > 3:
                message += f"  ... 还有{len(changes['added'])-3}个\n"
            message += "\n"
        
        if changes['removed']:
            message += f"❌ 移除头寸: {len(changes['removed'])}个\n"
            for pos in changes['removed'][:3]:
                usd_value = float(pos.get('usd_value', 0))
                message += f"  • NFT#{pos['tokenId']} - ${usd_value:,.2f}\n"
            if len(changes['removed']) > 3:
                message += f"  ... 还有{len(changes['removed'])-3}个\n"
            message += "\n"
        
        if changes['modified']:
            message += f"📝 修改头寸: {len(changes['modified'])}个\n"
            for mod in changes['modified'][:2]:
                old_pos = mod['old']
                new_pos = mod['new']
                old_value = float(old_pos.get('usd_value', 0))
                new_value = float(new_pos.get('usd_value', 0))
                message += f"  • NFT#{old_pos['tokenId']}\n"
                message += f"    价值: ${old_value:,.2f} → ${new_value:,.2f}\n"
            if len(changes['modified']) > 2:
                message += f"  ... 还有{len(changes['modified'])-2}个\n"
            message += "\n"
        
        # 当前池子状态 - 按价格区间排序展示前5个
        message += "📊 当前状态:\n\n"
        
        if current_positions:
            # 排序逻辑：按价格上限从高到低
            def get_sort_key(pos):
                upper_price = pos.get('p_upper_uset', 0)
                try:
                    if isinstance(upper_price, str) and '+' in upper_price:
                        return float('inf')  # 科学计数法排在最前面
                    return float(upper_price)
                except:
                    return 0
            
            sorted_positions = sorted(current_positions, key=get_sort_key, reverse=True)
            
            # 只显示前5个
            for i, pos in enumerate(sorted_positions[:5]):
                message += self.format_position_display(pos)
                if i < min(4, len(sorted_positions) - 1):
                    message += "\n\n"
            
            # 统计信息
            total_positions = len(current_positions)
            active_positions = len([p for p in current_positions if p.get('status') == 'ACTIVE'])
            total_value = sum(float(p.get('usd_value', 0)) for p in current_positions)
            
            message += f"\n\n📈 统计: {total_positions}个头寸, {active_positions}个活跃, 总价值: ${total_value:,.2f}"
            
            if total_positions > 5:
                message += f"\n... 还有 {total_positions - 5} 个头寸未显示"
        
        return message
    
    def send_telegram_message(self, message):
        """发送TG消息"""
        try:
            from telegram import Bot
            from telegram.error import TelegramError
        except ImportError:
            print("python-telegram-bot未安装，跳过TG发送")
            return False
        
        try:
            bot = Bot(token=self.tg_bot_token)
            
            # 如果消息太长，分割发送
            if len(message) > 4000:
                parts = [message[i:i+4000] for i in range(0, len(message), 4000)]
                for part in parts:
                    bot.send_message(chat_id=self.tg_chat_id, text=part)
                    time.sleep(1)
            else:
                bot.send_message(chat_id=self.tg_chat_id, text=message)
            
            print("TG消息发送成功")
            return True
        except Exception as e:
            print(f"TG消息发送失败: {e}")
            return False
    
    def commit_and_push_changes(self):
        """提交更改到GitHub"""
        try:
            subprocess.run(['git', 'config', '--global', 'user.email', 'actions@github.com'], check=True)
            subprocess.run(['git', 'config', '--global', 'user.name', 'GitHub Actions'], check=True)
            
            subprocess.run(['git', 'add', '.'], check=True)
            commit_result = subprocess.run(['git', 'commit', '-m', f'LP数据更新 {datetime.now().isoformat()}'], 
                         capture_output=True, text=True)
            
            if commit_result.returncode == 0:
                subprocess.run(['git', 'push'], check=True)
                print("数据已提交到GitHub")
            else:
                print("没有变化需要提交")
                
        except subprocess.CalledProcessError as e:
            print(f"Git操作失败: {e}")
    
    def monitor(self):
        """执行监控"""
        print("开始LP头寸监控...")
        
        # 加载之前的数据
        old_data = self.load_previous_data()
        old_positions = old_data.get('positions', [])
        
        # 获取最新数据
        new_positions = self.execute_dune_query()
        if new_positions is None:
            print("获取Dune数据失败，退出")
            return
        
        print(f"获取到 {len(new_positions)} 个头寸数据")
        
        # 比较变动
        changes = self.compare_positions(old_positions, new_positions)
        
        # 保存新数据
        new_data = {
            'positions': new_positions,
            'timestamp': datetime.now().isoformat(),
            'total_count': len(new_positions),
            'total_value': sum(float(p.get('usd_value', 0)) for p in new_positions)
        }
        self.save_current_data(new_data)
        
        # 如果有变动，发送通知
        has_changes = any([changes['added'], changes['removed'], changes['modified']])
        
        if has_changes:
            message = self.format_change_message(changes, new_positions)
            if message:
                success = self.send_telegram_message(message)
                if success:
                    print("检测到变动，已发送TG通知")
                else:
                    print("检测到变动，但TG发送失败")
        else:
            print("未检测到变动")
        
        # 提交到GitHub
        try:
            self.commit_and_push_changes()
        except Exception as e:
            print(f"Git提交失败: {e}")
        
        return changes

def main():
    try:
        monitor = LPMonitor()
        changes = monitor.monitor()
        
        # 输出摘要
        print(f"\n监控完成:")
        print(f"新增头寸: {len(changes['added'])}")
        print(f"移除头寸: {len(changes['removed'])}")
        print(f"修改头寸: {len(changes['modified'])}")
        
    except Exception as e:
        print(f"监控执行失败: {e}")
        raise

if __name__ == "__main__":
    main()
