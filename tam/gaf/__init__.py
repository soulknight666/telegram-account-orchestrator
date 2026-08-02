"""从 GAFBot 移植过来的号包处理引擎（机器人前端使用）。

原作（MIT License）:
  GAFBot — https://github.com/kugua332334554/GAFBot
  Copyright (c) 2026 kugua311
  完整许可证文本见仓库根目录 NOTICE.GAFBot

这一包里的模块都是「上传号包 -> 处理 -> 回传结果」的自助式工具，与 TAM 自己
的「托管账号」体系（db/manager/autokick）并存，两者共用同一份 .env 与数据目录。

去重说明（只搬 TAM 没有的）：
- 踢其他设备  -> 用 TAM 自带的 autokick / manager.terminate_other_sessions
- 账号登录    -> 用 TAM 自带的 manager.send_code / sign_in / importer
- 在线取码    -> 用 TAM 自带的 codefetch
所以 GAFBot 的 tishebei.py / login.py / luyou.py 没有搬进来。

注意：这些模块在 import 时就会读环境变量（各种 _BACK 文案），所以必须先加载
.env 再导入。tam.bot 已经处理好了这个顺序，别绕过它直接 import。
"""

__all__ = [
    "shaihuo", "xiugai2fa", "shuangxiang", "yinsi", "huzhuan", "zhuanapi",
    "qingli", "shailiao", "chaibao", "shaiban", "fangzhaohui", "xiaohui",
    "passkey", "shaireg", "zhenghe", "pay", "okpay_sign",
]
