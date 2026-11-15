ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache tzdata

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

COPY app /app
COPY translations /translations

WORKDIR /
CMD ["python3", "-m", "app.main"]
