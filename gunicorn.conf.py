# Gunicorn — gevent worker 在加载 wsgi 应用前完成 monkey patch；入口用 wsgi:app，勿在 app.py 里 patch。
# post_worker_init：在 worker 就绪后衔接 psycopg 与 gevent（与 dev.py 导入前 patch 对称）。
bind = '0.0.0.0:8000'
workers = 4
worker_class = 'gevent'
keepalive = 120
timeout = 360
graceful_timeout = 30
worker_connections = 1000


def post_worker_init(worker):
    import psycogreen.gevent
    psycogreen.gevent.patch_psycopg()
