FROM python:3.11-slim

WORKDIR /srv
RUN pip install --no-cache-dir requests pycryptodome

COPY merge.py sources.json entrypoint.sh ./
RUN chmod +x entrypoint.sh

# 融合产物目录（挂载出来可持久化）
VOLUME /srv/out
ENV MERGE_INTERVAL=86400
EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
