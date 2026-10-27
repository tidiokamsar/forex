"""Gunicorn settings for the ASTRA dashboard (Linux). The app listens on loopback only.

Remote clients reach it only through the local TLS reverse proxy (deploy/nginx/astra.conf.example).
A second instance cannot start: the port is already bound, and systemd runs a single unit.
"""

bind = "127.0.0.1:8765"
workers = 2
worker_class = "gthread"
threads = 4
timeout = 30
graceful_timeout = 20
keepalive = 5
# Request size limits (the app also refuses bodies over 64 KiB).
limit_request_line = 4094
limit_request_fields = 50
limit_request_field_size = 8190
# Only the local proxy may set X-Forwarded-Proto; the app reads X-Forwarded-For through ProxyFix (one hop).
forwarded_allow_ips = "127.0.0.1,::1"
# Accept only X-Forwarded-Proto for the scheme (the defaults also read X-Forwarded-Ssl/-Protocol) and no
# forwarder-provided SCRIPT_NAME/PATH_INFO.
secure_scheme_headers = {"X-FORWARDED-PROTO": "https"}
forwarder_headers = ""
# Build the app once in the master: the CSRF key and the databases are created before workers fork
# (no first-boot race between workers). Stores open one connection per operation, so forking is safe.
preload_app = True
# Access log to journald without cookies or query strings: method, path, status, size, duration.
accesslog = "-"
access_log_format = '%({x-forwarded-for}i)s "%(m)s %(U)s" %(s)s %(B)s %(M)sms'
errorlog = "-"
loglevel = "info"
proc_name = "astra-web"
