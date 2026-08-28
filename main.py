# -*- coding: utf-8 -*-
"""
main.py —— 程序入口

两种运行模式（同一套 Companion 核心，只是消息渠道不同）：

    python main.py                    微信模式（默认）：扫码登录后监听微信消息
    python main.py terminal           终端模式：命令行对话（原 main.py 的体验）
    python main.py --test-send [文本]  向登录者本人发一条测试消息

架构：
    Terminal ─┐
              ├──> Companion.chat(user_id, message) ──> Conversation/Memory/LLM
    WeChat  ──┘        （Gateway 负责微信侧的登录、长轮询、收发）
"""
import sys

from config import setup_logging

# 终端模式下对话者的固定 user_id（记忆按用户隔离）
TERMINAL_USER_ID = "terminal"


def run_terminal(companion) -> None:
    """终端对话模式：保留原 main.py 的交互体验。"""
    print("陪陪已上线（终端模式）。输入 exit 退出。")
    print("提示：直接运行 python main.py（不带参数）即可切换到微信模式。")

    while True:
        try:
            user_input = input("你：")
        except (EOFError, KeyboardInterrupt):
            print("\n再见～")
            return

        if user_input.strip() == "exit":
            print("再见～")
            return
        if not user_input.strip():
            continue

        try:
            response = companion.chat(TERMINAL_USER_ID, user_input)
        except Exception as e:
            # LLM 异常不退出程序，提示后继续
            print(f"陪陪出错了：{e}")
            continue

        print("陪陪：", response)


def run_wechat(companion, test_send: str | None = None) -> None:
    """微信网关模式。"""
    from wechat.gateway import WeChatGateway

    gateway = WeChatGateway(companion)

    if test_send is not None:
        # 测试模式：只发一条测试消息就退出（验收 Test 5）
        gateway.send_test_message(test_send)
        return

    gateway.start()


def main() -> None:
    # 初始化日志（控制台 + logs/app.log）
    setup_logging()

    # Companion 核心（终端和微信共用同一份 Memory / LLM）
    from companion.companion import Companion

    companion = Companion()

    # 解析运行模式：默认微信模式（不带参数直接进入）
    #   python main.py             → 微信模式
    #   python main.py terminal    → 终端对话模式
    args = sys.argv[1:]
    mode = args[0] if args else "wechat"

    if mode == "terminal":
        run_terminal(companion)
        return

    # 微信模式（test_send 为 None 时正常启动网关）
    test_send = None
    if "--test-send" in args:
        idx = args.index("--test-send")
        test_send = args[idx + 1] if idx + 1 < len(args) else "这是一条测试消息 🎉"
    run_wechat(companion, test_send)


if __name__ == "__main__":
    main()
