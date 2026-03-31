# Gunicorn — gevent worker，适合 SSE 长连接（与 nginx proxy_read_timeout 等配合调优）
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
