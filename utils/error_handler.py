# -*- coding: utf-8 -*-
"""
ErrorHandler — 全局异常处理

提供 @handle_errors 装饰器，自动捕获异常并弹出 QMessageBox 提示用户。
支持 Maya 和独立模式。
"""

import functools
import traceback


def handle_errors(context="", show_dialog=True, parent=None):
    """
    全局异常处理装饰器。

    用法:
      @handle_errors(context="\u52a0\u8f7d\u6750\u8d28\u5e93")
      def load_library(self, path):
          ...

    行为:
      - 捕获所有异常
      - 打印 traceback 到 stderr
      - show_dialog=True 时弹出 QMessageBox 提示用户
      - 不中断程序流程

    Args:
        context: 操作上下文描述（用于错误信息）
        show_dialog: 是否弹窗提示用户
        parent: 父 widget（自动从 args[0] 推断）
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                # 打印 traceback
                traceback.print_exc()

                # 弹窗提示
                if show_dialog:
                    # 尝试获取 parent widget
                    pw = parent
                    if pw is None and args:
                        first = args[0]
                        if hasattr(first, "isWidgetType") and first.isWidgetType():
                            pw = first

                    ctx = context or func.__name__
                    msg = f"\u64cd\u4f5c [{ctx}] \u5931\u8d25:\n{str(e)}"

                    try:
                        from PySide6.QtWidgets import QMessageBox
                    except ImportError:
                        try:
                            from PySide2.QtWidgets import QMessageBox
                        except ImportError:
                            print(f"[ErrorHandler] {msg}")
                            return None

                    QMessageBox.critical(pw, "\u9519\u8bef", msg)

                return None

        return wrapper

    return decorator
