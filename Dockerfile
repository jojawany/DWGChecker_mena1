FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# المنفذ الافتراضي محليًا 8000، وعلى Render يمرر PORT تلقائيًا
EXPOSE 8000
CMD sh -c "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"
