FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

# Logs en tiempo real (sin buffering de stdout)
ENV PYTHONUNBUFFERED=1

# Instalar Chromium para Selenium (undetected-chromedriver)
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium-browser \
    chromium-chromedriver \
    xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/chromium-browser /usr/bin/chromium \
    && (ls /usr/lib/chromium-browser/chromedriver 2>/dev/null && ln -sf /usr/lib/chromium-browser/chromedriver /usr/bin/chromedriver || true)

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browsers ya están en la imagen base — solo instalar chromium por si acaso
RUN playwright install chromium

COPY *.py ./
COPY chiletrabajos/ ./chiletrabajos/
COPY trabajando/ ./trabajando/
COPY indeed/ ./indeed/

CMD ["python", "-u", "main.py"]
