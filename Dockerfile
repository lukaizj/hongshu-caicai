FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Node.js for execjs (Xiaohongshu signature generation)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install crypto-js required by Xiaohongshu signature JS
COPY package.json package-lock.json ./
RUN npm install --production

COPY . .

# Create data directories
RUN mkdir -p datas/excel_datas datas/media_datas

EXPOSE 18080

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV HOST=0.0.0.0
ENV PORT=18080

# Default credentials for web console
ENV APP_USERNAME=admin
ENV APP_PASSWORD=admin

CMD ["python", "web_app.py"]
