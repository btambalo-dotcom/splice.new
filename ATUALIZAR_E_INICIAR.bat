@echo off
title Splice - Atualizar banco(s) e iniciar sistema
cd /d %~dp0

echo ===========================================
echo  ATUALIZANDO BANCOS SQLITE (.db)
echo ===========================================
echo.

if exist venv\Scripts\activate.bat (
  call venv\Scripts\activate.bat
)

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
