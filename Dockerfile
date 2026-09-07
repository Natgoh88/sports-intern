# Not tested locally - no Docker available in the environment this was
# built in. Straightforward image (python:slim + pip install + copy),
# but if `docker compose up` fails, check this file first.

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8501

CMD ["streamlit", "run", "dashboard.py", "--server.address=0.0.0.0"]
