FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

ENV PYTHONUNBUFFERED=1

# Install Google Chrome stable (real binary, not snap redirect like chromium-browser on Jammy)
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg xvfb \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor > /etc/apt/trusted.gpg.d/google.gpg \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browsers (pre-installed in base image, reinstall to confirm)
RUN playwright install chromium

# Download matching chromedriver via webdriver-manager at build time
RUN python -c "from webdriver_manager.chrome import ChromeDriverManager; p=ChromeDriverManager().install(); print('chromedriver:', p)"

COPY *.py ./
COPY chiletrabajos/ ./chiletrabajos/
COPY trabajando/ ./trabajando/
COPY indeed/ ./indeed/

CMD ["python", "-u", "main.py"]
