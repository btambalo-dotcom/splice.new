@echo off
title Splice - Atualizar banco(s) e iniciar sistema
cd /d %~dp0

echo ===========================================
echo  PREPARANDO AMBIENTE (venv + dependencias)
echo ===========================================
echo.

REM Cria venv se nao existir
if not exist venv\Scripts\python.exe (
  python -m venv venv
)

REM Instala/atualiza dependencias
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt

echo.
echo ===========================================
echo  ATUALIZANDO BANCOS SQLITE (.db)
echo ===========================================
echo.

venv\Scripts\python.exe auto_migrate_all_dbs.py

echo.
echo ===========================================
echo  INICIANDO SISTEMA
echo ===========================================
echo.

venv\Scripts\python.exe app.py

echo.
echo (Janela pode ser fechada quando desejar.)
pause
