FROM python:3.10-slim

WORKDIR /app

# تثبيت المتطلبات
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ بقية المشروع
COPY . .

# PORT تعيّنه Spaces تلقائيًا إلى 7860
ENV PORT=7860

# (اختياري) تعريض المنفذ لمرجع فقط
EXPOSE 7860

# تشغيل Uvicorn
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-7860}"]
