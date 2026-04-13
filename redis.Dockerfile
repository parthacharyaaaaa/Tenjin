FROM redis:7-alpine

RUN mkdir -p /data /var/log/redis /usr/local/etc/redis

RUN chown -R redis:redis /data /var/log/redis /usr/local/etc/redis

WORKDIR /data

USER redis

EXPOSE 6379

CMD ["redis-server", "/usr/local/etc/redis/redis.conf"]