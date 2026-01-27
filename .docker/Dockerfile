FROM python:3.12

USER 0

RUN mkdir -p /opt/thinvite

RUN useradd -ms /bin/bash thinvite

RUN chown -R thinvite /opt/thinvite

USER thinvite

WORKDIR /opt/thinvite

RUN pip install nicegui fastapi python-dotenv aiomysql discord.py twitchAPI

CMD ["python3", "/opt/thinvite/main.py"]


