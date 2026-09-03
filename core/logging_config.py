# -*- coding: utf-8 -*-
"""
=========================================================================
 SonicNarrative · 日志配置（core/logging_config.py）
=========================================================================
 统一日志格式，并把 uvicorn 自带的日志也接管过来，
 保证控制台里所有输出长得一致，方便排查问题。

 日志级别可以用环境变量调：
     set SONIC_LOG_LEVEL=DEBUG
=========================================================================
"""

from __future__ import annotations

import logging
import os
import sys

# 日志级别（INFO / DEBUG / WARNING ...）
LOG_LEVEL = os.getenv("SONIC_LOG_LEVEL", "INFO").upper()

# 格式：时间 | 级别 | 模块名 | 内容
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-26s | %(message)s"
DATE_FORMAT = "%H:%M:%S"


def setup_logging(level: str | None = None) -> None:
    """配置全局日志。

    参数
    ----
    level : 日志级别字符串；不传则读环境变量 SONIC_LOG_LEVEL，默认 INFO。
    """
    lv = getattr(logging, (level or LOG_LEVEL), logging.INFO)

    root = logging.getLogger()
    root.setLevel(lv)

    # ⚠️ uvicorn --reload 模式下，本模块会被重复执行。
    # 不先清空旧 handler 的话，每条日志会打印两遍。
    if root.handlers:
        root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(lv)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    root.addHandler(handler)

    # 让 uvicorn 的日志也交给 root 统一输出（否则格式会不一样）
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True


def get_logger(name: str) -> logging.Logger:
    """获取一个 logger。用法：logger = get_logger(__name__)"""
    return logging.getLogger(name)
