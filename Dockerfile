FROM python:3.10-slim-bullseye

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    wkhtmltopdf \
    libpango-1.0-0 \
    libpango1.0-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/

COPY . /app/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir gunicorn XlsxWriter \
    && pip install --no-cache-dir -r requirements.txt

EXPOSE 8092

ENTRYPOINT ["gunicorn", "-w", "2", "-b", "0.0.0.0:8092", "--timeout", "300", "organizer_extraction.wsgi"]
