FROM jrottenberg/ffmpeg:6.0-ubuntu

# Python と必要ライブラリを追加
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

COPY extractor.py ./

ENV PYTHONUNBUFFERED=1

# キューから1件処理して終了する想定
ENTRYPOINT ["python3", "extractor.py"]

