FROM python:3.10-slim

WORKDIR /app

# إعدادات مفيدة للحاوية
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# مهم لتفادي مشاكل العرض مع matplotlib على السيرفر
ENV MPLBACKEND=Agg

# تثبيت المتطلبات
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ بقية المشروع
COPY . .

# Hugging Face Spaces يمرر PORT=7860 تلقائياً
ENV PORT=7860
EXPOSE 7860

# تشغيل FastAPI عبر Uvicorn
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-7860}"]
