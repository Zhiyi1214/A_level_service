# Gunicorn — gevent worker 会在 worker 进程内对标准库做 monkey patch，勿在 app 里无条件重复 patch_all。
# post_worker_init 仅负责 psycopg 与 gevent 协同（Gunicorn 不处理第三方驱动）。
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
