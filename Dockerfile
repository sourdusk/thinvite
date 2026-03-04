FROM python:3.14

USER 0

RUN mkdir -p /opt/thinvite

RUN useradd -ms /bin/bash thinvite

RUN chown -R thinvite /opt/thinvite

USER thinvite

WORKDIR /opt/thinvite

COPY ./web/requirements.txt .

RUN pip install -r requirements.txt

CMD ["python3", "/opt/thinvite/main.py"]


