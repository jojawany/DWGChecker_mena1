@echo off
setlocal
chcp 65001 >nul

rem ==== اذهب لمجلد هذا الملف ====
cd /d %~dp0

echo ================================
echo   DXF Checker - Local Runner
echo ================================
echo.

rem ==== المنفذ (اختياري) ====
set PORT=%1
if "%PORT%"=="" set PORT=8000

rem ==== اختيار Python launcher ====
where py >nul 2>nul
if %errorlevel% neq 0 (
  echo لم يتم العثور على "py" - ساجرب "python"...
  where python >nul 2>nul || (
    echo ^> لا يوجد Python في PATH. ثبّتي Python 3.10+ ثم أعيدي المحاولة.
    pause
    exit /b 1
  )
  set PYTHON=python
) else (
  set PYTHON=py
)

rem ==== إنشاء البيئة الافتراضية إن لم تكن موجودة ====
if not exist ".venv\Scripts\python.exe" (
  echo ^> إنشاء البيئة الافتراضية .venv ...
  %PYTHON% -m venv .venv || (
    echo فشل إنشاء البيئة الافتراضية.
    pause
    exit /b 1
  )
)

rem ==== تفعيل البيئة ====
call ".venv\Scripts\activate.bat" || (
  echo فشل تفعيل البيئة الافتراضية.
  pause
  exit /b 1
)

rem ==== تثبيت المتطلبات ====
echo ^> ترقية pip ...
%PYTHON% -m pip install --upgrade pip

if exist "requirements.txt" (
  echo ^> تثبيت المتطلبات من requirements.txt ...
  %PYTHON% -m pip install -r requirements.txt
) else (
  echo ^> لم يتم العثور على requirements.txt - تثبيت الحزم الأساسية ...
  %PYTHON% -m pip install "fastapi[standard]" uvicorn ezdxf openpyxl reportlab matplotlib
)

rem ==== إعداد matplotlib بدون واجهة رسومية ====
set MPLBACKEND=Agg

rem ==== فتح المتصفح (اختياري) ====
start "" http://127.0.0.1:%PORT%

rem ==== تشغيل الخادم ====
echo.
echo ^> تشغيل الخادم على http://127.0.0.1:%PORT%  (Ctrl+C للإيقاف)
%PYTHON% -m uvicorn app:app --host 127.0.0.1 --port %PORT% --reload

if %errorlevel% neq 0 (
  echo حدث خطأ أثناء تشغيل الخادم.
  pause
)
endlocal
