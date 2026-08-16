FROM python:3.11-slim

WORKDIR /app

# Sistemske ovisnosti: tvoje + WeasyPrint runtime (Cairo, Pango, GDK-Pixbuf, fontovi)
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    gdal-bin \
    libgdal-dev \
    gettext \
    # WeasyPrint runtime deps:
    libcairo2 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    shared-mime-info \
    fonts-dejavu-core \
    libffi8 \
    && rm -rf /var/lib/apt/lists/*

# Kopiraj requirements i instaliraj Python ovisnosti
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopiraj aplikaciju
COPY app/ .

# Stvori potrebne direktorije
RUN mkdir -p media static logs

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
