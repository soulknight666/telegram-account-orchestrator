import requests
import logging
import os
import json
import time
import os as _os_for_path

def _data_path(name):
    base = _os_for_path.getenv("TAM_DATA_DIR") or "."
    try:
        _os_for_path.makedirs(base, exist_ok=True)
    except Exception:
        base = "."
    return _os_for_path.path.join(base, name)

from .okpay_sign import signed_request

ORDER_TIMEOUT = 300


def load_all_orders():
    file_path = _data_path('orders.json')
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}


def save_all_orders(data):
    file_path = _data_path('orders.json')
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def add_order(order_id, user_id, chat_id, created_time):
    orders = load_all_orders()
    orders[order_id] = {
        'user_id': user_id,
        'chat_id': chat_id,
        'created_time': created_time,
        'status': 'pending'
    }
    save_all_orders(orders)
    logging.info(f"订单添加: {order_id}, 用户: {user_id}")


def remove_order(order_id):
    orders = load_all_orders()
    if order_id in orders:
        del orders[order_id]
        save_all_orders(orders)
        logging.info(f"订单删除: {order_id}")
        return True
    return False


def get_order(order_id):
    orders = load_all_orders()
    return orders.get(order_id)


def cleanup_expired_orders():
    orders = load_all_orders()
    current_time = time.time()
    expired_orders = []

    for order_id, order_data in orders.items():
        if current_time - order_data['created_time'] > ORDER_TIMEOUT:
            expired_orders.append(order_id)

    for order_id in expired_orders:
        del orders[order_id]

    if expired_orders:
        save_all_orders(orders)
        logging.info(f"清理了 {len(expired_orders)} 个过期订单")
    return expired_orders


class OkayPay:
    def __init__(self, shop_id, token):
        self.id = str(shop_id).strip()
        self.token = str(token).strip()
        self.base_url = 'https://api.okaypay.me/shop/'

    def sign(self, data):
        #自签
        return signed_request(data, self.id, self.token)

    @staticmethod
    def _response_data(result):
        #data
        if not isinstance(result, dict):
            return {}
        data = result.get('data', {})
        if isinstance(data, list) and data:
            return data[0] if isinstance(data[0], dict) else {}
        return data if isinstance(data, dict) else {}

    def get_pay_link(self, unique_id, amount, coin, name):
        url = self.base_url + 'payLink'
        params = {
            'unique_id': unique_id,
            'name': name,
            'amount': str(amount),
            'coin': coin,
            'return_url': os.getenv('OKPAY_RETURN_URL', 'https://t.me/')
        }
        signed_data = self.sign(params)

        try:
            response = requests.post(
                url,
                data=signed_data,
                headers={'User-Agent': 'HTTP CLIENT'},
                timeout=10,
                verify=False
            )
            result = response.json()
            logging.info(f"OKPay 创建支付链接结果: {result}")
            data = self._response_data(result)

            if result.get('code') == 200 and data:
                return data.get('pay_url'), data.get('order_id')

            logging.error(f"OKPay API Error: {result}")
            return None, None
        except Exception as e:
            logging.error(f"OKPay Request Failed: {e}")
            return None, None

    def check_order(self, order_id):
        url = self.base_url + 'checkTransferByTxid'
        params = {
            'txid': order_id
        }
        signed_data = self.sign(params)

        try:
            response = requests.post(
                url,
                data=signed_data,
                timeout=5,
                verify=False,
                proxies={'http': None, 'https': None}
            )
            result = response.json()
            logging.info(f"轮询订单 {order_id} 结果: {result}")
            data = self._response_data(result)

            if result.get('code') == 200 and data:
                return str(data.get('status')) == '1'
            return False
        except Exception as e:
            logging.error(f"查询订单异常: {e}")
            return False


def load_all_users():
    file_path = _data_path('users.json')
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}


def save_all_users(data):
    file_path = _data_path('users.json')
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
