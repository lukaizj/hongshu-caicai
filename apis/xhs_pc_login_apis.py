import json
import time
import random
import uuid

import requests
import qrcode
from loguru import logger

from apis.xhs_pc_apis import XHS_Apis
from xhs_utils.http_util import REQUEST_TIMEOUT
from xhs_utils.xhs_util import generate_headers, generate_xs_xs_common, splice_str
from xhs_utils.common_util import generate_a1, generate_web_id


class XHSLoginApi:
    def __init__(self):
        self.base_url = "https://edith.xiaohongshu.com"
        self.as_url = "https://as.xiaohongshu.com"
        self.home_url = 'https://www.xiaohongshu.com/explore'

    @staticmethod
    def _get_sec_headers():
        return {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36',
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9',
            'content-type': 'application/json;charset=UTF-8',
            'sec-ch-ua': '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'origin': 'https://www.xiaohongshu.com',
            'referer': 'https://www.xiaohongshu.com/',
        }

    def _fetch_sec_cookies(self, cookies):
        api = '/api/sec/v1/scripting'
        data = {"callFrom": "web", "callback": "", "type": "ds", "appId": "xhs-pc-web"}

        xs, xt, xs_common = generate_xs_xs_common(cookies['a1'], api, data)
        headers = self._get_sec_headers()
        headers['x-s'] = xs
        headers['x-t'] = str(xt)
        headers['x-s-common'] = xs_common

        data_str = json.dumps(data, separators=(',', ':'), ensure_ascii=False)
        try:
            resp = requests.post(
                self.as_url + api,
                headers=headers, cookies=cookies,
                data=data_str.encode('utf-8'),
                timeout=REQUEST_TIMEOUT
            )
            res = self._parse_response_json(resp)
            return res.get('data', {}).get('secPoisonId')
        except Exception as e:
            logger.debug(f'fetch sec_poison_id failed: {e}')
            return None

    @staticmethod
    def _find_session_value(value):
        if isinstance(value, dict):
            for key in ('web_session', 'session', 'sessionId', 'session_id', 'token'):
                if value.get(key):
                    return value[key]
            for nested in value.values():
                session = XHSLoginApi._find_session_value(nested)
                if session:
                    return session
        if isinstance(value, list):
            for item in value:
                session = XHSLoginApi._find_session_value(item)
                if session:
                    return session
        return None

    @staticmethod
    def _describe_json_shape(value, depth=0):
        if depth >= 3:
            return type(value).__name__
        if isinstance(value, dict):
            return {key: XHSLoginApi._describe_json_shape(nested, depth + 1) for key, nested in value.items()}
        if isinstance(value, list):
            return [XHSLoginApi._describe_json_shape(value[0], depth + 1)] if value else []
        return type(value).__name__

    @staticmethod
    def _parse_response_json(resp):
        try:
            return resp.json()
        except Exception:
            text = resp.text.strip()
            decoder = json.JSONDecoder()
            for index, char in enumerate(text):
                if char != '{':
                    continue
                try:
                    value, _ = decoder.raw_decode(text[index:])
                    if isinstance(value, dict):
                        return value
                except Exception:
                    continue
            raise

    def _fetch_gid(self, cookies):
        api = '/api/sec/v1/shield/webprofile'
        data = {"platform": "Windows", "sdkVersion": "4.3.5", "svn": "2", "profileData": ""}

        xs, xt, xs_common = generate_xs_xs_common(cookies['a1'], api, data)
        headers = self._get_sec_headers()
        headers['x-s'] = xs
        headers['x-t'] = str(xt)
        headers['x-s-common'] = xs_common

        data_str = json.dumps(data, separators=(',', ':'), ensure_ascii=False)
        try:
            resp = requests.post(
                self.as_url + api,
                headers=headers, cookies=cookies,
                data=data_str.encode('utf-8'),
                timeout=REQUEST_TIMEOUT
            )
            for key, value in resp.cookies.items():
                cookies[key] = value
            return cookies.get('gid')
        except Exception as e:
            logger.debug(f'fetch gid failed: {e}')
            return None

    def generate_init_cookies(self):
        ts = int(time.time() * 1000)
        a1 = generate_a1()
        web_id = generate_web_id(a1)
        cookies = {
            'abRequestId': str(uuid.uuid4()),
            'ets': str(ts),
            'webBuild': '6.7.4',
            'xsecappid': 'xhs-pc-web',
            'loadts': str(ts + random.randint(50, 200)),
            'a1': a1,
            'webId': web_id,
        }

        sec_poison_id = self._fetch_sec_cookies(cookies)
        if sec_poison_id:
            cookies['sec_poison_id'] = sec_poison_id

        gid = self._fetch_gid(cookies)
        if gid:
            cookies['gid'] = gid

        return cookies

    def generate_qrcode(self, cookies):
        api = '/api/sns/web/v1/login/qrcode/create'
        data = {"qr_type": 1}

        headers, data = generate_headers(cookies['a1'], api, data)
        resp = requests.post(
            self.base_url + api,
            headers=headers, cookies=cookies, data=data,
            timeout=REQUEST_TIMEOUT
        )
        for key, value in resp.cookies.items():
            cookies[key] = value

        res = self._parse_response_json(resp)
        if not res.get('success'):
            return False, res.get('msg', '未知错误'), None
        data = res.get('data') or {}
        if not all(key in data for key in ('qr_id', 'code', 'url')):
            return False, res.get('msg', '二维码响应缺少必要字段'), {'cookies': cookies, 'res_json': res}

        return True, '成功', {
            'cookies': cookies,
            'qr_id': data['qr_id'],
            'code': data['code'],
            'qr_url': data['url'],
        }

    def check_qrcode_status(self, qr_id, code, cookies):
        api = '/api/qrcode/userinfo'
        data = {"qrId": qr_id, "code": code}

        headers, data = generate_headers(cookies['a1'], api, data)
        resp = requests.post(
            self.base_url + api,
            headers=headers, cookies=cookies, data=data,
            timeout=REQUEST_TIMEOUT
        )
        for key, value in resp.cookies.items():
            cookies[key] = value

        res = self._parse_response_json(resp)
        status = (res.get('data') or {}).get('codeStatus')
        if status is None:
            return False, res.get('msg', '二维码状态响应缺少 codeStatus'), cookies

        if status == 2:
            session = self._find_session_value(res)
            if session:
                cookies['web_session'] = session
            if not cookies.get('web_session'):
                cookies = self._login_by_qrcode_status(qr_id, code, cookies)

        status_map = {
            0: (False, '请扫描二维码'),
            1: (False, '请确认登录'),
            2: (bool(cookies.get('web_session')), '验证成功' if cookies.get('web_session') else '等待登录 Cookie 下发'),
            3: (False, '二维码已过期'),
        }
        success, msg = status_map.get(status, (False, f'未知状态: {status}'))
        return success, msg, cookies

    def _login_by_qrcode_status(self, qr_id, code, cookies):
        api = '/api/sns/web/v1/login/qrcode/status'
        attempts = [
            ('GET', {"qr_id": qr_id, "code": code}, None),
            ('GET', {"qrId": qr_id, "code": code}, None),
            ('POST', None, {"qr_id": qr_id, "code": code}),
            ('POST', None, {"qrId": qr_id, "code": code}),
        ]

        for method, params, body in attempts:
            request_cookies = dict(cookies)
            if method == 'GET':
                splice_api = splice_str(api, params)
                headers, _ = generate_headers(request_cookies['a1'], splice_api, method='GET')
                resp = requests.get(
                    self.base_url + splice_api,
                    headers=headers, cookies=request_cookies,
                    timeout=REQUEST_TIMEOUT
                )
            else:
                headers, data = generate_headers(request_cookies['a1'], api, body)
                resp = requests.post(
                    self.base_url + api,
                    headers=headers, cookies=request_cookies, data=data,
                    timeout=REQUEST_TIMEOUT
                )

            for key, value in resp.cookies.items():
                request_cookies[key] = value

            try:
                res = self._parse_response_json(resp)
            except Exception as exc:
                logger.info(f'QR login {method} cookie keys: {sorted(request_cookies.keys())}')
                logger.warning(f'QR login {method} returned non-JSON response: {exc}; prefix={resp.text[:80]!r}')
                cookies.update(request_cookies)
                if request_cookies.get('web_session'):
                    cookies['web_session'] = request_cookies['web_session']
                    return cookies
                continue

            logger.info(f'QR login {method} cookie keys: {sorted(request_cookies.keys())}')
            logger.info(f'QR login {method} response shape: {self._describe_json_shape(res)}')
            session = request_cookies.get('web_session') or self._find_session_value(res)
            cookies.update(request_cookies)
            if session:
                cookies['web_session'] = session
                return cookies

        logger.warning('QR login responses did not contain session-like field')
        return cookies

    def get_user_info(self, cookies):
        api = '/api/sns/web/v2/user/me'

        headers, _ = generate_headers(cookies['a1'], api)
        resp = requests.get(
            self.base_url + api,
            headers=headers, cookies=cookies,
            timeout=REQUEST_TIMEOUT
        )
        for key, value in resp.cookies.items():
            cookies[key] = value

        res = self._parse_response_json(resp)
        return res.get('success', False), res.get('data', {}), cookies

    def send_phone_code(self, phone, cookies, zone='86'):
        api = '/api/sns/web/v2/login/send_code'
        params = {"phone": phone, "zone": zone, "type": "login"}
        splice_api = splice_str(api, params)

        headers, _ = generate_headers(cookies['a1'], splice_api)
        resp = requests.get(
            self.base_url + splice_api,
            headers=headers, cookies=cookies,
            timeout=REQUEST_TIMEOUT
        )
        res = self._parse_response_json(resp)
        return res.get('success', False), res.get('msg', ''), res

    def login_by_phone(self, phone, code, cookies, zone='86'):
        check_api = '/api/sns/web/v1/login/check_code'
        params = {"phone": phone, "zone": zone, "code": code}
        splice_api = splice_str(check_api, params)

        headers, _ = generate_headers(cookies['a1'], splice_api)
        resp = requests.get(
            self.base_url + splice_api,
            headers=headers, cookies=cookies,
            timeout=REQUEST_TIMEOUT
        )
        res = self._parse_response_json(resp)
        if not res.get('success'):
            return False, res.get('msg', '验证码验证失败'), {'cookies': cookies}
        mobile_token = (res.get('data') or {}).get('mobile_token')
        if not mobile_token:
            return False, res.get('msg', '验证码响应缺少 mobile_token'), {'cookies': cookies, 'res_json': res}

        login_api = '/api/sns/web/v2/login/code'
        data = {"mobile_token": mobile_token, "zone": zone, "phone": phone}
        headers, data = generate_headers(cookies['a1'], login_api, data)
        resp = requests.post(
            self.base_url + login_api,
            headers=headers, cookies=cookies, data=data,
            timeout=REQUEST_TIMEOUT
        )
        for key, value in resp.cookies.items():
            cookies[key] = value

        res = self._parse_response_json(resp)
        if not res.get('success'):
            return False, res.get('msg', '登录失败'), {'cookies': cookies}
        session = (res.get('data') or {}).get('session')
        if not session:
            return False, res.get('msg', '登录响应缺少 session'), {'cookies': cookies, 'res_json': res}
        cookies['web_session'] = session
        return True, '成功', {
            'cookies': cookies,
            'res_json': res,
        }

    @staticmethod
    def cookies_to_str(cookies):
        return '; '.join(f'{k}={v}' for k, v in cookies.items())

    @staticmethod
    def show_qrcode_terminal(url):
        qr = qrcode.QRCode(box_size=1, border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)

    @staticmethod
    def show_qrcode_image(url):
        qr = qrcode.QRCode(box_size=10, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.show()

    def qrcode_login(self, show_in_terminal=True):
        logger.info('[1/4] 正在生成初始cookies...')
        cookies = self.generate_init_cookies()
        logger.info(f'初始 Cookie 字段: {sorted(cookies.keys())}')

        logger.info('[2/4] 正在获取二维码...')
        success, msg, qr_data = self.generate_qrcode(cookies)
        if not success:
            logger.error(f'获取二维码失败: {msg}')
            return None
        cookies = qr_data['cookies']

        logger.info('请使用小红书APP扫描以下二维码:')
        if show_in_terminal:
            self.show_qrcode_terminal(qr_data['qr_url'])
        else:
            self.show_qrcode_image(qr_data['qr_url'])

        logger.info('[3/4] 等待扫码...')
        while True:
            success, msg, cookies = self.check_qrcode_status(
                qr_data['qr_id'], qr_data['code'], cookies
            )
            if success:
                logger.info(msg)
                break
            if msg == '二维码已过期':
                logger.error(msg)
                return None
            time.sleep(2)

        logger.info('[4/4] 验证登录状态...')
        success, user_info, cookies = self.get_user_info(cookies)
        if success:
            logger.info(f'用户: {user_info.get("nickname", "未知")} (RedID: {user_info.get("red_id", "未知")})')
        else:
            logger.warning('获取用户信息失败，但cookies可能仍有效')

        cookies_str = self.cookies_to_str(cookies)
        logger.success('登录成功，Cookie 已生成')
        return cookies_str

    def phone_login(self):
        logger.info('[1/4] 正在生成初始cookies...')
        cookies = self.generate_init_cookies()
        logger.info(f'初始 Cookie 字段: {sorted(cookies.keys())}')

        phone = input('请输入手机号: ')
        logger.info('[2/4] 正在发送验证码...')
        success, msg, _ = self.send_phone_code(phone, cookies)
        if not success:
            logger.error(f'发送失败: {msg}')
            return None
        logger.info('验证码已发送')

        code = input('请输入验证码: ')
        logger.info('[3/4] 正在验证...')
        success, msg, result = self.login_by_phone(phone, code, cookies)
        if not success:
            logger.error(f'验证失败: {msg}')
            return None
        cookies = result['cookies']

        logger.info('[4/4] 验证登录状态...')
        success, user_info, cookies = self.get_user_info(cookies)
        if success:
            logger.info(f'用户: {user_info.get("nickname", "未知")} (RedID: {user_info.get("red_id", "未知")})')

        cookies_str = self.cookies_to_str(cookies)
        logger.success('登录成功，Cookie 已生成')
        return cookies_str


if __name__ == '__main__':
    login_api = XHSLoginApi()
    # cookies_str = login_api.qrcode_login(show_in_terminal=True)
    cookies_str = login_api.phone_login()

    xhs_apis = XHS_Apis()
    # 获取用户信息
    user_url = 'https://www.xiaohongshu.com/user/profile/67a332a2000000000d008358?xsec_token=ABTf9yz4cLHhTycIlksF0jOi1yIZgfcaQ6IXNNGdKJ8xg=&xsec_source=pc_feed'
    success, msg, user_info = xhs_apis.search_note("888666", cookies_str)
    print(success, msg, user_info)
