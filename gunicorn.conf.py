import multiprocessing

max_requests = 1000
max_requests_jitter = 50

log_file = "-"  # Output logs to stdout

bind = "0.0.0.0:8000"  # Bind to all network interfaces on port 8000

worker_class = "gthread"  # Use the gthread worker class
threads = multiprocessing.cpu_count() * 2  # Number of threads = 2 * number of CPU cores

workers = (multiprocessing.cpu_count() * 2) + 1  # Number of Gunicorn worker processes


