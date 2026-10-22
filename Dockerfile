FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt requirements-lock.txt ./
RUN pip install -r requirements-lock.txt

COPY . .

EXPOSE 8000

# --skip-geocode keeps startup offline: stations are placed from the bundled
# Gazetteer extract only. Run `load_fuel_prices` without it once to geocode
# the few unmatched places via Nominatim.
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py load_fuel_prices --skip-geocode && python manage.py runserver 0.0.0.0:8000"]
