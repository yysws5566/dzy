"""
Windows 控制台 UTF-8 编码修复（单例模式）
其他模块导入此模块即可自动处理编码
"""
import sys
import io

_utf8_fixed = False


def fix_encoding():
    """确保控制台使用 UTF-8 编码（仅执行一次）"""
    global _utf8_fixed
    if _utf8_fixed:
        return
    _utf8_fixed = True

    if sys.platform == "win32":
        # 方法1: 重新配置 stdout/stderr
        try:
            if hasattr(sys.stdout, "buffer"):
                sys.stdout = io.TextIOWrapper(
                    sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
                )
            if hasattr(sys.stderr, "buffer"):
                sys.stderr = io.TextIOWrapper(
                    sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
                )
        except Exception:
            pass

        # 方法2: 设置默认编码
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


# 自动执行
fix_encoding()
