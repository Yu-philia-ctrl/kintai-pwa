# ========================================================
# kintai-server Dockerfile
# jinjer_server.py を Docker コンテナで実行するための設定
# ========================================================

FROM python:3.11-slim

# 必要なシステムパッケージ
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Docker CLI をインストール (static binary — daemon は不要)
# コンテナ内から /var/run/docker.sock 経由でホストの Docker を操作する
ARG DOCKER_VERSION=27.5.1
RUN curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_VERSION}.tgz" \
    | tar xz --strip-components=1 -C /usr/local/bin docker/docker \
    && chmod +x /usr/local/bin/docker

WORKDIR /app

# Python 依存パッケージ (playwright は不要 = scraper 機能は Mac ネイティブ側で実行)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# アプリ本体をコピー
COPY jinjer_server.py report_sync.py generate_structure.py ./
COPY index.html sw.js manifest.json recover.html ./
COPY icon-apple.png icon-192.png icon-512.png ./

# データ永続化ディレクトリ（ホスト側ボリュームをマウント）
RUN mkdir -p /app/data /app/logs

EXPOSE 8899

# ヘルスチェック (30秒ごとに /api/health をポーリング)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fs http://localhost:8899/api/health || exit 1

CMD ["python3", "-u", "jinjer_server.py"]
