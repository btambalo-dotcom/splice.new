@echo off
title Splice - Atualizar banco e iniciar sistema
cd /d %~dp0

REM --- Garantir venv e dependencias ---
if not exist venv\Scripts\python.exe (
  python -m venv venv
)
call venv\Scripts\activate.bat
pip install -r requirements.txt
REM --- Fim venv ---

echo.
echo ===========================================
echo  ATUALIZANDO BANCO (colunas novas)
echo ===========================================
echo.

python fix_db.py

echo.
echo ===========================================
echo  INICIANDO SISTEMA
echo ===========================================
echo.

python app.py

echo.
echo (Janela pode ser fechada quando desejar.)
pause