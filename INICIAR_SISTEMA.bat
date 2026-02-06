@echo off
title Splice - Atualizar banco e iniciar sistema
cd /d %~dp0
echo.
echo ===========================================
echo  ATUALIZANDO BANCO (colunas novas)
echo ===========================================
echo.

if exist venv\Scripts\activate.bat (
  call venv\Scripts\activate.bat
)

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
