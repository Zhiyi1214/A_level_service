"""Gunicorn / 生产 WSGI 入口。

勿在此调用 gevent.monkey.patch_all()：使用 ``-k gevent`` 时由 worker 在加载本模块
之前完成 monkey patch；psycopg 与 gevent 的衔接见 ``gunicorn.conf.py`` 的
``post_worker_init``。
"""
from app import app

__all__ = ['app']
