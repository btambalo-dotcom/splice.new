@echo off
title Splice - Atualizar banco(s) e iniciar sistema
cd /d %~dp0

REM --- Garantir venv e dependencias ---
if not exist venv\Scripts\python.exe (
  python -m venv venv
)
call venv\Scripts\activate.bat
pip install -r requirements.txt
REM --- Fim venv ---


echo ===========================================
echo  ATUALIZANDO BANCOS SQLITE (.db)
echo ===========================================
echo.

python auto_migrate_all_dbs.py

echo.
echo ===========================================
echo  INICIANDO SISTEMA
echo ===========================================
echo.

python app.py

echo.
echo (Janela pode ser fechada quando desejar.)
pause